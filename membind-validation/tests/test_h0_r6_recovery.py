"""Offline RED contracts for R6 recovery from the consumed R5 live grant.

The R5 ``replacement-003`` run reached the workload, then a vLLM outage won
only after a concurrent qualification exception had already selected the wrong
terminal branch.  These tests bind the immutable, sanitized evidence needed to
classify that run as scientifically inconclusive and require a fresh, empty
``replacement-004`` namespace under the source-bound R6 harness.

This module never loads credentials, imports the temporary GPT lane, or contacts
construction, embedding, Neo4j, SSH, or any other service.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_harness_recovery as recovery  # noqa: E402
import h0_repair_admission as admission  # noqa: E402


INVALIDATED_ATTEMPT_ID = "h0-q1-b-20260810-replacement-003"
REPLACEMENT_ATTEMPT_ID = "h0-q1-b-20260810-replacement-004"
R5_ARTIFACT_SET_ID = "v1_3_harness_r5"
R6_ARTIFACT_SET_ID = "v1_3_harness_r6"
R6_HARNESS_REVISION = 6
R5_INDEX_REL = (
    "artifacts/h0_manifest_sets/v1_3_harness_r5/"
    "resolved_manifest_index_v1_3_harness_r5.json"
)
R5_INDEX_SHA256 = (
    "3f41f7520255a1ab64e9ee34efebaccbb05a1d580b7a390057ced0f02b3d13dd"
)
R6_INDEX_REL = (
    "artifacts/h0_manifest_sets/v1_3_harness_r6/"
    "resolved_manifest_index_v1_3_harness_r6.json"
)
CHECKPOINT_INDEX_REL = (
    "artifacts/h0_runs/h0/checkpoints/"
    f"{INVALIDATED_ATTEMPT_ID}/index.json"
)
CHECKPOINT_INDEX_SHA256 = (
    "0b813ee7c9f4940e6981398520bf823ced3544ff540f66e03a8181ead5622a76"
)
FAILURE_SEGMENT_REL = (
    "artifacts/h0_runs/h0/checkpoints/"
    f"{INVALIDATED_ATTEMPT_ID}/"
    "000019.candidate_failure.candidate_qualification_failure."
    "d1fad184dec05c3e32907c142382d9d1dd3b5655f2042205b201da3b21d2b732.json"
)
FAILURE_SEGMENT_SHA256 = (
    "d1fad184dec05c3e32907c142382d9d1dd3b5655f2042205b201da3b21d2b732"
)
LIVE_LOG_REL = "artifacts/live_logs/h0_q1_b_20260810_replacement_003.log"
LIVE_LOG_SHA256 = (
    "adf687a3a73f8acf100b5be561b2b471878b4e7fe696bf2c3200878501fea24e"
)
MISCLASSIFICATION_REPORT_REL = (
    "artifacts/h0_protocol_repair/reports/"
    "q1_h0_b_replacement_003_infrastructure_misclassification_20260810.json"
)
MISCLASSIFICATION_REPORT_SHA256 = (
    "218b062834ed66e4bbdf6b65ecb405c5c17ce7c3889360534f2bec484c43a6ac"
)
ROOT_CAUSE_REPORT_REL = (
    "artifacts/h0_protocol_repair/reports/"
    "q1_h0_b_replacement_003_concurrent_failure_root_cause_20260810.md"
)
ROOT_CAUSE_REPORT_SHA256 = (
    "153d480e4af93a38a5305bcf2b35d4e19a99d9c59860c20455e27e9a3e44430b"
)


def _required_callable(module: object, name: str):
    value = getattr(module, name, None)
    if not callable(value):
        raise AssertionError(f"required R6 recovery API is missing: {name}")
    return value


def _classification_kwargs() -> dict[str, object]:
    return {
        "root": ROOT,
        "stage_attempt_id": INVALIDATED_ATTEMPT_ID,
        "checkpoint_index_path": CHECKPOINT_INDEX_REL,
        "checkpoint_index_sha256": CHECKPOINT_INDEX_SHA256,
        "failure_segment_path": FAILURE_SEGMENT_REL,
        "failure_segment_sha256": FAILURE_SEGMENT_SHA256,
        "live_log_path": LIVE_LOG_REL,
        "live_log_sha256": LIVE_LOG_SHA256,
        "misclassification_report_path": MISCLASSIFICATION_REPORT_REL,
        "misclassification_report_sha256": MISCLASSIFICATION_REPORT_SHA256,
        "root_cause_report_path": ROOT_CAUSE_REPORT_REL,
        "root_cause_report_sha256": ROOT_CAUSE_REPORT_SHA256,
        "r5_manifest_index_path": R5_INDEX_REL,
        "r5_manifest_index_sha256": R5_INDEX_SHA256,
    }


def _expected_classification() -> dict[str, object]:
    return {
        "schema_version": (
            "membind.h0.r5-consumed-grant-infrastructure-interruption.v1"
        ),
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "stage_attempt_id": INVALIDATED_ATTEMPT_ID,
        "recorded_status": "candidate_failed",
        "recorded_failure_code": "candidate_qualification_failure",
        "recorded_terminal_classification_reliable": False,
        "scientific_status": "infrastructure_interrupted",
        "scientific_failure_class": "infrastructure_interruption",
        "stop_reason": "vllm_unreachable",
        "candidate_model_failure_supported": False,
        "infrastructure_interruption_supported": True,
        "classification_uses_model_response_content": False,
        "candidate_selection_may_continue": False,
        "requires_whole_stage_rerun": True,
        "logical_trial_count": 35,
        "http_attempt_count": 35,
        "completed_attempt_count": 33,
        "incomplete_concurrent_attempt_count": 2,
        "http_200_count": 26,
        "json_parse_success_count": 23,
        "pydantic_validation_success_count": 23,
        "semantic_utility_success_count": 23,
        "wire_request_observation_failure_count": 3,
        "vllm_unreachable_count": 7,
        "retry_count": 0,
        "embedding_workload_request_count": 44,
        "source_checkpoint_count": 6,
        "fresh_graph_count": 1,
        "closed_graph_count": 1,
        "cleanup_failure_count": 0,
        "cross_encoder_rank_call_count": 0,
        "partial_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "resume_interrupted_attempt_allowed": False,
        "source_checkpoints_reusable": False,
        "checkpoint_namespace_reusable": False,
        "replacement_attempt_id": REPLACEMENT_ATTEMPT_ID,
        "checkpoint_index_path": CHECKPOINT_INDEX_REL,
        "checkpoint_index_sha256": CHECKPOINT_INDEX_SHA256,
        "failure_segment_path": FAILURE_SEGMENT_REL,
        "failure_segment_sha256": FAILURE_SEGMENT_SHA256,
        "live_log_path": LIVE_LOG_REL,
        "live_log_sha256": LIVE_LOG_SHA256,
        "misclassification_report_path": MISCLASSIFICATION_REPORT_REL,
        "misclassification_report_sha256": MISCLASSIFICATION_REPORT_SHA256,
        "root_cause_report_path": ROOT_CAUSE_REPORT_REL,
        "root_cause_report_sha256": ROOT_CAUSE_REPORT_SHA256,
        "r5_manifest_index_path": R5_INDEX_REL,
        "r5_manifest_index_sha256": R5_INDEX_SHA256,
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }


def _r6_verification() -> dict[str, object]:
    return {
        "schema_version": "membind.h0.offline-artifact-verification.v3",
        "protocol_version": "current-validation-v1.3",
        "artifact_set_id": R6_ARTIFACT_SET_ID,
        "execution_harness_revision": R6_HARNESS_REVISION,
        "status": "verified_offline_not_live_authorized",
        "index_path": R6_INDEX_REL,
        "index_sha256": "6" * 64,
        "generated_json_file_count": 11,
        "binding_count": 10,
        "resolved_wrapper_count": 4,
        "source_spec_count": 4,
        "execution_source_count": 32,
        "secret_scan_passed": True,
        "live_eligible": False,
    }


def _expected_admission() -> dict[str, object]:
    return {
        "schema_version": "membind.h0.r6-recovery-admission.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "invalidated_stage_attempt_id": INVALIDATED_ATTEMPT_ID,
        "invalidated_checkpoint_index_sha256": CHECKPOINT_INDEX_SHA256,
        "failure_segment_sha256": FAILURE_SEGMENT_SHA256,
        "live_log_sha256": LIVE_LOG_SHA256,
        "misclassification_report_sha256": MISCLASSIFICATION_REPORT_SHA256,
        "root_cause_report_sha256": ROOT_CAUSE_REPORT_SHA256,
        "prior_manifest_index_sha256": R5_INDEX_SHA256,
        "repaired_manifest_index_sha256": "6" * 64,
        "scientific_failure_class": "infrastructure_interruption",
        "interrupted_stop_reason": "vllm_unreachable",
        "replacement_attempt_id": REPLACEMENT_ATTEMPT_ID,
        "one_shot_whole_stage_replacement": True,
        "resume_interrupted_attempt_allowed": False,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "source_checkpoints_reusable": False,
        "fresh_checkpoint_namespace_required": True,
        "scientific_configuration_unchanged": True,
        "live_authorized_by_this_admission": False,
        "secrets_persisted": False,
    }


class H0R6RecoveryEvidenceRedTests(TestCase):
    def test_new_identity_and_attempt_constants_are_exact(self):
        expected_recovery = {
            "R6_ARTIFACT_SET_ID": R6_ARTIFACT_SET_ID,
            "R6_HARNESS_REVISION": R6_HARNESS_REVISION,
            "R6_INDEX_PATH": R6_INDEX_REL,
            "R6_INVALIDATED_ATTEMPT_ID": INVALIDATED_ATTEMPT_ID,
            "R6_REPLACEMENT_ATTEMPT_ID": REPLACEMENT_ATTEMPT_ID,
        }
        expected_admission = {
            "H0_B_R6_ARTIFACT_SET_ID": R6_ARTIFACT_SET_ID,
            "H0_B_R6_HARNESS_REVISION": R6_HARNESS_REVISION,
            "H0_B_R6_INDEX_PATH": R6_INDEX_REL,
            "H0_B_R6_INVALIDATED_ATTEMPT_ID": INVALIDATED_ATTEMPT_ID,
            "H0_B_R6_REPLACEMENT_ATTEMPT_ID": REPLACEMENT_ATTEMPT_ID,
        }
        for module, expected in (
            (recovery, expected_recovery),
            (admission, expected_admission),
        ):
            for name, value in expected.items():
                with self.subTest(module=module.__name__, name=name):
                    self.assertEqual(getattr(module, name, None), value)

    def test_frozen_003_evidence_is_scientifically_infrastructure_interrupted(self):
        classify = _required_callable(
            recovery, "classify_h0_b_r5_infrastructure_misclassification"
        )
        observed = classify(**_classification_kwargs())
        self.assertEqual(observed, _expected_classification())
        self.assertFalse(observed["source_checkpoints_reusable"])
        self.assertFalse(observed["checkpoint_namespace_reusable"])

    def test_classifier_rejects_every_tampered_evidence_hash(self):
        classify = _required_callable(
            recovery, "classify_h0_b_r5_infrastructure_misclassification"
        )
        for field in (
            "checkpoint_index_sha256",
            "failure_segment_sha256",
            "live_log_sha256",
            "misclassification_report_sha256",
            "root_cause_report_sha256",
            "r5_manifest_index_sha256",
        ):
            with self.subTest(field=field):
                changed = _classification_kwargs()
                changed[field] = "0" * 64
                with self.assertRaises(recovery.H0HarnessRecoveryError):
                    classify(**changed)


class H0R6ManifestVerificationRedTests(TestCase):
    def test_verifier_accepts_only_exact_r6_revision_6_shape(self):
        validate = _required_callable(
            recovery, "validate_h0_b_r6_manifest_verification"
        )
        verification = _r6_verification()
        self.assertEqual(validate(verification), verification)

        for field, value in (
            ("artifact_set_id", R5_ARTIFACT_SET_ID),
            ("execution_harness_revision", 5),
            ("index_path", R5_INDEX_REL),
        ):
            with self.subTest(field=field):
                changed = {**verification, field: value}
                with self.assertRaises(recovery.H0HarnessRecoveryError):
                    validate(changed)


class H0R6AdmissionAndAuthorizationRedTests(TestCase):
    def test_admission_is_non_authorizing_and_allows_only_exact_004(self):
        build = _required_callable(admission, "build_h0_b_r6_recovery_admission")
        classification = _expected_classification()
        verification = _r6_verification()
        observed = build(
            classification=classification,
            manifest_verification=verification,
            replacement_attempt_id=REPLACEMENT_ATTEMPT_ID,
        )
        self.assertEqual(observed, _expected_admission())
        self.assertFalse(observed["live_authorized_by_this_admission"])

        for attempt_id in (
            INVALIDATED_ATTEMPT_ID,
            "h0-q1-b-20260810-replacement-005",
        ):
            with self.subTest(attempt_id=attempt_id), self.assertRaises(
                admission.H0RepairAdmissionError
            ):
                build(
                    classification=classification,
                    manifest_verification=verification,
                    replacement_attempt_id=attempt_id,
                )

        changed = deepcopy(classification)
        changed["source_checkpoints_reusable"] = True
        with self.assertRaises(admission.H0RepairAdmissionError):
            build(
                classification=changed,
                manifest_verification=verification,
                replacement_attempt_id=REPLACEMENT_ATTEMPT_ID,
            )

    def test_live_authorization_gate_accepts_only_exact_004_and_r6_admission(self):
        validate = _required_callable(
            recovery, "validate_h0_b_r6_live_authorization"
        )
        authorization = {
            "candidate_id": "Q1",
            "phase": "H0-B",
            "authorized_stage_attempt_id": REPLACEMENT_ATTEMPT_ID,
            "resolved_manifest_index_path": R6_INDEX_REL,
            "resolved_manifest_index_sha256": "6" * 64,
            "repair_admission": {"schema_version": "frozen-r3-admission"},
            "infrastructure_rerun_admission": {
                "schema_version": "frozen-r4-admission"
            },
            "post_workload_repair_admission": {
                "schema_version": "frozen-r5-admission"
            },
            "r6_recovery_admission": _expected_admission(),
        }
        self.assertEqual(
            validate(authorization, stage_attempt_id=REPLACEMENT_ATTEMPT_ID),
            authorization,
        )

        cases = []
        for attempt_id in (
            INVALIDATED_ATTEMPT_ID,
            "h0-q1-b-20260810-replacement-005",
        ):
            cases.append(
                (
                    f"authorization-{attempt_id}",
                    {**authorization, "authorized_stage_attempt_id": attempt_id},
                    REPLACEMENT_ATTEMPT_ID,
                )
            )
            cases.append(
                (f"runtime-{attempt_id}", authorization, attempt_id)
            )
        r5_authorization = deepcopy(authorization)
        r5_authorization["resolved_manifest_index_path"] = R5_INDEX_REL
        cases.append(("r5-manifest", r5_authorization, REPLACEMENT_ATTEMPT_ID))
        reusable = deepcopy(authorization)
        reusable["r6_recovery_admission"]["source_checkpoints_reusable"] = True
        cases.append(("checkpoint-reuse", reusable, REPLACEMENT_ATTEMPT_ID))

        for label, changed, runtime_attempt_id in cases:
            with self.subTest(label=label), self.assertRaises(
                recovery.H0HarnessRecoveryError
            ):
                validate(changed, stage_attempt_id=runtime_attempt_id)


if __name__ == "__main__":
    import unittest

    unittest.main()
