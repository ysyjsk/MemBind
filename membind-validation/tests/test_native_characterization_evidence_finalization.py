"""Fail-closed contracts for binding the final pre-C0 offline regression."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_evidence_finalization as finalization  # noqa: E402
import native_characterization_qualification_state as qualification  # noqa: E402
from current_state_gate import LiveAction, evaluate_live_action  # noqa: E402


FINAL_REGRESSION = (
    "artifacts/tdd/"
    "native_characterization_pre_c0_final_full_offline_green_20260811.log"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _historical_finalization_source_state() -> dict[str, object]:
    """Recover the exact qualified state consumed by evidence finalization.

    CURRENT_STATE has legitimately advanced through C0 and C2 since this
    transition ran.  Remove those later outputs and restore the historical
    no-blocker qualification point before checking the production source hash.
    """

    state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))
    for key in (
        "native_characterization_offline_evidence_finalization",
        "native_characterization_c0_authorization",
        "native_characterization_c0_completion",
        "native_characterization_c2_authorization",
        "native_characterization_c2_reauthorization",
        "native_characterization_c2_second_failure",
        "native_characterization_reference_alignment",
        "native_characterization_reference_c2_authorization",
        "native_characterization_c2_interruption",
    ):
        state.pop(key, None)
    state["status"] = "native_characterization_offline_only"
    state["current_action_scope"] = "native_characterization_offline_only"
    state["current_blocker"] = None
    state["next_allowed_action"] = qualification.TARGET_NEXT_ACTION
    state.pop("native_characterization_live_authorized", None)
    stage_progress = dict(state["stage_progress"])
    stage_progress["native_characterization"] = qualification.TARGET_PROGRESS
    state["stage_progress"] = stage_progress
    if hashlib.sha256(_canonical(state)).hexdigest() != finalization.SOURCE_STATE_SHA256:
        raise AssertionError("qualified source-state identity drift")
    return state


def _copy_fixture(root: Path) -> tuple[Path, Path]:
    validation = root / "membind-validation"
    validation.mkdir()
    state_path = validation / "CURRENT_STATE.json"
    state_path.write_bytes(_canonical(_historical_finalization_source_state()))
    shutil.copy2(
        REPO / qualification.WORKPLAN_PATH,
        root / qualification.WORKPLAN_PATH,
    )
    for relative, _digest in qualification.EVIDENCE.values():
        destination = validation / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    regression = validation / FINAL_REGRESSION
    regression.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / FINAL_REGRESSION, regression)
    return validation, state_path


class NativeCharacterizationEvidenceFinalizationTests(TestCase):
    def test_real_dry_run_appends_only_green_offline_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _validation, state_path = _copy_fixture(root)
            before = state_path.read_bytes()
            source = _historical_finalization_source_state()

            target = finalization.finalize_offline_evidence(
                state_path,
                repo_root=root,
                regression_path=FINAL_REGRESSION,
                dry_run=True,
            )

            self.assertEqual(state_path.read_bytes(), before)
        expected = deepcopy(source)
        expected.pop("native_characterization_offline_qualification")
        observed = deepcopy(target)
        observed.pop("native_characterization_offline_qualification")
        metadata = observed.pop(
            "native_characterization_offline_evidence_finalization"
        )
        self.assertEqual(observed, expected)
        self.assertEqual(
            target["native_characterization_offline_qualification"],
            source["native_characterization_offline_qualification"],
        )
        self.assertEqual(
            metadata["schema_version"],
            "membind.native-characterization-offline-evidence-finalization.v1",
        )
        self.assertEqual(
            metadata["source_state_sha256"], finalization.SOURCE_STATE_SHA256
        )
        regression = metadata["final_full_offline_regression"]
        self.assertEqual(regression["path"], FINAL_REGRESSION)
        self.assertEqual(regression["test_count"], 682)
        self.assertEqual(regression["status"], "green")
        self.assertEqual(
            regression["sha256"],
            hashlib.sha256((ROOT / FINAL_REGRESSION).read_bytes()).hexdigest(),
        )
        self.assertFalse(metadata["live_authorized"])
        for action in LiveAction:
            decision = evaluate_live_action(
                target,
                action,
                candidate_id="Q1" if action is LiveAction.H0_CANDIDATE else None,
            )
            self.assertFalse(decision.allowed, action.value)

    def test_commit_is_canonical_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _validation, state_path = _copy_fixture(root)

            first = finalization.finalize_offline_evidence(
                state_path,
                repo_root=root,
                regression_path=FINAL_REGRESSION,
                dry_run=False,
            )
            first_bytes = state_path.read_bytes()
            first_mtime = state_path.stat().st_mtime_ns
            second = finalization.finalize_offline_evidence(
                state_path,
                repo_root=root,
                regression_path=FINAL_REGRESSION,
                dry_run=False,
            )

            self.assertEqual(first, second)
            self.assertEqual(first_bytes, state_path.read_bytes())
            self.assertEqual(first_mtime, state_path.stat().st_mtime_ns)
            self.assertEqual(first_bytes, _canonical(first))
            self.assertEqual(first["authorized_live_actions"], [])
            self.assertFalse(first["live_h0_candidate_authorized"])
            self.assertFalse(first["service_admin_authorized"])

    def test_invalid_regression_inputs_fail_without_state_write(self) -> None:
        cases = {
            "not_green": "Ran 682 tests in 1.0s\nFAILED (failures=1)\n",
            "too_few": "Ran 678 tests in 1.0s\n\nOK\n",
            "duplicate_summary": (
                "Ran 682 tests in 1.0s\nRan 682 tests in 1.0s\n\nOK\n"
            ),
        }
        for label, content in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                validation, state_path = _copy_fixture(root)
                (validation / FINAL_REGRESSION).write_text(content, encoding="ascii")
                before = state_path.read_bytes()
                with self.assertRaises(
                    finalization.NativeCharacterizationEvidenceFinalizationError
                ):
                    finalization.finalize_offline_evidence(
                        state_path,
                        repo_root=root,
                        regression_path=FINAL_REGRESSION,
                        dry_run=False,
                    )
                self.assertEqual(state_path.read_bytes(), before)

    def test_path_reuse_escape_and_symlink_fail_without_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation, state_path = _copy_fixture(root)
            before = state_path.read_bytes()
            invalid_paths = (
                qualification.EVIDENCE["full_offline_regression"][0],
                "../outside.log",
                "/tmp/outside.log",
                "artifacts/not-tdd/final.log",
            )
            for path in invalid_paths:
                with self.subTest(path=path), self.assertRaises(
                    finalization.NativeCharacterizationEvidenceFinalizationError
                ):
                    finalization.finalize_offline_evidence(
                        state_path,
                        repo_root=root,
                        regression_path=path,
                        dry_run=False,
                    )
                self.assertEqual(state_path.read_bytes(), before)

            real = validation / FINAL_REGRESSION
            link = validation / "artifacts/tdd/final-link.log"
            os.symlink(real, link)
            with self.assertRaises(
                finalization.NativeCharacterizationEvidenceFinalizationError
            ):
                finalization.finalize_offline_evidence(
                    state_path,
                    repo_root=root,
                    regression_path="artifacts/tdd/final-link.log",
                    dry_run=False,
                )
            self.assertEqual(state_path.read_bytes(), before)

    def test_state_or_prior_evidence_drift_fails_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation, state_path = _copy_fixture(root)
            state = json.loads(state_path.read_text(encoding="ascii"))
            state["next_allowed_action"] = "drift"
            state_path.write_bytes(_canonical(state))
            before = state_path.read_bytes()
            with self.assertRaisesRegex(
                finalization.NativeCharacterizationEvidenceFinalizationError,
                "source_state",
            ):
                finalization.finalize_offline_evidence(
                    state_path,
                    repo_root=root,
                    regression_path=FINAL_REGRESSION,
                    dry_run=False,
                )
            self.assertEqual(state_path.read_bytes(), before)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation, state_path = _copy_fixture(root)
            prior = validation / qualification.EVIDENCE["c1_aa"][0]
            prior.write_bytes(b"drift")
            before = state_path.read_bytes()
            with self.assertRaisesRegex(
                finalization.NativeCharacterizationEvidenceFinalizationError,
                "qualification_evidence",
            ):
                finalization.finalize_offline_evidence(
                    state_path,
                    repo_root=root,
                    regression_path=FINAL_REGRESSION,
                    dry_run=False,
                )
            self.assertEqual(state_path.read_bytes(), before)


if __name__ == "__main__":
    import unittest

    unittest.main()
