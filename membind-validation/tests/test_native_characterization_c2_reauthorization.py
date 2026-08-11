"""TDD contracts for the narrow post-cleanup C2 reauthorization transition."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from current_state_gate import LiveAction, evaluate_live_action  # noqa: E402
from native_characterization_c2_cleanup import (  # noqa: E402
    CLEANUP_PRIMITIVE,
    FAILED_C2_ATTEMPT_ID as CLEANUP_FAILED_ATTEMPT_ID,
    INTERRUPTED_C2_ATTEMPT_ID,
    POLLUTED_C2_GROUP_ID as CLEANUP_POLLUTED_GROUP_ID,
    SCHEMA_VERSION as CLEANUP_SCHEMA_VERSION,
)
from native_characterization_c2_reauthorization import (  # noqa: E402
    C2ReauthorizationBindings,
    NativeCharacterizationC2ReauthorizationError,
    reauthorize_native_characterization_c2_live_only,
)
import native_characterization_c2_reauthorization as reauthorization  # noqa: E402


FAILED_ATTEMPT_ID = "c2-c5e5463facb3bce7"
POLLUTED_GROUP_ID = "nc-e1e2-400b9b78c2c218df"
CLEANUP_FREEZE = "artifacts/native_characterization/freeze_json_object.json"
REFERENCE_FREEZE = "artifacts/native_characterization/freeze_reference_aligned.json"
REFERENCE_AUTHORIZATION_KEY = "native_characterization_reference_c2_authorization"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _with_payload_hash(value: dict[str, object]) -> dict[str, object]:
    result = deepcopy(value)
    result["payload_sha256"] = _sha(_canonical(result))
    return result


class NativeCharacterizationC2ReauthorizationTests(TestCase):
    def test_contract_separates_cleanup_source_from_reference_execution(self) -> None:
        self.assertEqual(reauthorization.FAILED_C2_ATTEMPT_ID, FAILED_ATTEMPT_ID)
        self.assertEqual(
            reauthorization.CLEANUP_FREEZE_RELATIVE_PATH,
            CLEANUP_FREEZE,
        )
        self.assertEqual(
            reauthorization.REFERENCE_FREEZE_RELATIVE_PATH,
            REFERENCE_FREEZE,
        )
        self.assertNotEqual(
            reauthorization.CLEANUP_FREEZE_RELATIVE_PATH,
            reauthorization.REFERENCE_FREEZE_RELATIVE_PATH,
        )
        self.assertEqual(reauthorization.METADATA_KEY, REFERENCE_AUTHORIZATION_KEY)

    def _fixture(
        self,
    ) -> tuple[Path, Path, C2ReauthorizationBindings, dict[str, object]]:
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        validation = repo / "membind-validation"
        validation.mkdir()

        source_payloads = {
            "src/native_characterization_runtime.py": b"runtime-reference\n",
            "src/graphiti_native.py": b"qwen-transport-reference\n",
            "src/native_characterization_c2.py": b"c2-runner-reference\n",
        }
        for relative, content in source_payloads.items():
            path = validation / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        block_order = [
            {
                "block_index": index,
                "history_id": f"history-{index}",
                "graph_namespace": (
                    POLLUTED_GROUP_ID
                    if index == 0
                    else f"nc-e1e2-{index:016x}"
                ),
            }
            for index in range(4)
        ]
        cleanup_freeze = _with_payload_hash(
            {
                "schema_version": "membind.native-characterization-freeze.v1",
                "construction_compatibility_policy": {
                    "structured_output_mode": "json_object",
                },
                "screening": {"e1_e2": {"block_order": block_order}},
            }
        )
        cleanup_freeze_path = validation / CLEANUP_FREEZE
        cleanup_freeze_path.parent.mkdir(parents=True)
        cleanup_freeze_path.write_bytes(_canonical(cleanup_freeze))
        cleanup_freeze_sha256 = _sha(cleanup_freeze_path.read_bytes())

        reference_freeze = _with_payload_hash(
            {
                "schema_version": "membind.native-characterization-freeze.v1",
                "construction_compatibility_policy": {
                    "classification": (
                        "reference_aligned_with_declared_project_deviations"
                    ),
                    "structured_output_mode": "json_schema",
                    "structured_output_backend_requested": None,
                    "upstream_graphiti_behavior": False,
                    "project_generate_response_override": False,
                    "project_structured_parser": False,
                    "project_context_probe": False,
                    "project_retry_budget_matrix": False,
                    "requested_max_tokens": 16384,
                },
                "derivation": {
                    "parent_freeze_path": "artifacts/native_characterization/freeze.json",
                    "parent_freeze_sha256": "3" * 64,
                    "reason": "restore_pinned_graphiti_openai_generic_provider_path",
                },
                "input_hashes": {
                    "u0_runtime_source_sha256": _sha(
                        source_payloads["src/native_characterization_runtime.py"]
                    ),
                    "qwen_transport_source_sha256": _sha(
                        source_payloads["src/graphiti_native.py"]
                    ),
                },
                "screening": {"e1_e2": {"block_order": block_order}},
            }
        )
        reference_freeze_path = validation / REFERENCE_FREEZE
        reference_freeze_path.write_bytes(_canonical(reference_freeze))
        reference_freeze_sha256 = _sha(reference_freeze_path.read_bytes())

        cleanup = _with_payload_hash(
            {
                "schema_version": "membind.native-characterization-c2-cleanup.v1",
                "status": "verified_empty",
                "failed_attempt_id": FAILED_ATTEMPT_ID,
                "failed_attempt_valid": False,
                "failed_attempt_mergeable": False,
                "replacement_resume_allowed": False,
                "target_group_id": POLLUTED_GROUP_ID,
                "freeze_sha256": cleanup_freeze_sha256,
                "cleanup_primitive": (
                    "graphiti.clear_data(driver,group_ids=[target_group])"
                ),
                "pre_cleanup": {"node_count": 12, "relationship_count": 7},
                "post_cleanup": {"node_count": 0, "relationship_count": 0},
                "preexisting_empty": False,
            }
        )
        cleanup_path = validation / (
            "artifacts/native_characterization/c2_cleanup/"
            f"{FAILED_ATTEMPT_ID}.json"
        )
        cleanup_path.parent.mkdir(parents=True, exist_ok=True)
        cleanup_path.write_bytes(_canonical(cleanup) + b"\n")

        regression_path = validation / "artifacts/tdd/final_full_regression.log"
        regression_path.parent.mkdir(parents=True)
        regression_path.write_text(
            "Ran 784 tests in 12.345s\n\nOK\nexit_code: 0\n",
            encoding="ascii",
        )

        state: dict[str, object] = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_cleanup_only",
            "current_blocker": "c2_reference_aligned_cleanup_pending",
            "current_action_scope": "native_characterization_c2_cleanup_only",
            "authorized_live_actions": [],
            "native_characterization_live_authorized": False,
            "next_allowed_action": (
                "execute_scoped_c2_cleanup_reference_aligned_precondition"
            ),
            "live_h0_candidate_authorized": False,
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "native_characterization": (
                    "c0_c1_pass_reference_alignment_cleanup_only_pending"
                ),
                "full_unit_regression": "pass",
            },
            "native_characterization_c2_authorization": {
                "schema_version": (
                    "membind.native-characterization-c2-reauthorization.v1"
                ),
                "live_authorized": True,
                "failed_attempt_id": "c2-efb58c477f12adf6",
            },
            "native_characterization_reference_alignment": {
                "schema_version": (
                    "membind.native-characterization-reference-alignment.v1"
                ),
                "status": "offline_green_cleanup_pending",
                "canonical_freeze_path": (
                    "artifacts/native_characterization/freeze.json"
                ),
                "canonical_freeze_sha256": "3" * 64,
                "reference_freeze_path": REFERENCE_FREEZE,
                "reference_freeze_sha256": reference_freeze_sha256,
                "cleanup": {
                    "operator_authorized": True,
                    "execution_status": "pending",
                    "failed_attempt_id": FAILED_ATTEMPT_ID,
                    "failed_attempt_valid": False,
                    "failed_attempt_mergeable": False,
                    "target_group_id": POLLUTED_GROUP_ID,
                    "source_freeze_path": CLEANUP_FREEZE,
                    "source_freeze_sha256": cleanup_freeze_sha256,
                    "planned_evidence_path": str(
                        cleanup_path.relative_to(validation)
                    ),
                    "required_post_node_count": 0,
                    "required_post_relationship_count": 0,
                },
                "fresh_c2": {
                    "semantic_attempts_remaining": 1,
                    "run_id_pattern": "c2-[0-9a-f]{16}",
                    "start_source_sequence": 0,
                    "resume_allowed": False,
                    "prefix_merge_allowed": False,
                    "structured_output_mode": "json_schema",
                    "infrastructure_failure_action": (
                        "checkpoint_revoke_stop_and_notify"
                    ),
                    "structured_correctness_failure_action": (
                        "revoke_stop_envelope_unsuitable_no_fallback"
                    ),
                },
            },
            "unrelated_history": {"preserved": True},
        }
        state_path = validation / "CURRENT_STATE.json"
        state_path.write_bytes(_canonical(state))

        bindings = C2ReauthorizationBindings(
            source_state_sha256=_sha(_canonical(state)),
            cleanup_evidence_path=str(cleanup_path.relative_to(validation)),
            cleanup_evidence_sha256=_sha(cleanup_path.read_bytes()),
            final_full_regression_path=str(regression_path.relative_to(validation)),
            final_full_regression_sha256=_sha(regression_path.read_bytes()),
            final_full_regression_test_count=784,
            reference_freeze_sha256=reference_freeze_sha256,
            c2_runner_source_sha256=_sha(
                source_payloads["src/native_characterization_c2.py"]
            ),
        )
        return repo, state_path, bindings, state

    def _rewrite_cleanup(
        self,
        repo: Path,
        bindings: C2ReauthorizationBindings,
        **changes: object,
    ) -> C2ReauthorizationBindings:
        validation = repo / "membind-validation"
        path = validation / bindings.cleanup_evidence_path
        value = json.loads(path.read_text(encoding="ascii"))
        value.pop("payload_sha256")
        value.update(changes)
        path.write_bytes(_canonical(_with_payload_hash(value)) + b"\n")
        return C2ReauthorizationBindings(
            **{
                **bindings.__dict__,
                "cleanup_evidence_sha256": _sha(path.read_bytes()),
            }
        )

    def _interruption_fixture(
        self,
    ) -> tuple[Path, Path, C2ReauthorizationBindings, dict[str, object]]:
        repo, state_path, bindings, state = self._fixture()
        validation = repo / "membind-validation"
        reference_freeze_path = validation / REFERENCE_FREEZE
        reference_freeze_sha256 = _sha(reference_freeze_path.read_bytes())

        old_cleanup = validation / bindings.cleanup_evidence_path
        cleanup = json.loads(old_cleanup.read_text(encoding="ascii"))
        cleanup.pop("payload_sha256")
        cleanup.update(
            {
                "failed_attempt_id": INTERRUPTED_C2_ATTEMPT_ID,
                "freeze_sha256": reference_freeze_sha256,
                "pre_cleanup": {"node_count": 34, "relationship_count": 61},
            }
        )
        cleanup = _with_payload_hash(cleanup)
        cleanup_path = validation / (
            "artifacts/native_characterization/c2_cleanup/"
            f"{INTERRUPTED_C2_ATTEMPT_ID}.json"
        )
        cleanup_path.write_bytes(_canonical(cleanup) + b"\n")

        state["current_blocker"] = "c2_infrastructure_interruption_cleanup_pending"
        state["next_allowed_action"] = (
            "execute_scoped_c2_cleanup_after_infrastructure_interruption"
        )
        state["stage_progress"]["native_characterization"] = (
            "c0_c1_pass_reference_c2_infrastructure_interrupted_cleanup_pending"
        )
        alignment = state["native_characterization_reference_alignment"]
        alignment["status"] = "c2_infrastructure_interrupted_cleanup_pending"
        alignment["cleanup"].update(
            {
                "failed_attempt_id": INTERRUPTED_C2_ATTEMPT_ID,
                "replacement_resume_allowed": False,
                "source_freeze_path": REFERENCE_FREEZE,
                "source_freeze_sha256": reference_freeze_sha256,
                "planned_evidence_path": str(cleanup_path.relative_to(validation)),
            }
        )
        alignment["fresh_c2"]["live_authorized"] = False
        state[REFERENCE_AUTHORIZATION_KEY] = {
            "live_authorized": False,
            "replacement_resume_allowed": False,
            "replacement_start_source_sequence": 0,
            "semantic_attempts_authorized": 1,
            "consumed_by_run_id": INTERRUPTED_C2_ATTEMPT_ID,
        }
        state["native_characterization_c2_interruption"] = {
            "run_id": INTERRUPTED_C2_ATTEMPT_ID,
            "error_code": "openai.APIConnectionError",
            "attempt_valid": False,
            "attempt_mergeable": False,
            "resume_allowed": False,
            "prefix_merge_allowed": False,
            "semantic_attempt_consumed": False,
            "semantic_attempts_remaining": 1,
            "cleanup_authorized": True,
            "live_authorized": False,
        }
        state_path.write_bytes(_canonical(state))
        bindings = C2ReauthorizationBindings(
            **{
                **bindings.__dict__,
                "source_state_sha256": _sha(_canonical(state)),
                "cleanup_evidence_path": str(cleanup_path.relative_to(validation)),
                "cleanup_evidence_sha256": _sha(cleanup_path.read_bytes()),
            }
        )
        return repo, state_path, bindings, state

    def test_interruption_cleanup_reauthorizes_c2_without_consuming_semantic_attempt(self) -> None:
        repo, state_path, bindings, _ = self._interruption_fixture()

        target = reauthorize_native_characterization_c2_live_only(
            state_path,
            repo_root=repo,
            bindings=bindings,
            dry_run=True,
        )

        self.assertEqual(target["authorized_live_actions"], ["native_characterization_c2"])
        self.assertTrue(target["native_characterization_live_authorized"])
        self.assertFalse(target["service_admin_authorized"])
        self.assertEqual(
            target["native_characterization_reference_c2_authorization"][
                "failed_attempt_id"
            ],
            INTERRUPTED_C2_ATTEMPT_ID,
        )
        self.assertEqual(
            target["native_characterization_reference_c2_authorization"][
                "cleanup_source_freeze_path"
            ],
            REFERENCE_FREEZE,
        )
        fresh = target["native_characterization_reference_alignment"]["fresh_c2"]
        self.assertEqual(fresh["semantic_attempts_remaining"], 1)
        self.assertFalse(fresh["resume_allowed"])
        self.assertFalse(fresh["prefix_merge_allowed"])
        self.assertTrue(fresh["live_authorized"])
        interruption = target["native_characterization_c2_interruption"]
        self.assertFalse(interruption["semantic_attempt_consumed"])
        self.assertFalse(interruption["attempt_mergeable"])

    def test_dry_run_authorizes_only_c2_and_binds_exact_evidence(self) -> None:
        repo, state_path, bindings, source = self._fixture()
        before = state_path.read_bytes()

        target = reauthorize_native_characterization_c2_live_only(
            state_path,
            repo_root=repo,
            bindings=bindings,
            dry_run=True,
        )

        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(target["status"], "native_characterization_c2_live_only")
        self.assertFalse(target["service_admin_authorized"])
        self.assertTrue(target["native_characterization_live_authorized"])
        self.assertIsNone(target["current_blocker"])
        self.assertEqual(
            target["current_action_scope"],
            "native_characterization_c2_live_only",
        )
        self.assertEqual(
            target["authorized_live_actions"], ["native_characterization_c2"]
        )
        self.assertEqual(
            target["next_allowed_action"], "run_native_characterization_c2"
        )
        self.assertEqual(
            target["stage_progress"]["native_characterization"],
            "c0_c1_pass_reference_aligned_c2_authorized_from_episode_0",
        )
        self.assertTrue(
            evaluate_live_action(
                target, LiveAction.NATIVE_CHARACTERIZATION_C2
            ).allowed
        )
        for action in LiveAction:
            if action is not LiveAction.NATIVE_CHARACTERIZATION_C2:
                self.assertFalse(evaluate_live_action(target, action).allowed)

        metadata = target[REFERENCE_AUTHORIZATION_KEY]
        self.assertEqual(
            metadata["schema_version"],
            "membind.native-characterization-reference-c2-authorization.v1",
        )
        self.assertEqual(
            metadata["cleanup_evidence_sha256"],
            bindings.cleanup_evidence_sha256,
        )
        self.assertEqual(
            metadata["final_full_regression_sha256"],
            bindings.final_full_regression_sha256,
        )
        self.assertEqual(metadata["final_full_regression_test_count"], 784)
        self.assertEqual(metadata["failed_attempt_id"], FAILED_ATTEMPT_ID)
        self.assertEqual(metadata["polluted_group_id"], POLLUTED_GROUP_ID)
        self.assertEqual(metadata["cleanup_source_freeze_path"], CLEANUP_FREEZE)
        self.assertEqual(metadata["reference_freeze_path"], REFERENCE_FREEZE)
        self.assertEqual(
            metadata["reference_freeze_sha256"],
            bindings.reference_freeze_sha256,
        )
        self.assertEqual(
            metadata["c2_runner_source_sha256"],
            bindings.c2_runner_source_sha256,
        )
        self.assertEqual(metadata["replacement_start_source_sequence"], 0)
        self.assertFalse(metadata["replacement_resume_allowed"])
        self.assertTrue(metadata["live_authorized"])

        alignment = target["native_characterization_reference_alignment"]
        self.assertEqual(alignment["status"], "c2_live_authorized")
        self.assertFalse(alignment["cleanup"]["operator_authorized"])
        self.assertEqual(
            alignment["cleanup"]["execution_status"], "verified_empty"
        )
        self.assertEqual(
            alignment["cleanup"]["evidence_sha256"],
            bindings.cleanup_evidence_sha256,
        )

        allowed_changes = {
            "status",
            "current_blocker",
            "current_action_scope",
            "authorized_live_actions",
            "native_characterization_live_authorized",
            "next_allowed_action",
            "stage_progress",
            "native_characterization_reference_alignment",
            REFERENCE_AUTHORIZATION_KEY,
        }
        self.assertEqual(
            {key for key in target if target.get(key) != source.get(key)},
            allowed_changes,
        )
        source_progress = source["stage_progress"]
        target_progress = target["stage_progress"]
        self.assertEqual(
            {
                key
                for key in target_progress
                if target_progress.get(key) != source_progress.get(key)
            },
            {"native_characterization"},
        )

    def test_cleanup_helper_keeps_schema_and_target_with_latest_attribution(self) -> None:
        self.assertEqual(
            CLEANUP_SCHEMA_VERSION,
            "membind.native-characterization-c2-cleanup.v1",
        )
        self.assertEqual(FAILED_ATTEMPT_ID, "c2-c5e5463facb3bce7")
        self.assertEqual(CLEANUP_FAILED_ATTEMPT_ID, "c2-c5e5463facb3bce7")
        self.assertEqual(CLEANUP_POLLUTED_GROUP_ID, POLLUTED_GROUP_ID)
        self.assertEqual(
            CLEANUP_PRIMITIVE,
            "graphiti.clear_data(driver,group_ids=[target_group])",
        )

    def test_accepts_buffered_stdout_after_unittest_green_summary(self) -> None:
        """A green unittest summary may precede stdout flushed at process exit."""

        repo, state_path, bindings, _ = self._fixture()
        regression = (
            repo / "membind-validation" / bindings.final_full_regression_path
        )
        regression.write_text(
            "Ran 784 tests in 12.345s\n\n"
            "OK\n"
            '{"safe_diagnostic":"flushed_after_summary"}\n'
            "exit_code: 0\n",
            encoding="ascii",
        )
        bindings = C2ReauthorizationBindings(
            **{
                **bindings.__dict__,
                "final_full_regression_sha256": _sha(regression.read_bytes()),
            }
        )

        target = reauthorize_native_characterization_c2_live_only(
            state_path,
            repo_root=repo,
            bindings=bindings,
            dry_run=True,
        )

        self.assertEqual(
            target["authorized_live_actions"], ["native_characterization_c2"]
        )

    def test_apply_atomically_replaces_only_current_state(self) -> None:
        repo, state_path, bindings, _ = self._fixture()

        target = reauthorize_native_characterization_c2_live_only(
            state_path,
            repo_root=repo,
            bindings=bindings,
            dry_run=False,
        )

        self.assertEqual(json.loads(state_path.read_text(encoding="ascii")), target)
        self.assertEqual(list(state_path.parent.glob(".CURRENT_STATE.json.*")), [])

    def test_rejects_polluted_group_or_freeze_drift(self) -> None:
        for label, changes, reason in (
            (
                "cleanup target drift",
                {"target_group_id": "nc-e1e2-0000000000000001"},
                "cleanup_target_mismatch",
            ),
            (
                "cleanup freeze drift",
                {"freeze_sha256": "0" * 64},
                "cleanup_freeze_mismatch",
            ),
        ):
            with self.subTest(label=label):
                repo, state_path, bindings, _ = self._fixture()
                bindings = self._rewrite_cleanup(repo, bindings, **changes)
                with self.assertRaisesRegex(
                    NativeCharacterizationC2ReauthorizationError, reason
                ):
                    reauthorize_native_characterization_c2_live_only(
                        state_path,
                        repo_root=repo,
                        bindings=bindings,
                        dry_run=True,
                    )

        repo, state_path, bindings, _ = self._fixture()
        freeze_path = repo / "membind-validation" / CLEANUP_FREEZE
        freeze = json.loads(freeze_path.read_text(encoding="ascii"))
        freeze["screening"]["e1_e2"]["block_order"][0]["graph_namespace"] = (
            "nc-e1e2-0000000000000001"
        )
        freeze_path.write_bytes(_canonical(freeze))
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "cleanup_source_freeze_hash_mismatch",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=bindings,
                dry_run=True,
            )

    def test_rejects_reference_freeze_runtime_or_runner_source_drift(self) -> None:
        repo, state_path, bindings, _ = self._fixture()
        wrong_reference = C2ReauthorizationBindings(
            **{**bindings.__dict__, "reference_freeze_sha256": "0" * 64}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "reference_freeze_hash_mismatch",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=wrong_reference,
                dry_run=True,
            )

        repo, state_path, bindings, _ = self._fixture()
        runtime = repo / "membind-validation/src/native_characterization_runtime.py"
        runtime.write_bytes(b"runtime-drift\n")
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "u0_runtime_source_sha256_mismatch",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=bindings,
                dry_run=True,
            )

        repo, state_path, bindings, _ = self._fixture()
        wrong_runner = C2ReauthorizationBindings(
            **{**bindings.__dict__, "c2_runner_source_sha256": "0" * 64}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "c2_runner_source_hash_mismatch",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=wrong_runner,
                dry_run=True,
            )

    def test_rejects_failed_mergeable_or_residual_cleanup(self) -> None:
        cases = (
            ({"status": "error"}, "cleanup_not_verified"),
            ({"failed_attempt_id": "c2-0000000000000000"}, "failed_attempt_contract_mismatch"),
            ({"failed_attempt_valid": True}, "failed_attempt_contract_mismatch"),
            ({"failed_attempt_mergeable": True}, "failed_attempt_contract_mismatch"),
            ({"replacement_resume_allowed": True}, "failed_attempt_contract_mismatch"),
            ({"post_cleanup": {"node_count": 1, "relationship_count": 0}}, "cleanup_residual"),
            ({"post_cleanup": {"node_count": 0, "relationship_count": 1}}, "cleanup_residual"),
        )
        for changes, reason in cases:
            with self.subTest(changes=changes):
                repo, state_path, bindings, _ = self._fixture()
                bindings = self._rewrite_cleanup(repo, bindings, **changes)
                with self.assertRaisesRegex(
                    NativeCharacterizationC2ReauthorizationError, reason
                ):
                    reauthorize_native_characterization_c2_live_only(
                        state_path,
                        repo_root=repo,
                        bindings=bindings,
                        dry_run=True,
                    )

    def test_rejects_cleanup_file_or_payload_hash_mismatch(self) -> None:
        repo, state_path, bindings, _ = self._fixture()
        wrong_file_hash = C2ReauthorizationBindings(
            **{**bindings.__dict__, "cleanup_evidence_sha256": "0" * 64}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "cleanup_evidence_hash_mismatch",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=wrong_file_hash,
                dry_run=True,
            )

        repo, state_path, bindings, _ = self._fixture()
        cleanup = repo / "membind-validation" / bindings.cleanup_evidence_path
        value = json.loads(cleanup.read_text(encoding="ascii"))
        value["payload_sha256"] = "0" * 64
        cleanup.write_bytes(_canonical(value) + b"\n")
        wrong_payload = C2ReauthorizationBindings(
            **{
                **bindings.__dict__,
                "cleanup_evidence_sha256": _sha(cleanup.read_bytes()),
            }
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "cleanup_evidence_payload_mismatch",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=wrong_payload,
                dry_run=True,
            )

    def test_rejects_invalid_or_mismatching_full_regression(self) -> None:
        repo, state_path, bindings, _ = self._fixture()
        mismatch = C2ReauthorizationBindings(
            **{**bindings.__dict__, "final_full_regression_sha256": "0" * 64}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "final_full_regression_hash_mismatch",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path, repo_root=repo, bindings=mismatch, dry_run=True
            )

        repo, state_path, bindings, _ = self._fixture()
        regression = repo / "membind-validation" / bindings.final_full_regression_path
        regression.write_text("Ran 784 tests in 1.000s\n\nFAILED (failures=1)\n")
        invalid = C2ReauthorizationBindings(
            **{
                **bindings.__dict__,
                "final_full_regression_sha256": _sha(regression.read_bytes()),
            }
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "final_full_regression_not_green",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path, repo_root=repo, bindings=invalid, dry_run=True
            )

        repo, state_path, bindings, _ = self._fixture()
        regression = repo / "membind-validation" / bindings.final_full_regression_path
        regression.write_text(
            "Ran 784 tests in 1.000s\n\nOK\nexit_code: 1\n",
            encoding="ascii",
        )
        nonzero = C2ReauthorizationBindings(
            **{
                **bindings.__dict__,
                "final_full_regression_sha256": _sha(regression.read_bytes()),
            }
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "final_full_regression_not_green",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path, repo_root=repo, bindings=nonzero, dry_run=True
            )

        repo, state_path, bindings, _ = self._fixture()
        wrong_count = C2ReauthorizationBindings(
            **{**bindings.__dict__, "final_full_regression_test_count": 783}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "final_full_regression_test_count_mismatch",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path, repo_root=repo, bindings=wrong_count, dry_run=True
            )

    def test_rejects_source_hash_or_cleanup_pending_contract_drift(self) -> None:
        repo, state_path, bindings, _ = self._fixture()
        drifted_digest = C2ReauthorizationBindings(
            **{**bindings.__dict__, "source_state_sha256": "0" * 64}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError, "source_state_drift"
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=drifted_digest,
                dry_run=True,
            )

        repo, state_path, bindings, state = self._fixture()
        state["current_blocker"] = None
        state_path.write_bytes(_canonical(state))
        recomputed = C2ReauthorizationBindings(
            **{**bindings.__dict__, "source_state_sha256": _sha(_canonical(state))}
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError,
            "source_state_not_cleanup_pending",
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=recomputed,
                dry_run=True,
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
