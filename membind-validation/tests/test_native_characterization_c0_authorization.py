"""TDD contracts for the one-shot Native characterization C0 authorization.

These tests use a temporary repository and never touch the production state or
open a model, embedding, database, SSH, or other network client.
"""

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

from native_characterization_c0_authorization import (  # noqa: E402
    C0AuthorizationBindings,
    NativeCharacterizationC0AuthorizationError,
    authorize_native_characterization_c0_live_only,
)
from current_state_gate import LiveAction, evaluate_live_action  # noqa: E402


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class NativeCharacterizationC0AuthorizationTests(TestCase):
    def _fixture(self) -> tuple[Path, Path, dict[str, str], dict[str, object]]:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        validation = root / "membind-validation"
        validation.mkdir()
        state = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_offline_only",
            "current_action_scope": "native_characterization_offline_only",
            "current_blocker": None,
            "next_allowed_action": "operator_start_vllm_then_authorize_c0",
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "h0_live_gate": "forbidden_native_characterization",
                "native_characterization": (
                    "c1_qualified_c0_dry_run_pass_waiting_for_services"
                ),
            },
            "historical_h0_live_authorization": {"retired": True},
            "native_characterization_offline_qualification": {
                "schema_version": "membind.native-characterization-offline-qualification.v1",
                "instrumentation_contract_status": "qualified",
                "live_authorized": False,
                "workplan_id": "native-characterization-v1.1",
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
        freeze_path.parent.mkdir(parents=True)
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
        c1_path = validation / "artifacts/tdd/c1_qualification.json"
        c1_path.parent.mkdir(parents=True)
        c1_path.write_bytes(
            _canonical(
                {
                    "schema_version": "membind.native-characterization-c1-qualification.v1",
                    "classification": "clean_pass",
                    "semantic_parity": {"passed": True},
                }
            )
        )
        c0_path = validation / "artifacts/tdd/c0_dry_run.json"
        c0_path.write_bytes(
            _canonical(
                {
                    "schema_version": "membind.native-characterization-c0-preview.v1",
                    "live_request_performed": False,
                }
            )
        )
        digests = {
            "source_state_sha256": _sha(state_path.read_bytes()),
            "workplan_sha256": _sha(workplan_path.read_bytes()),
            "freeze_sha256": _sha(freeze_path.read_bytes()),
            "c1_evidence_sha256": _sha(c1_path.read_bytes()),
            "c0_dry_run_sha256": _sha(c0_path.read_bytes()),
        }
        qualification = state["native_characterization_offline_qualification"]
        assert isinstance(qualification, dict)
        qualification.update(
            {
                "workplan_sha256": digests["workplan_sha256"],
                "freeze": {
                    "path": "artifacts/native_characterization/freeze.json",
                    "sha256": digests["freeze_sha256"],
                },
                "c1_aa": {
                    "path": "artifacts/tdd/c1_qualification.json",
                    "sha256": digests["c1_evidence_sha256"],
                },
                "c0_dry_run": {
                    "path": "artifacts/tdd/c0_dry_run.json",
                    "sha256": digests["c0_dry_run_sha256"],
                    "live_request_performed": False,
                },
            }
        )
        state_path.write_bytes(_canonical(state))
        digests["source_state_sha256"] = _sha(state_path.read_bytes())
        return root, state_path, digests, state

    def _bindings(self, digests: dict[str, str]) -> C0AuthorizationBindings:
        return C0AuthorizationBindings(**digests, operator_service_ready=True)

    def test_offline_state_is_rejected_without_operator_confirmation(self):
        root, state_path, digests, _ = self._fixture()
        bindings = C0AuthorizationBindings(
            **digests, operator_service_ready=False
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC0AuthorizationError,
            "operator_service_not_confirmed",
        ):
            authorize_native_characterization_c0_live_only(
                state_path, repo_root=root, bindings=bindings, dry_run=True
            )

    def test_red_rejects_state_with_any_old_live_grant(self):
        root, state_path, digests, state = self._fixture()
        state["authorized_live_actions"] = ["h0_candidate"]
        state_path.write_bytes(_canonical(state))
        digests["source_state_sha256"] = _sha(state_path.read_bytes())
        with self.assertRaisesRegex(
            NativeCharacterizationC0AuthorizationError, "old_live_grant_present"
        ):
            authorize_native_characterization_c0_live_only(
                state_path,
                repo_root=root,
                bindings=self._bindings(digests),
                dry_run=True,
            )

    def test_green_changes_only_c0_authorization_pointers_and_binds_evidence(self):
        root, state_path, digests, source = self._fixture()
        before = deepcopy(source)
        self.assertFalse(
            evaluate_live_action(
                before, LiveAction.NATIVE_CHARACTERIZATION_C0
            ).allowed
        )
        target = authorize_native_characterization_c0_live_only(
            state_path,
            repo_root=root,
            bindings=self._bindings(digests),
            dry_run=True,
        )
        self.assertEqual(target["current_stage"], before["current_stage"])
        self.assertEqual(target["status"], before["status"])
        self.assertEqual(
            target["current_action_scope"], "native_characterization_c0_live_only"
        )
        self.assertEqual(
            target["authorized_live_actions"], ["native_characterization_c0"]
        )
        self.assertEqual(target["next_allowed_action"], "run_native_characterization_c0")
        self.assertFalse(target["live_h0_candidate_authorized"])
        self.assertFalse(target["service_admin_authorized"])
        self.assertEqual(target["stage_progress"], before["stage_progress"])
        self.assertTrue(
            evaluate_live_action(
                target, LiveAction.NATIVE_CHARACTERIZATION_C0
            ).allowed
        )
        for action in LiveAction:
            if action is LiveAction.NATIVE_CHARACTERIZATION_C0:
                continue
            self.assertFalse(evaluate_live_action(target, action).allowed)
        metadata = target["native_characterization_c0_authorization"]
        self.assertTrue(metadata["live_authorized"])
        self.assertEqual(metadata["source_state_sha256"], digests["source_state_sha256"])
        self.assertEqual(metadata["workplan_sha256"], digests["workplan_sha256"])
        self.assertEqual(metadata["freeze_sha256"], digests["freeze_sha256"])
        self.assertEqual(metadata["c1_evidence_sha256"], digests["c1_evidence_sha256"])
        self.assertEqual(metadata["c0_dry_run_sha256"], digests["c0_dry_run_sha256"])
        self.assertNotIn("api_key", json.dumps(metadata).lower())
        self.assertNotIn("operator_service_ready", metadata)
        self.assertEqual(
            set(target) - set(before), {"native_characterization_c0_authorization"}
        )
        self.assertEqual(state_path.read_bytes(), _canonical(before))

    def test_evidence_hash_drift_fails_without_state_write(self):
        root, state_path, digests, _ = self._fixture()
        c1 = root / "membind-validation/artifacts/tdd/c1_qualification.json"
        c1.write_bytes(b"drift")
        before = state_path.read_bytes()
        with self.assertRaisesRegex(
            NativeCharacterizationC0AuthorizationError,
            "c1_evidence_hash_mismatch",
        ):
            authorize_native_characterization_c0_live_only(
                state_path,
                repo_root=root,
                bindings=self._bindings(digests),
                dry_run=False,
            )
        self.assertEqual(state_path.read_bytes(), before)

    def test_commit_is_idempotent_and_target_drift_fails_closed(self):
        root, state_path, digests, _ = self._fixture()
        first = authorize_native_characterization_c0_live_only(
            state_path,
            repo_root=root,
            bindings=self._bindings(digests),
            dry_run=False,
        )
        committed = state_path.read_bytes()
        target_digest = _sha(committed)
        target_bindings = self._bindings(
            {**digests, "source_state_sha256": target_digest}
        )
        second = authorize_native_characterization_c0_live_only(
            state_path,
            repo_root=root,
            bindings=target_bindings,
            dry_run=False,
        )
        self.assertEqual(second, first)
        self.assertEqual(state_path.read_bytes(), committed)
        drifted = json.loads(committed)
        drifted["next_allowed_action"] = "tampered"
        state_path.write_bytes(_canonical(drifted))
        drift_bindings = self._bindings(
            {**digests, "source_state_sha256": _sha(state_path.read_bytes())}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC0AuthorizationError, "target_state_drift"
        ):
            authorize_native_characterization_c0_live_only(
                state_path,
                repo_root=root,
                bindings=drift_bindings,
                dry_run=True,
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
