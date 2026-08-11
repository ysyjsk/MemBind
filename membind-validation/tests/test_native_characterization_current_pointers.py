"""Document contracts for the current Native characterization resume point.

The frozen workplan describes the experiment, while these three maintained
pointers describe execution progress.  Historical H0 ledgers may remain below
the pointer but must not be mistaken for current authority.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DOCUMENTS = (
    REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md",
    ROOT / "EXPERIMENT_PLAN.md",
    ROOT / "GLOBAL_MEMORY.md",
)
EXECUTION_PLAN = ROOT / "EXPERIMENT_PLAN.md"
SUMMARY_DOCUMENTS = (DOCUMENTS[0], DOCUMENTS[2])
START = "<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_START -->"
END = "<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_END -->"

EXPECTED = {
    "protocol_version": "current-validation-v1.3",
    "current_stage": "NATIVE_CHARACTERIZATION",
    "status": "native_characterization_offline_only",
    "current_blocker": "c2_polluted_namespace_cleanup_pending",
    "current_action_scope": "native_characterization_offline_only",
    "stage_progress.native_characterization": (
        "c0_c1_pass_c2_failed_attempt_invalid_cleanup_tdd_pending"
    ),
    "instrumentation_contract_status": "qualified",
    "c1_aa_classification": "clean_pass",
    "c0_dry_run_passed": "true",
    "c0_dry_run_live_request_performed": "false",
    "c0_live_passed": "true",
    "authorized_live_actions": "[]",
    "live_h0_candidate_authorized": "false",
    "service_admin_authorized": "false",
    "native_characterization_live_authorized": "false",
    "next_allowed_action": "implement_scoped_c2_cleanup_offline",
}

FAILED_C2_ATTEMPT = "c2-efb58c477f12adf6"
POLLUTED_C2_GROUP = "nc-e1e2-400b9b78c2c218df"
RECOVERY_REQUIRED_TEXT = (
    "c2_recovery_scope=single_frozen_group_only",
    f"failed_attempt_id={FAILED_C2_ATTEMPT}",
    "failed_attempt_valid=false",
    "failed_attempt_mergeable=false",
    "replacement_resume_allowed=false",
    f"polluted_group_id={POLLUTED_C2_GROUP}",
    "cleanup_primitive=graphiti.clear_data(driver,group_ids=[target_group])",
    (
        "cleanup_target_binding=target_group==polluted_group_id=="
        "freeze.screening.e1_e2.block_order[0].graph_namespace"
    ),
    "cleanup_rejects=none,empty,multiple,other_frozen,non_frozen",
    "cleanup_requires_explicit_operator_authorization=true",
    "post_cleanup_node_count_required=0",
    "post_cleanup_relationship_count_required=0",
    "replacement_start_source_sequence=0",
    "prior_c2_live_grant=consumed_by_failed_attempt",
    "post_cleanup_live_transition=reuse_existing_c2_only_gate",
    "structured_output_second_failure_action=stop_and_assess_json_object",
    "workplan_v1_1_modified=false",
    "freeze_modified=false",
    "new_recovery_framework_allowed=false",
)


def _pointer(text: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise AssertionError("current pointer markers must occur exactly once")
    start = text.index(START) + len(START)
    end = text.index(END, start)
    return text[start:end]


def _fields(block: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("```") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


class NativeCharacterizationCurrentPointerTests(TestCase):
    def test_machine_state_is_offline_waiting_for_scoped_c2_cleanup(self) -> None:
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))

        self.assertEqual(state["current_stage"], EXPECTED["current_stage"])
        self.assertEqual(state["status"], EXPECTED["status"])
        self.assertEqual(state["current_blocker"], EXPECTED["current_blocker"])
        self.assertEqual(
            state["current_action_scope"], EXPECTED["current_action_scope"]
        )
        self.assertEqual(
            state["stage_progress"]["native_characterization"],
            EXPECTED["stage_progress.native_characterization"],
        )
        self.assertEqual(state["authorized_live_actions"], [])
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertFalse(state["service_admin_authorized"])
        self.assertEqual(
            state["next_allowed_action"], EXPECTED["next_allowed_action"]
        )
        qualification = state["native_characterization_offline_qualification"]
        self.assertEqual(
            qualification["instrumentation_contract_status"], "qualified"
        )
        self.assertEqual(qualification["c1_aa"]["classification"], "clean_pass")
        self.assertFalse(qualification["c0_dry_run"]["live_request_performed"])
        self.assertFalse(qualification["live_authorized"])
        completion = state["native_characterization_c0_completion"]
        self.assertEqual(completion["c0_status"], "pass")
        self.assertTrue(completion["grant_consumed"])

    def test_current_documents_freeze_the_minimal_non_mergeable_c2_recovery(self) -> None:
        execution = EXECUTION_PLAN.read_text(encoding="utf-8")
        for required in RECOVERY_REQUIRED_TEXT:
            self.assertIn(required, execution)
        self.assertEqual(execution.count("C2_MINIMAL_RECOVERY_POINTER_START"), 1)

        reference = (
            "c2_minimal_recovery_contract="
            "membind-validation/EXPERIMENT_PLAN.md#C2_MINIMAL_RECOVERY_POINTER"
        )
        for path in SUMMARY_DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(reference, text)
                self.assertNotIn("C2_MINIMAL_RECOVERY_POINTER_START", text)

        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                current = _pointer(path.read_text(encoding="utf-8"))
                self.assertNotIn("recovery_freeze", current)
                self.assertNotIn("replacement_registry", current)

    def test_all_current_documents_have_the_same_exact_pointer(self) -> None:
        blocks = []
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                block = _pointer(text)
                self.assertEqual(_fields(block), EXPECTED)
                blocks.append(block)
        self.assertEqual(len(set(blocks)), 1)

    def test_current_pointer_is_fail_closed_and_h0_is_explicit_history(self) -> None:
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                current = _pointer(text)
                history = text[text.index(END) + len(END) :]
                self.assertNotIn("live_h0_candidate_authorized=true", current)
                self.assertNotIn("authorized_live_actions=h0_candidate", current)
                self.assertIn("HISTORICAL_SOLUTION_LANE_BELOW=true", history)
                self.assertIn("live_h0_candidate_authorized=true", history)


if __name__ == "__main__":
    import unittest

    unittest.main()
