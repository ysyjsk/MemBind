"""C3/E2 artifact binding and offline C4 transition contracts."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_c3_completion import (  # noqa: E402
    C3CompletionBindings,
    NativeCharacterizationC3CompletionError,
    complete_native_characterization_c3,
)


RUN_ID = "c2-17cdaabd562e9673"
MANIFEST_SHA = "1" * 64
CHECKPOINT_SHA = "2" * 64
E1_SHA = "3" * 64
ANALYZER_BYTES = b'"""fixture analyzer"""\n'
FOCUSED_LOG_BYTES = b"........\nRan 8 tests\nOK\n"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _seal(value: dict[str, object]) -> dict[str, object]:
    result = deepcopy(value)
    result["payload_sha256"] = _sha(_canonical(result))
    return result


class NativeCharacterizationC3CompletionTests(TestCase):
    maxDiff = None

    def _source_state(self) -> dict[str, object]:
        return {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_c3_offline_only",
            "current_action_scope": "native_characterization_c3_offline_only",
            "current_blocker": None,
            "next_allowed_action": "build_native_characterization_dependency_map_offline",
            "authorized_live_actions": [],
            "native_characterization_live_authorized": False,
            "live_h0_candidate_authorized": False,
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "native_characterization": "c2_verified_c3_dependency_audit_pending",
                "historical": "preserved",
            },
            "native_characterization_c2_completion": {
                "status": "verified",
                "run_id": RUN_ID,
                "manifest_sha256": MANIFEST_SHA,
                "checkpoint_sha256": CHECKPOINT_SHA,
                "e1_breakdown_sha256": E1_SHA,
                "top_level_e1_breakdown_sha256": E1_SHA,
                "episode_count": 188,
                "block_count": 4,
                "grant_consumed": True,
                "live_authorized": False,
            },
            "historical_evidence": {"sha256": "4" * 64},
        }

    def _fixture(self, root: Path) -> tuple[Path, C3CompletionBindings]:
        validation = root / "membind-validation"
        artifact_root = validation / "artifacts/native_characterization"
        tdd_root = validation / "artifacts/tdd"
        source_root = validation / "src"
        artifact_root.mkdir(parents=True)
        tdd_root.mkdir(parents=True)
        source_root.mkdir(parents=True)

        analyzer = source_root / "native_characterization_c3.py"
        analyzer.write_bytes(ANALYZER_BYTES)
        analyzer_sha = _sha(ANALYZER_BYTES)
        c2_verification = {
            "status": "verified",
            "run_id": RUN_ID,
            "manifest_sha256": MANIFEST_SHA,
            "checkpoint_sha256": CHECKPOINT_SHA,
            "e1_breakdown_sha256": E1_SHA,
            "top_level_e1_breakdown_sha256": E1_SHA,
        }
        dependency_map = _seal(
            {
                "schema_version": "membind.native-characterization-dependency-map.v1",
                "status": "complete",
                "stage": "C3/E2",
                "run_id": RUN_ID,
                "phase_rules": [{"phase": str(index)} for index in range(8)],
                "provenance": {
                    "builder_source_sha256": analyzer_sha,
                    "c2_verification": c2_verification,
                },
            }
        )
        dependency_path = artifact_root / "dependency_map.json"
        dependency_path.write_bytes(_canonical(dependency_map) + b"\n")
        e2 = _seal(
            {
                "schema_version": "membind.native-characterization-e2-opportunity.v1",
                "status": "complete",
                "stage": "C3/E2",
                "run_id": RUN_ID,
                "aggregate": {
                    "episode_count": 188,
                    "T_total_ns": 1000,
                    "p_L": 0.2,
                    "p_U": 0.2,
                    "speedup_bounds": {},
                },
                "intervals": [{} for _ in range(188 * 8)],
                "histories": [{"history_id": str(index)} for index in range(4)],
                "provenance": {
                    "analyzer_source_sha256": analyzer_sha,
                    "dependency_map_payload_sha256": dependency_map["payload_sha256"],
                    "c2_verification": c2_verification,
                },
            }
        )
        e2_path = artifact_root / "e2_dependency_opportunity.json"
        e2_path.write_bytes(_canonical(e2) + b"\n")
        focused_log = tdd_root / "native_characterization_c3_focused_green.log"
        focused_log.write_bytes(FOCUSED_LOG_BYTES)

        state_path = validation / "CURRENT_STATE.json"
        state_path.write_bytes(_canonical(self._source_state()))
        return state_path, C3CompletionBindings(
            source_state_sha256=_sha(state_path.read_bytes()),
            dependency_map_relative_path=(
                "artifacts/native_characterization/dependency_map.json"
            ),
            dependency_map_sha256=_sha(dependency_path.read_bytes()),
            dependency_map_payload_sha256=str(dependency_map["payload_sha256"]),
            e2_relative_path=(
                "artifacts/native_characterization/e2_dependency_opportunity.json"
            ),
            e2_sha256=_sha(e2_path.read_bytes()),
            e2_payload_sha256=str(e2["payload_sha256"]),
            focused_log_relative_path=(
                "artifacts/tdd/native_characterization_c3_focused_green.log"
            ),
            focused_log_sha256=_sha(FOCUSED_LOG_BYTES),
            focused_test_count=8,
        )

    def test_dry_run_binds_artifacts_and_enters_c4_offline_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path, bindings = self._fixture(root)
            source = state_path.read_bytes()

            target = complete_native_characterization_c3(
                state_path,
                repo_root=root,
                bindings=bindings,
                dry_run=True,
            )

            self.assertEqual(state_path.read_bytes(), source)
            self.assertEqual(target["status"], "native_characterization_c4_offline_only")
            self.assertEqual(
                target["current_action_scope"],
                "native_characterization_c4_offline_only",
            )
            self.assertEqual(target["authorized_live_actions"], [])
            self.assertFalse(target["native_characterization_live_authorized"])
            self.assertEqual(
                target["next_allowed_action"],
                "build_native_characterization_e3_harness_offline",
            )
            self.assertEqual(
                target["stage_progress"]["native_characterization"],
                "c2_c3_complete_c4_offline_tdd_pending",
            )
            completion = target["native_characterization_c3_completion"]
            self.assertEqual(completion["status"], "complete")
            self.assertEqual(completion["run_id"], RUN_ID)
            self.assertEqual(completion["episode_count"], 188)
            self.assertEqual(completion["history_count"], 4)
            self.assertEqual(completion["interval_count"], 188 * 8)
            self.assertEqual(completion["focused_test_count"], 8)
            self.assertEqual(
                target["historical_evidence"],
                self._source_state()["historical_evidence"],
            )

    def test_write_is_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path, bindings = self._fixture(root)

            first = complete_native_characterization_c3(
                state_path,
                repo_root=root,
                bindings=bindings,
                dry_run=False,
            )
            first_bytes = state_path.read_bytes()
            second = complete_native_characterization_c3(
                state_path,
                repo_root=root,
                bindings=bindings,
                dry_run=False,
            )

            self.assertEqual(first, second)
            self.assertEqual(state_path.read_bytes(), first_bytes)

    def test_artifact_drift_fails_closed_without_state_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path, bindings = self._fixture(root)
            source = state_path.read_bytes()
            dependency_path = (
                state_path.parent / bindings.dependency_map_relative_path
            )
            dependency_path.write_bytes(dependency_path.read_bytes() + b"\n")

            with self.assertRaises(NativeCharacterizationC3CompletionError):
                complete_native_characterization_c3(
                    state_path,
                    repo_root=root,
                    bindings=bindings,
                    dry_run=False,
                )

            self.assertEqual(state_path.read_bytes(), source)

    def test_source_state_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path, bindings = self._fixture(root)
            state = json.loads(state_path.read_text("ascii"))
            state["current_blocker"] = "unexpected"
            state_path.write_bytes(_canonical(state))

            with self.assertRaises(NativeCharacterizationC3CompletionError):
                complete_native_characterization_c3(
                    state_path,
                    repo_root=root,
                    bindings=bindings,
                    dry_run=False,
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
