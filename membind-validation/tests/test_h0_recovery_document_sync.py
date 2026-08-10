"""Cross-document contract for the current H0-B R5 offline repair stage.

The four maintained protocol/memory documents must expose the same immutable
attempt evidence and the same post-workload R5 repair workflow.  This test reads
documentation only; it never loads credentials, state, artifacts, or services.
"""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    ROOT.parent / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md",
    ROOT.parent / "MemBind_basic_validation_experiment.md",
    ROOT / "EXPERIMENT_PLAN.md",
    ROOT / "GLOBAL_MEMORY.md",
)

REQUIRED_FACTS = (
    "current_recovery_stage=h0_b_harness_recovery_r3_one_shot_replacement",
    "historical_r2_evidence_preserved=true",
    "h0-q1-a-20260809-replacement-001",
    "checkpoint_index_sha256=91c202b2494a690483a345fb73d04733c8f68b9c980edef8caa46565868438f7",
    "runtime_definition_sha256=ada353cf5a418005e06ed5b9549d277b8c72a4aa08aec278d242e4df65f74739",
    "terminal_result_sha256=f5315092bc3942cbd1ced6d3673730d17aa65f7483f5f20c1be97de705dc5227",
    "trial_response_sha256[0]=a84685fc62c8c82f8f59e62d4c3cbbc9772e7fe3c99e026b3eaeeb4dbfe6703e",
    "trial_response_sha256[1]=a84685fc62c8c82f8f59e62d4c3cbbc9772e7fe3c99e026b3eaeeb4dbfe6703e",
    "trial_response_sha256[2]=a84685fc62c8c82f8f59e62d4c3cbbc9772e7fe3c99e026b3eaeeb4dbfe6703e",
    "logical_trials=3/3",
    "http_attempts=3/3",
    "json_parse=3/3",
    "pydantic_validation=3/3",
    "semantic_utility=3/3",
    "h0-q1-b-20260809-attempt-001",
    "checkpoint_index_sha256=fa6280ede4387775c719abd410478b5e1db358d840a10a69025c5a6cddd48896",
    "classification=harness_compatibility_failure_not_candidate_result",
    "logical_trial_count=0",
    "http_attempt_count=0",
    "embedding_workload_request_count=0",
    "history_count=0",
    "source_checkpoint_count=0",
    "fresh_graph_count=0",
    "old_attempt_immutable=true",
    "old_attempt_resumable=false",
    "old_and_new_evidence_mergeable=false",
    "Graphiti nominal clients: EmbedderClient + CrossEncoderClient",
    "preworkload_progress=corpus_ready,history_factory_ready,graph_construction_started,graph_construction_ready",
    "artifact_set_id=v1_3_harness_r3",
    "execution_harness_revision=3",
    "index=artifacts/h0_manifest_sets/v1_3_harness_r3/resolved_manifest_index_v1_3_harness_r3.json",
    "execution_source_count=32",
    "revoke -> r3 TDD/artifact -> transparent decision -> bind offline -> exact one-shot replacement authorize -> 49 sources",
    "connection/timeout/429/5xx -> durable checkpoint -> immediate stop_and_report",
    "startup_monitoring=frequent; stable_monitoring=long_interval; program_output=detailed_segmented",
    "mainline_gpt55_temporary_access=forbidden",
    "current_recovery_stage=h0_b_infrastructure_rerun_r4_offline_binding",
    "h0_b_interrupted_attempt_id=h0-q1-b-20260809-replacement-001",
    "checkpoint_index_sha256=7305c1ff2c5790223bb22a0ad8a3e6749c3752950164641eb5a546cfe8aa4553",
    "classification=infrastructure_interruption_not_candidate_result",
    "stop_reason=vllm_unreachable",
    "artifact_set_id=v1_3_harness_r4",
    "execution_harness_revision=4",
    "index_sha256=a08b3f704c9680476990f24edc239d4af50ced39edcf9aae0d529b5ed14332d7",
    "replacement_attempt_id=h0-q1-b-20260810-replacement-002",
    "revoke consumed r3 grant -> R4 TDD/artifact -> transparent infrastructure decision -> bind offline -> exact one-shot replacement-002 authorize",
    "current_recovery_stage=h0_b_infrastructure_rerun_r4_live_authorized",
    "status=h0_q1_b_live_only",
    "current_blocker=none",
    "current_action_scope=h0_q1_b_live_only",
    "live_h0_candidate_authorized=true",
    "authorized_stage_attempt_id=h0-q1-b-20260810-replacement-002",
    "r4_decision_sha256=ec0c8b6c6d10c0a69e8a4fb3793ccb47f865f00668b58e2c9cce02bd5a2b5a8d",
    "r4_tdd_evidence_sha256=316769827a48b940dc6cb33ca4284c9244aafef8e45a8046f2977fd00d5e87a1",
    "state_sha256=558c93b76a0b9b8056d01efa5e013ab5992f767eb6c7047739925f39040690d1",
    "replacement_checkpoint_exists=false",
    "next_allowed_action=run_q1_h0-b-infrastructure-rerun",
    "current_recovery_stage=h0_b_post_workload_harness_repair_r5_offline_only",
    "h0_b_post_workload_failed_attempt_id=h0-q1-b-20260810-replacement-002",
    "checkpoint_index_sha256=e2187d3e101459e9c9a873d8dffb3fbcc858d139833f7f392eedff1c2c78c665",
    "failure_segment_sha256=689285595818aac01f008cb279d3a71cdb084abe35dd79e04e23e93d9d3eadd5",
    "source_checkpoint_sha256=1cdb5b70c86790d144179e855143018d2a97cd32d9e9fc70d5c1e218cd88211c",
    "classification=local_execution_harness_interface_contract_not_candidate_result",
    "workload_reached=true",
    "logical_trial_count=6",
    "http_attempt_count=6",
    "embedding_workload_request_count=4",
    "source_checkpoint_count=1",
    "old_attempt_resumable=false",
    "old_and_new_evidence_mergeable=false",
    "artifact_set_id=v1_3_harness_r5",
    "execution_harness_revision=5",
    "index=artifacts/h0_manifest_sets/v1_3_harness_r5/resolved_manifest_index_v1_3_harness_r5.json",
    "index_sha256=3f41f7520255a1ab64e9ee34efebaccbb05a1d580b7a390057ced0f02b3d13dd",
    "execution_source_count=32",
    "r5_status=offline_resolved_not_live_authorized",
    "status=h0_b_post_workload_harness_failure_live_revoked",
    "current_blocker=manifest_contract_failure",
    "current_action_scope=h0_b_post_workload_harness_repair_offline_only",
    "live_h0_candidate_authorized=false",
    "authorized_live_actions=none",
    "authorized_h0_candidate_id=none",
    "replacement_attempt_id=h0-q1-b-20260810-replacement-003",
    "next_allowed_action=prepare_h0_b_post_workload_harness_repair",
    "current_recovery_stage=h0_b_post_workload_harness_repair_r5_live_authorized",
    "authorized_stage_attempt_id=h0-q1-b-20260810-replacement-003",
    "r5_decision_sha256=98841771c9ccf35fca6526e36295cb5f1439c256332a47a62ffac87693cc0084",
    "r5_tdd_evidence_sha256=cb2b6d8a2e56f4ee207dbaf538da2c5c273dc701977b013fefb7af482207b89a",
    "decision_result_blind=false",
    "prior_model_workload_output_observed=true",
    "repair_required_independent_of_model_response_content=true",
    "old_attempt_qualification_reusable=false",
    "old_and_new_trial_counts_mergeable=false",
    "resume_failed_attempt_allowed=false",
    "state_sha256=e4c376bdb4559140d2380144c76bc33579c694d90cf098330cb4ede9b462c6c3",
    "next_allowed_action=run_q1_h0-b-post-workload-replacement",
    "current_recovery_stage=h0_b_replacement_003_infrastructure_stop_pending_offline_closure",
    "terminal_attempt_id=h0-q1-b-20260810-replacement-003",
    "terminal_checkpoint_index_sha256=0b813ee7c9f4940e6981398520bf823ced3544ff540f66e03a8181ead5622a76",
    "recorded_terminal_status=candidate_failed",
    "recorded_failure_code=candidate_qualification_failure",
    "evidence_classification=infrastructure_interruption_misclassified_as_candidate_qualification_failure",
    "construction_vllm_unreachable_count=7",
    "wire_request_observation_failure_count=3",
    "incomplete_concurrent_attempt_count=2",
    "source_checkpoint_count=6",
    "candidate_selection_continuation_allowed=false",
    "current_state_live_grant_consumed=true",
    "live_execution_allowed=false",
    "next_allowed_action=stop_and_report_then_offline_tdd",
    "replacement_003_report_sha256=218b062834ed66e4bbdf6b65ecb405c5c17ce7c3889360534f2bec484c43a6ac",
)

STALE_CURRENT_ACTIONS = (
    "next_allowed_action: complete the H0 harness-r2 protocol repair offline",
    "The only next work is offline H0 harness-r2 repair TDD",
)


class H0RecoveryDocumentSyncTests(TestCase):
    def test_all_four_documents_share_current_recovery_facts(self):
        for path in DOCUMENTS:
            text = path.read_text(encoding="utf-8")
            for fact in REQUIRED_FACTS:
                with self.subTest(document=path.name, fact=fact):
                    self.assertTrue(fact in text, f"{path.name} lacks {fact}")

    def test_current_action_no_longer_points_to_pre_h0_a_r2_work(self):
        for path in DOCUMENTS:
            text = path.read_text(encoding="utf-8")
            for stale in STALE_CURRENT_ACTIONS:
                with self.subTest(document=path.name, stale=stale):
                    self.assertTrue(
                        stale not in text, f"{path.name} retains stale action: {stale}"
                    )

    def test_each_document_has_one_current_r5_live_authorization_block(self):
        marker = (
            "current_recovery_stage="
            "h0_b_post_workload_harness_repair_r5_live_authorized"
        )
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                self.assertEqual(path.read_text(encoding="utf-8").count(marker), 1)


if __name__ == "__main__":
    import unittest

    unittest.main()
