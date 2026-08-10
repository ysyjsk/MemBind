"""Offline contracts for the R4 bind/authorize protocol after an infra stop.

These tests use injected validators and synthetic evidence. They freeze the
state-machine and artifact identities before any implementation may authorize
the replacement-002 live run.
"""

from __future__ import annotations

import sys
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_artifacts  # noqa: E402
import h0_harness_recovery as recovery  # noqa: E402
import h0_live_preflight  # noqa: E402
from h0_repair_admission import (  # noqa: E402
    H0RepairAdmissionError,
    build_h0_b_infrastructure_rerun_decision,
    verify_h0_b_infrastructure_rerun_decision,
    write_h0_b_infrastructure_rerun_decision,
)
from h0_phase_state import H0PhaseStateError, build_h0_successor_phase_live_state  # noqa: E402
from h0_runtime import canonical_json_sha256  # noqa: E402


RERUN_ID = "h0-q1-b-20260810-replacement-002"


class H0BInfrastructureRerunProtocolTests(TestCase):
    bindings = {
        "resolved_manifest_index_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r4/"
            "resolved_manifest_index_v1_3_harness_r4.json"
        ),
        "resolved_manifest_index_sha256": "6" * 64,
        "resolved_candidate_manifest_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r4/"
            "resolved_candidates/Q1.777.json"
        ),
        "resolved_candidate_manifest_sha256": "7" * 64,
        "resolved_shared_base_manifest_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r4/"
            "resolved_candidates/shared_base.888.json"
        ),
        "resolved_shared_base_manifest_sha256": "8" * 64,
    }
    verification = {
        "schema_version": "membind.h0.offline-artifact-verification.v3",
        "protocol_version": "current-validation-v1.3",
        "artifact_set_id": "v1_3_harness_r4",
        "execution_harness_revision": 4,
        "status": "verified_offline_not_live_authorized",
        "index_path": bindings["resolved_manifest_index_path"],
        "index_sha256": bindings["resolved_manifest_index_sha256"],
        "generated_json_file_count": 11,
        "binding_count": 10,
        "resolved_wrapper_count": 4,
        "source_spec_count": 4,
        "execution_source_count": 32,
        "secret_scan_passed": True,
        "live_eligible": False,
    }
    tdd = {
        name: {
            "path": f"artifacts/tdd/infra_{name}.log",
            "sha256": "9" * 64,
            "test_count": 1,
        }
        for name in (
            "latest_red",
            "latest_green",
            "latest_focused",
            "latest_full_regression",
        )
    }
    repair = {
        "schema_version": "membind.h0.harness-repair-admission.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": "artifacts/decisions/harness.json",
        "decision_sha256": "1" * 64,
        "decision_result_blind": False,
        "prior_model_workload_output_observed": False,
        "repair_required_independent_of_model_output": True,
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "replacement_attempt_id": "h0-q1-b-20260809-replacement-001",
        "invalidated_stage_attempt_id": "h0-q1-b-20260809-attempt-001",
        "invalidated_checkpoint_index_sha256": "2" * 64,
        "failure_report_sha256": "3" * 64,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "prior_manifest_index_sha256": "a" * 64,
        "repaired_manifest_index_sha256": "4" * 64,
        "secrets_persisted": False,
    }
    infra = {
        "schema_version": "membind.h0.infrastructure-rerun-admission.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": "artifacts/decisions/infra.json",
        "decision_sha256": "5" * 64,
        "interrupted_stage_attempt_id": "h0-q1-b-20260809-replacement-001",
        "interrupted_checkpoint_index_sha256": "b" * 64,
        "interrupted_stop_reason": "vllm_unreachable",
        "prior_harness_repair_admission_sha256": canonical_json_sha256(repair),
        "replacement_attempt_id": RERUN_ID,
        "one_shot_whole_stage_replacement": True,
        "resume_interrupted_attempt_allowed": False,
        "prior_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "scientific_configuration_unchanged": True,
        "prior_manifest_index_sha256": "4" * 64,
        "recovered_manifest_index_sha256": "6" * 64,
        "secrets_persisted": False,
    }
    completion = {
        "schema_version": "membind.h0.prior-phase-terminal-completion.v1",
        "protocol_version": "current-validation-v1.3",
        "status": "qualified_terminal_completion",
        "qualified": True,
        "candidate_id": "Q1",
        "phase": "H0-A",
        "candidate_advance_allowed": True,
        "partial_qualification_reusable": True,
        "requires_whole_stage_rerun": False,
        "stage_attempt_id": "h0-q1-a-20260809-replacement-001",
        "checkpoint_index_path": "artifacts/h0-a/index.json",
        "checkpoint_index_sha256": "c" * 64,
        "runtime_definition_sha256": "d" * 64,
        "terminal_result_sha256": "e" * 64,
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }

    def _closed(self) -> dict:
        return {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "H0",
            "status": "h0_b_infrastructure_interrupted_live_revoked",
            "current_action_scope": "h0_b_infrastructure_recovery_offline_only",
            "current_blocker": "vllm_unreachable",
            "stage_progress": {"h0_live_gate": "forbidden"},
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "h0_phase_completions": {"H0-A": deepcopy(self.completion)},
            "h0_b_infrastructure_interruption": {
                "schema_version": "membind.h0.infrastructure-interruption.v1",
                "protocol_version": "current-validation-v1.3",
                "candidate_id": "Q1",
                "phase": "H0-B",
                "stage_attempt_id": "h0-q1-b-20260809-replacement-001",
                "checkpoint_index_path": "artifacts/h0-b-001/index.json",
                "checkpoint_index_sha256": "b" * 64,
                "stop_reason": "vllm_unreachable",
                "resume_authorized": False,
                "rerun_authorized": False,
                "old_and_new_trial_counts_mergeable": False,
                "partial_qualification_reusable": False,
                "prior_manifest_bindings": {
                    "resolved_manifest_index_path": (
                        "artifacts/h0_manifest_sets/v1_3_harness_r3/"
                        "resolved_manifest_index_v1_3_harness_r3.json"
                    ),
                    "resolved_manifest_index_sha256": "4" * 64,
                    "resolved_candidate_manifest_path": (
                        "artifacts/h0_manifest_sets/v1_3_harness_r3/"
                        "resolved_candidates/Q1.777.json"
                    ),
                    "resolved_candidate_manifest_sha256": "7" * 64,
                    "resolved_shared_base_manifest_path": (
                        "artifacts/h0_manifest_sets/v1_3_harness_r3/"
                        "resolved_candidates/shared_base.888.json"
                    ),
                    "resolved_shared_base_manifest_sha256": "8" * 64,
                },
                "prior_phase_completion": {
                    key: self.completion[key]
                    for key in (
                        "stage_attempt_id",
                        "checkpoint_index_path",
                        "checkpoint_index_sha256",
                        "runtime_definition_sha256",
                        "terminal_result_sha256",
                    )
                },
                "prior_harness_repair_admission": deepcopy(self.repair),
                "prior_harness_repair_admission_sha256": canonical_json_sha256(
                    self.repair
                ),
                "secrets_persisted": False,
            },
        }

    def _validators(self):
        return (
            Mock(return_value=(deepcopy(self.bindings), deepcopy(self.verification))),
            Mock(return_value=deepcopy(self.tdd)),
            Mock(return_value=deepcopy(self.infra)),
        )

    def test_current_execution_identity_is_r5_and_r4_index_is_immutable_history(self):
        self.assertEqual(h0_artifacts.H0_ARTIFACT_SET_ID, "v1_3_harness_r5")
        self.assertEqual(h0_artifacts.H0_EXECUTION_HARNESS_REVISION, 5)
        self.assertEqual(h0_live_preflight._ARTIFACT_SET_ID, "v1_3_harness_r5")
        self.assertEqual(h0_live_preflight._EXECUTION_HARNESS_REVISION, 5)
        self.assertEqual(
            __import__("hashlib").sha256(
                (ROOT / "artifacts/h0_manifest_sets/v1_3_harness_r4/"
                 "resolved_manifest_index_v1_3_harness_r4.json").read_bytes()
            ).hexdigest(),
            "a08b3f704c9680476990f24edc239d4af50ced39edcf9aae0d529b5ed14332d7",
        )

    def test_closed_state_binds_r4_offline_then_authorizes_only_exact_002(self):
        manifest, tdd, decision = self._validators()
        bound = recovery.build_h0_b_infrastructure_rerun_bound_state(
            self._closed(),
            root=ROOT,
            manifest_verification=self.verification,
            tdd_evidence=self.tdd,
            rerun_decision_path=self.infra["decision_path"],
            rerun_decision_sha256=self.infra["decision_sha256"],
            manifest_validator=manifest,
            tdd_validator=tdd,
            rerun_decision_verifier=decision,
        )
        self.assertFalse(bound["live_h0_candidate_authorized"])
        self.assertEqual(bound["stage_progress"]["h0_live_gate"], "forbidden")
        live = recovery.build_h0_b_infrastructure_rerun_live_state(
            bound,
            root=ROOT,
            manifest_validator=manifest,
            tdd_validator=tdd,
            rerun_decision_verifier=decision,
        )
        authorization = live["live_h0_authorization"]
        self.assertEqual(authorization["authorized_stage_attempt_id"], RERUN_ID)
        self.assertEqual(authorization["repair_admission"], self.repair)
        self.assertEqual(authorization["infrastructure_rerun_admission"], self.infra)
        self.assertEqual(
            authorization["resolved_manifest_index_sha256"],
            self.bindings["resolved_manifest_index_sha256"],
        )

    def test_h0_b_completion_must_match_current_authorized_attempt(self):
        source = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "H0",
            "status": "h0_q1_b_live_only",
            "current_action_scope": "h0_q1_b_live_only",
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "stage_progress": {"h0_live_gate": "h0_q1_b_live_only"},
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-B",
                "authorized_stage_attempt_id": RERUN_ID,
                **deepcopy(self.bindings),
            },
        }
        wrong = {
            "qualified": True,
            "candidate_id": "Q1",
            "phase": "H0-B",
            "stage_attempt_id": "some-other-qualified-attempt",
            "checkpoint_index_path": "artifacts/other/index.json",
            "checkpoint_index_sha256": "1" * 64,
            "runtime_definition_sha256": "2" * 64,
            "terminal_result_sha256": "3" * 64,
            "secrets_persisted": False,
        }
        with self.assertRaises(H0PhaseStateError):
            build_h0_successor_phase_live_state(
                source,
                root=ROOT,
                completed_phase="H0-B",
                stage_attempt_id=wrong["stage_attempt_id"],
                checkpoint_index_path=wrong["checkpoint_index_path"],
                checkpoint_index_sha256=wrong["checkpoint_index_sha256"],
                runtime_definition_sha256=wrong["runtime_definition_sha256"],
                completion_validator=Mock(return_value=wrong),
            )

    def test_r4_decision_is_reproducible_immutable_and_returns_exact_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "membind-validation"
            for relative in ("configs/h0",):
                shutil.copytree(ROOT / relative, staged / relative)
            files = (
                *h0_artifacts.H0_EXECUTION_SOURCE_PATHS,
                "artifacts/dataset/frozen_split_v1_3.json",
                "artifacts/environment/embedding_model_fingerprint.json",
                "artifacts/environment/v3_construction_runtime_evidence_20260809.json",
                "artifacts/diagnostics/h0_q1_b_replacement_001_infrastructure_interruption_20260809.json",
                "artifacts/h0_protocol_repair/decisions/q1_h0_b_harness_compatibility_repair.json",
            )
            for relative in files:
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            for relative in (
                "artifacts/h0_manifest_sets/v1_3_harness_r3",
                "artifacts/h0_manifest_sets/v1_3_harness_r4",
                (
                    "artifacts/h0_runs/h0/checkpoints/"
                    "h0-q1-b-20260809-replacement-001"
                ),
            ):
                shutil.copytree(ROOT / relative, staged / relative)

            r4_index = staged / (
                "artifacts/h0_manifest_sets/v1_3_harness_r4/"
                "resolved_manifest_index_v1_3_harness_r4.json"
            )
            verification = {
                "schema_version": "membind.h0.offline-artifact-verification.v3",
                "protocol_version": "current-validation-v1.3",
                "artifact_set_id": "v1_3_harness_r4",
                "execution_harness_revision": 4,
                "status": "verified_offline_not_live_authorized",
                "index_path": r4_index.relative_to(staged).as_posix(),
                "index_sha256": __import__("hashlib").sha256(
                    r4_index.read_bytes()
                ).hexdigest(),
                "generated_json_file_count": 11,
                "binding_count": 10,
                "resolved_wrapper_count": 4,
                "source_spec_count": 4,
                "execution_source_count": 32,
                "secret_scan_passed": True,
                "live_eligible": False,
            }
            decision = build_h0_b_infrastructure_rerun_decision(
                root=staged,
                manifest_verification=verification,
                replacement_attempt_id=RERUN_ID,
            )
            written = write_h0_b_infrastructure_rerun_decision(
                root=staged,
                manifest_verification=verification,
                replacement_attempt_id=RERUN_ID,
            )
            admission = verify_h0_b_infrastructure_rerun_decision(
                root=staged,
                decision_path=written["decision_path"],
                decision_sha256=written["decision_sha256"],
                manifest_verification=verification,
            )
            self.assertEqual(decision["replacement"]["attempt_id"], RERUN_ID)
            self.assertEqual(set(admission), set(self.infra))
            self.assertEqual(
                admission["recovered_manifest_index_sha256"],
                verification["index_sha256"],
            )
            self.assertFalse(admission["old_and_new_trial_counts_mergeable"])
            before = (staged / written["decision_path"]).read_bytes()
            self.assertEqual(
                write_h0_b_infrastructure_rerun_decision(
                    root=staged,
                    manifest_verification=verification,
                    replacement_attempt_id=RERUN_ID,
                ),
                written,
            )
            self.assertEqual((staged / written["decision_path"]).read_bytes(), before)
            with self.assertRaises(H0RepairAdmissionError):
                build_h0_b_infrastructure_rerun_decision(
                    root=staged,
                    manifest_verification=verification,
                    replacement_attempt_id="h0-q1-b-20260810-replacement-003",
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
