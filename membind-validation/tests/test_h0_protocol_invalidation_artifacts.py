"""Repository contracts preserving the invalidated Q1/H0-A protocol attempt.

These tests keep the technical execution observation, protocol disposition,
machine gate, and immutable checkpoint from drifting into contradictory states.
They perform local reads only and never load credentials or contact a service.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
ATTEMPT_ID = "h0-q1-a-20260809-attempt-001"
CHECKPOINT_REL = (
    "artifacts/h0_runs/h0/checkpoints/"
    f"{ATTEMPT_ID}/index.json"
)
CHECKPOINT_SHA256 = (
    "127c81b39ccd705d7c67dc936e953992d5be97f4065fd56f3655db52d12ad309"
)
INVALIDATION_STATUS = "invalidated_protocol_gate_order"
INVALIDATION_REASON = "protocol_gate_order_violation"


def _read_json(relative: str) -> dict[str, object]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{relative} must contain a JSON object")
    return value


class H0ProtocolInvalidationArtifactTests(TestCase):
    """Protect the fail-closed disposition of the invalid H0-A attempt."""

    def test_machine_state_preserves_invalidation_after_native_transition(self):
        state = _read_json("CURRENT_STATE.json")
        invalidation = state["h0_live_authorization_invalidation"]

        self.assertEqual(state["current_stage"], "NATIVE_CHARACTERIZATION")
        self.assertEqual(state["status"], "native_characterization_offline_only")
        self.assertEqual(
            state["current_action_scope"], "native_characterization_offline_only"
        )
        self.assertEqual(
            state["current_blocker"],
            "c2_json_object_validation_failure_stop_no_fallback",
        )
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertEqual(
            state["authorized_live_actions"], []
        )
        self.assertIsNone(state["authorized_h0_candidate_id"])
        self.assertEqual(
            state["historical_h0_live_authorization"][
                "authorized_stage_attempt_id"
            ],
            "h0-q1-b-20260810-replacement-004",
        )
        self.assertEqual(
            state["stage_progress"]["h0_live_gate"],
            "forbidden_native_characterization",
        )
        transition = state["native_characterization_transition"]
        self.assertFalse(transition["live_authorized"])
        self.assertEqual(
            transition["retired_stage_attempt_id"],
            "h0-q1-b-20260810-replacement-004",
        )
        self.assertEqual(
            state["h0_b_post_workload_harness_failure"]["stage_attempt_id"],
            "h0-q1-b-20260810-replacement-002",
        )

        self.assertEqual(invalidation["reason"], INVALIDATION_REASON)
        self.assertEqual(invalidation["candidate_id"], "Q1")
        self.assertEqual(invalidation["phase"], "H0-A")
        self.assertEqual(invalidation["stage_attempt_id"], ATTEMPT_ID)
        self.assertEqual(invalidation["checkpoint_index_path"], CHECKPOINT_REL)
        self.assertEqual(
            invalidation["checkpoint_index_sha256"], CHECKPOINT_SHA256
        )
        self.assertFalse(invalidation["candidate_rerun_authorized"])
        self.assertFalse(invalidation["candidate_advance_authorized"])
        self.assertFalse(invalidation["live_transition_authorized"])

    def test_result_separates_technical_success_from_protocol_qualification(self):
        result = _read_json("artifacts/diagnostics/h0_q1_a_result_20260809.json")
        validity = result["protocol_validity"]
        technical = result["technical_execution"]

        self.assertEqual(result["status"], INVALIDATION_STATUS)
        self.assertEqual(result["checkpoint"]["terminal_status"], "stage_complete")
        self.assertTrue(technical["recorded_checks_passed"])
        self.assertFalse(technical["protocol_qualified"])
        self.assertEqual(validity["status"], INVALIDATION_STATUS)
        self.assertEqual(validity["reason"], INVALIDATION_REASON)
        self.assertFalse(validity["protocol_qualified"])
        self.assertFalse(validity["candidate_selection_evidence_eligible"])
        self.assertFalse(validity["rerun_automatically_authorized"])
        self.assertFalse(validity["candidate_advance_allowed"])
        self.assertFalse(validity["qualification_reusable"])
        self.assertNotIn("qualification", result)

    def test_original_checkpoint_remains_byte_identical_and_technically_terminal(self):
        checkpoint_path = ROOT / CHECKPOINT_REL
        self.assertEqual(
            hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(), CHECKPOINT_SHA256
        )
        checkpoint = _read_json(CHECKPOINT_REL)
        self.assertEqual(checkpoint["status"], "stage_complete")
        self.assertEqual(checkpoint["candidate_id"], "Q1")
        self.assertEqual(checkpoint["phase"], "H0-A")
        self.assertEqual(checkpoint["stage_attempt_id"], ATTEMPT_ID)


if __name__ == "__main__":
    import unittest

    unittest.main()
