"""TDD contracts for consuming the one-shot C0 grant after a valid pass."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from current_state_gate import LiveAction, evaluate_live_action  # noqa: E402
from native_characterization_c0_completion import (  # noqa: E402
    C0CompletionBindings,
    NativeCharacterizationC0CompletionError,
    complete_native_characterization_c0,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class NativeCharacterizationC0CompletionTests(TestCase):
    def _fixture(self) -> tuple[Path, Path, C0CompletionBindings, dict[str, object]]:
        root = Path(tempfile.mkdtemp())
        validation = root / "membind-validation"
        run = validation / "artifacts/native_characterization/runs/c0-0123456789abcdef"
        run.mkdir(parents=True)
        manifest = {
            "schema_version": "membind.native-characterization-c0-result.v1",
            "artifact_id": "native-characterization-c0",
            "run_id": "c0-0123456789abcdef",
            "stage": "C0",
            "status": "pass",
            "interpretation": "engineering_viability_only_not_research_result",
            "history_id": "history",
            "source_sequence": 0,
            "episode_source_sha256": "1" * 64,
            "graph_namespace": "nc-c0-0123456789abcdef",
            "add_episode_latency_ns": 10,
            "result_counts": {"nodes": 1},
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
            "current_action_scope": "native_characterization_c0_live_only",
            "current_blocker": None,
            "next_allowed_action": "run_native_characterization_c0",
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": ["native_characterization_c0"],
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "h0_live_gate": "forbidden_native_characterization",
                "native_characterization": (
                    "c1_qualified_c0_dry_run_pass_waiting_for_services"
                ),
            },
            "native_characterization_c0_authorization": {
                "schema_version": "membind.native-characterization-c0-authorization.v1",
                "source_state_sha256": "2" * 64,
                "live_authorized": True,
            },
        }
        state_path = validation / "CURRENT_STATE.json"
        state_path.write_bytes(_canonical(state))
        bindings = C0CompletionBindings(
            source_state_sha256=_sha(state_path.read_bytes()),
            manifest_sha256=_sha(manifest_path.read_bytes()),
            checkpoint_sha256=_sha(checkpoint_path.read_bytes()),
            manifest_relative_path=str(manifest_path.relative_to(validation)),
            checkpoint_relative_path=str(checkpoint_path.relative_to(validation)),
        )
        return root, state_path, bindings, state

    def test_valid_pass_consumes_only_c0_grant_and_binds_evidence(self) -> None:
        root, state_path, bindings, source = self._fixture()
        target = complete_native_characterization_c0(
            state_path, repo_root=root, bindings=bindings, dry_run=True
        )
        self.assertEqual(target["current_stage"], "NATIVE_CHARACTERIZATION")
        self.assertEqual(target["status"], "native_characterization_offline_only")
        self.assertEqual(
            target["current_action_scope"], "native_characterization_offline_only"
        )
        self.assertEqual(target["authorized_live_actions"], [])
        self.assertEqual(target["next_allowed_action"], "implement_c2_runner_offline")
        self.assertEqual(
            target["stage_progress"]["native_characterization"],
            "c0_pass_c2_runner_tdd_pending",
        )
        self.assertEqual(
            target["native_characterization_c0_authorization"],
            source["native_characterization_c0_authorization"],
        )
        self.assertTrue(
            target["native_characterization_c0_completion"]["grant_consumed"]
        )
        for action in LiveAction:
            self.assertFalse(evaluate_live_action(target, action).allowed)
        self.assertEqual(state_path.read_bytes(), _canonical(source))

    def test_tampered_checkpoint_or_nonpass_manifest_fails_without_write(self) -> None:
        root, state_path, bindings, _ = self._fixture()
        checkpoint = root / "membind-validation" / bindings.checkpoint_relative_path
        checkpoint.write_bytes(b"drift")
        before = state_path.read_bytes()
        with self.assertRaisesRegex(
            NativeCharacterizationC0CompletionError, "checkpoint_hash_mismatch"
        ):
            complete_native_characterization_c0(
                state_path, repo_root=root, bindings=bindings, dry_run=False
            )
        self.assertEqual(state_path.read_bytes(), before)

    def test_apply_is_atomic_and_idempotent_but_target_drift_fails(self) -> None:
        root, state_path, bindings, _ = self._fixture()
        first = complete_native_characterization_c0(
            state_path, repo_root=root, bindings=bindings, dry_run=False
        )
        committed = state_path.read_bytes()
        target_bindings = C0CompletionBindings(
            **{
                **bindings.__dict__,
                "source_state_sha256": _sha(committed),
            }
        )
        second = complete_native_characterization_c0(
            state_path, repo_root=root, bindings=target_bindings, dry_run=False
        )
        self.assertEqual(first, second)
        drifted = json.loads(committed)
        drifted["next_allowed_action"] = "tampered"
        state_path.write_bytes(_canonical(drifted))
        drift_bindings = C0CompletionBindings(
            **{
                **bindings.__dict__,
                "source_state_sha256": _sha(state_path.read_bytes()),
            }
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC0CompletionError, "target_state_drift"
        ):
            complete_native_characterization_c0(
                state_path, repo_root=root, bindings=drift_bindings, dry_run=True
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
