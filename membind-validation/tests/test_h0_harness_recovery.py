"""Offline contracts for one-shot recovery from the H0-B harness failure.

The failed r2 attempt reached all three readiness gates but made no workload
request.  These tests freeze the evidence and state transitions needed before a
new r3 whole-stage H0-B attempt can be authorized.  They never load credentials
or contact construction, embedding, Neo4j, or SSH services.
"""

from __future__ import annotations

import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_harness_recovery import (  # noqa: E402
    H0HarnessRecoveryError,
    build_h0_b_harness_repair_bound_state,
    build_h0_b_harness_revoked_state,
    build_h0_b_replacement_live_state,
)
from h0_runtime import (  # noqa: E402
    H0StateGateError,
    authorize_h0_live_entry,
    canonical_json_bytes,
    sha256_file,
)


class H0BHarnessRecoveryTests(TestCase):
    old_attempt_id = "h0-q1-b-20260809-attempt-001"
    replacement_attempt_id = "h0-q1-b-20260809-replacement-001"

    r2_bindings = {
        "resolved_manifest_index_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r2/"
            "resolved_manifest_index_v1_3_harness_r2.json"
        ),
        "resolved_manifest_index_sha256": "1" * 64,
        "resolved_candidate_manifest_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r2/"
            "resolved_candidates/Q1.222.json"
        ),
        "resolved_candidate_manifest_sha256": "2" * 64,
        "resolved_shared_base_manifest_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r2/"
            "resolved_candidates/shared_base.333.json"
        ),
        "resolved_shared_base_manifest_sha256": "3" * 64,
    }
    r3_bindings = {
        "resolved_manifest_index_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r3/"
            "resolved_manifest_index_v1_3_harness_r3.json"
        ),
        "resolved_manifest_index_sha256": "4" * 64,
        "resolved_candidate_manifest_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r3/"
            "resolved_candidates/Q1.555.json"
        ),
        "resolved_candidate_manifest_sha256": "5" * 64,
        "resolved_shared_base_manifest_path": (
            "artifacts/h0_manifest_sets/v1_3_harness_r3/"
            "resolved_candidates/shared_base.666.json"
        ),
        "resolved_shared_base_manifest_sha256": "6" * 64,
    }
    h0_a_completion = {
        "schema_version": "membind.h0.prior-phase-terminal-completion.v1",
        "protocol_version": "current-validation-v1.3",
        "status": "qualified_terminal_completion",
        "qualified": True,
        "candidate_id": "Q1",
        "phase": "H0-A",
        "stage_attempt_id": "h0-q1-a-20260809-replacement-001",
        "checkpoint_index_path": (
            "artifacts/h0_runs/h0/checkpoints/"
            "h0-q1-a-20260809-replacement-001/index.json"
        ),
        "checkpoint_index_sha256": "7" * 64,
        "terminal_result_sha256": "8" * 64,
        "runtime_definition_sha256": "9" * 64,
        "candidate_advance_allowed": True,
        "partial_qualification_reusable": True,
        "requires_whole_stage_rerun": False,
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }

    def _write_json(self, root: Path, relative: str, value: dict) -> tuple[str, str]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(value))
        return relative, sha256_file(path)

    def _live_r2_state(self) -> dict:
        prior = {
            key: self.h0_a_completion[key]
            for key in (
                "stage_attempt_id",
                "checkpoint_index_path",
                "checkpoint_index_sha256",
                "runtime_definition_sha256",
                "terminal_result_sha256",
            )
        }
        return {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "H0",
            "status": "h0_q1_b_live_only",
            "current_action_scope": "h0_q1_b_live_only",
            "current_blocker": None,
            "stage_progress": {
                "h0_live_gate": "h0_q1_b_live_only",
                "h0_candidate_progression": "h0-a_qualified_h0-b_authorized",
                "preserved": "yes",
            },
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "service_admin_authorized": False,
            "next_allowed_action": "run_q1_h0-b",
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-B",
                **deepcopy(self.r2_bindings),
                "prior_phase_completion": prior,
            },
            "h0_phase_completions": {"H0-A": deepcopy(self.h0_a_completion)},
            "unrelated": {"preserved": True},
        }

    def _failure_evidence(self, root: Path) -> dict:
        segment_relative, segment_sha = self._write_json(
            root,
            (
                "artifacts/h0_runs/h0/checkpoints/"
                f"{self.old_attempt_id}/000009.candidate_failure."
                "manifest_contract_failure.json"
            ),
            {
                "schema_version": "membind.h0.checkpoint-segment.v1",
                "protocol_version": "current-validation-v1.3",
                "stage_attempt_id": self.old_attempt_id,
                "segment_kind": "candidate_failure",
                "segment_id": "manifest_contract_failure",
                "payload": {
                    "failure_code": "manifest_contract_failure",
                    "attempt_ledger": {"logical_trials": [], "http_attempts": []},
                    "runtime_evidence": {
                        "fresh_graph_count": 0,
                        "histories": [],
                        "embedding_workload_request_count": 0,
                    },
                    "candidate_advance_allowed": False,
                },
                "secrets_persisted": False,
                "raw_prompts_persisted": False,
                "raw_responses_persisted": False,
            },
        )
        checkpoint_relative, checkpoint_sha = self._write_json(
            root,
            f"artifacts/h0_runs/h0/checkpoints/{self.old_attempt_id}/index.json",
            {
                "schema_version": "membind.h0.checkpoint-index.v1",
                "protocol_version": "current-validation-v1.3",
                "stage_attempt_id": self.old_attempt_id,
                "candidate_id": "Q1",
                "phase": "H0-B",
                "status": "candidate_failed",
                "failure_code": "manifest_contract_failure",
                "failure_evidence_sha256": "a" * 64,
                "candidate_advance_allowed": False,
                "candidate_selection_may_continue": True,
                "partial_qualification_reusable": False,
                "requires_whole_stage_rerun": False,
                "secrets_persisted": False,
                "raw_prompts_persisted": False,
                "raw_responses_persisted": False,
            },
        )
        report = {
            "schema_version": "membind.h0.harness-compatibility-failure-report.v1",
            "protocol_version": "current-validation-v1.3",
            "status": "h0_b_pre_workload_harness_compatibility_failure",
            "classification": "harness_compatibility_failure_not_candidate_result",
            "candidate_id": "Q1",
            "phase": "H0-B",
            "stage_attempt_id": self.old_attempt_id,
            "checkpoint_index_path": checkpoint_relative,
            "checkpoint_index_sha256": checkpoint_sha,
            "failure_segment_path": segment_relative,
            "failure_segment_sha256": segment_sha,
            "failure_code": "manifest_contract_failure",
            "readiness_qualified": True,
            "logical_trial_count": 0,
            "http_attempt_count": 0,
            "source_checkpoint_count": 0,
            "history_count": 0,
            "fresh_graph_count": 0,
            "embedding_workload_request_count": 0,
            "model_workload_output_observed": False,
            "candidate_qualification_interpretable": False,
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }
        report_relative, report_sha = self._write_json(
            root,
            "artifacts/diagnostics/h0_q1_b_harness_failure.json",
            report,
        )
        return {
            "checkpoint_index_path": checkpoint_relative,
            "checkpoint_index_sha256": checkpoint_sha,
            "failure_report_path": report_relative,
            "failure_report_sha256": report_sha,
            "report": report,
        }

    def _revoked(self, root: Path) -> tuple[dict, dict]:
        evidence = self._failure_evidence(root)
        revoked = build_h0_b_harness_revoked_state(
            self._live_r2_state(),
            root=root,
            stage_attempt_id=self.old_attempt_id,
            checkpoint_index_path=evidence["checkpoint_index_path"],
            checkpoint_index_sha256=evidence["checkpoint_index_sha256"],
            failure_report_path=evidence["failure_report_path"],
            failure_report_sha256=evidence["failure_report_sha256"],
        )
        return revoked, evidence

    def _verification(self) -> dict:
        return {
            "schema_version": "membind.h0.offline-artifact-verification.v3",
            "protocol_version": "current-validation-v1.3",
            "artifact_set_id": "v1_3_harness_r3",
            "execution_harness_revision": 3,
            "status": "verified_offline_not_live_authorized",
            "index_path": self.r3_bindings["resolved_manifest_index_path"],
            "index_sha256": self.r3_bindings["resolved_manifest_index_sha256"],
            "generated_json_file_count": 11,
            "binding_count": 10,
            "resolved_wrapper_count": 4,
            "source_spec_count": 4,
            "execution_source_count": 32,
            "secret_scan_passed": True,
            "live_eligible": False,
        }

    def _tdd(self) -> dict:
        return {
            name: {
                "path": f"artifacts/tdd/h0_b_recovery_{name}.log",
                "sha256": "b" * 64,
                "test_count": 1,
            }
            for name in (
                "latest_red",
                "latest_green",
                "latest_focused",
                "latest_full_regression",
            )
        }

    def _admission(self, evidence: dict) -> dict:
        return {
            "schema_version": "membind.h0.harness-repair-admission.v1",
            "protocol_version": "current-validation-v1.3",
            "candidate_id": "Q1",
            "phase": "H0-B",
            "decision_path": (
                "artifacts/h0_protocol_repair/decisions/"
                "q1_h0_b_harness_compatibility_repair.json"
            ),
            "decision_sha256": "c" * 64,
            "decision_result_blind": False,
            "prior_model_workload_output_observed": False,
            "repair_required_independent_of_model_output": True,
            "scientific_configuration_unchanged": True,
            "one_shot_whole_stage_replacement": True,
            "replacement_attempt_id": self.replacement_attempt_id,
            "invalidated_stage_attempt_id": self.old_attempt_id,
            "invalidated_checkpoint_index_sha256": evidence[
                "checkpoint_index_sha256"
            ],
            "failure_report_sha256": evidence["failure_report_sha256"],
            "old_attempt_qualification_reusable": False,
            "old_and_new_trial_counts_mergeable": False,
            "prior_manifest_index_sha256": self.r2_bindings[
                "resolved_manifest_index_sha256"
            ],
            "repaired_manifest_index_sha256": self.r3_bindings[
                "resolved_manifest_index_sha256"
            ],
            "secrets_persisted": False,
        }

    def _validators(self, evidence: dict):
        verification = self._verification()
        tdd = self._tdd()
        admission = self._admission(evidence)
        return (
            verification,
            tdd,
            admission,
            Mock(return_value=(deepcopy(self.r3_bindings), deepcopy(verification))),
            Mock(return_value=deepcopy(tdd)),
            Mock(return_value=deepcopy(admission)),
        )

    def test_exact_r2_failure_revocation_clears_stale_gate_and_preserves_h0_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = self._live_r2_state()
            original = deepcopy(source)
            evidence = self._failure_evidence(root)
            revoked = build_h0_b_harness_revoked_state(
                source,
                root=root,
                stage_attempt_id=self.old_attempt_id,
                checkpoint_index_path=evidence["checkpoint_index_path"],
                checkpoint_index_sha256=evidence["checkpoint_index_sha256"],
                failure_report_path=evidence["failure_report_path"],
                failure_report_sha256=evidence["failure_report_sha256"],
            )

            self.assertEqual(source, original)
            self.assertEqual(
                revoked["status"], "h0_b_harness_compatibility_failure_live_revoked"
            )
            self.assertEqual(
                revoked["current_action_scope"], "h0_b_harness_repair_offline_only"
            )
            self.assertEqual(
                revoked["current_blocker"],
                "h0_b_pre_workload_harness_compatibility_failure",
            )
            self.assertFalse(revoked["live_h0_candidate_authorized"])
            self.assertEqual(revoked["authorized_live_actions"], [])
            self.assertIsNone(revoked["authorized_h0_candidate_id"])
            self.assertNotIn("live_h0_authorization", revoked)
            self.assertEqual(
                revoked["h0_phase_completions"]["H0-A"], self.h0_a_completion
            )
            self.assertEqual(revoked["unrelated"], {"preserved": True})
            invalidation = revoked["h0_b_harness_invalidation"]
            self.assertEqual(
                invalidation["reason"],
                "h0_b_pre_workload_harness_compatibility_failure",
            )
            self.assertEqual(
                invalidation["checkpoint_index_sha256"],
                evidence["checkpoint_index_sha256"],
            )
            self.assertEqual(
                invalidation["failure_report_sha256"],
                evidence["failure_report_sha256"],
            )
            self.assertFalse(invalidation["candidate_rerun_authorized"])
            self.assertFalse(invalidation["candidate_advance_authorized"])
            self.assertFalse(invalidation["live_transition_authorized"])

            state_path = root / "CURRENT_STATE.json"
            state_path.write_bytes(canonical_json_bytes(revoked))
            with self.assertRaises(H0StateGateError):
                authorize_h0_live_entry(
                    state_path=state_path, candidate_id="Q1", phase="H0-B"
                )

    def test_revocation_rejects_nonzero_workload_or_any_evidence_binding_drift(self):
        mutations = (
            ("logical_trial_count", 1),
            ("http_attempt_count", 1),
            ("fresh_graph_count", 1),
            ("embedding_workload_request_count", 1),
            ("model_workload_output_observed", True),
            ("candidate_qualification_interpretable", True),
        )
        for field, value in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                evidence = self._failure_evidence(root)
                report = deepcopy(evidence["report"])
                report[field] = value
                _, report_sha = self._write_json(
                    root, evidence["failure_report_path"], report
                )
                with self.assertRaises(H0HarnessRecoveryError):
                    build_h0_b_harness_revoked_state(
                        self._live_r2_state(),
                        root=root,
                        stage_attempt_id=self.old_attempt_id,
                        checkpoint_index_path=evidence["checkpoint_index_path"],
                        checkpoint_index_sha256=evidence["checkpoint_index_sha256"],
                        failure_report_path=evidence["failure_report_path"],
                        failure_report_sha256=report_sha,
                    )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            evidence = self._failure_evidence(root)
            for field, value in (
                ("checkpoint_index_sha256", "0" * 64),
                ("failure_report_sha256", "0" * 64),
                ("stage_attempt_id", "wrong-attempt"),
            ):
                with self.subTest(binding=field):
                    kwargs = {
                        "stage_attempt_id": self.old_attempt_id,
                        "checkpoint_index_path": evidence["checkpoint_index_path"],
                        "checkpoint_index_sha256": evidence[
                            "checkpoint_index_sha256"
                        ],
                        "failure_report_path": evidence["failure_report_path"],
                        "failure_report_sha256": evidence["failure_report_sha256"],
                    }
                    kwargs[field] = value
                    with self.assertRaises(H0HarnessRecoveryError):
                        build_h0_b_harness_revoked_state(
                            self._live_r2_state(), root=root, **kwargs
                        )

    def test_r3_repair_binding_stays_offline_and_binds_exact_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            revoked, evidence = self._revoked(root)
            verification, tdd, admission, manifest, tdd_validator, repair = (
                self._validators(evidence)
            )
            bound = build_h0_b_harness_repair_bound_state(
                revoked,
                root=root,
                manifest_verification=verification,
                tdd_evidence=tdd,
                repair_decision_path=admission["decision_path"],
                repair_decision_sha256=admission["decision_sha256"],
                manifest_validator=manifest,
                tdd_validator=tdd_validator,
                repair_decision_verifier=repair,
            )

            self.assertEqual(
                bound["status"], "h0_b_harness_repair_verified_not_live_authorized"
            )
            self.assertEqual(
                bound["current_action_scope"], "h0_b_harness_repair_verified_only"
            )
            self.assertFalse(bound["live_h0_candidate_authorized"])
            self.assertEqual(bound["authorized_live_actions"], [])
            self.assertEqual(
                bound["h0_phase_completions"]["H0-A"], self.h0_a_completion
            )
            prerequisites = bound["h0_b_harness_repair_live_prerequisites"]
            self.assertEqual(prerequisites["artifact_bindings"], self.r3_bindings)
            self.assertEqual(prerequisites["manifest_verification"], verification)
            self.assertEqual(prerequisites["tdd_evidence"], tdd)
            self.assertEqual(prerequisites["repair_admission"], admission)
            self.assertFalse(prerequisites["live_transition_performed"])
            self.assertEqual(
                bound["h0_b_harness_invalidation"],
                revoked["h0_b_harness_invalidation"],
            )

    def test_r3_binding_rejects_revision_or_decision_mismatch(self):
        cases = (
            ("artifact_set_id", "v1_3_harness_r2"),
            ("execution_harness_revision", 2),
            ("phase", "H0-A"),
            ("prior_model_workload_output_observed", True),
            ("old_attempt_qualification_reusable", True),
            ("old_and_new_trial_counts_mergeable", True),
        )
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                revoked, evidence = self._revoked(root)
                verification, tdd, admission, manifest, tdd_validator, repair = (
                    self._validators(evidence)
                )
                if field in verification:
                    verification[field] = value
                    manifest.return_value = (
                        deepcopy(self.r3_bindings),
                        deepcopy(verification),
                    )
                else:
                    admission[field] = value
                    repair.return_value = deepcopy(admission)
                with self.assertRaises(H0HarnessRecoveryError):
                    build_h0_b_harness_repair_bound_state(
                        revoked,
                        root=root,
                        manifest_verification=verification,
                        tdd_evidence=tdd,
                        repair_decision_path=admission["decision_path"],
                        repair_decision_sha256=admission["decision_sha256"],
                        manifest_validator=manifest,
                        tdd_validator=tdd_validator,
                        repair_decision_verifier=repair,
                    )

    def test_live_state_authorizes_one_exact_whole_stage_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            revoked, evidence = self._revoked(root)
            verification, tdd, admission, manifest, tdd_validator, repair = (
                self._validators(evidence)
            )
            bound = build_h0_b_harness_repair_bound_state(
                revoked,
                root=root,
                manifest_verification=verification,
                tdd_evidence=tdd,
                repair_decision_path=admission["decision_path"],
                repair_decision_sha256=admission["decision_sha256"],
                manifest_validator=manifest,
                tdd_validator=tdd_validator,
                repair_decision_verifier=repair,
            )
            live = build_h0_b_replacement_live_state(
                bound,
                root=root,
                manifest_validator=manifest,
                tdd_validator=tdd_validator,
                repair_decision_verifier=repair,
            )

            self.assertEqual(live["status"], "h0_q1_b_live_only")
            self.assertEqual(live["current_action_scope"], "h0_q1_b_live_only")
            self.assertTrue(live["live_h0_candidate_authorized"])
            self.assertEqual(live["authorized_live_actions"], ["h0_candidate"])
            authorization = live["live_h0_authorization"]
            self.assertEqual(authorization["candidate_id"], "Q1")
            self.assertEqual(authorization["phase"], "H0-B")
            self.assertEqual(
                authorization["authorized_stage_attempt_id"],
                self.replacement_attempt_id,
            )
            self.assertEqual(authorization["repair_admission"], admission)
            self.assertEqual(
                authorization["prior_phase_completion"]["stage_attempt_id"],
                self.h0_a_completion["stage_attempt_id"],
            )
            self.assertEqual(
                live["h0_phase_completions"]["H0-A"], self.h0_a_completion
            )
            self.assertTrue(
                live["h0_b_harness_repair_live_prerequisites"][
                    "live_transition_performed"
                ]
            )
            with self.assertRaises(H0HarnessRecoveryError):
                build_h0_b_replacement_live_state(
                    live,
                    root=root,
                    manifest_validator=manifest,
                    tdd_validator=tdd_validator,
                    repair_decision_verifier=repair,
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
