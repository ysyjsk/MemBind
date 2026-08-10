"""Offline state-machine contracts for repaired Q1 H0-A/B/C progression."""

from __future__ import annotations

import sys
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_phase_state import (  # noqa: E402
    H0PhaseStateError,
    build_h0_repair_bound_state,
    build_h0_replacement_live_state,
    build_h0_successor_phase_live_state,
    transition_h0_replacement_live,
)
from h0_runtime import (  # noqa: E402
    H0StateGateError,
    authorize_h0_live_entry,
    canonical_json_bytes,
)


class H0PhaseStateTests(TestCase):
    bindings = {
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
    verification = {
        "schema_version": "membind.h0.offline-artifact-verification.v2",
        "protocol_version": "current-validation-v1.3",
        "artifact_set_id": "v1_3_harness_r2",
        "execution_harness_revision": 2,
        "status": "verified_offline_not_live_authorized",
        "index_path": bindings["resolved_manifest_index_path"],
        "index_sha256": bindings["resolved_manifest_index_sha256"],
        "generated_json_file_count": 10,
        "binding_count": 9,
        "resolved_wrapper_count": 4,
        "source_spec_count": 4,
        "secret_scan_passed": True,
        "live_eligible": False,
    }
    tdd = {
        name: {"path": f"artifacts/tdd/{name}.log", "sha256": "4" * 64, "test_count": 1}
        for name in ("latest_red", "latest_green", "latest_focused", "latest_full_regression")
    }
    admission = {
        "schema_version": "membind.h0.repair-admission.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-A",
        "decision_path": "artifacts/h0_protocol_repair/decisions/decision.json",
        "decision_sha256": "5" * 64,
        "decision_result_blind": False,
        "one_shot_replacement": True,
        "replacement_attempt_id": "h0-q1-a-20260809-replacement-001",
        "invalidated_stage_attempt_id": "h0-q1-a-20260809-attempt-001",
        "invalidated_checkpoint_index_sha256": "6" * 64,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "candidate_spec_projection_sha256": "7" * 64,
        "repaired_manifest_index_sha256": "1" * 64,
        "secrets_persisted": False,
    }

    def _revoked(self) -> dict:
        return {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "H0",
            "status": "h0_live_authorization_revoked",
            "current_action_scope": "h0_live_forbidden",
            "current_blocker": "h0_protocol_gate_order_violation",
            "stage_progress": {
                "h0_live_gate": "forbidden",
                "h0_candidate_progression": "blocked_protocol_gate_order_violation",
                "preserved": "yes",
            },
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "h0_live_authorization_invalidation": {
                "schema_version": "membind.h0.live-authorization-invalidation.v1",
                "protocol_version": "current-validation-v1.3",
                "status": "invalidated_no_rerun_or_advance_authorized",
                "reason": "protocol_gate_order_violation",
                "candidate_id": "Q1",
                "phase": "H0-A",
                "stage_attempt_id": "h0-q1-a-20260809-attempt-001",
                "checkpoint_index_path": (
                    "artifacts/h0_runs/h0/checkpoints/"
                    "h0-q1-a-20260809-attempt-001/index.json"
                ),
                "checkpoint_index_sha256": "6" * 64,
                "candidate_rerun_authorized": False,
                "candidate_advance_authorized": False,
                "live_transition_authorized": False,
            },
            "unrelated": {"preserved": True},
        }

    def _validators(self):
        manifest = Mock(return_value=(deepcopy(self.bindings), deepcopy(self.verification)))
        tdd = Mock(return_value=deepcopy(self.tdd))
        repair = Mock(return_value=deepcopy(self.admission))
        return manifest, tdd, repair

    def _repair_bound(self) -> dict:
        manifest, tdd, repair = self._validators()
        return build_h0_repair_bound_state(
            self._revoked(),
            root=ROOT,
            manifest_verification=self.verification,
            tdd_evidence=self.tdd,
            repair_decision_path=self.admission["decision_path"],
            repair_decision_sha256=self.admission["decision_sha256"],
            manifest_validator=manifest,
            tdd_validator=tdd,
            repair_decision_verifier=repair,
        )

    def test_revoked_to_repair_bound_remains_live_forbidden_and_preserves_invalidation(self):
        source = self._revoked()
        original = deepcopy(source)
        bound = self._repair_bound()
        self.assertEqual(source, original)
        self.assertEqual(bound["status"], "h0_protocol_repair_verified_not_live_authorized")
        self.assertEqual(bound["current_action_scope"], "h0_protocol_repair_verified_only")
        self.assertFalse(bound["live_h0_candidate_authorized"])
        self.assertEqual(bound["authorized_live_actions"], [])
        self.assertEqual(
            bound["h0_live_authorization_invalidation"],
            source["h0_live_authorization_invalidation"],
        )
        prerequisites = bound["h0_repair_live_prerequisites"]
        self.assertEqual(prerequisites["repair_admission"], self.admission)
        self.assertFalse(prerequisites["live_transition_performed"])

    def test_repair_bound_to_exact_replacement_consumes_one_shot_admission(self):
        manifest, tdd, repair = self._validators()
        live = build_h0_replacement_live_state(
            self._repair_bound(),
            root=ROOT,
            manifest_validator=manifest,
            tdd_validator=tdd,
            repair_decision_verifier=repair,
        )
        self.assertEqual(live["status"], "h0_q1_a_live_only")
        self.assertTrue(live["live_h0_candidate_authorized"])
        self.assertEqual(live["live_h0_authorization"]["repair_admission"], self.admission)
        self.assertEqual(
            live["live_h0_authorization"]["authorized_stage_attempt_id"],
            self.admission["replacement_attempt_id"],
        )
        self.assertTrue(
            live["h0_repair_live_prerequisites"]["live_transition_performed"]
        )
        with self.assertRaises(H0PhaseStateError):
            build_h0_replacement_live_state(
                live,
                root=ROOT,
                manifest_validator=manifest,
                tdd_validator=tdd,
                repair_decision_verifier=repair,
            )

    def test_a_terminal_authorizes_only_b_and_b_terminal_authorizes_only_c(self):
        manifest, tdd, repair = self._validators()
        live_a = build_h0_replacement_live_state(
            self._repair_bound(),
            root=ROOT,
            manifest_validator=manifest,
            tdd_validator=tdd,
            repair_decision_verifier=repair,
        )
        a_completion = {
            "schema_version": "membind.h0.prior-phase-terminal-completion.v1",
            "qualified": True,
            "candidate_id": "Q1",
            "phase": "H0-A",
            "stage_attempt_id": self.admission["replacement_attempt_id"],
            "checkpoint_index_path": "artifacts/h0_runs/h0/checkpoints/a/index.json",
            "checkpoint_index_sha256": "8" * 64,
            "terminal_result_sha256": "9" * 64,
            "runtime_definition_sha256": "a" * 64,
            "secrets_persisted": False,
        }
        validate_a = Mock(return_value=deepcopy(a_completion))
        live_b = build_h0_successor_phase_live_state(
            live_a,
            root=ROOT,
            completed_phase="H0-A",
            stage_attempt_id=a_completion["stage_attempt_id"],
            checkpoint_index_path=a_completion["checkpoint_index_path"],
            checkpoint_index_sha256=a_completion["checkpoint_index_sha256"],
            runtime_definition_sha256=a_completion["runtime_definition_sha256"],
            completion_validator=validate_a,
        )
        self.assertEqual(live_b["status"], "h0_q1_b_live_only")
        self.assertEqual(live_b["live_h0_authorization"]["phase"], "H0-B")
        self.assertEqual(
            live_b["live_h0_authorization"]["prior_phase_completion"]["stage_attempt_id"],
            a_completion["stage_attempt_id"],
        )

        b_completion = {
            "schema_version": "membind.h0.full-history-terminal-completion.v1",
            "qualified": True,
            "candidate_id": "Q1",
            "phase": "H0-B",
            "stage_attempt_id": "h0-q1-b-qualified-001",
            "checkpoint_index_path": "artifacts/h0_runs/h0/checkpoints/b/index.json",
            "checkpoint_index_sha256": "b" * 64,
            "terminal_result_sha256": "c" * 64,
            "runtime_definition_sha256": "d" * 64,
            "secrets_persisted": False,
        }
        # H0-B is entered through its separate explicit attempt transition.
        live_b["live_h0_authorization"]["authorized_stage_attempt_id"] = (
            b_completion["stage_attempt_id"]
        )
        live_c = build_h0_successor_phase_live_state(
            live_b,
            root=ROOT,
            completed_phase="H0-B",
            stage_attempt_id=b_completion["stage_attempt_id"],
            checkpoint_index_path=b_completion["checkpoint_index_path"],
            checkpoint_index_sha256=b_completion["checkpoint_index_sha256"],
            runtime_definition_sha256=b_completion["runtime_definition_sha256"],
            completion_validator=Mock(return_value=deepcopy(b_completion)),
        )
        self.assertEqual(live_c["status"], "h0_q1_c_live_only")
        self.assertEqual(live_c["live_h0_authorization"]["phase"], "H0-C")
        self.assertEqual(
            live_c["h0_phase_completions"]["H0-A"], a_completion
        )
        self.assertEqual(
            live_c["h0_phase_completions"]["H0-B"], b_completion
        )

    def test_wrong_replacement_or_unqualified_terminal_fails_closed(self):
        manifest, tdd, repair = self._validators()
        live_a = build_h0_replacement_live_state(
            self._repair_bound(),
            root=ROOT,
            manifest_validator=manifest,
            tdd_validator=tdd,
            repair_decision_verifier=repair,
        )
        with self.assertRaises(H0PhaseStateError):
            build_h0_successor_phase_live_state(
                live_a,
                root=ROOT,
                completed_phase="H0-A",
                stage_attempt_id="wrong-attempt",
                checkpoint_index_path="artifacts/h0_runs/h0/checkpoints/x/index.json",
                checkpoint_index_sha256="8" * 64,
                runtime_definition_sha256="a" * 64,
                completion_validator=Mock(
                    return_value={"qualified": True, "phase": "H0-A"}
                ),
            )
        self.assertEqual(live_a["status"], "h0_q1_a_live_only")

    def test_generated_live_states_are_accepted_only_for_their_exact_phase(self):
        manifest, tdd, repair = self._validators()
        live = build_h0_replacement_live_state(
            self._repair_bound(),
            root=ROOT,
            manifest_validator=manifest,
            tdd_validator=tdd,
            repair_decision_verifier=repair,
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(json.dumps(live), encoding="ascii")
            observed = authorize_h0_live_entry(
                state_path=state_path,
                candidate_id="Q1",
                phase="H0-A",
            )
            self.assertEqual(
                observed["authorized_stage_attempt_id"],
                self.admission["replacement_attempt_id"],
            )
            with self.assertRaises(H0StateGateError):
                authorize_h0_live_entry(
                    state_path=state_path,
                    candidate_id="Q1",
                    phase="H0-B",
                )

    def test_persisted_transition_has_side_effect_free_dry_run_and_one_atomic_commit(self):
        source = self._repair_bound()
        target = source | {"status": "h0_q1_a_live_only", "marker": "derived"}
        builder = Mock(return_value=deepcopy(target))
        preview = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = root / "state.json"
            path.write_bytes(canonical_json_bytes(source))
            before = path.read_bytes()
            dry = transition_h0_replacement_live(
                path,
                root=root,
                dry_run=True,
                state_builder=builder,
                preview_validator=preview,
            )
            self.assertEqual(dry, target)
            self.assertEqual(path.read_bytes(), before)
            preview.assert_called_once()

            committed = transition_h0_replacement_live(
                path,
                root=root,
                dry_run=False,
                state_builder=builder,
                preview_validator=preview,
            )
            self.assertEqual(committed, target)
            self.assertEqual(path.read_bytes(), canonical_json_bytes(target))
            self.assertEqual(preview.call_count, 2)


if __name__ == "__main__":
    import unittest

    unittest.main()
