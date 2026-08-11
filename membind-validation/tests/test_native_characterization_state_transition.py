"""Offline contracts for entering the frozen Native characterization lane.

Every filesystem mutation in this suite is confined to a temporary repository.
The production ``CURRENT_STATE.json`` and all live services remain untouched.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_state_transition as transition  # noqa: E402
from current_state_gate import LiveAction, evaluate_live_action  # noqa: E402


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _sha256(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


class NativeCharacterizationStateTransitionTests(TestCase):
    maxDiff = None

    def _source_state(self) -> dict[str, object]:
        return {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "H0",
            "status": "h0_q1_b_live_only",
            "current_action_scope": "h0_q1_b_live_only",
            "current_blocker": None,
            "next_allowed_action": "run_q1_h0-b-r6-replacement-004",
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "h0_live_gate": "h0_q1_b_live_only",
                "h0_candidate_progression": "h0_b_r6_replacement_authorized_once",
                "unrelated_progress": "preserved",
            },
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-B",
                "authorized_stage_attempt_id": (
                    "h0-q1-b-20260810-replacement-004"
                ),
                "resolved_manifest_index_path": "artifacts/h0/r6/index.json",
                "resolved_manifest_index_sha256": "1" * 64,
                "r6_recovery_admission": {"schema_version": "historical-r6"},
            },
            "evidence": {"historical": "preserved", "digest": "2" * 64},
            "completed_stages": {"V2": {"status": "pass"}},
            "invalidated_diagnostics": {"old_probe": {"reason": "historical"}},
            "historical_blocker": "v3_smoke_002_m0_structured_output_failure",
            "h0_live_authorization_invalidation": {
                "phase": "H0-A",
                "status": "invalidated_no_rerun_or_advance_authorized",
            },
            "h0_offline_live_prerequisites": {
                "live_transition_performed": True,
                "status": "verified_offline_not_live_authorized",
            },
            "h0_phase_completions": {
                "H0-A": {"candidate_advance_allowed": True}
            },
        }

    def _checkpoint(self) -> dict[str, object]:
        return {
            "schema_version": "membind.h0.checkpoint-index.v1",
            "protocol_version": "current-validation-v1.3",
            "stage_attempt_id": "h0-q1-b-20260810-replacement-004",
            "candidate_id": "Q1",
            "phase": "H0-B",
            "status": "infrastructure_interrupted",
            "stop_reason": "vllm_unreachable",
        }

    def _fixture(self, root: Path) -> tuple[Path, dict[str, object], dict[str, str]]:
        state = self._source_state()
        state_path = root / transition.STATE_RELATIVE_PATH
        state_path.parent.mkdir(parents=True)
        state_path.write_bytes(_canonical(state))

        workplan = root / transition.WORKPLAN_RELATIVE_PATH
        workplan.write_text(
            "# MemBind Native Graphiti Construction Characterization Workplan v1.1\n"
            "> **Protocol ID**: `native-characterization-v1.1`\n"
            "WORKPLAN_FREEZE=true\n",
            encoding="ascii",
        )

        checkpoint = root / transition.CHECKPOINT_RELATIVE_PATH
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(_canonical(self._checkpoint()))

        identities = {
            "source": _sha256(state_path.read_bytes()),
            "workplan": _sha256(workplan.read_bytes()),
            "checkpoint": _sha256(checkpoint.read_bytes()),
        }
        return state_path, state, identities

    def _identity_patch(self, identities: dict[str, str]):
        return patch.multiple(
            transition,
            EXPECTED_SOURCE_STATE_SHA256=identities["source"],
            EXPECTED_WORKPLAN_SHA256=identities["workplan"],
            EXPECTED_CHECKPOINT_INDEX_SHA256=identities["checkpoint"],
        )

    def _transition(self, root: Path, *, dry_run: bool) -> dict[str, object]:
        state_path, _, identities = self._fixture(root)
        with self._identity_patch(identities):
            return transition.transition_native_characterization_offline(
                state_path,
                repo_root=root,
                dry_run=dry_run,
            )

    def test_pure_builder_revokes_active_grants_and_preserves_history(self):
        source = self._source_state()
        original = deepcopy(source)
        source_sha256 = _sha256(_canonical(source))

        target = transition.build_native_characterization_offline_state(
            source,
            source_state_sha256=source_sha256,
            workplan_sha256="3" * 64,
            checkpoint_index_sha256="4" * 64,
        )

        self.assertEqual(source, original)
        self.assertEqual(target["protocol_version"], "current-validation-v1.3")
        self.assertEqual(target["current_stage"], "NATIVE_CHARACTERIZATION")
        self.assertEqual(target["status"], "native_characterization_offline_only")
        self.assertEqual(
            target["current_action_scope"], "native_characterization_offline_only"
        )
        self.assertEqual(
            target["next_allowed_action"], "implement_c1_instrumentation_offline"
        )
        self.assertIsNone(target["current_blocker"])
        self.assertFalse(target["live_h0_candidate_authorized"])
        self.assertEqual(target["authorized_live_actions"], [])
        self.assertIsNone(target["authorized_h0_candidate_id"])
        self.assertFalse(target["service_admin_authorized"])
        self.assertFalse(target["v3_smoke_003_authorized"])
        self.assertNotIn("live_h0_authorization", target)
        self.assertEqual(
            target["historical_h0_live_authorization"],
            source["live_h0_authorization"],
        )
        self.assertIsNot(
            target["historical_h0_live_authorization"],
            source["live_h0_authorization"],
        )
        for field in (
            "evidence",
            "completed_stages",
            "invalidated_diagnostics",
            "historical_blocker",
            "h0_live_authorization_invalidation",
            "h0_offline_live_prerequisites",
            "h0_phase_completions",
        ):
            self.assertEqual(target[field], source[field])

    def test_target_binds_frozen_identity_and_denies_every_known_live_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self._transition(root, dry_run=True)

        metadata = target["native_characterization_transition"]
        self.assertEqual(
            metadata["schema_version"],
            "membind.native-characterization-transition.v1",
        )
        self.assertEqual(metadata["workplan_id"], "native-characterization-v1.1")
        self.assertFalse(metadata["live_authorized"])
        self.assertEqual(
            metadata["retired_stage_attempt_id"],
            "h0-q1-b-20260810-replacement-004",
        )
        for action in LiveAction:
            with self.subTest(action=action.value):
                decision = evaluate_live_action(
                    target,
                    action,
                    candidate_id="Q1" if action is LiveAction.H0_CANDIDATE else None,
                )
                self.assertFalse(decision.allowed)

    def test_dry_run_is_read_only_and_creates_no_lock_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            before = state_path.read_bytes()
            with self._identity_patch(identities):
                preview = transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=True,
                )

            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(preview["current_stage"], "NATIVE_CHARACTERIZATION")
            self.assertFalse(
                (state_path.parent / f".{state_path.name}.transition.lock").exists()
            )
            self.assertEqual(list(state_path.parent.glob(f".{state_path.name}.*.tmp")), [])

    def test_commit_is_canonical_atomic_and_preserves_file_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            state_path.chmod(0o640)
            with self._identity_patch(identities):
                target = transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )

            self.assertEqual(state_path.read_bytes(), _canonical(target))
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o640)
            self.assertEqual(list(state_path.parent.glob(f".{state_path.name}.*.tmp")), [])

    def test_second_commit_is_idempotent_and_performs_no_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            with self._identity_patch(identities):
                first = transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )
                before = state_path.read_bytes()
                with patch.object(transition, "_atomic_write") as writer:
                    second = transition.transition_native_characterization_offline(
                        state_path,
                        repo_root=root,
                        dry_run=False,
                    )

                writer.assert_not_called()
            self.assertEqual(first, second)
            self.assertEqual(state_path.read_bytes(), before)

    def test_second_commit_is_idempotent_and_preserves_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            with self._identity_patch(identities):
                transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )
                before_mtime = state_path.stat().st_mtime_ns
                second = transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )

            self.assertEqual(second["current_stage"], "NATIVE_CHARACTERIZATION")
            self.assertEqual(state_path.stat().st_mtime_ns, before_mtime)

    def test_source_and_target_drift_are_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, source, identities = self._fixture(root)
            changed = deepcopy(source)
            changed["evidence"]["historical"] = "drifted"
            state_path.write_bytes(_canonical(changed))
            before = state_path.read_bytes()
            with self._identity_patch(identities), self.assertRaisesRegex(
                transition.NativeCharacterizationStateTransitionError,
                "source_state_drift",
            ):
                transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )
            self.assertEqual(state_path.read_bytes(), before)

            state_path.write_bytes(_canonical(source))
            with self._identity_patch(identities):
                transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )
            target = json.loads(state_path.read_text(encoding="ascii"))
            target["evidence"]["historical"] = "target-drift"
            state_path.write_bytes(_canonical(target))
            before = state_path.read_bytes()
            with self._identity_patch(identities), self.assertRaisesRegex(
                transition.NativeCharacterizationStateTransitionError,
                "target_state_drift",
            ):
                transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )
            self.assertEqual(state_path.read_bytes(), before)

    def test_workplan_and_checkpoint_drift_fail_before_state_write(self):
        mutations = (
            (transition.WORKPLAN_RELATIVE_PATH, b"workplan drift\n", "workplan_hash_mismatch"),
            (transition.CHECKPOINT_RELATIVE_PATH, b"{}", "checkpoint_hash_mismatch"),
        )
        for relative, content, reason in mutations:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state_path, _, identities = self._fixture(root)
                (root / relative).write_bytes(content)
                before = state_path.read_bytes()
                with self._identity_patch(identities), self.assertRaisesRegex(
                    transition.NativeCharacterizationStateTransitionError,
                    reason,
                ):
                    transition.transition_native_characterization_offline(
                        state_path,
                        repo_root=root,
                        dry_run=False,
                    )
                self.assertEqual(state_path.read_bytes(), before)

    def test_hashed_historical_checkpoint_may_use_indented_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            checkpoint_path = root / transition.CHECKPOINT_RELATIVE_PATH
            checkpoint_path.write_text(
                json.dumps(self._checkpoint(), indent=2, sort_keys=True) + "\n",
                encoding="ascii",
            )
            identities["checkpoint"] = _sha256(checkpoint_path.read_bytes())

            with self._identity_patch(identities):
                target = transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=True,
                )

            self.assertEqual(target["current_stage"], "NATIVE_CHARACTERIZATION")

    def test_noncanonical_state_symlink_and_root_escape_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, source, identities = self._fixture(root)
            state_path.write_text(json.dumps(source, indent=2), encoding="ascii")
            with self._identity_patch(identities), self.assertRaisesRegex(
                transition.NativeCharacterizationStateTransitionError,
                "state_file_not_canonical",
            ):
                transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=True,
                )

            outside = root.parent / f"{root.name}-outside.json"
            outside.write_bytes(_canonical(source))
            self.addCleanup(outside.unlink, missing_ok=True)
            with self._identity_patch(identities), self.assertRaisesRegex(
                transition.NativeCharacterizationStateTransitionError,
                "state_path_escapes_root",
            ):
                transition.transition_native_characterization_offline(
                    outside,
                    repo_root=root,
                    dry_run=True,
                )

    def test_atomic_replace_failure_preserves_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            before = state_path.read_bytes()
            with self._identity_patch(identities), patch.object(
                transition.os,
                "replace",
                side_effect=OSError("injected"),
            ), self.assertRaisesRegex(
                transition.NativeCharacterizationStateTransitionError,
                "atomic_write_failed",
            ):
                transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )

            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(list(state_path.parent.glob(f".{state_path.name}.*.tmp")), [])

    def test_directory_fsync_failure_preserves_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            before = state_path.read_bytes()

            # The first fsync is for the temporary file; the second is for the
            # containing directory after os.replace.  A failed durability
            # acknowledgement must not leave a new state visible.
            with self._identity_patch(identities), patch.object(
                transition.os,
                "fsync",
                side_effect=[None, OSError("directory fsync injected")],
            ), self.assertRaisesRegex(
                transition.NativeCharacterizationStateTransitionError,
                "atomic_write_failed",
            ):
                transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=False,
                )

            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(list(state_path.parent.glob(f".{state_path.name}.*.tmp")), [])

    def test_unsafe_state_values_are_rejected_even_when_hash_matches(self):
        for field, value in (
            ("api_key", "do-not-persist"),
            ("note", "Bearer do-not-persist"),
            ("path", ".env"),
        ):
            with self.subTest(field=field):
                source = self._source_state()
                source[field] = value
                with self.assertRaisesRegex(
                    transition.NativeCharacterizationStateTransitionError,
                    "unsafe_source_state",
                ):
                    transition.build_native_characterization_offline_state(
                        source,
                        source_state_sha256=_sha256(_canonical(source)),
                        workplan_sha256="3" * 64,
                        checkpoint_index_sha256="4" * 64,
                    )

    def test_module_has_a_standard_library_only_offline_import_boundary(self):
        source_path = ROOT / "src" / "native_characterization_state_transition.py"
        tree = ast.parse(source_path.read_text(encoding="ascii"))
        imports = {
            node.names[0].name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertLessEqual(
            imports,
            {
                "__future__",
                "argparse",
                "contextlib",
                "copy",
                "fcntl",
                "hashlib",
                "json",
                "os",
                "pathlib",
                "re",
                "sys",
                "tempfile",
                "typing",
            },
        )
        encoded = source_path.read_text(encoding="ascii").casefold()
        for forbidden in (
            "load_env_file",
            "dotenv",
            "socket",
            "httpx",
            "requests",
            "urllib",
            "subprocess",
            "openai",
            "import graphiti",
            "from graphiti",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_dry_run_argument_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            with self._identity_patch(identities), self.assertRaisesRegex(
                transition.NativeCharacterizationStateTransitionError,
                "dry_run_not_boolean",
            ):
                transition.transition_native_characterization_offline(
                    state_path,
                    repo_root=root,
                    dry_run=1,
                )

    def test_cli_dry_run_is_available_without_loading_runtime_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _, identities = self._fixture(root)
            argv = [
                "native_characterization_state_transition",
                "--state",
                str(state_path),
                "--repo-root",
                str(root),
                "--dry-run",
            ]
            with self._identity_patch(identities), patch.object(sys, "argv", argv):
                self.assertEqual(transition._main(), 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
