"""TDD contracts for the offline-only C4/E3 live authorization transition."""

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
from native_characterization_c4_authorization import (  # noqa: E402
    C4AuthorizationBindings,
    NativeCharacterizationC4AuthorizationError,
    authorize_native_characterization_c4_live_only,
)


def _canonical(value: object, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return raw + (b"\n" if newline else b"")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sealed(value: dict[str, object]) -> dict[str, object]:
    result = deepcopy(value)
    result["payload_sha256"] = _sha(_canonical(result))
    return result


class NativeCharacterizationC4AuthorizationTests(TestCase):
    maxDiff = None

    def _fixture(self) -> tuple[Path, Path, dict[str, object], dict[str, object]]:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        validation = root / "membind-validation"
        validation.mkdir()

        run_id = "c2-17cdaabd562e9673"
        c2_verification = _sealed(
            {
                "schema_version": "membind.native-characterization-c2-verification-evidence.v1",
                "status": "verified",
                "run_id": run_id,
                "result": {
                    "status": "verified",
                    "run_id": run_id,
                    "manifest_sha256": "1" * 64,
                    "checkpoint_sha256": "2" * 64,
                    "e1_breakdown_sha256": "3" * 64,
                    "top_level_e1_breakdown_sha256": "3" * 64,
                },
            }
        )
        verification_path = validation / f"artifacts/diagnostics/native_characterization_{run_id}_verification.json"
        verification_path.parent.mkdir(parents=True)
        verification_path.write_bytes(_canonical(c2_verification))

        dependency_map = _sealed(
            {
                "schema_version": "membind.native-characterization-dependency-map.v1",
                "status": "complete",
                "stage": "C3/E2",
                "run_id": run_id,
            }
        )
        dependency_path = validation / "artifacts/native_characterization/dependency_map.json"
        dependency_path.parent.mkdir(parents=True, exist_ok=True)
        dependency_path.write_bytes(_canonical(dependency_map, newline=True))
        e2 = _sealed(
            {
                "schema_version": "membind.native-characterization-e2-opportunity.v1",
                "status": "complete",
                "stage": "C3/E2",
                "run_id": run_id,
            }
        )
        e2_path = validation / "artifacts/native_characterization/e2_dependency_opportunity.json"
        e2_path.write_bytes(_canonical(e2, newline=True))

        analyzer_path = validation / "src/native_characterization_c3.py"
        analyzer_path.parent.mkdir(parents=True)
        analyzer_path.write_text("# C3 analyzer\n", encoding="ascii")
        c3_log_path = validation / "artifacts/tdd/c3_completion_green.log"
        c3_log_path.parent.mkdir(parents=True)
        c3_log_path.write_text("Ran 12 tests\nOK\n", encoding="ascii")

        freeze = _sealed(
            {
                "schema_version": "membind.native-characterization-freeze.v1",
                "run_id": "native-characterization-freeze-reference-aligned-64k",
                "runtime_identities": {
                    "construction": {
                        "vllm_version": "0.26.0",
                        "served_model_id": "qwen3-32b-fp8",
                        "max_model_len": 65536,
                        "rope_type": "yarn",
                        "yarn_factor": 2.0,
                        "original_max_position_embeddings": 32768,
                        "rope_theta": 1000000,
                    },
                    "embedding": {"served_model_id": "qwen3-embedding-0.6b"},
                },
                "state_transition": {
                    "execution_envelope_updated": True,
                    "live_authorized": False,
                },
            }
        )
        freeze_path = validation / "artifacts/native_characterization/freeze_reference_aligned_64k.json"
        freeze_path.write_bytes(_canonical(freeze, newline=True))

        episode_ids = [f"07741c45:{index}" for index in range(49)]
        block_schedules: list[dict[str, object]] = []
        for block_index, method in enumerate(
            ["Native-Sync"] * 5 + ["Native-Async-Serial"] * 5
        ):
            load = [0.5, 0.8, 1.0, 1.2, 1.5][block_index % 5]
            interval = [100, 80, 60, 50, 40][block_index % 5]
            block_schedules.append(
                {
                    "block_index": block_index,
                    "method": method,
                    "normalized_offered_load": load,
                    "graph_namespace": f"nc-e3-{block_index:016x}",
                    "interarrival_ns": interval,
                    "absolute_arrival_offsets_ns": [
                        index * interval for index in range(49)
                    ],
                }
            )
        schedule = _sealed(
            {
                "schema_version": "membind.native-characterization-c4-schedule-dry-run.v1",
                "status": "dry_run",
                "stage": "C4/E3_OFFLINE_SCHEDULE",
                "run_id": run_id,
                "history_id": "07741c45",
                "schedule_semantics": "controlled_deterministic_absolute_open_loop_replay",
                "episode_ids": episode_ids,
                "load_schedules": [
                    {
                        "normalized_offered_load": load,
                        "interarrival_ns": interval,
                        "absolute_arrival_offsets_ns": [index * interval for index in range(49)],
                    }
                    for load, interval in zip([0.5, 0.8, 1.0, 1.2, 1.5], [100, 80, 60, 50, 40])
                ],
                "block_schedules": block_schedules,
                "provenance": {
                    "freeze_path": "artifacts/native_characterization/freeze_reference_aligned_64k.json",
                    "freeze_sha256": _sha(freeze_path.read_bytes()),
                    "freeze_payload_sha256": freeze["payload_sha256"],
                    "c2_verification": {
                        "status": "verified",
                        "run_id": run_id,
                        "manifest_sha256": "1" * 64,
                        "checkpoint_sha256": "2" * 64,
                        "e1_breakdown_sha256": "3" * 64,
                        "top_level_e1_breakdown_sha256": "3" * 64,
                    },
                },
            }
        )
        schedule_path = validation / "artifacts/diagnostics/c4_schedule.json"
        schedule_path.write_text(
            json.dumps(schedule, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="ascii",
        )

        c4_source = validation / "src/native_characterization_c4.py"
        c4_source.write_text("# C4 harness\n", encoding="ascii")
        c4_test = validation / "tests/test_native_characterization_c4.py"
        c4_test.parent.mkdir()
        c4_test.write_text("# C4 tests\n", encoding="ascii")
        c4_log = validation / "artifacts/tdd/c4_green.log"
        c4_log.write_text("Ran 8 tests\nOK\n", encoding="ascii")

        c2_completion = {
            "schema_version": "membind.native-characterization-c2-completion.v1",
            "status": "verified",
            "run_id": run_id,
            "verification_path": str(verification_path.relative_to(validation)),
            "verification_sha256": _sha(verification_path.read_bytes()),
            "verification_payload_sha256": c2_verification["payload_sha256"],
            "manifest_sha256": "1" * 64,
            "checkpoint_sha256": "2" * 64,
            "e1_breakdown_sha256": "3" * 64,
            "top_level_e1_breakdown_sha256": "3" * 64,
            "freeze_sha256": _sha(freeze_path.read_bytes()),
            "grant_consumed": True,
            "live_authorized": False,
        }
        c3_completion = {
            "schema_version": "membind.native-characterization-c3-completion.v1",
            "status": "complete",
            "run_id": run_id,
            "dependency_map_path": str(dependency_path.relative_to(validation)),
            "dependency_map_sha256": _sha(dependency_path.read_bytes()),
            "dependency_map_payload_sha256": dependency_map["payload_sha256"],
            "e2_path": str(e2_path.relative_to(validation)),
            "e2_sha256": _sha(e2_path.read_bytes()),
            "e2_payload_sha256": e2["payload_sha256"],
            "analyzer_source_path": str(analyzer_path.relative_to(validation)),
            "analyzer_source_sha256": _sha(analyzer_path.read_bytes()),
            "focused_log_path": str(c3_log_path.relative_to(validation)),
            "focused_log_sha256": _sha(c3_log_path.read_bytes()),
            "focused_test_count": 12,
            "episode_count": 188,
            "history_count": 4,
            "live_authorized": False,
        }
        state = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_c4_offline_only",
            "current_action_scope": "native_characterization_c4_offline_only",
            "current_blocker": None,
            "next_allowed_action": "build_native_characterization_e3_harness_offline",
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "native_characterization_live_authorized": False,
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "native_characterization": "c2_c3_complete_c4_offline_tdd_pending"
            },
            "native_characterization_c2_completion": c2_completion,
            "native_characterization_c3_completion": c3_completion,
        }
        state_path = validation / "CURRENT_STATE.json"
        state_path.write_bytes(_canonical(state))
        bindings: dict[str, object] = {
            "source_state_sha256": _sha(state_path.read_bytes()),
            "schedule_relative_path": str(schedule_path.relative_to(validation)),
            "schedule_sha256": _sha(schedule_path.read_bytes()),
            "schedule_payload_sha256": schedule["payload_sha256"],
            "freeze_relative_path": str(freeze_path.relative_to(validation)),
            "freeze_sha256": _sha(freeze_path.read_bytes()),
            "freeze_payload_sha256": freeze["payload_sha256"],
            "c4_source_relative_path": str(c4_source.relative_to(validation)),
            "c4_source_sha256": _sha(c4_source.read_bytes()),
            "c4_test_relative_path": str(c4_test.relative_to(validation)),
            "c4_test_sha256": _sha(c4_test.read_bytes()),
            "c4_green_log_relative_path": str(c4_log.relative_to(validation)),
            "c4_green_log_sha256": _sha(c4_log.read_bytes()),
            "c4_focused_test_count": 8,
            "operator_authorized": True,
        }
        return root, state_path, bindings, state

    @staticmethod
    def _bindings(values: dict[str, object]) -> C4AuthorizationBindings:
        return C4AuthorizationBindings(**values)

    def test_operator_authorization_must_be_explicit_true(self) -> None:
        root, state_path, values, _ = self._fixture()
        values["operator_authorized"] = False
        with self.assertRaisesRegex(
            NativeCharacterizationC4AuthorizationError,
            "operator_authorization_required",
        ):
            authorize_native_characterization_c4_live_only(
                state_path,
                repo_root=root,
                bindings=self._bindings(values),
                dry_run=True,
            )

    def test_dry_run_authorizes_only_c4_and_binds_all_evidence(self) -> None:
        root, state_path, values, source = self._fixture()
        before = state_path.read_bytes()
        target = authorize_native_characterization_c4_live_only(
            state_path,
            repo_root=root,
            bindings=self._bindings(values),
            dry_run=True,
        )

        self.assertEqual(target["status"], "native_characterization_c4_offline_only")
        self.assertEqual(target["current_action_scope"], "native_characterization_c4_live_only")
        self.assertEqual(target["next_allowed_action"], "run_native_characterization_c4")
        self.assertEqual(target["authorized_live_actions"], ["native_characterization_c4"])
        self.assertTrue(target["native_characterization_live_authorized"])
        self.assertFalse(target["service_admin_authorized"])
        self.assertTrue(
            evaluate_live_action(target, LiveAction.NATIVE_CHARACTERIZATION_C4).allowed
        )
        for action in LiveAction:
            if action is not LiveAction.NATIVE_CHARACTERIZATION_C4:
                self.assertFalse(evaluate_live_action(target, action).allowed)

        metadata = target["native_characterization_c4_authorization"]
        self.assertEqual(metadata["schedule_sha256"], values["schedule_sha256"])
        self.assertEqual(metadata["schedule_payload_sha256"], values["schedule_payload_sha256"])
        self.assertEqual(metadata["freeze_sha256"], values["freeze_sha256"])
        self.assertEqual(metadata["c4_source_sha256"], values["c4_source_sha256"])
        self.assertEqual(metadata["c4_test_sha256"], values["c4_test_sha256"])
        self.assertEqual(metadata["c2_evidence"]["run_id"], "c2-17cdaabd562e9673")
        self.assertEqual(metadata["c3_evidence"]["status"], "complete")
        self.assertIs(metadata["operator_authorization_input"], True)
        self.assertTrue(metadata["live_authorized"])
        self.assertNotIn("api_key", json.dumps(metadata).casefold())
        self.assertEqual(
            set(target) - set(source), {"native_characterization_c4_authorization"}
        )
        self.assertEqual(state_path.read_bytes(), before)

    def test_exact_source_shape_and_deny_all_are_required(self) -> None:
        root, state_path, values, state = self._fixture()
        for key, replacement in (
            ("status", "native_characterization_c3_offline_only"),
            ("next_allowed_action", "run_native_characterization_c4"),
            ("authorized_live_actions", ["native_characterization_c2"]),
            ("native_characterization_live_authorized", True),
        ):
            with self.subTest(key=key):
                changed = deepcopy(state)
                changed[key] = replacement
                state_path.write_bytes(_canonical(changed))
                changed_values = dict(values, source_state_sha256=_sha(state_path.read_bytes()))
                with self.assertRaisesRegex(
                    NativeCharacterizationC4AuthorizationError,
                    "source_state_not_exact_c4_offline",
                ):
                    authorize_native_characterization_c4_live_only(
                        state_path,
                        repo_root=root,
                        bindings=self._bindings(changed_values),
                        dry_run=True,
                    )
                state_path.write_bytes(_canonical(state))

    def test_schedule_freeze_and_c3_artifact_drift_fail_without_write(self) -> None:
        mutations = (
            ("schedule_relative_path", lambda path: path.write_text("{}", encoding="ascii"), "schedule_hash_mismatch"),
            ("freeze_relative_path", lambda path: path.write_text("{}", encoding="ascii"), "freeze_hash_mismatch"),
            ("c3_dependency", lambda path: path.write_text("{}", encoding="ascii"), "c3_dependency_map_hash_mismatch"),
        )
        for selector, mutate, reason in mutations:
            with self.subTest(selector=selector):
                root, state_path, values, state = self._fixture()
                validation = root / "membind-validation"
                if selector == "c3_dependency":
                    relative = state["native_characterization_c3_completion"]["dependency_map_path"]
                else:
                    relative = values[selector]
                mutate(validation / str(relative))
                before = state_path.read_bytes()
                with self.assertRaisesRegex(
                    NativeCharacterizationC4AuthorizationError,
                    reason,
                ):
                    authorize_native_characterization_c4_live_only(
                        state_path,
                        repo_root=root,
                        bindings=self._bindings(values),
                        dry_run=False,
                    )
                self.assertEqual(state_path.read_bytes(), before)

    def test_wrong_64k_identity_fails_closed(self) -> None:
        root, state_path, values, _ = self._fixture()
        freeze_path = root / "membind-validation" / str(values["freeze_relative_path"])
        freeze = json.loads(freeze_path.read_bytes())
        freeze["runtime_identities"]["construction"]["max_model_len"] = 40960
        freeze.pop("payload_sha256")
        freeze = _sealed(freeze)
        freeze_path.write_bytes(_canonical(freeze, newline=True))
        values.update(
            freeze_sha256=_sha(freeze_path.read_bytes()),
            freeze_payload_sha256=freeze["payload_sha256"],
        )
        with self.assertRaisesRegex(
            NativeCharacterizationC4AuthorizationError,
            "freeze_64k_contract_mismatch",
        ):
            authorize_native_characterization_c4_live_only(
                state_path,
                repo_root=root,
                bindings=self._bindings(values),
                dry_run=True,
            )

    def test_commit_is_atomic_idempotent_and_target_drift_fails_closed(self) -> None:
        root, state_path, values, _ = self._fixture()
        first = authorize_native_characterization_c4_live_only(
            state_path,
            repo_root=root,
            bindings=self._bindings(values),
            dry_run=False,
        )
        committed = state_path.read_bytes()
        second_values = dict(values, source_state_sha256=_sha(committed))
        second = authorize_native_characterization_c4_live_only(
            state_path,
            repo_root=root,
            bindings=self._bindings(second_values),
            dry_run=False,
        )
        self.assertEqual(first, second)
        self.assertEqual(state_path.read_bytes(), committed)

        drifted = json.loads(committed)
        drifted["authorized_live_actions"] = ["native_characterization_c4", "service_admin"]
        state_path.write_bytes(_canonical(drifted))
        drift_values = dict(values, source_state_sha256=_sha(state_path.read_bytes()))
        with self.assertRaisesRegex(
            NativeCharacterizationC4AuthorizationError,
            "target_state_drift",
        ):
            authorize_native_characterization_c4_live_only(
                state_path,
                repo_root=root,
                bindings=self._bindings(drift_values),
                dry_run=True,
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
