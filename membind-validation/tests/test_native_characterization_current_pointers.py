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
    "current_blocker": (
        "c2_second_structured_output_failure_requires_protocol_decision"
    ),
    "current_action_scope": "native_characterization_offline_only",
    "stage_progress.native_characterization": (
        "c0_c1_pass_c2_second_json_schema_failure_stopped"
    ),
    "instrumentation_contract_status": "measurement_correctness_repair_pending",
    "c1_aa_classification": "clean_pass",
    "c0_dry_run_passed": "true",
    "c0_dry_run_live_request_performed": "false",
    "c0_live_passed": "true",
    "authorized_live_actions": "[]",
    "live_h0_candidate_authorized": "false",
    "service_admin_authorized": "false",
    "native_characterization_live_authorized": "false",
    "next_allowed_action": "assess_c2_json_object_protocol_deviation",
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
    "cleanup_helper_status=focused_green",
    "cleanup_helper_source_sha256=8a356d514240b8b1ca983c602fcd3b37364b5a91dd985ba01f6ad650542cb1d4",
    "cleanup_helper_test_sha256=37b92adc8f21be63a854fb0cce44ddcdfdac1265a3bc90257b3e2007462791a5",
    "cleanup_helper_focused_log_sha256=4d0a1b81b78f9b4003831fd624162a9042212154ea37f09b29732cb02a889585",
    "c2_reauthorization_status=c2_only_live_authorized",
    "c2_reauthorization_source_sha256=730fc474e2bac106eb6f1734c4a7feb232f7b845f75fbda80443f4f89c484eb3",
    "c2_reauthorization_test_sha256=8ef0383cab28c6df5e2b5705ddb1813dc259eb922fabd9ecb67a7748e33f6cda",
    "c2_reauthorization_integrated_green_sha256=277a0945b258de079294c73c06621b390100a811a0b59558144610527afeb17c",
    "c2_reauthorization_buffered_stdout_red_sha256=163179b486a9b3ed58c043fcd7fae5bdd4cd687cfba406a85d511aa420724597",
    "c2_reauthorization_buffered_stdout_green_sha256=421ed1bdb40c2f6a16b3b1d929a626608d3213316e584ffac75b95b3c97ee7c5",
    "post_cleanup_node_count_required=0",
    "post_cleanup_relationship_count_required=0",
    "cleanup_execution_status=verified_empty",
    "cleanup_evidence_sha256=9e2738a037ce330f4c176633b2424a8065a30e544396a2f4cff5c70d17b7e83b",
    "reauthorization_receipt_sha256=9ba9bef91bc5cbf2b445edb7f0e53ba9c2f38f270dd10046689c38617ed10f79",
    "replacement_start_source_sequence=0",
    "prior_c2_live_grant=consumed_by_failed_attempt",
    "post_cleanup_live_transition=reuse_existing_c2_only_gate",
    "structured_output_second_failure_action=stop_and_assess_json_object",
    "workplan_v1_1_modified=false",
    "freeze_modified=false",
    "new_recovery_framework_allowed=false",
    "second_failed_attempt_id=c2-723261287e32e182",
    "second_failed_attempt_completed_episodes=10",
    "second_failed_attempt_valid=false",
    "second_failed_attempt_mergeable=false",
    "second_failed_attempt_resume_allowed=false",
    "second_failed_attempt_cleanup_authorized=false",
    "second_failed_attempt_json_object_authorized=false",
    (
        "second_failed_attempt_report_sha256="
        "df9f369e68a5b131b2f70d05e4e2e58a95eb86602a3e8fe30d0ef6f3bf218cf7"
    ),
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
    def test_machine_state_stops_after_second_c2_structured_failure(self) -> None:
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
        self.assertEqual(
            state["authorized_live_actions"], []
        )
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertFalse(state["service_admin_authorized"])
        self.assertEqual(
            state["next_allowed_action"], EXPECTED["next_allowed_action"]
        )
        failure = state["native_characterization_c2_second_failure"]
        self.assertEqual(failure["run_id"], "c2-723261287e32e182")
        self.assertEqual(failure["completed_episode_count"], 10)
        self.assertFalse(failure["attempt_valid"])
        self.assertFalse(failure["attempt_mergeable"])
        self.assertFalse(failure["resume_allowed"])
        self.assertFalse(failure["cleanup_authorized"])
        self.assertFalse(failure["json_object_authorized"])
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

    def test_current_pointer_is_offline_only_and_h0_is_explicit_history(self) -> None:
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                current = _pointer(text)
                history = text[text.index(END) + len(END) :]
                self.assertNotIn("live_h0_candidate_authorized=true", current)
                self.assertNotIn("authorized_live_actions=h0_candidate", current)
                self.assertIn("authorized_live_actions=[]", current)
                self.assertIn(
                    "next_allowed_action=assess_c2_json_object_protocol_deviation",
                    current,
                )
                self.assertIn("HISTORICAL_SOLUTION_LANE_BELOW=true", history)
                self.assertIn("live_h0_candidate_authorized=true", history)


if __name__ == "__main__":
    import unittest

    unittest.main()
