"""TDD contracts for the one-shot Native characterization C2 authorization."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from current_state_gate import LiveAction, evaluate_live_action  # noqa: E402
from native_characterization_c2_authorization import (  # noqa: E402
    C2AuthorizationBindings,
    NativeCharacterizationC2AuthorizationError,
    authorize_native_characterization_c2_live_only,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class NativeCharacterizationC2AuthorizationTests(TestCase):
    def _fixture(self) -> tuple[Path, Path, dict[str, str], dict[str, object]]:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        validation = root / "membind-validation"
        validation.mkdir()
        run = validation / "artifacts/native_characterization/runs/c0-0123456789abcdef"
        run.mkdir(parents=True)

        manifest = {
            "schema_version": "membind.native-characterization-c0-result.v1",
            "artifact_id": "native-characterization-c0",
            "run_id": "c0-0123456789abcdef",
            "stage": "C0",
            "status": "pass",
            "add_episode_latency_ns": 10,
            "error_code": None,
            "runtime_config": {"classification": "U0"},
        }
        manifest["payload_sha256"] = _sha(_canonical(manifest))
        manifest_path = run / "manifest.json"
        manifest_path.write_bytes(_canonical(manifest) + b"\n")
        checkpoint = {
            "schema_version": "membind.native-characterization-checkpoint.v1",
            "run_id": manifest["run_id"],
            "stage": "C0",
            "status": "pass",
            "completed_source_sequences": [0],
            "manifest_payload_sha256": manifest["payload_sha256"],
            "error_code": None,
        }
        checkpoint["payload_sha256"] = _sha(_canonical(checkpoint))
        checkpoint_path = run / "checkpoint.json"
        checkpoint_path.write_bytes(_canonical(checkpoint) + b"\n")

        state = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_offline_only",
            "current_action_scope": "native_characterization_offline_only",
            "current_blocker": None,
            "next_allowed_action": "implement_c2_runner_offline",
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "h0_live_gate": "forbidden_native_characterization",
                "native_characterization": "c0_pass_c2_runner_tdd_pending",
            },
            "native_characterization_c0_completion": {
                "schema_version": "membind.native-characterization-c0-completion.v1",
                "manifest_path": str(manifest_path.relative_to(validation)),
                "manifest_sha256": _sha(manifest_path.read_bytes()),
                "checkpoint_path": str(checkpoint_path.relative_to(validation)),
                "checkpoint_sha256": _sha(checkpoint_path.read_bytes()),
                "run_id": manifest["run_id"],
                "grant_consumed": True,
                "c0_status": "pass",
            },
        }
        state_path = validation / "CURRENT_STATE.json"
        state_path.write_bytes(_canonical(state))

        workplan_path = root / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
        workplan_path.write_text(
            "# Native Graphiti Characterization Workplan v1.1\n"
            "WORKPLAN_FREEZE=true\n"
            "protocol_review_status=closed\n",
            encoding="ascii",
        )
        freeze_path = validation / "artifacts/native_characterization/freeze.json"
        freeze_path.parent.mkdir(parents=True, exist_ok=True)
        freeze_path.write_bytes(
            _canonical(
                {
                    "schema_version": "membind.native-characterization-freeze.v1",
                    "protocol": {
                        "freeze_marker": True,
                        "id": "native-characterization-v1.1",
                    },
                }
            )
        )
        source_path = validation / "src/native_characterization_c2.py"
        source_path.parent.mkdir(parents=True)
        source_path.write_text("# runner\n", encoding="ascii")
        test_path = validation / "tests/test_native_characterization_c2.py"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("# tests\n", encoding="ascii")
        log_path = validation / "artifacts/tdd/c2_green.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("OK\n", encoding="ascii")

        digests = {
            "source_state_sha256": _sha(state_path.read_bytes()),
            "workplan_sha256": _sha(workplan_path.read_bytes()),
            "freeze_sha256": _sha(freeze_path.read_bytes()),
            "c0_manifest_sha256": _sha(manifest_path.read_bytes()),
            "c0_checkpoint_sha256": _sha(checkpoint_path.read_bytes()),
            "c2_runner_source_sha256": _sha(source_path.read_bytes()),
            "c2_runner_test_sha256": _sha(test_path.read_bytes()),
            "c2_runner_green_log_sha256": _sha(log_path.read_bytes()),
            "c2_runner_source_path": str(source_path.relative_to(validation)),
            "c2_runner_test_path": str(test_path.relative_to(validation)),
            "c2_runner_green_log_path": str(log_path.relative_to(validation)),
        }
        return root, state_path, digests, state

    def _bindings(self, digests: dict[str, str]) -> C2AuthorizationBindings:
        return C2AuthorizationBindings(**digests, operator_service_ready=True)

    def test_rejects_missing_operator_service_confirmation(self) -> None:
        root, state_path, digests, _ = self._fixture()
        with self.assertRaisesRegex(
            NativeCharacterizationC2AuthorizationError,
            "operator_service_not_confirmed",
        ):
            authorize_native_characterization_c2_live_only(
                state_path,
                repo_root=root,
                bindings=C2AuthorizationBindings(
                    **digests, operator_service_ready=False
                ),
                dry_run=True,
            )

    def test_authorizes_only_c2_live_action_and_binds_runner_evidence(self) -> None:
        root, state_path, digests, source = self._fixture()
        before = deepcopy(source)
        target = authorize_native_characterization_c2_live_only(
            state_path,
            repo_root=root,
            bindings=self._bindings(digests),
            dry_run=True,
        )
        self.assertEqual(target["current_stage"], "NATIVE_CHARACTERIZATION")
        self.assertEqual(target["status"], "native_characterization_offline_only")
        self.assertEqual(
            target["current_action_scope"], "native_characterization_c2_live_only"
        )
        self.assertEqual(
            target["authorized_live_actions"], ["native_characterization_c2"]
        )
        self.assertEqual(target["next_allowed_action"], "run_native_characterization_c2")
        self.assertFalse(target["live_h0_candidate_authorized"])
        self.assertFalse(target["service_admin_authorized"])
        self.assertTrue(
            evaluate_live_action(
                target, LiveAction.NATIVE_CHARACTERIZATION_C2
            ).allowed
        )
        for action in LiveAction:
            if action is LiveAction.NATIVE_CHARACTERIZATION_C2:
                continue
            self.assertFalse(evaluate_live_action(target, action).allowed)
        metadata = target["native_characterization_c2_authorization"]
        self.assertTrue(metadata["live_authorized"])
        self.assertEqual(metadata["source_state_sha256"], digests["source_state_sha256"])
        self.assertEqual(
            metadata["c2_runner_source_sha256"],
            digests["c2_runner_source_sha256"],
        )
        self.assertEqual(
            metadata["c2_runner_green_log_sha256"],
            digests["c2_runner_green_log_sha256"],
        )
        self.assertNotIn("api_key", json.dumps(metadata).lower())
        self.assertNotIn("operator_service_ready", metadata)
        self.assertEqual(
            set(target) - set(before), {"native_characterization_c2_authorization"}
        )
        self.assertEqual(state_path.read_bytes(), _canonical(before))

    def test_evidence_drift_fails_without_state_write(self) -> None:
        root, state_path, digests, _ = self._fixture()
        log = root / "membind-validation" / digests["c2_runner_green_log_path"]
        log.write_text("FAILED\n", encoding="ascii")
        before = state_path.read_bytes()
        with self.assertRaisesRegex(
            NativeCharacterizationC2AuthorizationError,
            "c2_runner_green_log_hash_mismatch",
        ):
            authorize_native_characterization_c2_live_only(
                state_path,
                repo_root=root,
                bindings=self._bindings(digests),
                dry_run=False,
            )
        self.assertEqual(state_path.read_bytes(), before)

    def test_commit_is_idempotent_and_target_drift_fails_closed(self) -> None:
        root, state_path, digests, _ = self._fixture()
        first = authorize_native_characterization_c2_live_only(
            state_path,
            repo_root=root,
            bindings=self._bindings(digests),
            dry_run=False,
        )
        committed = state_path.read_bytes()
        target_bindings = self._bindings(
            {**digests, "source_state_sha256": _sha(committed)}
        )
        second = authorize_native_characterization_c2_live_only(
            state_path,
            repo_root=root,
            bindings=target_bindings,
            dry_run=False,
        )
        self.assertEqual(first, second)
        drifted = json.loads(committed)
        drifted["next_allowed_action"] = "tampered"
        state_path.write_bytes(_canonical(drifted))
        drift_bindings = self._bindings(
            {**digests, "source_state_sha256": _sha(state_path.read_bytes())}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2AuthorizationError,
            "target_state_drift",
        ):
            authorize_native_characterization_c2_live_only(
                state_path, repo_root=root, bindings=drift_bindings, dry_run=True
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
