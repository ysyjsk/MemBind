"""TDD contracts for closing one infrastructure-interrupted C2 grant."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_c2_interruption import (  # noqa: E402
    ArtifactBinding,
    C2InterruptionBindings,
    C2InterruptionError,
    build_interrupted_state_and_report,
    finalize_c2_interruption,
)


RUN_ID = "c2-2fe3711c62933407"
GROUP_ID = "nc-e1e2-400b9b78c2c218df"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _sha(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def _with_payload(value: dict) -> dict:
    result = dict(value)
    result["payload_sha256"] = _sha(_canonical(result))
    return result


def _source_state() -> dict:
    return {
        "protocol_version": "current-validation-v1.3",
        "current_stage": "NATIVE_CHARACTERIZATION",
        "status": "native_characterization_c2_live_only",
        "current_blocker": None,
        "current_action_scope": "native_characterization_c2_live_only",
        "authorized_live_actions": ["native_characterization_c2"],
        "native_characterization_live_authorized": True,
        "live_h0_candidate_authorized": False,
        "service_admin_authorized": False,
        "next_allowed_action": "run_native_characterization_c2",
        "stage_progress": {
            "native_characterization": (
                "c0_c1_pass_reference_aligned_c2_authorized_from_episode_0"
            )
        },
        "native_characterization_reference_alignment": {
            "status": "c2_live_authorized",
            "reference_freeze_path": (
                "artifacts/native_characterization/freeze_reference_aligned.json"
            ),
            "reference_freeze_sha256": "f" * 64,
            "fresh_c2": {
                "live_authorized": True,
                "semantic_attempts_remaining": 1,
                "resume_allowed": False,
                "prefix_merge_allowed": False,
                "start_source_sequence": 0,
            },
            "cleanup": {"operator_authorized": False},
        },
        "native_characterization_reference_c2_authorization": {
            "live_authorized": True,
            "replacement_resume_allowed": False,
            "replacement_start_source_sequence": 0,
            "semantic_attempts_authorized": 1,
        },
    }


class C2InterruptionFixture:
    def __init__(self, temporary: str) -> None:
        self.repo = Path(temporary)
        self.validation = self.repo / "membind-validation"
        self.validation.mkdir()
        self.state = _source_state()
        self.state_path = self.validation / "CURRENT_STATE.json"
        self.state_path.write_bytes(_canonical(self.state) + b"\n")

        run_relative = f"artifacts/native_characterization/runs/{RUN_ID}"
        run_root = self.validation / run_relative
        block_root = run_root / "blocks/000_07741c45"
        block_root.mkdir(parents=True)
        completed_ids = [f"07741c45:{index}" for index in range(9)]
        history = [
            {
                "block_index": 0,
                "episode_id": episode_id,
                "event_type": "episode_completed",
                "history_id": "07741c45",
                "source_sequence": index,
                "status": "completed",
            }
            for index, episode_id in enumerate(completed_ids)
        ]
        checkpoint = _with_payload(
            {
                "schema_version": "membind.native-characterization-c2-checkpoint.v1",
                "run_id": RUN_ID,
                "stage": "C2",
                "status": "error",
                "error_code": "openai.APIConnectionError",
                "planned_block_indices": [0, 1, 2, 3],
                "completed_block_indices": [],
                "completed_episode_ids": completed_ids,
                "checkpoint_history": history,
            }
        )
        block_checkpoint = _with_payload(
            {
                **{key: value for key, value in checkpoint.items() if key != "payload_sha256"},
                "status": "episode_completed",
                "error_code": None,
            }
        )
        self.checkpoint = run_root / "checkpoint.json"
        self.block_checkpoint = block_root / "checkpoint.json"
        self.checkpoint.write_bytes(_canonical(checkpoint) + b"\n")
        self.block_checkpoint.write_bytes(_canonical(block_checkpoint) + b"\n")

        self.artifacts: list[ArtifactBinding] = []
        for name in ("events", "spans", "llm", "embedding", "db", "errors"):
            path = run_root / f"{name}.jsonl"
            lines = []
            for index in range(10):
                spans = []
                if name == "errors" and index == 9:
                    spans = [
                        {
                            "phase": "llm-transport",
                            "status": "error",
                            "error_code": "openai.APIConnectionError",
                        }
                    ]
                lines.append(
                    _canonical(
                        {
                            "schema_version": f"membind.native-characterization-c2-{name}.v1",
                            "run_id": RUN_ID,
                            "episode_id": f"07741c45:{index}",
                            "source_sequence": index,
                            "spans": spans,
                        }
                    )
                )
            encoded = b"\n".join(lines) + b"\n"
            path.write_bytes(encoded)
            self.artifacts.append(
                ArtifactBinding(
                    path=f"{run_relative}/{name}.jsonl",
                    sha256=_sha(encoded),
                    line_count=10,
                )
            )
        trace = block_root / "trace.jsonl"
        trace_encoded = b"\n".join(
            _canonical(
                {
                    "schema_version": "membind.native_characterization.trace.v1",
                    "run_id": RUN_ID,
                    "episode_id": f"07741c45:{index}",
                    "source_sequence": index,
                    "spans": [],
                }
            )
            for index in range(10)
        ) + b"\n"
        trace.write_bytes(trace_encoded)
        self.artifacts.append(
            ArtifactBinding(
                path=f"{run_relative}/blocks/000_07741c45/trace.jsonl",
                sha256=_sha(trace_encoded),
                line_count=10,
            )
        )

        self.outer_log = self.validation / "artifacts/tdd/c2-live.log"
        self.outer_log.parent.mkdir(parents=True)
        self.outer_log.write_text(
            "Error in generating LLM response: Connection error.\n"
            '{"error_code":"openai.APIConnectionError","status":"error"}\n',
            encoding="ascii",
        )
        self.freeze = self.validation / (
            "artifacts/native_characterization/freeze_reference_aligned.json"
        )
        self.freeze.write_text('{"frozen":true}\n', encoding="ascii")
        self.state["native_characterization_reference_alignment"][
            "reference_freeze_sha256"
        ] = _sha(self.freeze.read_bytes())
        self.state_path.write_bytes(_canonical(self.state) + b"\n")
        self.workplan = self.repo / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
        self.workplan.write_text("frozen workplan\n", encoding="ascii")
        self.report = self.validation / (
            f"artifacts/diagnostics/native_characterization_{RUN_ID}_interruption.json"
        )

        self.bindings = C2InterruptionBindings(
            run_id=RUN_ID,
            source_state_sha256=_sha(_canonical(self.state)),
            checkpoint_path=f"{run_relative}/checkpoint.json",
            checkpoint_sha256=_sha(self.checkpoint.read_bytes()),
            block_checkpoint_path=(
                f"{run_relative}/blocks/000_07741c45/checkpoint.json"
            ),
            block_checkpoint_sha256=_sha(self.block_checkpoint.read_bytes()),
            artifacts=tuple(self.artifacts),
            outer_log_path="artifacts/tdd/c2-live.log",
            outer_log_sha256=_sha(self.outer_log.read_bytes()),
            freeze_path="artifacts/native_characterization/freeze_reference_aligned.json",
            freeze_sha256=_sha(self.freeze.read_bytes()),
            workplan_path="MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md",
            workplan_sha256=_sha(self.workplan.read_bytes()),
            report_path=(
                f"artifacts/diagnostics/native_characterization_{RUN_ID}_interruption.json"
            ),
            graph_namespace=GROUP_ID,
        )


class NativeCharacterizationC2InterruptionTests(TestCase):
    def test_build_binds_interruption_and_revokes_every_live_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = C2InterruptionFixture(tmp)
            target, report = build_interrupted_state_and_report(
                fixture.state,
                validation_root=fixture.validation,
                repo_root=fixture.repo,
                bindings=fixture.bindings,
            )

        self.assertEqual(report["classification"], "infrastructure_interruption")
        self.assertEqual(report["completed_episode_count"], 9)
        self.assertEqual(report["failed_source_sequence"], 9)
        self.assertEqual(report["error_code"], "openai.APIConnectionError")
        self.assertFalse(report["attempt_valid"])
        self.assertFalse(report["attempt_mergeable"])
        self.assertFalse(report["resume_allowed"])
        self.assertFalse(report["semantic_attempt_consumed"])
        self.assertEqual(target["authorized_live_actions"], [])
        self.assertFalse(target["native_characterization_live_authorized"])
        self.assertFalse(target["service_admin_authorized"])
        self.assertEqual(target["status"], "native_characterization_cleanup_only")
        cleanup = target["native_characterization_reference_alignment"]["cleanup"]
        self.assertTrue(cleanup["operator_authorized"])
        self.assertEqual(cleanup["failed_attempt_id"], RUN_ID)
        self.assertEqual(cleanup["target_group_id"], GROUP_ID)
        metadata = target["native_characterization_c2_interruption"]
        self.assertFalse(metadata["attempt_valid"])
        self.assertFalse(metadata["attempt_mergeable"])
        self.assertFalse(metadata["resume_allowed"])
        self.assertTrue(metadata["cleanup_authorized"])

    def test_rejects_wrong_hash_source_drift_and_non_connection_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = C2InterruptionFixture(tmp)
            for bindings, reason in (
                (replace(fixture.bindings, checkpoint_sha256="0" * 64), "checkpoint_hash_mismatch"),
                (replace(fixture.bindings, source_state_sha256="0" * 64), "source_state_hash_mismatch"),
            ):
                with self.subTest(reason=reason):
                    with self.assertRaisesRegex(C2InterruptionError, reason):
                        build_interrupted_state_and_report(
                            fixture.state,
                            validation_root=fixture.validation,
                            repo_root=fixture.repo,
                            bindings=bindings,
                        )

            checkpoint = json.loads(fixture.checkpoint.read_text("ascii"))
            checkpoint["error_code"] = "json.decoder.JSONDecodeError"
            checkpoint["payload_sha256"] = _sha(
                _canonical({k: v for k, v in checkpoint.items() if k != "payload_sha256"})
            )
            fixture.checkpoint.write_bytes(_canonical(checkpoint) + b"\n")
            changed = replace(
                fixture.bindings,
                checkpoint_sha256=_sha(fixture.checkpoint.read_bytes()),
            )
            with self.assertRaisesRegex(C2InterruptionError, "checkpoint_contract_mismatch"):
                build_interrupted_state_and_report(
                    fixture.state,
                    validation_root=fixture.validation,
                    repo_root=fixture.repo,
                    bindings=changed,
                )

    def test_finalize_is_dry_by_default_then_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = C2InterruptionFixture(tmp)
            preview = finalize_c2_interruption(
                state_path=fixture.state_path,
                validation_root=fixture.validation,
                repo_root=fixture.repo,
                bindings=fixture.bindings,
                apply=False,
            )
            self.assertEqual(preview["status"], "validated_not_applied")
            self.assertFalse(fixture.report.exists())
            self.assertTrue(json.loads(fixture.state_path.read_text("ascii"))["native_characterization_live_authorized"])

            applied = finalize_c2_interruption(
                state_path=fixture.state_path,
                validation_root=fixture.validation,
                repo_root=fixture.repo,
                bindings=fixture.bindings,
                apply=True,
            )
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(fixture.report.is_file())
            target = json.loads(fixture.state_path.read_text("ascii"))
            self.assertFalse(target["native_characterization_live_authorized"])

            repeated = finalize_c2_interruption(
                state_path=fixture.state_path,
                validation_root=fixture.validation,
                repo_root=fixture.repo,
                bindings=fixture.bindings,
                apply=True,
            )
            self.assertEqual(repeated["status"], "already_applied")
            self.assertEqual(applied["target_state_sha256"], repeated["target_state_sha256"])


if __name__ == "__main__":
    import unittest

    unittest.main()
