"""RED contracts for one H0-B rerun after an infrastructure interruption.

The first H0-B attempt is a preserved pre-workload harness failure.  Its exact
replacement then terminated at readiness with ``infrastructure_interrupted``.
This file freezes the only admissible next action: close the consumed live
grant and authorize one fresh whole-stage attempt bound to a new r4 artifact.

All evidence is synthetic and lives under temporary directories.  Every
service-facing dependency is forbidden, so this suite cannot read credentials
or contact construction, embedding, Neo4j, or SSH while establishing RED.
"""

from __future__ import annotations

import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_harness_recovery as recovery  # noqa: E402
from h0_full_history_live import execute_h0_full_history_live  # noqa: E402
from h0_runtime import (  # noqa: E402
    H0CheckpointStore,
    H0StateGateError,
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_file,
)


FAILED_ATTEMPT_ID = "h0-q1-b-20260809-attempt-001"
INTERRUPTED_ATTEMPT_ID = "h0-q1-b-20260809-replacement-001"
RERUN_ATTEMPT_ID = "h0-q1-b-20260810-replacement-002"


def _harness_repair_admission(failed_checkpoint_sha256: str) -> dict[str, object]:
    """Return the already-consumed r3 repair admission for replacement-001."""

    return {
        "schema_version": "membind.h0.harness-repair-admission.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": "artifacts/h0_protocol_repair/decisions/harness.json",
        "decision_sha256": "1" * 64,
        "decision_result_blind": False,
        "prior_model_workload_output_observed": False,
        "repair_required_independent_of_model_output": True,
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "replacement_attempt_id": INTERRUPTED_ATTEMPT_ID,
        "invalidated_stage_attempt_id": FAILED_ATTEMPT_ID,
        "invalidated_checkpoint_index_sha256": failed_checkpoint_sha256,
        "failure_report_sha256": "2" * 64,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "prior_manifest_index_sha256": "3" * 64,
        "repaired_manifest_index_sha256": "4" * 64,
        "secrets_persisted": False,
    }


def _infrastructure_rerun_admission(
    *,
    interrupted_checkpoint_sha256: str,
    harness_admission: dict[str, object],
) -> dict[str, object]:
    """Bind one fresh attempt to both old attempts and an r4 source bundle."""

    return {
        "schema_version": "membind.h0.infrastructure-rerun-admission.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": "artifacts/h0_protocol_repair/decisions/infra-rerun.json",
        "decision_sha256": "5" * 64,
        "interrupted_stage_attempt_id": INTERRUPTED_ATTEMPT_ID,
        "interrupted_checkpoint_index_sha256": interrupted_checkpoint_sha256,
        "interrupted_stop_reason": "vllm_unreachable",
        "prior_harness_repair_admission_sha256": canonical_json_sha256(
            harness_admission
        ),
        "replacement_attempt_id": RERUN_ATTEMPT_ID,
        "one_shot_whole_stage_replacement": True,
        "resume_interrupted_attempt_allowed": False,
        "prior_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "scientific_configuration_unchanged": True,
        "prior_manifest_index_sha256": "4" * 64,
        "recovered_manifest_index_sha256": "6" * 64,
        "secrets_persisted": False,
    }


def _create_prior_attempts(
    root: Path,
) -> tuple[H0CheckpointStore, H0CheckpointStore, dict[str, object], dict[str, object]]:
    failed = H0CheckpointStore(
        root=root,
        stage_attempt_id=FAILED_ATTEMPT_ID,
        candidate_id="Q1",
        phase="H0-B",
    )
    failed.mark_candidate_failure("manifest_contract_failure", "a" * 64)
    harness = _harness_repair_admission(sha256_file(failed.index_path))
    interrupted = H0CheckpointStore(
        root=root,
        stage_attempt_id=INTERRUPTED_ATTEMPT_ID,
        candidate_id="Q1",
        phase="H0-B",
        repair_admission=harness,
    )
    interrupted.mark_infrastructure_interruption("vllm_unreachable")
    infra = _infrastructure_rerun_admission(
        interrupted_checkpoint_sha256=sha256_file(interrupted.index_path),
        harness_admission=harness,
    )
    return failed, interrupted, harness, infra


def _consumed_live_state(
    *,
    interrupted_checkpoint_path: str,
    interrupted_checkpoint_sha256: str,
    harness_admission: dict[str, object],
) -> dict[str, object]:
    return {
        "protocol_version": "current-validation-v1.3",
        "current_stage": "H0",
        "status": "h0_q1_b_live_only",
        "current_action_scope": "h0_q1_b_live_only",
        "current_blocker": None,
        "stage_progress": {
            "h0_live_gate": "h0_q1_b_live_only",
            "h0_candidate_progression": "h0_b_harness_replacement_authorized_once",
            "preserved": "yes",
        },
        "live_h0_candidate_authorized": True,
        "authorized_live_actions": ["h0_candidate"],
        "authorized_h0_candidate_id": "Q1",
        "next_allowed_action": "run_q1_h0-b_replacement",
        "live_h0_authorization": {
            "candidate_id": "Q1",
            "phase": "H0-B",
            "authorized_stage_attempt_id": INTERRUPTED_ATTEMPT_ID,
            "resolved_manifest_index_path": "artifacts/r3/index.json",
            "resolved_manifest_index_sha256": "4" * 64,
            "resolved_candidate_manifest_path": "artifacts/r3/Q1.json",
            "resolved_candidate_manifest_sha256": "7" * 64,
            "resolved_shared_base_manifest_path": "artifacts/r3/shared.json",
            "resolved_shared_base_manifest_sha256": "8" * 64,
            "prior_phase_completion": {
                "stage_attempt_id": "h0-q1-a-20260809-replacement-001",
                "checkpoint_index_path": "artifacts/h0-a/index.json",
                "checkpoint_index_sha256": "9" * 64,
                "runtime_definition_sha256": "a" * 64,
                "terminal_result_sha256": "b" * 64,
            },
            "repair_admission": deepcopy(harness_admission),
        },
        "h0_phase_completions": {
            "H0-A": {
                "phase": "H0-A",
                "qualified": True,
                "stage_attempt_id": "h0-q1-a-20260809-replacement-001",
            }
        },
        "h0_b_harness_invalidation": {"stage_attempt_id": FAILED_ATTEMPT_ID},
        "h0_b_harness_repair_live_prerequisites": {
            "live_transition_performed": True,
            "repair_admission": deepcopy(harness_admission),
        },
        "unrelated": {"preserved": True},
        "expected_interruption_binding": {
            "checkpoint_index_path": interrupted_checkpoint_path,
            "checkpoint_index_sha256": interrupted_checkpoint_sha256,
        },
    }


def _write_interrupted_fixture(root: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    failed, interrupted, harness, infra = _create_prior_attempts(root / "runs")
    relative = interrupted.index_path.relative_to(root).as_posix()
    state = _consumed_live_state(
        interrupted_checkpoint_path=relative,
        interrupted_checkpoint_sha256=sha256_file(interrupted.index_path),
        harness_admission=harness,
    )
    state_path = root / "CURRENT_STATE.json"
    state_path.write_bytes(canonical_json_bytes(state))
    # Keep references alive for clarity: both are immutable evidence inputs.
    assert failed.index_path.is_file() and interrupted.index_path.is_file()
    return state_path, state, infra


class H0BInfrastructureStateRecoveryRedTests(TestCase):
    def test_consumed_live_grant_closes_only_against_exact_terminal_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state_path, source, _ = _write_interrupted_fixture(root)
            binding = source["expected_interruption_binding"]
            closed = recovery.build_h0_b_infrastructure_interrupted_state(
                source,
                root=root,
                stage_attempt_id=INTERRUPTED_ATTEMPT_ID,
                checkpoint_index_path=binding["checkpoint_index_path"],
                checkpoint_index_sha256=binding["checkpoint_index_sha256"],
            )

            self.assertEqual(state_path.read_bytes(), canonical_json_bytes(source))
            self.assertEqual(
                closed["status"], "h0_b_infrastructure_interrupted_live_revoked"
            )
            self.assertEqual(
                closed["current_action_scope"], "h0_b_infrastructure_recovery_offline_only"
            )
            self.assertEqual(closed["current_blocker"], "vllm_unreachable")
            self.assertFalse(closed["live_h0_candidate_authorized"])
            self.assertEqual(closed["authorized_live_actions"], [])
            self.assertIsNone(closed["authorized_h0_candidate_id"])
            self.assertNotIn("live_h0_authorization", closed)
            self.assertEqual(closed["h0_phase_completions"], source["h0_phase_completions"])
            self.assertEqual(
                closed["h0_b_harness_invalidation"], source["h0_b_harness_invalidation"]
            )
            self.assertEqual(closed["unrelated"], {"preserved": True})
            interruption = closed["h0_b_infrastructure_interruption"]
            self.assertEqual(interruption["stage_attempt_id"], INTERRUPTED_ATTEMPT_ID)
            self.assertEqual(interruption["checkpoint_index_sha256"], binding["checkpoint_index_sha256"])
            self.assertEqual(interruption["stop_reason"], "vllm_unreachable")
            self.assertFalse(interruption["resume_authorized"])
            self.assertFalse(interruption["rerun_authorized"])
            self.assertFalse(interruption["old_and_new_trial_counts_mergeable"])

    def test_closure_rejects_wrong_hash_nonterminal_resume_and_configuration_drift(self):
        mutations = (
            ("checkpoint_hash", "0" * 64),
            ("checkpoint_status", "running"),
            ("attempt_id", RERUN_ATTEMPT_ID),
            ("authorized_attempt", RERUN_ATTEMPT_ID),
            ("manifest_hash", "0" * 64),
        )
        for label, value in mutations:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                _, source, _ = _write_interrupted_fixture(root)
                binding = source["expected_interruption_binding"]
                checkpoint = root / binding["checkpoint_index_path"]
                stage_attempt_id = INTERRUPTED_ATTEMPT_ID
                checkpoint_sha = binding["checkpoint_index_sha256"]
                if label == "checkpoint_hash":
                    checkpoint_sha = value
                elif label == "checkpoint_status":
                    changed = deepcopy(__import__("json").loads(checkpoint.read_text()))
                    changed["status"] = value
                    checkpoint.write_bytes(canonical_json_bytes(changed))
                    checkpoint_sha = sha256_file(checkpoint)
                elif label == "attempt_id":
                    stage_attempt_id = value
                elif label == "authorized_attempt":
                    source["live_h0_authorization"]["authorized_stage_attempt_id"] = value
                else:
                    source["live_h0_authorization"]["resolved_manifest_index_sha256"] = value

                with self.assertRaises(recovery.H0HarnessRecoveryError):
                    recovery.build_h0_b_infrastructure_interrupted_state(
                        source,
                        root=root,
                        stage_attempt_id=stage_attempt_id,
                        checkpoint_index_path=binding["checkpoint_index_path"],
                        checkpoint_index_sha256=checkpoint_sha,
                    )

    def test_closure_transition_is_zero_write_dry_run_then_one_atomic_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state_path, source, _ = _write_interrupted_fixture(root)
            before = state_path.read_bytes()
            binding = source["expected_interruption_binding"]
            kwargs = {
                "root": root,
                "stage_attempt_id": INTERRUPTED_ATTEMPT_ID,
                "checkpoint_index_path": binding["checkpoint_index_path"],
                "checkpoint_index_sha256": binding["checkpoint_index_sha256"],
            }

            preview = recovery.transition_h0_b_infrastructure_interrupted(
                state_path, **kwargs
            )
            self.assertEqual(state_path.read_bytes(), before)
            self.assertFalse(preview["live_h0_candidate_authorized"])

            from h0_state_transition import _atomic_write as real_atomic_write

            with patch(
                "h0_harness_recovery._atomic_write", wraps=real_atomic_write
            ) as atomic:
                committed = recovery.transition_h0_b_infrastructure_interrupted(
                    state_path, **kwargs, dry_run=False
                )
                atomic.assert_called_once()
            self.assertEqual(committed, preview)
            self.assertEqual(state_path.read_bytes(), canonical_json_bytes(committed))

            with self.assertRaises(recovery.H0HarnessRecoveryError):
                recovery.transition_h0_b_infrastructure_interrupted(
                    state_path, **kwargs, dry_run=False
                )

    def test_commit_rechecks_source_and_preserves_concurrent_operator_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state_path, source, _ = _write_interrupted_fixture(root)
            binding = source["expected_interruption_binding"]
            concurrent = {**source, "operator_update": "preserve"}
            concurrent_bytes = canonical_json_bytes(concurrent)

            def build_then_change(*args, **kwargs):
                state_path.write_bytes(concurrent_bytes)
                return recovery.build_h0_b_infrastructure_interrupted_state(
                    *args, **kwargs
                )

            with (
                patch("h0_harness_recovery._atomic_write") as atomic,
                self.assertRaisesRegex(
                    recovery.H0HarnessRecoveryError, "state_changed"
                ),
            ):
                recovery.transition_h0_b_infrastructure_interrupted(
                    state_path,
                    root=root,
                    stage_attempt_id=INTERRUPTED_ATTEMPT_ID,
                    checkpoint_index_path=binding["checkpoint_index_path"],
                    checkpoint_index_sha256=binding["checkpoint_index_sha256"],
                    state_builder=build_then_change,
                    dry_run=False,
                )
            atomic.assert_not_called()
            self.assertEqual(state_path.read_bytes(), concurrent_bytes)


class H0BInfrastructureCheckpointAdmissionRedTests(TestCase):
    def test_exact_replacement_002_preserves_old_indices_and_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            failed, interrupted, harness, infra = _create_prior_attempts(root)
            failed_before = failed.index_path.read_bytes()
            interrupted_before = interrupted.index_path.read_bytes()

            rerun = H0CheckpointStore(
                root=root,
                stage_attempt_id=RERUN_ATTEMPT_ID,
                candidate_id="Q1",
                phase="H0-B",
                repair_admission=harness,
                infrastructure_rerun_admission=infra,
            )

            self.assertEqual(failed.index_path.read_bytes(), failed_before)
            self.assertEqual(interrupted.index_path.read_bytes(), interrupted_before)
            self.assertEqual(rerun.index["prior_matching_attempt_count"], 2)
            self.assertEqual(rerun.index["infrastructure_interrupted_attempt_count"], 1)
            self.assertTrue(rerun.index["whole_stage_rerun"])
            self.assertTrue(rerun.index["harness_repair_replacement"])
            self.assertTrue(rerun.index["infrastructure_rerun_replacement"])
            self.assertEqual(rerun.index["repair_admission"], harness)
            self.assertEqual(rerun.index["infrastructure_rerun_admission"], infra)
            self.assertEqual(rerun.index["segments"], [])
            self.assertFalse(rerun.index["partial_qualification_reusable"])

    def test_checkpoint_rejects_reuse_duplicate_resume_and_any_binding_drift(self):
        mutations = (
            {"replacement_attempt_id": "h0-q1-b-20260810-replacement-003"},
            {"interrupted_stage_attempt_id": FAILED_ATTEMPT_ID},
            {"interrupted_checkpoint_index_sha256": "0" * 64},
            {"interrupted_stop_reason": "embedding_unreachable"},
            {"prior_harness_repair_admission_sha256": "0" * 64},
            {"one_shot_whole_stage_replacement": False},
            {"resume_interrupted_attempt_allowed": True},
            {"prior_attempt_qualification_reusable": True},
            {"old_and_new_trial_counts_mergeable": True},
            {"scientific_configuration_unchanged": False},
            {"prior_manifest_index_sha256": "0" * 64},
            {"recovered_manifest_index_sha256": "4" * 64},
            {"unexpected_field": "forbidden"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                _, _, harness, infra = _create_prior_attempts(root)
                changed = {**infra, **mutation}
                with self.assertRaises(H0StateGateError):
                    H0CheckpointStore(
                        root=root,
                        stage_attempt_id=RERUN_ATTEMPT_ID,
                        candidate_id="Q1",
                        phase="H0-B",
                        repair_admission=harness,
                        infrastructure_rerun_admission=changed,
                    )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _, _, harness, infra = _create_prior_attempts(root)
            H0CheckpointStore(
                root=root,
                stage_attempt_id=RERUN_ATTEMPT_ID,
                candidate_id="Q1",
                phase="H0-B",
                repair_admission=harness,
                infrastructure_rerun_admission=infra,
            )
            with self.assertRaises((FileExistsError, H0StateGateError)):
                H0CheckpointStore(
                    root=root,
                    stage_attempt_id=RERUN_ATTEMPT_ID,
                    candidate_id="Q1",
                    phase="H0-B",
                    repair_admission=harness,
                    infrastructure_rerun_admission=infra,
                )
            with self.assertRaises(H0StateGateError):
                H0CheckpointStore(
                    root=root,
                    stage_attempt_id="h0-q1-b-20260810-replacement-003",
                    candidate_id="Q1",
                    phase="H0-B",
                    repair_admission=harness,
                    infrastructure_rerun_admission=infra,
                )


class H0BInfrastructureRunnerAdmissionRedTests(IsolatedAsyncioTestCase):
    def _authorization(
        self, harness: dict[str, object], infra: dict[str, object]
    ) -> dict[str, object]:
        return {
            "candidate_id": "Q1",
            "phase": "H0-B",
            "authorized_stage_attempt_id": RERUN_ATTEMPT_ID,
            "resolved_manifest_index_sha256": infra["recovered_manifest_index_sha256"],
            "resolved_candidate_manifest_sha256": "7" * 64,
            "resolved_shared_base_manifest_sha256": "8" * 64,
            "prior_phase_completion": {
                "stage_attempt_id": "h0-q1-a-20260809-replacement-001",
                "checkpoint_index_path": "prior/index.json",
                "checkpoint_index_sha256": "9" * 64,
                "runtime_definition_sha256": "a" * 64,
            },
            "repair_admission": deepcopy(harness),
            "infrastructure_rerun_admission": deepcopy(infra),
        }

    async def test_runner_forwards_both_exact_admissions_before_any_service_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _, _, harness, infra = _create_prior_attempts(root / "prior-runs")
            authorization = self._authorization(harness, infra)
            captured: dict[str, object] = {}

            class StopAtCheckpoint(RuntimeError):
                pass

            def checkpoint_store(**kwargs):
                captured.update(kwargs)
                raise StopAtCheckpoint("checkpoint boundary")

            forbidden = Mock(side_effect=AssertionError("service action is forbidden"))
            definition = SimpleNamespace(
                identity={"candidate_id": "Q1", "phase": "H0-B"},
                candidate=SimpleNamespace(candidate_id="Q1"),
                embedding_namespace={
                    "served_model_id": "qwen3-embedding-0.6b",
                    "dimension": 1024,
                    "normalization": "l2",
                },
                semantic_guardrail={"schema_version": "offline-guardrail"},
                definition_sha256="c" * 64,
            )
            with self.assertRaises(StopAtCheckpoint):
                await execute_h0_full_history_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=root / "new-runs",
                    stage_attempt_id=RERUN_ATTEMPT_ID,
                    candidate_id="Q1",
                    phase="H0-B",
                    authorization_checker=Mock(return_value=authorization),
                    runtime_definition_loader=Mock(return_value=definition),
                    prior_completion_validator=Mock(return_value={"qualified": True}),
                    checkpoint_store_factory=checkpoint_store,
                    credential_loader=forbidden,
                    readiness_runner=forbidden,
                    corpus_loader=forbidden,
                    history_factory_builder=forbidden,
                    full_history_runner=forbidden,
                    phase_runner=forbidden,
                    progress_sink=forbidden,
                )

            self.assertEqual(captured["stage_attempt_id"], RERUN_ATTEMPT_ID)
            self.assertEqual(captured["repair_admission"], harness)
            self.assertEqual(captured["infrastructure_rerun_admission"], infra)
            forbidden.assert_not_called()

    async def test_runner_rejects_resume_wrong_attempt_and_config_drift_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _, _, harness, infra = _create_prior_attempts(root / "prior-runs")
            cases = (
                ("resume", INTERRUPTED_ATTEMPT_ID, self._authorization(harness, infra)),
                ("wrong-id", "h0-q1-b-20260810-replacement-003", self._authorization(harness, infra)),
                (
                    "manifest-drift",
                    RERUN_ATTEMPT_ID,
                    {
                        **self._authorization(harness, infra),
                        "resolved_manifest_index_sha256": "0" * 64,
                    },
                ),
            )
            for label, attempt_id, authorization in cases:
                with self.subTest(case=label):
                    runtime = Mock(side_effect=AssertionError("runtime touched"))
                    checkpoint = Mock(side_effect=AssertionError("checkpoint touched"))
                    with self.assertRaises(H0StateGateError):
                        await execute_h0_full_history_live(
                            root=root,
                            state_path=root / "state.json",
                            artifacts_root=root / "new-runs",
                            stage_attempt_id=attempt_id,
                            candidate_id="Q1",
                            phase="H0-B",
                            authorization_checker=Mock(return_value=authorization),
                            runtime_definition_loader=runtime,
                            prior_completion_validator=Mock(
                                side_effect=AssertionError("prior touched")
                            ),
                            checkpoint_store_factory=checkpoint,
                            credential_loader=Mock(side_effect=AssertionError("env touched")),
                            readiness_runner=Mock(side_effect=AssertionError("readiness touched")),
                            corpus_loader=Mock(side_effect=AssertionError("corpus touched")),
                            history_factory_builder=Mock(side_effect=AssertionError("graph touched")),
                            full_history_runner=Mock(side_effect=AssertionError("workload touched")),
                            phase_runner=Mock(side_effect=AssertionError("phase touched")),
                        )
                    runtime.assert_not_called()
                    checkpoint.assert_not_called()


if __name__ == "__main__":
    import unittest

    unittest.main()
