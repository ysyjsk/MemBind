"""Offline TDD for the one-way C4-result to C5-only authority transition."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module():
    return importlib.import_module("native_characterization_c5_authorization")


class NativeCharacterizationC5AuthorizationTests(TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        validation = root / "membind-validation"
        (validation / "artifacts/native_characterization/runs/c4-source").mkdir(
            parents=True
        )
        freeze = validation / "artifacts/native_characterization/freeze_reference_aligned_64k.json"
        workplan = root / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
        c4_summary = validation / "artifacts/native_characterization/runs/c4-source/e3_sync_async.json"
        freeze.write_bytes((ROOT / "artifacts/native_characterization/freeze_reference_aligned_64k.json").read_bytes())
        workplan.write_bytes((ROOT.parent / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md").read_bytes())
        c4_summary.write_text(
            json.dumps(
                {
                    "schema_version": "membind.native-characterization-e3-sync-async.v1",
                    "status": "complete",
                    "run_id": "c4-source",
                    "block_count": 10,
                    "episode_count": 490,
                    "payload_sha256": "a" * 64,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="ascii",
        )
        state = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "current_action_scope": "native_characterization_c4_live_only",
            "status": "native_characterization_c4_live_only",
            "authorized_live_actions": ["native_characterization_c4"],
            "next_allowed_action": "run_native_characterization_c4",
            "native_characterization_live_authorized": True,
            "live_h0_candidate_authorized": False,
            "service_admin_authorized": False,
        }
        state_path = validation / "CURRENT_STATE.json"
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="ascii")
        return validation, state_path, c4_summary

    def test_transition_binds_exact_frozen_c5_matrix_and_grants_only_c5(self) -> None:
        auth = _module()
        with tempfile.TemporaryDirectory() as temporary:
            validation, state_path, c4_summary = self._fixture(Path(temporary))
            result = auth.authorize_c5(
                validation_root=validation,
                state_path=state_path,
                c4_summary_path=c4_summary,
                c4_summary_sha256=_sha(c4_summary),
            )
            state = json.loads(state_path.read_text("ascii"))

        self.assertEqual(result["status"], "authorized")
        self.assertEqual(state["current_action_scope"], "native_characterization_c5_live_only")
        self.assertEqual(state["authorized_live_actions"], ["native_characterization_c5"])
        self.assertEqual(state["next_allowed_action"], "run_native_characterization_c5")
        evidence = state["native_characterization_c5_authorization"]
        self.assertEqual(evidence["history_id"], "07741c45")
        self.assertEqual(evidence["episode_count"], 49)
        self.assertEqual(evidence["concurrency_grid"], [1, 2, 4, 8])
        self.assertEqual(
            evidence["graph_namespaces"],
            [
                "nc-e4-1434fcb947df5c3d",
                "nc-e4-b352061ffa0d4b21",
                "nc-e4-c15538d1fe2801cb",
                "nc-e4-2a427029b1a8b2ac",
            ],
        )
        self.assertEqual(evidence["screening_pass_count"], 1)

    def test_c4_hash_drift_fails_before_state_write(self) -> None:
        auth = _module()
        with tempfile.TemporaryDirectory() as temporary:
            validation, state_path, c4_summary = self._fixture(Path(temporary))
            before = state_path.read_bytes()
            with self.assertRaisesRegex(auth.C5AuthorizationError, "c4_summary_hash_mismatch"):
                auth.authorize_c5(
                    validation_root=validation,
                    state_path=state_path,
                    c4_summary_path=c4_summary,
                    c4_summary_sha256="f" * 64,
                )
            self.assertEqual(state_path.read_bytes(), before)

    def test_transition_is_idempotent_but_cannot_widen_authority(self) -> None:
        auth = _module()
        with tempfile.TemporaryDirectory() as temporary:
            validation, state_path, c4_summary = self._fixture(Path(temporary))
            kwargs = {
                "validation_root": validation,
                "state_path": state_path,
                "c4_summary_path": c4_summary,
                "c4_summary_sha256": _sha(c4_summary),
            }
            first = auth.authorize_c5(**kwargs)
            first_bytes = state_path.read_bytes()
            second = auth.authorize_c5(**kwargs)
            self.assertEqual(second, first)
            self.assertEqual(state_path.read_bytes(), first_bytes)
            state = json.loads(first_bytes)
            state["authorized_live_actions"].append("service_admin")
            state_path.write_text(json.dumps(state, sort_keys=True), encoding="ascii")
            with self.assertRaisesRegex(auth.C5AuthorizationError, "source_state_not_exact"):
                auth.authorize_c5(**kwargs)


if __name__ == "__main__":
    import unittest

    unittest.main()
