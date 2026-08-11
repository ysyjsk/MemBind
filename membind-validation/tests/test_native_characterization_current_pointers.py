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
    "status": "native_characterization_c2_live_only",
    "current_blocker": "none",
    "current_action_scope": "native_characterization_c2_live_only",
    "stage_progress.native_characterization": (
        "c0_c1_pass_reference_aligned_c2_authorized_from_episode_0"
    ),
    "instrumentation_contract_status": "qualified_overhead_report_only",
    "c1_aa_classification": "clean_pass",
    "c0_dry_run_passed": "true",
    "c0_dry_run_live_request_performed": "false",
    "c0_live_passed": "true",
    "reference_alignment_decision": (
        "artifacts/diagnostics/"
        "native_characterization_reference_alignment_decision_20260811.md"
    ),
    "reference_alignment_decision_sha256": (
        "e367529c381fd93b957a6ba1a69c064217fa4d190e62fa1250d784b751bd8904"
    ),
    "reference_aligned_freeze": (
        "artifacts/native_characterization/freeze_reference_aligned.json"
    ),
    "reference_aligned_freeze_sha256": (
        "cea700f73f7dc942deeb49195e0a3ca235c35ec51a1c06fdab0edd94738330a7"
    ),
    "interrupted_c2_attempt": "c2-2fe3711c62933407",
    "interruption_classification": "infrastructure_interruption",
    "interruption_error_code": "openai.APIConnectionError",
    "interruption_completed_episode_count": "9",
    "interruption_failed_source_sequence": "9",
    "interruption_attempt_valid": "false",
    "interruption_attempt_mergeable": "false",
    "interruption_resume_allowed": "false",
    "interruption_semantic_attempt_consumed": "false",
    "interruption_report": (
        "artifacts/diagnostics/"
        "native_characterization_c2-2fe3711c62933407_interruption.json"
    ),
    "interruption_report_sha256": (
        "be1922abfbe9887e633228000b371b92a342daba63f43d4f0408ddcf9bf7a986"
    ),
    "interruption_checkpoint": (
        "artifacts/native_characterization/runs/"
        "c2-2fe3711c62933407/checkpoint.json"
    ),
    "interruption_checkpoint_sha256": (
        "2010f6eecf82d1cab8706cd5136445c08175b3ddf9e1e1d11b8ec5f16a3735b8"
    ),
    "interruption_outer_log": (
        "artifacts/tdd/"
        "native_characterization_c2-2fe3711c62933407_live_20260811.log"
    ),
    "interruption_outer_log_sha256": (
        "3a453f968c6cb5b30a3ae198ac4ec79a569f8993d5a2b5e2e9ab5c32f6f646e1"
    ),
    "cleanup_target_attempt": "c2-2fe3711c62933407",
    "cleanup_target_group": "nc-e1e2-400b9b78c2c218df",
    "cleanup_source_freeze": (
        "artifacts/native_characterization/freeze_reference_aligned.json"
    ),
    "cleanup_source_freeze_sha256": (
        "cea700f73f7dc942deeb49195e0a3ca235c35ec51a1c06fdab0edd94738330a7"
    ),
    "cleanup_planned_evidence": (
        "artifacts/native_characterization/c2_cleanup/c2-2fe3711c62933407.json"
    ),
    "cleanup_execution_status": "verified_empty",
    "cleanup_evidence": (
        "artifacts/native_characterization/c2_cleanup/c2-2fe3711c62933407.json"
    ),
    "cleanup_evidence_sha256": (
        "0db64d28dc5dda72bbf9bd8c0ea8a0b18673ffc7abafb694f0d90afaf06964d9"
    ),
    "cleanup_evidence_payload_sha256": (
        "f130e0eab36910bf86406b0e166e2a3ce02e8abc16ef17274ad7817142514d71"
    ),
    "cleanup_pre_node_count": "34",
    "cleanup_pre_relationship_count": "61",
    "cleanup_post_node_count": "0",
    "cleanup_post_relationship_count": "0",
    "final_full_regression": (
        "artifacts/tdd/"
        "native_characterization_c2_interruption_final_full_offline_regression_20260811.log"
    ),
    "final_full_regression_sha256": (
        "439cb3b8779b8514efd4a07ddd2b5b10f60706eb918a22e5f00b184175b6e25c"
    ),
    "final_full_regression_test_count": "793",
    "fresh_c2_start_source_sequence": "0",
    "fresh_c2_resume_allowed": "false",
    "fresh_c2_attempts_remaining": "1",
    "authorized_live_actions": "[native_characterization_c2]",
    "live_h0_candidate_authorized": "false",
    "service_admin_authorized": "false",
    "native_characterization_live_authorized": "true",
    "next_allowed_action": "run_native_characterization_c2",
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
    def test_machine_state_authorizes_only_fresh_c2_from_episode_zero(self) -> None:
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))

        self.assertEqual(state["current_stage"], EXPECTED["current_stage"])
        self.assertEqual(state["status"], EXPECTED["status"])
        self.assertIsNone(state["current_blocker"])
        self.assertEqual(
            state["current_action_scope"], EXPECTED["current_action_scope"]
        )
        self.assertEqual(
            state["stage_progress"]["native_characterization"],
            EXPECTED["stage_progress.native_characterization"],
        )
        self.assertEqual(
            state["authorized_live_actions"], ["native_characterization_c2"]
        )
        self.assertTrue(state["native_characterization_live_authorized"])
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertFalse(state["service_admin_authorized"])
        self.assertEqual(
            state["next_allowed_action"], EXPECTED["next_allowed_action"]
        )
        alignment = state["native_characterization_reference_alignment"]
        self.assertEqual(
            alignment["status"], "c2_live_authorized"
        )
        self.assertEqual(
            alignment["decision_path"], EXPECTED["reference_alignment_decision"]
        )
        self.assertEqual(
            alignment["reference_freeze_path"], EXPECTED["reference_aligned_freeze"]
        )
        self.assertEqual(
            alignment["reference_freeze_sha256"],
            EXPECTED["reference_aligned_freeze_sha256"],
        )
        self.assertEqual(alignment["cleanup"]["failed_attempt_id"], "c2-2fe3711c62933407")
        self.assertEqual(alignment["cleanup"]["target_group_id"], POLLUTED_C2_GROUP)
        self.assertEqual(
            alignment["cleanup"]["execution_status"], "verified_empty"
        )
        self.assertFalse(alignment["cleanup"]["operator_authorized"])
        self.assertEqual(
            alignment["cleanup"]["source_freeze_path"],
            EXPECTED["cleanup_source_freeze"],
        )
        self.assertEqual(
            alignment["cleanup"]["source_freeze_sha256"],
            EXPECTED["cleanup_source_freeze_sha256"],
        )
        self.assertEqual(
            alignment["cleanup"]["planned_evidence_path"],
            EXPECTED["cleanup_planned_evidence"],
        )
        self.assertEqual(alignment["fresh_c2"]["semantic_attempts_remaining"], 1)
        self.assertEqual(alignment["fresh_c2"]["start_source_sequence"], 0)
        self.assertFalse(alignment["fresh_c2"]["resume_allowed"])
        self.assertTrue(alignment["fresh_c2"]["live_authorized"])
        receipt = state["native_characterization_reference_c2_authorization"]
        self.assertTrue(receipt["live_authorized"])
        self.assertEqual(receipt["failed_attempt_id"], "c2-2fe3711c62933407")
        self.assertEqual(receipt["final_full_regression_test_count"], 793)
        self.assertEqual(receipt["replacement_start_source_sequence"], 0)
        self.assertFalse(receipt["replacement_resume_allowed"])
        interruption = state["native_characterization_c2_interruption"]
        self.assertEqual(interruption["run_id"], "c2-2fe3711c62933407")
        self.assertEqual(interruption["error_code"], "openai.APIConnectionError")
        self.assertEqual(interruption["completed_episode_count"], 9)
        self.assertEqual(interruption["failed_source_sequence"], 9)
        self.assertFalse(interruption["attempt_valid"])
        self.assertFalse(interruption["attempt_mergeable"])
        self.assertFalse(interruption["resume_allowed"])
        self.assertFalse(interruption["semantic_attempt_consumed"])
        self.assertEqual(interruption["semantic_attempts_remaining"], 1)
        self.assertTrue(interruption["cleanup_authorized"])
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

    def test_current_pointer_authorizes_only_c2_and_keeps_h0_as_history(self) -> None:
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                current = _pointer(text)
                history = text[text.index(END) + len(END) :]
                self.assertNotIn("live_h0_candidate_authorized=true", current)
                self.assertNotIn("authorized_live_actions=h0_candidate", current)
                self.assertIn(
                    "authorized_live_actions=[native_characterization_c2]", current
                )
                self.assertIn(
                    "next_allowed_action=run_native_characterization_c2", current
                )
                self.assertIn("HISTORICAL_SOLUTION_LANE_BELOW=true", history)
                self.assertIn("live_h0_candidate_authorized=true", history)


if __name__ == "__main__":
    import unittest

    unittest.main()
