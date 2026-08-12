"""C2 verification persistence and offline C3 transition contracts."""

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
from native_characterization_c2_completion import (  # noqa: E402
    C2CompletionBindings,
    NativeCharacterizationC2CompletionError,
    complete_native_characterization_c2,
    persist_c2_verification,
)
from tests import test_native_characterization_c2_verify as verifier_fixture  # noqa: E402


RUN_ID = verifier_fixture.RUN_ID


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class NativeCharacterizationC2CompletionTests(TestCase):
    maxDiff = None

    def _source_state(self) -> dict[str, object]:
        return {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_c2_live_only",
            "current_action_scope": "native_characterization_c2_live_only",
            "current_blocker": None,
            "next_allowed_action": "run_native_characterization_c2",
            "authorized_live_actions": ["native_characterization_c2"],
            "native_characterization_live_authorized": True,
            "live_h0_candidate_authorized": False,
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "native_characterization": (
                    "c0_c1_pass_reference_aligned_c2_authorized_from_episode_0"
                ),
                "historical": "preserved",
            },
            "native_characterization_reference_alignment": {
                "status": "c2_live_authorized",
                "fresh_c2": {
                    "live_authorized": True,
                    "semantic_attempts_remaining": 1,
                    "start_source_sequence": 0,
                    "resume_allowed": False,
                    "prefix_merge_allowed": False,
                },
            },
            "native_characterization_reference_c2_authorization": {
                "live_authorized": True,
                "replacement_start_source_sequence": 0,
                "replacement_resume_allowed": False,
            },
            "native_characterization_c2_authorization": {
                "live_authorized": True,
            },
            "native_characterization_c2_reauthorization": {
                "live_authorized": True,
            },
            "historical_evidence": {"sha256": "1" * 64},
        }

    def _fixture(self, root: Path) -> tuple[Path, C2CompletionBindings]:
        validation = root / "membind-validation"
        verifier_fixture._build_valid_run(validation)
        verifier_source = validation / "src/native_characterization_c2_verify.py"
        verifier_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "src/native_characterization_c2_verify.py", verifier_source)

        state = self._source_state()
        state_path = validation / "CURRENT_STATE.json"
        state_path.write_bytes(_canonical(state))
        persisted = persist_c2_verification(validation, RUN_ID)
        return state_path, C2CompletionBindings(
            source_state_sha256=_sha(state_path.read_bytes()),
            verification_relative_path=persisted["relative_path"],
            verification_sha256=persisted["sha256"],
            verification_payload_sha256=persisted["payload_sha256"],
        )

    def test_verification_is_sealed_persisted_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path, bindings = self._fixture(root)
            validation = state_path.parent
            artifact = validation / bindings.verification_relative_path
            before_run = {
                path.relative_to(validation).as_posix(): path.read_bytes()
                for path in (
                    validation
                    / "artifacts/native_characterization/runs"
                    / RUN_ID
                ).rglob("*")
                if path.is_file()
            }

            repeated = persist_c2_verification(validation, RUN_ID)
            payload = json.loads(artifact.read_text("ascii"))
            after_run = {
                path.relative_to(validation).as_posix(): path.read_bytes()
                for path in (
                    validation
                    / "artifacts/native_characterization/runs"
                    / RUN_ID
                ).rglob("*")
                if path.is_file()
            }

            self.assertEqual(repeated["sha256"], bindings.verification_sha256)
            self.assertEqual(payload["status"], "verified")
            self.assertEqual(payload["run_id"], RUN_ID)
            self.assertEqual(payload["result"]["status"], "verified")
            self.assertEqual(payload["payload_sha256"], bindings.verification_payload_sha256)
            self.assertEqual(before_run, after_run)

    def test_transition_revokes_c2_and_enters_c3_offline_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path, bindings = self._fixture(root)
            source = json.loads(state_path.read_text("ascii"))
            target = complete_native_characterization_c2(
                state_path,
                repo_root=root,
                bindings=bindings,
                dry_run=True,
            )

            self.assertEqual(source["historical_evidence"], target["historical_evidence"])
            self.assertEqual(target["status"], "native_characterization_c3_offline_only")
            self.assertEqual(
                target["current_action_scope"],
                "native_characterization_c3_offline_only",
            )
            self.assertEqual(target["authorized_live_actions"], [])
            self.assertFalse(target["native_characterization_live_authorized"])
            self.assertEqual(
                target["next_allowed_action"],
                "build_native_characterization_dependency_map_offline",
            )
            completion = target["native_characterization_c2_completion"]
            self.assertEqual(completion["run_id"], RUN_ID)
            self.assertEqual(completion["status"], "verified")
            self.assertEqual(completion["episode_count"], 1)
            self.assertEqual(completion["block_count"], 1)
            self.assertTrue(completion["grant_consumed"])
            self.assertFalse(
                target["native_characterization_reference_alignment"]
                ["fresh_c2"]["live_authorized"]
            )
            self.assertEqual(
                target["native_characterization_reference_alignment"]
                ["fresh_c2"]["semantic_attempts_remaining"],
                0,
            )
            for action in LiveAction:
                with self.subTest(action=action.value):
                    self.assertFalse(evaluate_live_action(target, action).allowed)

    def test_commit_is_atomic_idempotent_and_dry_run_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path, bindings = self._fixture(root)
            before = state_path.read_bytes()
            preview = complete_native_characterization_c2(
                state_path,
                repo_root=root,
                bindings=bindings,
                dry_run=True,
            )
            self.assertEqual(state_path.read_bytes(), before)

            committed = complete_native_characterization_c2(
                state_path,
                repo_root=root,
                bindings=bindings,
                dry_run=False,
            )
            first_bytes = state_path.read_bytes()
            repeated = complete_native_characterization_c2(
                state_path,
                repo_root=root,
                bindings=bindings,
                dry_run=False,
            )
            self.assertEqual(preview, committed)
            self.assertEqual(repeated, committed)
            self.assertEqual(state_path.read_bytes(), first_bytes)
            self.assertEqual(first_bytes, _canonical(committed))

    def test_state_or_verification_tamper_fails_closed(self) -> None:
        for mutation in ("state", "verification"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state_path, bindings = self._fixture(root)
                if mutation == "state":
                    state = json.loads(state_path.read_text("ascii"))
                    state["next_allowed_action"] = "tampered"
                    state_path.write_bytes(_canonical(state))
                else:
                    artifact = state_path.parent / bindings.verification_relative_path
                    value = json.loads(artifact.read_text("ascii"))
                    value["status"] = "tampered"
                    artifact.write_bytes(_canonical(value))

                with self.assertRaises(NativeCharacterizationC2CompletionError):
                    complete_native_characterization_c2(
                        state_path,
                        repo_root=root,
                        bindings=bindings,
                        dry_run=True,
                    )


if __name__ == "__main__":
    import unittest

    unittest.main()
