"""Offline state advancement after C1 qualification and C0 dry-run."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_qualification_state as progress  # noqa: E402
from current_state_gate import LiveAction, evaluate_live_action  # noqa: E402


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _qualified_state_bytes() -> bytes:
    """Recover the exact historical input state for the C1 qualification transition."""

    state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))
    # These are outputs of later Native-characterization transitions.  The C1
    # fixture must represent the earlier transition source, not today's
    # cleanup-pending execution state.
    for key in (
        "native_characterization_offline_qualification",
        "native_characterization_offline_evidence_finalization",
        "native_characterization_c0_authorization",
        "native_characterization_c0_completion",
        "native_characterization_c2_authorization",
        "native_characterization_c2_reauthorization",
        "native_characterization_c2_second_failure",
    ):
        state.pop(key, None)
    state["current_blocker"] = None
    state["next_allowed_action"] = "implement_c1_instrumentation_offline"
    stage_progress = dict(state["stage_progress"])
    stage_progress["native_characterization"] = "c1_instrumentation_tdd_pending"
    state["stage_progress"] = stage_progress
    encoded = _canonical(state)
    if hashlib.sha256(encoded).hexdigest() != progress.BASE_STATE_SHA256:
        raise AssertionError("historical C1 qualification source fixture drifted")
    return encoded


def _copy_evidence(target_repo: Path, target_validation: Path) -> None:
    shutil.copy2(
        REPO / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md",
        target_repo / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md",
    )
    for relative, _digest in progress.EVIDENCE.values():
        destination = target_validation / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)


class NativeCharacterizationQualificationStateTests(TestCase):
    def test_real_dry_run_builds_exact_offline_waiting_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation = root / "membind-validation"
            validation.mkdir()
            state_path = validation / "CURRENT_STATE.json"
            state_path.write_bytes(_qualified_state_bytes())
            _copy_evidence(root, validation)
            before = state_path.read_bytes()
            target = progress.advance_native_characterization_offline(
                state_path, repo_root=root, dry_run=True
            )
            self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(target["current_stage"], "NATIVE_CHARACTERIZATION")
        self.assertEqual(target["status"], "native_characterization_offline_only")
        self.assertEqual(target["authorized_live_actions"], [])
        self.assertFalse(target["live_h0_candidate_authorized"])
        self.assertEqual(
            target["next_allowed_action"],
            "operator_start_vllm_then_authorize_c0",
        )
        self.assertEqual(
            target["stage_progress"]["native_characterization"],
            "c1_qualified_c0_dry_run_pass_waiting_for_services",
        )
        for action in LiveAction:
            decision = evaluate_live_action(
                target,
                action,
                candidate_id="Q1" if action is LiveAction.H0_CANDIDATE else None,
            )
            self.assertFalse(decision.allowed, action.value)

    def test_qualification_metadata_binds_green_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation = root / "membind-validation"
            validation.mkdir()
            state_path = validation / "CURRENT_STATE.json"
            state_path.write_bytes(_qualified_state_bytes())
            _copy_evidence(root, validation)
            target = progress.advance_native_characterization_offline(
                state_path, repo_root=root, dry_run=True
            )
        evidence = target["native_characterization_offline_qualification"]
        self.assertEqual(evidence["instrumentation_contract_status"], "qualified")
        self.assertEqual(evidence["c1_aa"]["classification"], "clean_pass")
        self.assertTrue(evidence["c1_aa"]["semantic_parity"])
        self.assertEqual(evidence["focused_regression"]["test_count"], 154)
        self.assertEqual(evidence["full_offline_regression"]["test_count"], 675)
        self.assertFalse(evidence["c0_dry_run"]["live_request_performed"])
        self.assertFalse(evidence["live_authorized"])
        for section in (
            "c1_lifecycle",
            "c1_aa",
            "focused_regression",
            "full_offline_regression",
            "c0_dry_run",
            "freeze",
            "phase_map",
        ):
            path = ROOT / evidence[section]["path"]
            self.assertEqual(
                evidence[section]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
            )
        real = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))
        self.assertEqual(
            real["native_characterization_offline_qualification"], evidence
        )

    def test_commit_is_canonical_idempotent_and_does_not_authorize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation = root / "membind-validation"
            validation.mkdir()
            state_path = validation / "CURRENT_STATE.json"
            state_path.write_bytes(_qualified_state_bytes())
            _copy_evidence(root, validation)

            first = progress.advance_native_characterization_offline(
                state_path, repo_root=root, dry_run=False
            )
            before = state_path.read_bytes()
            before_mtime = state_path.stat().st_mtime_ns
            second = progress.advance_native_characterization_offline(
                state_path, repo_root=root, dry_run=False
            )
            self.assertEqual(first, second)
            self.assertEqual(before, state_path.read_bytes())
            self.assertEqual(before_mtime, state_path.stat().st_mtime_ns)
            self.assertEqual(before, _canonical(first))
            self.assertEqual(first["authorized_live_actions"], [])

    def test_source_or_evidence_drift_fails_without_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation = root / "membind-validation"
            validation.mkdir()
            state_path = validation / "CURRENT_STATE.json"
            state_path.write_bytes(_qualified_state_bytes())
            _copy_evidence(root, validation)
            evidence_path = validation / progress.EVIDENCE["c1_aa"][0]
            evidence_path.write_bytes(b"drift")
            before = state_path.read_bytes()
            with self.assertRaisesRegex(
                progress.NativeCharacterizationQualificationStateError,
                "evidence_hash_mismatch",
            ):
                progress.advance_native_characterization_offline(
                    state_path, repo_root=root, dry_run=False
                )
            self.assertEqual(state_path.read_bytes(), before)


if __name__ == "__main__":
    import unittest

    unittest.main()
