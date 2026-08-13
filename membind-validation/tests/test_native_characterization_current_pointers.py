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
        "artifacts/native_characterization/freeze_reference_aligned_64k.json"
    ),
    "reference_aligned_freeze_sha256": (
        "3b086ace7841bccc2479f2043f0767b4ab9ea3d4fd74459ce65ae5cccfb0b3b0"
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
    "serving_envelope_failed_c2_attempt": "c2-4cc7d0599bbbbdac",
    "serving_envelope_failure_error_code": "openai.BadRequestError",
    "serving_envelope_failure_completed_episode_count": "10",
    "serving_envelope_failure_completed_block_count": "0",
    "serving_envelope_failure_failed_source_sequence": "10",
    "serving_envelope_failure_attempt_valid": "false",
    "serving_envelope_failure_attempt_mergeable": "false",
    "serving_envelope_failure_resume_allowed": "false",
    "serving_envelope_failure_prefix_merge_allowed": "false",
    "serving_envelope_failure_report": (
        "artifacts/diagnostics/"
        "native_characterization_c2-4cc7d0599bbbbdac_serving_envelope_failure.json"
    ),
    "serving_envelope_failure_report_sha256": (
        "c92ddb5b1c8b4fb20cb048816668a5d0e03516439524cd9a78f0906b2a14355f"
    ),
    "serving_envelope_failure_checkpoint": (
        "artifacts/native_characterization/runs/"
        "c2-4cc7d0599bbbbdac/checkpoint.json"
    ),
    "serving_envelope_failure_checkpoint_sha256": (
        "4fc29a435790c55e17c8d4966203fc39784237100131475e82993dc2bf5df120"
    ),
    "serving_envelope_failure_outer_log": (
        "artifacts/tdd/native_characterization_c2-4cc7d0599bbbbdac_live_20260811.log"
    ),
    "serving_envelope_failure_outer_log_sha256": (
        "68544c5a79be0e30ca6a97da54baa7916aeb1c94913d2cd1ad00af202c8de81f"
    ),
    "serving_envelope_64k_status": "64K_ENVELOPE_PASS",
    "serving_envelope_64k_evidence": (
        "artifacts/environment/"
        "native_characterization_64k_serving_envelope_20260812.json"
    ),
    "serving_envelope_64k_evidence_sha256": (
        "724f9bbfdf49cbf0e07def5c5fae619dcbd7b322f8a513b5c5cb8217c524b341"
    ),
    "serving_envelope_64k_actual_prompt_tokens": "26024",
    "serving_envelope_64k_requested_max_tokens": "16384",
    "serving_envelope_64k_max_model_len": "65536",
    "cleanup_target_attempt": "c2-4cc7d0599bbbbdac",
    "cleanup_target_group": "nc-e1e2-400b9b78c2c218df",
    "cleanup_source_freeze": (
        "artifacts/native_characterization/freeze_reference_aligned.json"
    ),
    "cleanup_source_freeze_sha256": (
        "cea700f73f7dc942deeb49195e0a3ca235c35ec51a1c06fdab0edd94738330a7"
    ),
    "cleanup_planned_evidence": (
        "artifacts/native_characterization/c2_cleanup/c2-4cc7d0599bbbbdac.json"
    ),
    "cleanup_execution_status": "verified_empty",
    "cleanup_evidence": (
        "artifacts/native_characterization/c2_cleanup/c2-4cc7d0599bbbbdac.json"
    ),
    "cleanup_evidence_sha256": (
        "d52d65fc985753863b0437e3940085a7986f6902acba697f9175af7d391df08e"
    ),
    "cleanup_evidence_payload_sha256": (
        "c721cc0da76cc5544cff1dc0e4342d05a5b647d4e82b741cdca770a5de5004a6"
    ),
    "cleanup_pre_node_count": "51",
    "cleanup_pre_relationship_count": "89",
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
    "recovery_focused_tests": (
        "artifacts/tdd/"
        "native_characterization_c2_64k_recovery_focused_green_20260812.log"
    ),
    "recovery_focused_tests_sha256": (
        "c489f17752ddd5052627b0df07a49831b3d3eac62795f17defd6af869b006c4b"
    ),
    "recovery_focused_test_count": "50",
    "fresh_c2_start_source_sequence": "0",
    "fresh_c2_resume_allowed": "false",
    "fresh_c2_attempts_remaining": "0",
    "completed_c2_run": "c2-17cdaabd562e9673",
    "completed_c2_episode_count": "188",
    "completed_c2_block_count": "4",
    "completed_c2_manifest_sha256": (
        "f03276ef88bfdc8062967db504514c83d941d37f929a8dbca5c37fab7aa69417"
    ),
    "completed_c2_checkpoint_sha256": (
        "bee2e1a0e2130d6c9f3f579829680b64a3b732b814b7a09a2115f28042e42235"
    ),
    "completed_c2_e1_breakdown_sha256": (
        "b06deae7a1387a6705adb5f897c92856fda6f55bebb1c277a39965bdeda952cb"
    ),
    "c2_verification": (
        "artifacts/diagnostics/"
        "native_characterization_c2-17cdaabd562e9673_verification.json"
    ),
    "c2_verification_sha256": (
        "67e4a5a59b1b2c32427516b067f477975673ae9b366d21d32324bb45da531b01"
    ),
    "c2_verification_payload_sha256": (
        "d2f7ba19ebd372b67dc1f90661c7cb72b83984524fbdf3d02cea29ed9b010eaf"
    ),
    "c2_completion_source_state_sha256": (
        "90e2af7e89a644422d915a80de2ca9a98d684766a738adca260e345938f8e0ae"
    ),
    "c2_completion_focused_tests": (
        "artifacts/tdd/"
        "native_characterization_c2_completion_and_verifier_focused_green_20260812.log"
    ),
    "c2_completion_focused_tests_sha256": (
        "519ad67f25f0c4973221640b0af5b9caa24a9661287e133fe567c053bfedf359"
    ),
    "c2_completion_focused_test_count": "13",
    "completed_c3_run": "c2-17cdaabd562e9673",
    "c3_dependency_map": "artifacts/native_characterization/dependency_map.json",
    "c3_dependency_map_sha256": (
        "7fde0235a4110bf83383b68df15827c518bbf448fbd1e4e1d780c8efe06af398"
    ),
    "c3_dependency_map_payload_sha256": (
        "e5f53ed575030f2acb7024e7913808c524c71e2db88853632bbe935caa4904ac"
    ),
    "c3_e2_artifact": (
        "artifacts/native_characterization/e2_dependency_opportunity.json"
    ),
    "c3_e2_sha256": (
        "a80ca5a8e763c19eea9d2cde1dbe001425200d04c857384cb862cc65ccf1887f"
    ),
    "c3_e2_payload_sha256": (
        "7adc924db06e33e319d973a9b6ceaf402866bda4ea38a8755d3781f2ca86449f"
    ),
    "c3_analyzer_source_sha256": (
        "dc0956070081d4017068878350edfc768508d6cf40389c14d2fb7e5f81ee703c"
    ),
    "c3_completion_source_state_sha256": (
        "f86e33d0434bb267599e2c562ea3f319910c50f4949ff8b420655bd585db6e59"
    ),
    "c3_completion_focused_tests": (
        "artifacts/tdd/"
        "native_characterization_c3_completion_focused_green_20260812.log"
    ),
    "c3_completion_focused_tests_sha256": (
        "022178a7a892cbbf5a0970108bc01391560cbd613446333a5db19adb181b884c"
    ),
    "c3_completion_focused_test_count": "12",
    "c3_episode_count": "188",
    "c3_history_count": "4",
    "c3_interval_count": "1504",
    "c3_T_total_ns": "9081843769634",
    "c3_p_L": "0.2291969234941911",
    "c3_p_U": "0.2291969234941911",
    "c3_S2": "1.1294310624004833",
    "c3_S4": "1.2075802604205235",
    "c3_S8": "1.2508557542912377",
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
    def test_machine_state_binds_completed_c2_c3_and_exact_single_authority(self) -> None:
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))

        self.assertEqual(state["current_stage"], EXPECTED["current_stage"])
        self.assertIn("current_blocker", state)
        actions = state["authorized_live_actions"]
        self.assertIsInstance(actions, list)
        self.assertLessEqual(len(actions), 1)
        if actions:
            action = actions[0]
            self.assertTrue(action.startswith("native_characterization_"))
            self.assertEqual(state["status"], f"{action}_live_only")
            self.assertEqual(state["current_action_scope"], f"{action}_live_only")
            self.assertEqual(state["next_allowed_action"], f"run_{action}")
            self.assertTrue(state["native_characterization_live_authorized"])
        else:
            self.assertFalse(state["native_characterization_live_authorized"])
            self.assertFalse(str(state["status"]).endswith("_live_only"))
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertFalse(state["service_admin_authorized"])
        alignment = state["native_characterization_reference_alignment"]
        self.assertEqual(
            alignment["status"], "c2_completed_verified_c3_offline"
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
        self.assertEqual(alignment["cleanup"]["failed_attempt_id"], "c2-4cc7d0599bbbbdac")
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
        self.assertEqual(alignment["fresh_c2"]["semantic_attempts_remaining"], 0)
        self.assertEqual(alignment["fresh_c2"]["start_source_sequence"], 0)
        self.assertFalse(alignment["fresh_c2"]["resume_allowed"])
        self.assertFalse(alignment["fresh_c2"]["live_authorized"])
        self.assertEqual(
            alignment["fresh_c2"]["completed_run_id"], "c2-17cdaabd562e9673"
        )
        receipt = state["native_characterization_reference_c2_authorization"]
        self.assertFalse(receipt["live_authorized"])
        self.assertTrue(receipt["grant_consumed"])
        self.assertEqual(receipt["completed_run_id"], "c2-17cdaabd562e9673")
        self.assertEqual(receipt["failed_attempt_id"], "c2-4cc7d0599bbbbdac")
        self.assertEqual(receipt["final_full_regression_test_count"], 793)
        self.assertEqual(receipt["focused_test_count"], 50)
        self.assertEqual(
            receipt["execution_envelope_sha256"],
            EXPECTED["serving_envelope_64k_evidence_sha256"],
        )
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
        serving_failure = state["native_characterization_c2_serving_envelope_failure"]
        self.assertEqual(serving_failure["run_id"], "c2-4cc7d0599bbbbdac")
        self.assertEqual(serving_failure["error_code"], "openai.BadRequestError")
        self.assertEqual(serving_failure["completed_episode_count"], 10)
        self.assertEqual(serving_failure["completed_block_count"], 0)
        self.assertFalse(serving_failure["attempt_valid"])
        self.assertFalse(serving_failure["attempt_mergeable"])
        self.assertFalse(serving_failure["resume_allowed"])
        envelope = state["native_characterization_64k_serving_envelope"]
        self.assertEqual(envelope["qualification_status"], "64K_ENVELOPE_PASS")
        self.assertEqual(envelope["max_model_len"], 65536)
        self.assertEqual(envelope["requested_max_tokens"], 16384)
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
        c2_completion = state["native_characterization_c2_completion"]
        self.assertEqual(c2_completion["status"], "verified")
        self.assertEqual(c2_completion["run_id"], "c2-17cdaabd562e9673")
        self.assertEqual(c2_completion["episode_count"], 188)
        self.assertEqual(c2_completion["block_count"], 4)
        self.assertTrue(c2_completion["grant_consumed"])
        self.assertFalse(c2_completion["live_authorized"])
        c3_completion = state["native_characterization_c3_completion"]
        self.assertEqual(c3_completion["status"], "complete")
        self.assertEqual(c3_completion["run_id"], "c2-17cdaabd562e9673")
        self.assertEqual(c3_completion["episode_count"], 188)
        self.assertEqual(c3_completion["history_count"], 4)
        self.assertEqual(c3_completion["interval_count"], 1504)
        self.assertEqual(c3_completion["p_L"], c3_completion["p_U"])
        self.assertFalse(c3_completion["live_authorized"])

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
        fields = []
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                block = _pointer(text)
                blocks.append(block)
                fields.append(_fields(block))
        self.assertEqual(len(set(blocks)), 1)
        for observed in fields[1:]:
            self.assertEqual(observed, fields[0])

        # Completed C2/C3 evidence remains pinned; execution authority is
        # intentionally absent from EXPECTED because it advances by stage.
        for key, value in EXPECTED.items():
            self.assertEqual(fields[0].get(key), value, key)

    def test_current_pointer_has_at_most_one_exact_authority_and_keeps_h0_as_history(self) -> None:
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                current = _pointer(text)
                current_fields = _fields(current)
                history = text[text.index(END) + len(END) :]
                self.assertNotIn("live_h0_candidate_authorized=true", current)
                self.assertNotIn("authorized_live_actions=h0_candidate", current)
                declared = current_fields["authorized_live_actions"]
                if declared == "[]":
                    self.assertEqual(
                        current_fields["native_characterization_live_authorized"],
                        "false",
                    )
                else:
                    actions = [part for part in declared.split(",") if part]
                    self.assertEqual(len(actions), 1)
                    action = actions[0]
                    self.assertTrue(action.startswith("native_characterization_"))
                    self.assertEqual(
                        current_fields["current_action_scope"],
                        f"{action}_live_only",
                    )
                    self.assertEqual(
                        current_fields["next_allowed_action"], f"run_{action}"
                    )
                self.assertIn("HISTORICAL_SOLUTION_LANE_BELOW=true", history)
                self.assertIn("live_h0_candidate_authorized=true", history)


if __name__ == "__main__":
    import unittest

    unittest.main()
