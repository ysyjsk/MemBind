"""TDD contracts for closing the consumed R5 grant and opening R6 offline."""

from __future__ import annotations

import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import h0_harness_recovery as recovery  # noqa: E402
import h0_repair_admission as admission  # noqa: E402

from test_h0_r6_recovery import (  # noqa: E402
    R5_INDEX_REL,
    R5_INDEX_SHA256,
    _expected_admission,
    _expected_classification,
    _r6_verification,
)


def _consumed_state() -> dict[str, object]:
    return {
        "protocol_version": "current-validation-v1.3",
        "current_stage": "H0",
        "status": "h0_q1_b_live_only",
        "current_action_scope": "h0_q1_b_live_only",
        "current_blocker": None,
        "stage_progress": {"h0_live_gate": "h0_q1_b_live_only"},
        "live_h0_candidate_authorized": True,
        "authorized_live_actions": ["h0_candidate"],
        "authorized_h0_candidate_id": "Q1",
        "live_h0_authorization": {
            "candidate_id": "Q1",
            "phase": "H0-B",
            "authorized_stage_attempt_id": "h0-q1-b-20260810-replacement-003",
            "resolved_manifest_index_path": R5_INDEX_REL,
            "resolved_manifest_index_sha256": R5_INDEX_SHA256,
            "repair_admission": {"replacement_attempt_id": "h0-q1-b-20260809-replacement-001"},
            "infrastructure_rerun_admission": {"replacement_attempt_id": "h0-q1-b-20260810-replacement-002"},
            "post_workload_repair_admission": {"replacement_attempt_id": "h0-q1-b-20260810-replacement-003"},
        },
    }


class H0R6StateMachineTests(TestCase):
    def test_revoke_consumed_003_is_atomic_in_intent_and_forbids_reuse(self):
        build = getattr(recovery, "build_h0_b_r6_recovery_revoked_state", None)
        self.assertIsNotNone(build)
        classification = _expected_classification()
        source = _consumed_state()
        revoked = build(source, classification=classification)
        self.assertFalse(revoked["live_h0_candidate_authorized"])
        self.assertNotIn("live_h0_authorization", revoked)
        self.assertEqual(revoked["status"], "h0_b_r6_recovery_required_live_revoked")
        context = revoked["h0_b_r6_recovery_context"]
        self.assertTrue(context["consumed_grant_closed"])
        self.assertFalse(context["source_checkpoints_reusable"])
        self.assertFalse(context["checkpoint_namespace_reusable"])
        self.assertEqual(context["replacement_attempt_id"], recovery.R6_REPLACEMENT_ATTEMPT_ID)

        changed = deepcopy(source)
        changed["live_h0_authorization"]["authorized_stage_attempt_id"] = (
            recovery.R6_REPLACEMENT_ATTEMPT_ID
        )
        with self.assertRaises(recovery.H0HarnessRecoveryError):
            build(changed, classification=classification)

    def test_bind_r6_keeps_live_closed_and_authorize_is_exact_004(self):
        revoke = recovery.build_h0_b_r6_recovery_revoked_state
        revoked = revoke(_consumed_state(), classification=_expected_classification())
        r6_admission = admission.build_h0_b_r6_recovery_admission(
            classification=_expected_classification(),
            manifest_verification=_r6_verification(),
        )
        bind = getattr(recovery, "build_h0_b_r6_recovery_bound_state", None)
        authorize = getattr(recovery, "build_h0_b_r6_recovery_live_state", None)
        self.assertIsNotNone(bind)
        self.assertIsNotNone(authorize)
        bindings = {
            "resolved_manifest_index_path": _r6_verification()["index_path"],
            "resolved_manifest_index_sha256": _r6_verification()["index_sha256"],
            "resolved_candidate_manifest_path": "artifacts/h0_manifest_sets/v1_3_harness_r6/resolved_candidates/Q1." + "1" * 64 + ".json",
            "resolved_candidate_manifest_sha256": "1" * 64,
            "resolved_shared_base_manifest_path": "artifacts/h0_manifest_sets/v1_3_harness_r6/resolved_candidates/shared_base." + "2" * 64 + ".json",
            "resolved_shared_base_manifest_sha256": "2" * 64,
        }
        bound = bind(
            revoked,
            root=ROOT,
            manifest_verification=_r6_verification(),
            tdd_evidence={"latest_green": {}, "latest_red": {}, "latest_focused": {}, "latest_full_regression": {}},
            r6_recovery_admission=r6_admission,
            artifact_bindings=bindings,
            tdd_validator=lambda _root, value: value,
        )
        self.assertFalse(bound["live_h0_candidate_authorized"])
        self.assertNotIn("live_h0_authorization", bound)
        live = authorize(bound)
        self.assertTrue(live["live_h0_candidate_authorized"])
        self.assertEqual(
            live["live_h0_authorization"]["authorized_stage_attempt_id"],
            recovery.R6_REPLACEMENT_ATTEMPT_ID,
        )
        self.assertEqual(
            live["live_h0_authorization"]["resolved_manifest_index_path"],
            recovery.R6_INDEX_PATH,
        )
        self.assertIn("r6_recovery_admission", live["live_h0_authorization"])
        with self.assertRaises(recovery.H0HarnessRecoveryError):
            authorize({**bound, "h0_b_r6_recovery_live_prerequisites": {}})

    def test_default_revoke_and_live_transitions_accept_the_shared_driver_contract(self):
        classification = _expected_classification()
        verification = _r6_verification()
        bindings = {
            "resolved_manifest_index_path": verification["index_path"],
            "resolved_manifest_index_sha256": verification["index_sha256"],
            "resolved_candidate_manifest_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r6/"
                "resolved_candidates/Q1." + "1" * 64 + ".json"
            ),
            "resolved_candidate_manifest_sha256": "1" * 64,
            "resolved_shared_base_manifest_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r6/"
                "resolved_candidates/shared_base." + "2" * 64 + ".json"
            ),
            "resolved_shared_base_manifest_sha256": "2" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state_path = root / "CURRENT_STATE.json"
            source_bytes = recovery.canonical_json_bytes(_consumed_state())
            state_path.write_bytes(source_bytes)

            revoked = recovery.transition_h0_b_r6_recovery_revoke(
                state_path,
                root=root,
                classification=classification,
                dry_run=True,
            )
            self.assertEqual(
                revoked["status"], "h0_b_r6_recovery_required_live_revoked"
            )
            self.assertEqual(state_path.read_bytes(), source_bytes)

            r6_admission = admission.build_h0_b_r6_recovery_admission(
                classification=classification,
                manifest_verification=verification,
            )
            bound = recovery.build_h0_b_r6_recovery_bound_state(
                revoked,
                root=root,
                manifest_verification=verification,
                tdd_evidence={
                    "latest_green": {},
                    "latest_red": {},
                    "latest_focused": {},
                    "latest_full_regression": {},
                },
                r6_recovery_admission=r6_admission,
                artifact_bindings=bindings,
                tdd_validator=lambda _root, value: value,
            )
            bound_bytes = recovery.canonical_json_bytes(bound)
            state_path.write_bytes(bound_bytes)
            live = recovery.transition_h0_b_r6_recovery_live(
                state_path,
                root=root,
                dry_run=True,
            )
            self.assertEqual(
                live["live_h0_authorization"]["authorized_stage_attempt_id"],
                recovery.R6_REPLACEMENT_ATTEMPT_ID,
            )
            self.assertEqual(state_path.read_bytes(), bound_bytes)


if __name__ == "__main__":
    import unittest

    unittest.main()
