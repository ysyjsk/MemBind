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
    POLLUTED_C2_GROUP_ID as CLEANUP_POLLUTED_GROUP_ID,
    SCHEMA_VERSION as CLEANUP_SCHEMA_VERSION,
)
from native_characterization_c2_reauthorization import (  # noqa: E402
    C2ReauthorizationBindings,
    NativeCharacterizationC2ReauthorizationError,
    reauthorize_native_characterization_c2_live_only,
)


FAILED_ATTEMPT_ID = "c2-efb58c477f12adf6"
POLLUTED_GROUP_ID = "nc-e1e2-400b9b78c2c218df"


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
    def _fixture(
        self,
    ) -> tuple[Path, Path, C2ReauthorizationBindings, dict[str, object]]:
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        validation = repo / "membind-validation"
        validation.mkdir()

        freeze = {
            "schema_version": "membind.native-characterization-freeze.v1",
            "screening": {
                "e1_e2": {
                    "block_order": [
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
                }
            },
        }
        freeze_path = validation / "artifacts/native_characterization/freeze.json"
        freeze_path.parent.mkdir(parents=True)
        freeze_path.write_bytes(_canonical(freeze))
        freeze_sha256 = _sha(freeze_path.read_bytes())

        cleanup = _with_payload_hash(
            {
                "schema_version": "membind.native-characterization-c2-cleanup.v1",
                "status": "verified_empty",
                "failed_attempt_id": FAILED_ATTEMPT_ID,
                "failed_attempt_valid": False,
                "failed_attempt_mergeable": False,
                "replacement_resume_allowed": False,
                "target_group_id": POLLUTED_GROUP_ID,
                "freeze_sha256": freeze_sha256,
                "cleanup_primitive": (
                    "graphiti.clear_data(driver,group_ids=[target_group])"
                ),
                "pre_cleanup": {"node_count": 12, "relationship_count": 7},
                "post_cleanup": {"node_count": 0, "relationship_count": 0},
                "preexisting_empty": False,
            }
        )
        cleanup_path = validation / "artifacts/native_characterization/c2_cleanup.json"
        cleanup_path.write_bytes(_canonical(cleanup) + b"\n")

        regression_path = validation / "artifacts/tdd/final_full_regression.log"
        regression_path.parent.mkdir(parents=True)
        regression_path.write_text(
            "Ran 719 tests in 12.345s\n\nOK\nexit_code: 0\n",
            encoding="ascii",
        )

        state: dict[str, object] = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_offline_only",
            "current_blocker": "c2_polluted_namespace_cleanup_pending",
            "current_action_scope": "native_characterization_offline_only",
            "authorized_live_actions": [],
            "next_allowed_action": "implement_scoped_c2_cleanup_offline",
            "live_h0_candidate_authorized": False,
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "native_characterization": (
                    "c0_c1_pass_c2_failed_attempt_invalid_cleanup_tdd_pending"
                ),
                "full_unit_regression": "pass",
            },
            "native_characterization_c2_authorization": {
                "schema_version": (
                    "membind.native-characterization-c2-authorization.v1"
                ),
                "live_authorized": True,
                "freeze_sha256": freeze_sha256,
                "workplan_id": "native-characterization-v1.1",
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
            final_full_regression_test_count=719,
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
        self.assertEqual(target["status"], source["status"])
        self.assertFalse(target["service_admin_authorized"])
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
            "c0_c1_pass_c2_replacement_authorized_from_episode_0",
        )
        self.assertTrue(
            evaluate_live_action(
                target, LiveAction.NATIVE_CHARACTERIZATION_C2
            ).allowed
        )
        for action in LiveAction:
            if action is not LiveAction.NATIVE_CHARACTERIZATION_C2:
                self.assertFalse(evaluate_live_action(target, action).allowed)

        metadata = target["native_characterization_c2_reauthorization"]
        self.assertEqual(
            metadata["schema_version"],
            "membind.native-characterization-c2-reauthorization.v1",
        )
        self.assertEqual(
            metadata["cleanup_evidence_sha256"],
            bindings.cleanup_evidence_sha256,
        )
        self.assertEqual(
            metadata["final_full_regression_sha256"],
            bindings.final_full_regression_sha256,
        )
        self.assertEqual(metadata["final_full_regression_test_count"], 719)
        self.assertEqual(metadata["failed_attempt_id"], FAILED_ATTEMPT_ID)
        self.assertEqual(metadata["polluted_group_id"], POLLUTED_GROUP_ID)
        self.assertTrue(metadata["live_authorized"])

        allowed_changes = {
            "current_blocker",
            "current_action_scope",
            "authorized_live_actions",
            "next_allowed_action",
            "stage_progress",
            "native_characterization_c2_reauthorization",
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

    def test_cleanup_helper_contract_is_the_exact_consumed_schema(self) -> None:
        self.assertEqual(
            CLEANUP_SCHEMA_VERSION,
            "membind.native-characterization-c2-cleanup.v1",
        )
        self.assertEqual(CLEANUP_FAILED_ATTEMPT_ID, FAILED_ATTEMPT_ID)
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
            "Ran 719 tests in 12.345s\n\n"
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
        freeze_path = repo / "membind-validation/artifacts/native_characterization/freeze.json"
        freeze = json.loads(freeze_path.read_text(encoding="ascii"))
        freeze["screening"]["e1_e2"]["block_order"][0]["graph_namespace"] = (
            "nc-e1e2-0000000000000001"
        )
        freeze_path.write_bytes(_canonical(freeze))
        with self.assertRaisesRegex(
            NativeCharacterizationC2ReauthorizationError, "freeze_hash_mismatch"
        ):
            reauthorize_native_characterization_c2_live_only(
                state_path,
                repo_root=repo,
                bindings=bindings,
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
        regression.write_text("Ran 719 tests in 1.000s\n\nFAILED (failures=1)\n")
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
            "Ran 719 tests in 1.000s\n\nOK\nexit_code: 1\n",
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
            **{**bindings.__dict__, "final_full_regression_test_count": 718}
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
