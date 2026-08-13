"""Offline TDD for the one-way C4-result to C5-only authority transition."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
C4_RUN_ID = "c4-8e76fba0288047f9"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def auth_payload_sha256(value: dict[str, object]) -> str:
    candidate = dict(value)
    candidate.pop("payload_sha256", None)
    raw = json.dumps(
        candidate, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _module():
    return importlib.import_module("native_characterization_c5_authorization")


class NativeCharacterizationC5AuthorizationTests(TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        validation = root / "membind-validation"
        c4_root = validation / f"artifacts/native_characterization/runs/{C4_RUN_ID}"
        c4_root.mkdir(
            parents=True
        )
        freeze = validation / "artifacts/native_characterization/freeze_reference_aligned_64k.json"
        workplan = root / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
        c4_summary = c4_root / "e3_sync_async.json"
        judge_root = validation / "artifacts/judge_qualification/runs/jq-b00a9689796c1e67"
        judge_root.mkdir(parents=True)
        judge_runtime = judge_root / "runtime_identity.json"
        judge_summary = judge_root / "qualification_summary.json"
        for relative in _module().C5_LIVE_TCB_PATHS.values():
            destination = validation / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / relative).read_bytes())
        focused = validation / _module().C5_FOCUSED_REGRESSION_RELATIVE_PATH
        focused.parent.mkdir(parents=True, exist_ok=True)
        focused.write_text("Ran 80 tests in 1.000s\n\nOK\n", encoding="ascii")
        full = validation / _module().C5_FULL_REGRESSION_RELATIVE_PATH
        full.write_text("Ran 1086 tests in 2.000s\n\nOK\n", encoding="ascii")
        stale = validation / _module().C5_STALE_REGRESSION_RELATIVE_PATH
        stale.write_text("Ran 80 tests in 0.100s\n\nOK\n", encoding="ascii")
        freeze.write_bytes((ROOT / "artifacts/native_characterization/freeze_reference_aligned_64k.json").read_bytes())
        workplan.write_bytes((ROOT.parent / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md").read_bytes())
        c4_payload = {
            "schema_version": "membind.native-characterization-e3-sync-async.v1",
            "status": "complete",
            "run_id": C4_RUN_ID,
            "block_count": 10,
            "episode_count": 490,
            "mergeable": False,
            "durable_evidence": {
                "enqueue_count": 245,
                "publication_count": 490,
                "episode_checkpoint_count": 490,
                "block_checkpoint_count": 10,
            },
        }
        c4_payload["payload_sha256"] = auth_payload_sha256(c4_payload)
        c4_summary.write_text(
            json.dumps(c4_payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        c4_checkpoint = {
            "schema_version": "membind.native-characterization-c4-checkpoint.v1",
            "run_id": C4_RUN_ID,
            "stage": "C4/E3",
            "checkpoint_level": "root",
            "status": "incomplete_invalid_non_mergeable",
            "block_index": None,
            "source_sequence": None,
            "progress": {
                "completed_block_indices": list(range(10)),
                "completed_episode_count": 490,
                "failure_stage": "verification",
            },
            "failure": {
                "error_class": "builtins.TypeError",
                "token_envelope": {
                    "prompt_tokens": None,
                    "output_tokens": None,
                    "requested_max_tokens": None,
                },
            },
        }
        c4_checkpoint["payload_sha256"] = auth_payload_sha256(c4_checkpoint)
        (c4_root / "checkpoint.json").write_text(
            json.dumps(c4_checkpoint, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        c4_events = []
        for sequence in range(735):
            event = {
                "schema_version": "membind.native-characterization-c4-event.v1",
                "run_id": C4_RUN_ID,
                "event_sequence": sequence,
                "event_type": "enqueue" if sequence < 245 else "publication",
            }
            event["payload_sha256"] = auth_payload_sha256(event)
            c4_events.append(event)
        c4_failure = {
            "schema_version": "membind.native-characterization-c4-event.v1",
            "run_id": C4_RUN_ID,
            "event_sequence": 735,
            "event_type": "failure",
            "status": "incomplete_invalid_non_mergeable",
            "failure_scope": "stage",
            "failure_stage": "verification",
            "error_class": "builtins.TypeError",
            "block_index": None,
            "source_sequence": None,
            "completed_block_count": 10,
            "completed_episode_count": 490,
        }
        c4_failure["payload_sha256"] = auth_payload_sha256(c4_failure)
        c4_events.append(c4_failure)
        (c4_root / "events.jsonl").write_text(
            "".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                for item in c4_events
            ),
            encoding="ascii",
        )
        judge_fixture = {
            "schema_version": "membind.judge-qualification-freeze.v1",
            "protocol_id": "judge-qualification-v1.0",
            "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
            "strict_pass_gate": {"planned_item_count": 14},
            "items": [
                {
                    "item_index": index,
                    "item_id": f"item-{index:02d}",
                    "human_label": index % 2 == 0,
                }
                for index in range(14)
            ],
        }
        judge_fixture["payload_sha256"] = auth_payload_sha256(judge_fixture)
        judge_freeze = judge_root / "fixture_freeze.json"
        judge_freeze.write_text(
            json.dumps(judge_fixture, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        runtime = {
            "schema_version": "membind.judge-runtime-identity.v1",
            "run_id": "jq-b00a9689796c1e67",
            "identity": {
                "served_model_name": "qwen3-32b-fp8",
                "vllm_version": "0.26.0",
                "max_model_len": 65536,
                "effective_enable_thinking": False,
                "backend_public_config": {
                    "backend": "openai_compatible_chat_completions",
                    "served_model_name": "qwen3-32b-fp8",
                    "temperature": 0,
                    "max_tokens": 10,
                    "n": 1,
                    "max_attempts": 1,
                    "sdk_hidden_retries": 0,
                    "effective_enable_thinking": False,
                },
            },
        }
        runtime["payload_sha256"] = auth_payload_sha256(runtime)
        judge_runtime.write_text(
            json.dumps(runtime, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        manifest = {
            "schema_version": "membind.judge-qualification-run.v1",
            "protocol_id": "judge-qualification-v1.0",
            "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
            "run_id": "jq-b00a9689796c1e67",
            "freeze_file_sha256": _sha(judge_freeze),
            "freeze_payload_sha256": judge_fixture["payload_sha256"],
            "runtime_identity_file_sha256": _sha(judge_runtime),
            "runtime_identity_payload_sha256": runtime["payload_sha256"],
        }
        manifest["payload_sha256"] = auth_payload_sha256(manifest)
        judge_manifest = judge_root / "manifest.json"
        judge_manifest.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        judge_event_values = []
        previous = None
        for sequence in range(28):
            event = {
                "schema_version": "membind.judge-qualification-event.v1",
                "run_id": "jq-b00a9689796c1e67",
                "event_sequence": sequence,
                "event_type": (
                    "dispatch_intent_durable"
                    if sequence % 2 == 0
                    else "terminal_success"
                ),
                "item_index": sequence // 2,
                "previous_event_sha256": previous,
            }
            event["payload_sha256"] = auth_payload_sha256(event)
            previous = event["payload_sha256"]
            judge_event_values.append(event)
        judge_events = judge_root / "events.jsonl"
        judge_events.write_text(
            "".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                for item in judge_event_values
            ),
            encoding="ascii",
        )
        checkpoint = {
            "schema_version": "membind.judge-qualification-checkpoint.v1",
            "run_id": "jq-b00a9689796c1e67",
            "status": "complete",
            "phase": "finalized",
            "next_item_index": 14,
            "terminal_item_count": 14,
            "event_count": 28,
            "last_event_payload_sha256": previous,
            "freeze_payload_sha256": judge_fixture["payload_sha256"],
            "runtime_identity_payload_sha256": runtime["payload_sha256"],
            "failed_item_id": None,
            "failure_class": None,
        }
        checkpoint["payload_sha256"] = auth_payload_sha256(checkpoint)
        judge_checkpoint = judge_root / "checkpoint.json"
        judge_checkpoint.write_text(
            json.dumps(checkpoint, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        summary = {
            "schema_version": "membind.judge-qualification-summary.v1",
            "protocol_id": "judge-qualification-v1.0",
            "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
            "run_id": "jq-b00a9689796c1e67",
            "attempt_status": "complete",
            "qualification_status": "PASS",
            "mergeable": True,
            "planned_item_count": 14,
            "terminal_item_count": 14,
            "eligible_item_count": 14,
            "agreement_count": 14,
            "observed_agreement": 1.0,
            "cohens_kappa": 1.0,
            "invalid_output_count": 0,
            "service_error_count": 0,
            "retry_count_total": 0,
            "confusion_matrix": {
                "true_positive": 7,
                "true_negative": 7,
                "false_positive": 0,
                "false_negative": 0,
            },
            "freeze_payload_sha256": judge_fixture["payload_sha256"],
            "runtime_identity_payload_sha256": runtime["payload_sha256"],
        }
        summary["payload_sha256"] = auth_payload_sha256(summary)
        judge_summary.write_text(
            json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        state = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "current_action_scope": "native_characterization_c4_live_only",
            "status": "native_characterization_c4_live_only",
            "authorized_live_actions": ["native_characterization_c4"],
            "next_allowed_action": "run_native_characterization_c4",
            "native_characterization_live_authorized": True,
            "live_h0_candidate_authorized": False,
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "current_blocker": None,
            "stage_progress": {
                "native_characterization": "c2_c3_complete_c4_offline_tdd_pending"
            },
        }
        state_path = validation / "CURRENT_STATE.json"
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="ascii")
        return validation, state_path, c4_summary

    def test_transition_binds_exact_frozen_c5_matrix_and_grants_only_c5(self) -> None:
        auth = _module()
        with tempfile.TemporaryDirectory() as temporary:
            validation, state_path, c4_summary = self._fixture(Path(temporary))
            result = auth.authorize_c5(
                validation_root=validation,
                state_path=state_path,
                c4_summary_path=c4_summary,
                c4_summary_sha256=_sha(c4_summary),
            )
            state = json.loads(state_path.read_text("ascii"))
            expected_freeze_payload_sha256 = json.loads(
                (
                    validation
                    / "artifacts/native_characterization/freeze_reference_aligned_64k.json"
                ).read_text("ascii")
            )["payload_sha256"]
            expected_c4_payload_sha256 = json.loads(
                c4_summary.read_text("ascii")
            )["payload_sha256"]

        self.assertEqual(result["status"], "authorized")
        self.assertEqual(state["current_action_scope"], "native_characterization_c5_live_only")
        self.assertEqual(state["authorized_live_actions"], ["native_characterization_c5"])
        self.assertEqual(state["next_allowed_action"], "run_native_characterization_c5")
        evidence = state["native_characterization_c5_authorization"]
        self.assertEqual(evidence["history_id"], "07741c45")
        self.assertEqual(evidence["episode_count"], 49)
        self.assertEqual(evidence["concurrency_grid"], [1, 2, 4, 8])
        self.assertEqual(
            evidence["graph_namespaces"],
            [
                "nc-e4-1434fcb947df5c3d",
                "nc-e4-b352061ffa0d4b21",
                "nc-e4-c15538d1fe2801cb",
                "nc-e4-2a427029b1a8b2ac",
            ],
        )
        self.assertEqual(evidence["screening_pass_count"], 1)
        self.assertEqual(
            evidence["freeze_payload_sha256"], expected_freeze_payload_sha256
        )
        self.assertEqual(
            evidence["c4_summary_payload_sha256"],
            expected_c4_payload_sha256,
        )
        self.assertEqual(
            evidence["judge_qualification_summary_path"],
            "artifacts/judge_qualification/runs/jq-b00a9689796c1e67/qualification_summary.json",
        )
        self.assertEqual(
            evidence["judge_runtime_identity_path"],
            "artifacts/judge_qualification/runs/jq-b00a9689796c1e67/runtime_identity.json",
        )
        self.assertEqual(len(evidence["judge_qualification_summary_sha256"]), 64)
        self.assertEqual(len(evidence["judge_runtime_identity_sha256"]), 64)
        disposition = evidence["c4_disposition"]
        self.assertEqual(disposition["run_id"], C4_RUN_ID)
        self.assertEqual(disposition["summary_status"], "complete")
        self.assertEqual(disposition["summary_mergeable"], False)
        self.assertEqual(
            disposition["bounded_use"],
            "c4_summary_sufficient_for_c5_progression_without_reclassifying_"
            "c4_attempt_mergeable",
        )
        self.assertEqual(
            [
                disposition["retained_failure"]["attempt_status"],
                disposition["retained_failure"]["failure_stage"],
                disposition["retained_failure"]["error_class"],
            ],
            [
                "incomplete_invalid_non_mergeable",
                "verification",
                "builtins.TypeError",
            ],
        )
        self.assertEqual(
            disposition["checkpoint_path"],
            f"artifacts/native_characterization/runs/{C4_RUN_ID}/checkpoint.json",
        )
        self.assertEqual(disposition["event_count"], 736)
        self.assertEqual(disposition["failure_event_count"], 1)
        self.assertEqual(set(evidence["c5_live_tcb_paths"]), set(evidence["c5_live_tcb_sha256"]))
        self.assertGreaterEqual(len(evidence["c5_live_tcb_paths"]), 13)
        self.assertIn(
            "src/native_characterization_c5_live.py",
            evidence["c5_live_tcb_paths"].values(),
        )
        self.assertFalse(
            any(
                path.startswith("artifacts/tdd/")
                for path in evidence["c5_live_tcb_paths"].values()
            )
        )
        self.assertEqual(evidence["c5_focused_regression"]["status"], "green")
        self.assertGreater(evidence["c5_focused_regression"]["test_count"], 0)
        self.assertEqual(evidence["c5_full_offline_regression"]["status"], "green")
        self.assertGreater(evidence["c5_full_offline_regression"]["test_count"], 0)
        self.assertEqual(evidence["c5_stale_state_regression"]["status"], "green")
        self.assertGreater(evidence["c5_stale_state_regression"]["test_count"], 0)
        self.assertEqual(evidence["judge_closure"]["terminal_item_count"], 14)
        self.assertEqual(evidence["judge_closure"]["event_count"], 28)

    def test_c4_hash_drift_fails_before_state_write(self) -> None:
        auth = _module()
        with tempfile.TemporaryDirectory() as temporary:
            validation, state_path, c4_summary = self._fixture(Path(temporary))
            before = state_path.read_bytes()
            with self.assertRaisesRegex(auth.C5AuthorizationError, "c4_summary_hash_mismatch"):
                auth.authorize_c5(
                    validation_root=validation,
                    state_path=state_path,
                    c4_summary_path=c4_summary,
                    c4_summary_sha256="f" * 64,
                )
            self.assertEqual(state_path.read_bytes(), before)

    def test_freeze_and_c4_payload_seal_drift_fail_before_state_write(self) -> None:
        auth = _module()
        for artifact in ("freeze", "c4"):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as temporary:
                validation, state_path, c4_summary = self._fixture(Path(temporary))
                target = (
                    validation
                    / "artifacts/native_characterization/freeze_reference_aligned_64k.json"
                    if artifact == "freeze"
                    else c4_summary
                )
                value = json.loads(target.read_text("ascii"))
                if artifact == "freeze":
                    value["state_transition"]["authorization_status"] = "tampered"
                else:
                    value["unsealed_tamper"] = True
                target.write_text(
                    json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="ascii",
                )
                before = state_path.read_bytes()
                with self.assertRaisesRegex(
                    auth.C5AuthorizationError,
                    f"{artifact}_payload_mismatch",
                ):
                    auth.authorize_c5(
                        validation_root=validation,
                        state_path=state_path,
                        c4_summary_path=c4_summary,
                        c4_summary_sha256=_sha(c4_summary),
                    )
                self.assertEqual(state_path.read_bytes(), before)

    def test_transition_is_idempotent_but_cannot_widen_authority(self) -> None:
        auth = _module()
        with tempfile.TemporaryDirectory() as temporary:
            validation, state_path, c4_summary = self._fixture(Path(temporary))
            kwargs = {
                "validation_root": validation,
                "state_path": state_path,
                "c4_summary_path": c4_summary,
                "c4_summary_sha256": _sha(c4_summary),
            }
            first = auth.authorize_c5(**kwargs)
            first_bytes = state_path.read_bytes()
            second = auth.authorize_c5(**kwargs)
            self.assertEqual(second, first)
            self.assertEqual(state_path.read_bytes(), first_bytes)
            state = json.loads(first_bytes)
            state["authorized_live_actions"].append("service_admin")
            state_path.write_text(json.dumps(state, sort_keys=True), encoding="ascii")
            with self.assertRaisesRegex(auth.C5AuthorizationError, "source_state_not_exact"):
                auth.authorize_c5(**kwargs)

    def test_idempotent_target_cannot_drop_internal_payload_seals(self) -> None:
        auth = _module()
        for field in ("freeze_payload_sha256", "c4_summary_payload_sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                validation, state_path, c4_summary = self._fixture(Path(temporary))
                kwargs = {
                    "validation_root": validation,
                    "state_path": state_path,
                    "c4_summary_path": c4_summary,
                    "c4_summary_sha256": _sha(c4_summary),
                }
                auth.authorize_c5(**kwargs)
                state = json.loads(state_path.read_text("ascii"))
                state["native_characterization_c5_authorization"].pop(field)
                state_path.write_text(json.dumps(state, sort_keys=True), encoding="ascii")

                with self.assertRaisesRegex(
                    auth.C5AuthorizationError, "source_state_not_exact"
                ):
                    auth.authorize_c5(**kwargs)


if __name__ == "__main__":
    import unittest

    unittest.main()
