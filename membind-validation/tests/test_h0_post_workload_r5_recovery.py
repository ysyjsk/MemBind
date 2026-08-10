"""RED contracts for the H0-B post-workload R5 recovery.

Replacement-002 reached the candidate workload, persisted one source checkpoint,
and then stopped on a local Graphiti/embedder interface mismatch.  Its model
outputs therefore remain disclosed diagnostic evidence, but none of its trials,
graphs, histories, or source checkpoints may qualify or be merged into the exact
replacement-003 whole-stage run.

These tests are offline-only.  They read immutable sanitized local artifacts,
write only temporary fixtures, and never load credentials or contact services.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_artifacts  # noqa: E402
import h0_control  # noqa: E402
import h0_full_history_live  # noqa: E402
import h0_harness_recovery as recovery  # noqa: E402
import h0_live_preflight  # noqa: E402
import h0_repair_admission as admission  # noqa: E402
from h0_runtime import (  # noqa: E402
    H0CheckpointStore,
    H0StateGateError,
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_file,
)


FAILED_ATTEMPT_ID = "h0-q1-b-20260809-attempt-001"
INTERRUPTED_ATTEMPT_ID = "h0-q1-b-20260809-replacement-001"
POST_WORKLOAD_ATTEMPT_ID = "h0-q1-b-20260810-replacement-002"
REPLACEMENT_ATTEMPT_ID = "h0-q1-b-20260810-replacement-003"
R4_INDEX_REL = (
    "artifacts/h0_manifest_sets/v1_3_harness_r4/"
    "resolved_manifest_index_v1_3_harness_r4.json"
)
R4_INDEX_SHA256 = (
    "a08b3f704c9680476990f24edc239d4af50ced39edcf9aae0d529b5ed14332d7"
)
CHECKPOINT_REL = (
    "artifacts/h0_runs/h0/checkpoints/"
    f"{POST_WORKLOAD_ATTEMPT_ID}/index.json"
)
CHECKPOINT_SHA256 = (
    "e2187d3e101459e9c9a873d8dffb3fbcc858d139833f7f392eedff1c2c78c665"
)
FAILURE_SEGMENT_REL = (
    "artifacts/h0_runs/h0/checkpoints/"
    f"{POST_WORKLOAD_ATTEMPT_ID}/"
    "000014.candidate_failure.manifest_contract_failure."
    "689285595818aac01f008cb279d3a71cdb084abe35dd79e04e23e93d9d3eadd5.json"
)
FAILURE_SEGMENT_SHA256 = (
    "689285595818aac01f008cb279d3a71cdb084abe35dd79e04e23e93d9d3eadd5"
)
SOURCE_CHECKPOINT_REL = (
    "artifacts/h0_runs/h0/checkpoints/"
    f"{POST_WORKLOAD_ATTEMPT_ID}/"
    "000013.source_sequence.07741c45-000."
    "1cdb5b70c86790d144179e855143018d2a97cd32d9e9fc70d5c1e218cd88211c.json"
)
SOURCE_CHECKPOINT_SHA256 = (
    "1cdb5b70c86790d144179e855143018d2a97cd32d9e9fc70d5c1e218cd88211c"
)
LIVE_LOG_REL = "artifacts/live_logs/h0_q1_b_20260810_replacement_002.log"
LIVE_LOG_SHA256 = (
    "3e6819b01be43045739cdc4c2d5cd95bf8e7b85bd001300dfa92eb1d36dc4deb"
)
OFFLINE_PROBE_REL = (
    "artifacts/diagnostics/"
    "h0_q1_b_replacement_002_embedding_contract_offline_probe_20260810_002.log"
)
OFFLINE_PROBE_SHA256 = (
    "06b255f8450852c31afce839d13bedad97f32857c86ac204e86fc6857cb06a3e"
)
R5_INDEX_REL = (
    "artifacts/h0_manifest_sets/v1_3_harness_r5/"
    "resolved_manifest_index_v1_3_harness_r5.json"
)
DECISION_REL = (
    "artifacts/h0_protocol_repair/decisions/"
    "q1_h0_b_post_workload_harness_repair.json"
)


def _required_callable(module: object, name: str):
    value = getattr(module, name, None)
    if not callable(value):
        raise AssertionError(f"required R5 recovery API is missing: {name}")
    return value


def _classification_kwargs(root: Path = ROOT) -> dict[str, object]:
    return {
        "root": root,
        "stage_attempt_id": POST_WORKLOAD_ATTEMPT_ID,
        "checkpoint_index_path": CHECKPOINT_REL,
        "checkpoint_index_sha256": CHECKPOINT_SHA256,
        "failure_segment_path": FAILURE_SEGMENT_REL,
        "failure_segment_sha256": FAILURE_SEGMENT_SHA256,
        "source_checkpoint_path": SOURCE_CHECKPOINT_REL,
        "source_checkpoint_sha256": SOURCE_CHECKPOINT_SHA256,
        "live_log_path": LIVE_LOG_REL,
        "live_log_sha256": LIVE_LOG_SHA256,
        "offline_probe_path": OFFLINE_PROBE_REL,
        "offline_probe_sha256": OFFLINE_PROBE_SHA256,
    }


def _expected_classification() -> dict[str, object]:
    return {
        "schema_version": "membind.h0.post-workload-harness-failure.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "stage_attempt_id": POST_WORKLOAD_ATTEMPT_ID,
        "status": "candidate_failed",
        "failure_code": "manifest_contract_failure",
        "failure_stage": "history_workload",
        "failure_origin": "local_execution_harness_interface_contract",
        "workload_reached": True,
        "prior_model_workload_output_observed": True,
        "candidate_model_failure_supported": False,
        "infrastructure_failure_supported": False,
        "repair_required_independent_of_model_response_content": True,
        "logical_trial_count": 6,
        "http_attempt_count": 6,
        "http_200_count": 6,
        "json_parse_success_count": 6,
        "pydantic_validation_success_count": 6,
        "semantic_utility_success_count": 6,
        "retry_count": 0,
        "embedding_workload_request_count": 4,
        "source_checkpoint_count": 1,
        "fresh_graph_count": 1,
        "closed_graph_count": 1,
        "cleanup_failure_count": 0,
        "cross_encoder_rank_call_count": 0,
        "partial_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "resume_failed_attempt_allowed": False,
        "requires_whole_stage_replacement": True,
        "checkpoint_index_path": CHECKPOINT_REL,
        "checkpoint_index_sha256": CHECKPOINT_SHA256,
        "failure_segment_path": FAILURE_SEGMENT_REL,
        "failure_segment_sha256": FAILURE_SEGMENT_SHA256,
        "source_checkpoint_path": SOURCE_CHECKPOINT_REL,
        "source_checkpoint_sha256": SOURCE_CHECKPOINT_SHA256,
        "live_log_path": LIVE_LOG_REL,
        "live_log_sha256": LIVE_LOG_SHA256,
        "offline_probe_path": OFFLINE_PROBE_REL,
        "offline_probe_sha256": OFFLINE_PROBE_SHA256,
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }


def _post_workload_admission(
    checkpoint: dict[str, object], *, repaired_index_sha256: str = "f" * 64
) -> dict[str, object]:
    repair = checkpoint["repair_admission"]
    infrastructure = checkpoint["infrastructure_rerun_admission"]
    assert isinstance(repair, dict) and isinstance(infrastructure, dict)
    return {
        "schema_version": "membind.h0.post-workload-harness-repair-admission.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": DECISION_REL,
        "decision_sha256": "d" * 64,
        "decision_result_blind": False,
        "prior_model_workload_output_observed": True,
        "repair_required_independent_of_model_response_content": True,
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "replacement_attempt_id": REPLACEMENT_ATTEMPT_ID,
        "invalidated_stage_attempt_id": POST_WORKLOAD_ATTEMPT_ID,
        "invalidated_checkpoint_index_sha256": CHECKPOINT_SHA256,
        "failure_segment_sha256": FAILURE_SEGMENT_SHA256,
        "source_checkpoint_sha256": SOURCE_CHECKPOINT_SHA256,
        "live_log_sha256": LIVE_LOG_SHA256,
        "offline_probe_sha256": OFFLINE_PROBE_SHA256,
        "prior_harness_repair_admission_sha256": canonical_json_sha256(repair),
        "prior_infrastructure_rerun_admission_sha256": canonical_json_sha256(
            infrastructure
        ),
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "resume_failed_attempt_allowed": False,
        "prior_manifest_index_sha256": R4_INDEX_SHA256,
        "repaired_manifest_index_sha256": repaired_index_sha256,
        "secrets_persisted": False,
    }


def _r5_verification() -> dict[str, object]:
    return {
        "schema_version": "membind.h0.offline-artifact-verification.v3",
        "protocol_version": "current-validation-v1.3",
        "artifact_set_id": "v1_3_harness_r5",
        "execution_harness_revision": 5,
        "status": "verified_offline_not_live_authorized",
        "index_path": R5_INDEX_REL,
        "index_sha256": "f" * 64,
        "generated_json_file_count": 11,
        "binding_count": 10,
        "resolved_wrapper_count": 4,
        "source_spec_count": 4,
        "execution_source_count": 32,
        "secret_scan_passed": True,
        "live_eligible": False,
    }


def _r5_bindings() -> dict[str, str]:
    prefix = "artifacts/h0_manifest_sets/v1_3_harness_r5"
    return {
        "resolved_manifest_index_path": R5_INDEX_REL,
        "resolved_manifest_index_sha256": "f" * 64,
        "resolved_candidate_manifest_path": f"{prefix}/resolved_candidates/Q1.{('1' * 64)}.json",
        "resolved_candidate_manifest_sha256": "1" * 64,
        "resolved_shared_base_manifest_path": f"{prefix}/resolved_candidates/shared_base.{('2' * 64)}.json",
        "resolved_shared_base_manifest_sha256": "2" * 64,
    }


def _tdd_evidence() -> dict[str, dict[str, object]]:
    return {
        name: {
            "path": f"artifacts/tdd/post-workload-{name}.log",
            "sha256": str(index + 3) * 64,
            "test_count": index + 1,
        }
        for index, name in enumerate(
            ("latest_red", "latest_green", "latest_focused", "latest_full_regression")
        )
    }


def _consumed_live_state(checkpoint: dict[str, object]) -> dict[str, object]:
    repair = deepcopy(checkpoint["repair_admission"])
    infrastructure = deepcopy(checkpoint["infrastructure_rerun_admission"])
    return {
        "protocol_version": "current-validation-v1.3",
        "current_stage": "H0",
        "status": "h0_q1_b_live_only",
        "current_action_scope": "h0_q1_b_live_only",
        "current_blocker": None,
        "stage_progress": {
            "h0_live_gate": "h0_q1_b_live_only",
            "h0_candidate_progression": "h0_b_infrastructure_rerun_authorized_once",
            "h0_offline_manifest_binding": "v1_3_harness_r4_verified",
            "preserved": "yes",
        },
        "live_h0_candidate_authorized": True,
        "authorized_live_actions": ["h0_candidate"],
        "authorized_h0_candidate_id": "Q1",
        "next_allowed_action": "run_q1_h0-b-infrastructure-rerun",
        "live_h0_authorization": {
            "candidate_id": "Q1",
            "phase": "H0-B",
            "authorized_stage_attempt_id": POST_WORKLOAD_ATTEMPT_ID,
            "resolved_manifest_index_path": R4_INDEX_REL,
            "resolved_manifest_index_sha256": R4_INDEX_SHA256,
            "resolved_candidate_manifest_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r4/resolved_candidates/"
                "Q1.0224692fb291fe3d86548f1fca1a4f3b9da345750535e101027ae6fc5b85218a.json"
            ),
            "resolved_candidate_manifest_sha256": (
                "0224692fb291fe3d86548f1fca1a4f3b9da345750535e101027ae6fc5b85218a"
            ),
            "resolved_shared_base_manifest_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r4/resolved_candidates/"
                "shared_base.b31e1559a0c2d3a707de55b9a45d1ca59b09b810529aa5f78a4ad0051b1f8fff.json"
            ),
            "resolved_shared_base_manifest_sha256": (
                "b31e1559a0c2d3a707de55b9a45d1ca59b09b810529aa5f78a4ad0051b1f8fff"
            ),
            "prior_phase_completion": {
                "stage_attempt_id": "h0-q1-a-20260809-replacement-001",
                "checkpoint_index_path": "artifacts/h0-a/index.json",
                "checkpoint_index_sha256": "9" * 64,
                "runtime_definition_sha256": "a" * 64,
                "terminal_result_sha256": "b" * 64,
            },
            "repair_admission": repair,
            "infrastructure_rerun_admission": infrastructure,
        },
        "h0_phase_completions": {
            "H0-A": {
                "phase": "H0-A",
                "qualified": True,
                "stage_attempt_id": "h0-q1-a-20260809-replacement-001",
            }
        },
        "h0_b_infrastructure_rerun_live_prerequisites": {
            "live_transition_performed": True,
            "prior_harness_repair_admission": deepcopy(repair),
            "infrastructure_rerun_admission": deepcopy(infrastructure),
        },
        "unrelated": {"preserved": True},
    }


def _copy_post_workload_checkpoint(root: Path) -> dict[str, object]:
    source = ROOT / Path(CHECKPOINT_REL).parent
    target = root / Path(CHECKPOINT_REL).parent
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    for relative in (LIVE_LOG_REL, OFFLINE_PROBE_REL):
        copied = root / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, copied)
    return json.loads((root / CHECKPOINT_REL).read_text(encoding="ascii"))


class H0BPostWorkloadEvidenceRedTests(TestCase):
    def test_exact_terminal_evidence_is_classified_without_model_content(self):
        classify = _required_callable(
            recovery, "classify_h0_b_post_workload_harness_failure"
        )
        observed = classify(**_classification_kwargs())
        self.assertEqual(observed, _expected_classification())

    def test_classifier_rejects_each_tampered_binding_fail_closed(self):
        classify = _required_callable(
            recovery, "classify_h0_b_post_workload_harness_failure"
        )
        for field in (
            "checkpoint_index_sha256",
            "failure_segment_sha256",
            "source_checkpoint_sha256",
            "live_log_sha256",
            "offline_probe_sha256",
        ):
            with self.subTest(field=field):
                changed = _classification_kwargs()
                changed[field] = "0" * 64
                with self.assertRaises(recovery.H0HarnessRecoveryError):
                    classify(**changed)

    def test_r4_remains_immutable_while_all_runtime_loaders_move_to_r5(self):
        self.assertEqual(sha256_file(ROOT / R4_INDEX_REL), R4_INDEX_SHA256)
        self.assertEqual(h0_artifacts.H0_ARTIFACT_SET_ID, "v1_3_harness_r5")
        self.assertEqual(h0_artifacts.H0_EXECUTION_HARNESS_REVISION, 5)
        self.assertEqual(
            h0_artifacts.H0_RESOLVED_MANIFEST_INDEX_REL,
            R5_INDEX_REL,
        )
        self.assertEqual(h0_live_preflight._ARTIFACT_SET_ID, "v1_3_harness_r5")
        self.assertEqual(h0_live_preflight._EXECUTION_HARNESS_REVISION, 5)
        self.assertEqual(h0_live_preflight._RESOLVED_INDEX_REL, R5_INDEX_REL)


class H0BPostWorkloadStateRedTests(TestCase):
    def _fixture(self, root: Path) -> tuple[Path, dict[str, object]]:
        checkpoint = _copy_post_workload_checkpoint(root)
        source = _consumed_live_state(checkpoint)
        state_path = root / "CURRENT_STATE.json"
        state_path.write_bytes(canonical_json_bytes(source))
        return state_path, source

    def _transition_kwargs(self, root: Path, builder: Mock | None = None):
        kwargs: dict[str, object] = {
            "root": root,
            "stage_attempt_id": POST_WORKLOAD_ATTEMPT_ID,
            "checkpoint_index_path": CHECKPOINT_REL,
            "checkpoint_index_sha256": CHECKPOINT_SHA256,
            "failure_segment_path": FAILURE_SEGMENT_REL,
            "failure_segment_sha256": FAILURE_SEGMENT_SHA256,
            "source_checkpoint_path": SOURCE_CHECKPOINT_REL,
            "source_checkpoint_sha256": SOURCE_CHECKPOINT_SHA256,
            "live_log_path": LIVE_LOG_REL,
            "live_log_sha256": LIVE_LOG_SHA256,
            "offline_probe_path": OFFLINE_PROBE_REL,
            "offline_probe_sha256": OFFLINE_PROBE_SHA256,
        }
        if builder is not None:
            kwargs["state_builder"] = builder
        return kwargs

    def test_revoke_closes_consumed_002_grant_and_preserves_h0_a(self):
        build = _required_callable(
            recovery, "build_h0_b_post_workload_harness_revoked_state"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _, source = self._fixture(root)
            closed = build(deepcopy(source), **self._transition_kwargs(root))

        self.assertEqual(
            closed["status"], "h0_b_post_workload_harness_failure_live_revoked"
        )
        self.assertEqual(
            closed["current_action_scope"],
            "h0_b_post_workload_harness_repair_offline_only",
        )
        self.assertEqual(closed["current_blocker"], "manifest_contract_failure")
        self.assertFalse(closed["live_h0_candidate_authorized"])
        self.assertEqual(closed["authorized_live_actions"], [])
        self.assertIsNone(closed["authorized_h0_candidate_id"])
        self.assertNotIn("live_h0_authorization", closed)
        self.assertEqual(closed["h0_phase_completions"], source["h0_phase_completions"])
        self.assertEqual(closed["unrelated"], {"preserved": True})
        failure = closed["h0_b_post_workload_harness_failure"]
        self.assertEqual(failure, _expected_classification())
        self.assertFalse(failure["partial_qualification_reusable"])
        self.assertFalse(failure["old_and_new_trial_counts_mergeable"])

    def test_transition_is_zero_write_dry_run_then_one_atomic_commit(self):
        transition = _required_callable(
            recovery, "transition_h0_b_post_workload_harness_revoke"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state_path, source = self._fixture(root)
            before = state_path.read_bytes()
            target = deepcopy(source)
            target.update(
                {
                    "status": "h0_b_post_workload_harness_failure_live_revoked",
                    "current_action_scope": (
                        "h0_b_post_workload_harness_repair_offline_only"
                    ),
                    "current_blocker": "manifest_contract_failure",
                    "live_h0_candidate_authorized": False,
                    "authorized_live_actions": [],
                    "authorized_h0_candidate_id": None,
                }
            )
            target["stage_progress"]["h0_live_gate"] = "forbidden"
            target.pop("live_h0_authorization")
            builder = Mock(return_value=deepcopy(target))
            kwargs = self._transition_kwargs(root, builder)

            preview = transition(state_path, **kwargs)
            self.assertEqual(preview, target)
            self.assertEqual(state_path.read_bytes(), before)

            from h0_state_transition import _atomic_write as real_atomic_write

            with patch(
                "h0_harness_recovery._atomic_write", wraps=real_atomic_write
            ) as atomic:
                committed = transition(state_path, **kwargs, dry_run=False)
                atomic.assert_called_once()
            self.assertEqual(committed, target)
            self.assertEqual(state_path.read_bytes(), canonical_json_bytes(target))

    def test_commit_preserves_concurrent_operator_state_and_writes_nothing(self):
        transition = _required_callable(
            recovery, "transition_h0_b_post_workload_harness_revoke"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state_path, source = self._fixture(root)
            concurrent = {**source, "operator_update": "preserve"}
            concurrent_bytes = canonical_json_bytes(concurrent)

            def build_then_change(*_args, **_kwargs):
                state_path.write_bytes(concurrent_bytes)
                target = deepcopy(source)
                target["live_h0_candidate_authorized"] = False
                target["authorized_live_actions"] = []
                target["authorized_h0_candidate_id"] = None
                target["stage_progress"]["h0_live_gate"] = "forbidden"
                target.pop("live_h0_authorization")
                return target

            with (
                patch("h0_harness_recovery._atomic_write") as atomic,
                self.assertRaisesRegex(
                    recovery.H0HarnessRecoveryError, "state_changed"
                ),
            ):
                transition(
                    state_path,
                    **self._transition_kwargs(root, Mock(side_effect=build_then_change)),
                    dry_run=False,
                )
            atomic.assert_not_called()
            self.assertEqual(state_path.read_bytes(), concurrent_bytes)


class H0BPostWorkloadR5BindingRedTests(TestCase):
    def test_bind_remains_offline_then_live_builder_authorizes_only_003(self):
        bind = _required_callable(
            recovery, "build_h0_b_post_workload_harness_repair_bound_state"
        )
        authorize = _required_callable(
            recovery, "build_h0_b_post_workload_harness_replacement_live_state"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            checkpoint = _copy_post_workload_checkpoint(root)
            revoked = recovery.build_h0_b_post_workload_harness_revoked_state(
                _consumed_live_state(checkpoint),
                **H0BPostWorkloadStateRedTests()._transition_kwargs(root),
            )
            verification = _r5_verification()
            tdd = _tdd_evidence()
            post_workload = _post_workload_admission(checkpoint)
            manifest_validator = Mock(
                return_value=(_r5_bindings(), verification)
            )
            tdd_validator = Mock(return_value=tdd)
            decision_verifier = Mock(return_value=post_workload)
            bound = bind(
                revoked,
                root=root,
                manifest_verification=verification,
                tdd_evidence=tdd,
                repair_decision_path=DECISION_REL,
                repair_decision_sha256="d" * 64,
                manifest_validator=manifest_validator,
                tdd_validator=tdd_validator,
                repair_decision_verifier=decision_verifier,
            )
            self.assertFalse(bound["live_h0_candidate_authorized"])
            self.assertNotIn("live_h0_authorization", bound)
            self.assertEqual(
                bound["status"],
                "h0_b_post_workload_harness_repair_verified_not_live_authorized",
            )
            live = authorize(
                bound,
                root=root,
                manifest_validator=manifest_validator,
                tdd_validator=tdd_validator,
                repair_decision_verifier=decision_verifier,
            )

        self.assertTrue(live["live_h0_candidate_authorized"])
        authorization = live["live_h0_authorization"]
        self.assertEqual(
            authorization["authorized_stage_attempt_id"], REPLACEMENT_ATTEMPT_ID
        )
        self.assertEqual(
            authorization["resolved_manifest_index_path"], R5_INDEX_REL
        )
        self.assertEqual(
            authorization["post_workload_repair_admission"], post_workload
        )
        self.assertEqual(
            authorization["repair_admission"], checkpoint["repair_admission"]
        )
        self.assertEqual(
            authorization["infrastructure_rerun_admission"],
            checkpoint["infrastructure_rerun_admission"],
        )
        self.assertEqual(live["next_allowed_action"], "run_q1_h0-b-post-workload-replacement")

    def test_live_builder_rejects_tampered_persisted_post_workload_admission(self):
        authorize = _required_callable(
            recovery, "build_h0_b_post_workload_harness_replacement_live_state"
        )
        with self.assertRaises(recovery.H0HarnessRecoveryError):
            authorize(
                {
                    "status": "h0_b_post_workload_harness_repair_verified_not_live_authorized",
                    "h0_b_post_workload_harness_repair_live_prerequisites": {
                        "live_transition_performed": False,
                        "post_workload_repair_admission": {
                            "prior_model_workload_output_observed": False
                        },
                    },
                },
                root=ROOT,
            )


class H0BPostWorkloadAdmissionRedTests(TestCase):
    def _decision(self, checkpoint: dict[str, object]) -> dict[str, object]:
        repair_admission = _post_workload_admission(checkpoint)
        return {
            "schema_version": (
                "membind.h0.post-workload-harness-repair-decision.v1"
            ),
            "protocol_version": "current-validation-v1.3",
            "status": "approved_one_shot_whole_stage_replacement_not_live_authorized",
            "candidate_id": "Q1",
            "phase": "H0-B",
            "decision_result_blind": False,
            "prior_model_workload_output_observed": True,
            "repair_required_independent_of_model_response_content": True,
            "scientific_configuration_unchanged": True,
            "one_shot_whole_stage_replacement": True,
            "resume_failed_attempt_allowed": False,
            "old_attempt_qualification_reusable": False,
            "old_and_new_trial_counts_mergeable": False,
            "invalidated_attempt": _expected_classification(),
            "prior_repair_chain": {
                "harness_repair_admission_sha256": repair_admission[
                    "prior_harness_repair_admission_sha256"
                ],
                "infrastructure_rerun_admission_sha256": repair_admission[
                    "prior_infrastructure_rerun_admission_sha256"
                ],
            },
            "prior_execution_binding": {
                "artifact_set_id": "v1_3_harness_r4",
                "execution_harness_revision": 4,
                "manifest_index_path": R4_INDEX_REL,
                "manifest_index_sha256": R4_INDEX_SHA256,
            },
            "repaired_execution_binding": {
                "artifact_set_id": "v1_3_harness_r5",
                "execution_harness_revision": 5,
                "manifest_index_path": R5_INDEX_REL,
                "manifest_index_sha256": "f" * 64,
            },
            "replacement": {
                "attempt_id": REPLACEMENT_ATTEMPT_ID,
                "invalidated_attempt_id": POST_WORKLOAD_ATTEMPT_ID,
                "candidate_id": "Q1",
                "phase": "H0-B",
                "whole_stage": True,
                "one_shot": True,
                "old_attempt_evidence_reused": False,
                "live_authorized_by_this_artifact": False,
            },
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }

    def test_verify_returns_independent_transparent_nonblind_admission(self):
        verify = _required_callable(
            admission, "verify_h0_b_post_workload_harness_repair_decision"
        )
        build_name = "build_h0_b_post_workload_harness_repair_decision"
        _required_callable(admission, build_name)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            checkpoint = _copy_post_workload_checkpoint(root)
            decision = self._decision(checkpoint)
            target = root / DECISION_REL
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(canonical_json_bytes(decision))
            verification = {
                "artifact_set_id": "v1_3_harness_r5",
                "execution_harness_revision": 5,
                "index_path": R5_INDEX_REL,
                "index_sha256": "f" * 64,
            }
            with patch.object(admission, build_name, return_value=decision):
                observed = verify(
                    root=root,
                    decision_path=DECISION_REL,
                    decision_sha256=sha256_file(target),
                    manifest_verification=verification,
                )

        expected = _post_workload_admission(checkpoint)
        expected["decision_sha256"] = canonical_json_sha256(decision)
        self.assertEqual(observed, expected)
        self.assertFalse(observed["decision_result_blind"])
        self.assertTrue(observed["prior_model_workload_output_observed"])
        self.assertTrue(
            observed["repair_required_independent_of_model_response_content"]
        )
        self.assertFalse(observed["old_attempt_qualification_reusable"])
        self.assertFalse(observed["old_and_new_trial_counts_mergeable"])
        self.assertFalse(observed["resume_failed_attempt_allowed"])

    def test_verifier_rejects_tamper_wrong_path_and_extra_field(self):
        verify = _required_callable(
            admission, "verify_h0_b_post_workload_harness_repair_decision"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            checkpoint = _copy_post_workload_checkpoint(root)
            decision = self._decision(checkpoint)
            target = root / DECISION_REL
            target.parent.mkdir(parents=True, exist_ok=True)
            changed = {**decision, "unexpected": False}
            target.write_bytes(canonical_json_bytes(changed))
            with self.assertRaises(admission.H0RepairAdmissionError):
                verify(
                    root=root,
                    decision_path=DECISION_REL,
                    decision_sha256=sha256_file(target),
                    manifest_verification={
                        "artifact_set_id": "v1_3_harness_r5",
                        "execution_harness_revision": 5,
                        "index_path": R5_INDEX_REL,
                        "index_sha256": "f" * 64,
                    },
                )


class H0BPostWorkloadCheckpointAdmissionRedTests(TestCase):
    def _copy_history(self, root: Path) -> dict[str, object]:
        source = ROOT / "artifacts/h0_runs/h0/checkpoints"
        target = root / "h0/checkpoints"
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt_id in (
            FAILED_ATTEMPT_ID,
            INTERRUPTED_ATTEMPT_ID,
            POST_WORKLOAD_ATTEMPT_ID,
        ):
            shutil.copytree(source / attempt_id, target / attempt_id)
        return json.loads(
            (target / POST_WORKLOAD_ATTEMPT_ID / "index.json").read_text(
                encoding="ascii"
            )
        )

    def test_exact_replacement_003_starts_empty_and_preserves_all_old_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            checkpoint = self._copy_history(root)
            repair = checkpoint["repair_admission"]
            infrastructure = checkpoint["infrastructure_rerun_admission"]
            post_workload = _post_workload_admission(checkpoint)
            old_paths = sorted((root / "h0/checkpoints").glob("*/index.json"))
            before = {path: path.read_bytes() for path in old_paths}

            replacement = H0CheckpointStore(
                root=root,
                stage_attempt_id=REPLACEMENT_ATTEMPT_ID,
                candidate_id="Q1",
                phase="H0-B",
                repair_admission=repair,
                infrastructure_rerun_admission=infrastructure,
                post_workload_repair_admission=post_workload,
            )

            self.assertEqual({path: path.read_bytes() for path in old_paths}, before)
            self.assertEqual(replacement.index["prior_matching_attempt_count"], 3)
            self.assertEqual(
                replacement.index["infrastructure_interrupted_attempt_count"], 1
            )
            self.assertTrue(replacement.index["whole_stage_rerun"])
            self.assertTrue(replacement.index["post_workload_harness_replacement"])
            self.assertEqual(
                replacement.index["post_workload_repair_admission"], post_workload
            )
            self.assertEqual(replacement.index["segments"], [])
            self.assertFalse(replacement.index["partial_qualification_reusable"])

    def test_replacement_003_rejects_tamper_resume_duplicate_and_third_attempt(self):
        mutations = (
            {"replacement_attempt_id": "h0-q1-b-20260810-replacement-004"},
            {"invalidated_stage_attempt_id": INTERRUPTED_ATTEMPT_ID},
            {"invalidated_checkpoint_index_sha256": "0" * 64},
            {"failure_segment_sha256": "0" * 64},
            {"source_checkpoint_sha256": "0" * 64},
            {"prior_model_workload_output_observed": False},
            {"repair_required_independent_of_model_response_content": False},
            {"one_shot_whole_stage_replacement": False},
            {"resume_failed_attempt_allowed": True},
            {"old_attempt_qualification_reusable": True},
            {"old_and_new_trial_counts_mergeable": True},
            {"prior_manifest_index_sha256": "0" * 64},
            {"unexpected": False},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                checkpoint = self._copy_history(root)
                changed = {**_post_workload_admission(checkpoint), **mutation}
                with self.assertRaises(H0StateGateError):
                    H0CheckpointStore(
                        root=root,
                        stage_attempt_id=REPLACEMENT_ATTEMPT_ID,
                        candidate_id="Q1",
                        phase="H0-B",
                        repair_admission=checkpoint["repair_admission"],
                        infrastructure_rerun_admission=checkpoint[
                            "infrastructure_rerun_admission"
                        ],
                        post_workload_repair_admission=changed,
                    )


class H0BPostWorkloadControlRedTests(TestCase):
    def test_control_plane_exposes_four_separate_r5_recovery_actions(self):
        parser = h0_control._build_parser()
        cases = (
            (
                "revoke-h0-b-post-workload-harness",
                [
                    "--attempt-id",
                    POST_WORKLOAD_ATTEMPT_ID,
                    "--checkpoint-index",
                    CHECKPOINT_REL,
                    "--checkpoint-index-sha256",
                    CHECKPOINT_SHA256,
                    "--failure-segment",
                    FAILURE_SEGMENT_REL,
                    "--failure-segment-sha256",
                    FAILURE_SEGMENT_SHA256,
                    "--source-checkpoint",
                    SOURCE_CHECKPOINT_REL,
                    "--source-checkpoint-sha256",
                    SOURCE_CHECKPOINT_SHA256,
                    "--live-log",
                    LIVE_LOG_REL,
                    "--live-log-sha256",
                    LIVE_LOG_SHA256,
                    "--offline-probe",
                    OFFLINE_PROBE_REL,
                    "--offline-probe-sha256",
                    OFFLINE_PROBE_SHA256,
                ],
            ),
            (
                "prepare-h0-b-post-workload-harness-repair",
                ["--replacement-attempt-id", REPLACEMENT_ATTEMPT_ID],
            ),
            (
                "bind-h0-b-post-workload-harness-repair",
                [
                    "--tdd-evidence",
                    "artifacts/tdd/r5.json",
                    "--decision",
                    DECISION_REL,
                    "--decision-sha256",
                    "d" * 64,
                ],
            ),
            ("authorize-q1-b-post-workload-harness-replacement", []),
        )
        for command, arguments in cases:
            with self.subTest(command=command):
                parsed = parser.parse_args([command, *arguments])
                self.assertEqual(parsed.command, command)


class H0BPostWorkloadLiveRunnerRedTests(IsolatedAsyncioTestCase):
    def _authorization(self, checkpoint: dict[str, object]) -> dict[str, object]:
        authorization = deepcopy(
            _consumed_live_state(checkpoint)["live_h0_authorization"]
        )
        authorization.update(_r5_bindings())
        authorization["authorized_stage_attempt_id"] = REPLACEMENT_ATTEMPT_ID
        authorization["post_workload_repair_admission"] = (
            _post_workload_admission(checkpoint)
        )
        return authorization

    async def test_exact_003_admissions_are_forwarded_before_credentials(self):
        checkpoint = json.loads((ROOT / CHECKPOINT_REL).read_text(encoding="ascii"))
        authorization = self._authorization(checkpoint)
        captured: dict[str, object] = {}

        def checkpoint_store(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("checkpoint-boundary")

        definition = SimpleNamespace(
            identity={"candidate_id": "Q1", "phase": "H0-B"},
            candidate=SimpleNamespace(candidate_id="Q1"),
            embedding_namespace={
                "served_model_id": "qwen3-embedding-0.6b",
                "dimension": 1024,
                "normalization": "l2",
            },
            semantic_guardrail={},
            definition_sha256="6" * 64,
        )
        with self.assertRaisesRegex(RuntimeError, "checkpoint-boundary"):
            await h0_full_history_live.execute_h0_full_history_live(
                root=ROOT,
                state_path=ROOT / "state.json",
                artifacts_root=ROOT / "unused-runs",
                stage_attempt_id=REPLACEMENT_ATTEMPT_ID,
                candidate_id="Q1",
                phase="H0-B",
                authorization_checker=Mock(return_value=authorization),
                runtime_definition_loader=Mock(return_value=definition),
                prior_completion_validator=Mock(return_value={"qualified": True}),
                checkpoint_store_factory=checkpoint_store,
                credential_loader=Mock(side_effect=AssertionError("credentials touched")),
            )
        self.assertEqual(captured["repair_admission"], checkpoint["repair_admission"])
        self.assertEqual(
            captured["infrastructure_rerun_admission"],
            checkpoint["infrastructure_rerun_admission"],
        )
        self.assertEqual(
            captured["post_workload_repair_admission"],
            authorization["post_workload_repair_admission"],
        )

    async def test_tampered_post_workload_admission_fails_before_runtime(self):
        checkpoint = json.loads((ROOT / CHECKPOINT_REL).read_text(encoding="ascii"))
        authorization = self._authorization(checkpoint)
        changed = dict(authorization["post_workload_repair_admission"])
        changed["prior_model_workload_output_observed"] = False
        authorization["post_workload_repair_admission"] = changed
        runtime = Mock(side_effect=AssertionError("runtime touched"))
        with self.assertRaises(H0StateGateError):
            await h0_full_history_live.execute_h0_full_history_live(
                root=ROOT,
                state_path=ROOT / "state.json",
                artifacts_root=ROOT / "unused-runs",
                stage_attempt_id=REPLACEMENT_ATTEMPT_ID,
                candidate_id="Q1",
                phase="H0-B",
                authorization_checker=Mock(return_value=authorization),
                runtime_definition_loader=runtime,
            )
        runtime.assert_not_called()


if __name__ == "__main__":
    import unittest

    unittest.main()
