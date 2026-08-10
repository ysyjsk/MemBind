"""Offline TDD contracts for binding H0 evidence before live authorization.

These tests use only temporary files.  They never read project credentials,
open a network client, or mutate the repository's real ``CURRENT_STATE.json``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_live_preflight import load_authorized_h0_runtime_identity  # noqa: E402
import h0_state_transition as state_transition  # noqa: E402
from h0_runtime import (  # noqa: E402
    H0ManifestError,
    H0StateGateError,
    authorize_h0_live_entry,
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_file,
)
from h0_state_transition import (  # noqa: E402
    H0StateTransitionError,
    build_h0_live_authorization_revoked_state,
    build_h0_offline_bound_state,
    build_q1_h0_a_live_state,
    persist_h0_offline_bound_state,
    transition_h0_live_authorization_revoke,
    transition_q1_h0_a_live,
)


class H0StateTransitionTests(TestCase):
    ARTIFACT_SET_ID = "v1_3_harness_r2"
    HARNESS_REVISION = 2
    ARTIFACT_SET_ROOT = "artifacts/h0_manifest_sets/v1_3_harness_r2"

    def _write_json(self, root: Path, relative: str, value: dict) -> tuple[str, str]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(value))
        return relative, sha256_file(path)

    def _write_content_addressed_json(
        self,
        root: Path,
        directory: str,
        stem: str,
        value: dict,
    ) -> tuple[str, str]:
        digest = canonical_json_sha256(value)
        return self._write_json(root, f"{directory}/{stem}.{digest}.json", value)

    def _fixture(self, root: Path) -> tuple[dict, dict, dict]:
        shared = {
            "schema_version": "membind.h0.resolved-shared-host-base.v1",
            "protocol_version": "current-validation-v1.3",
            "status": "offline_resolved_not_live_authorized",
            "live_eligible": False,
            "source_base": {
                "construction": {
                    "base_url": "http://10.87.5.247:8000/v1",
                    "served_model_id": "qwen3-32b-fp8",
                    "vllm_version": "0.26.0",
                    "context_limit": 40960,
                }
            },
        }
        shared_path, shared_sha = self._write_content_addressed_json(
            root,
            f"{self.ARTIFACT_SET_ROOT}/resolved_candidates",
            "shared_base",
            shared,
        )
        candidate = {
            "schema_version": "membind.h0.resolved-candidate.v1",
            "protocol_version": "current-validation-v1.3",
            "status": "offline_resolved_not_live_authorized",
            "live_eligible": False,
            "candidate_id": "Q1",
            "resolved_shared_base_sha256": shared_sha,
        }
        candidate_path, candidate_sha = self._write_content_addressed_json(
            root,
            f"{self.ARTIFACT_SET_ROOT}/resolved_candidates",
            "Q1",
            candidate,
        )
        resolved_manifests = {
            "shared_base": {"path": shared_path, "sha256": shared_sha},
            "Q1": {"path": candidate_path, "sha256": candidate_sha},
        }
        for candidate_id in ("Q2", "Q3"):
            placeholder = {
                "schema_version": "membind.h0.resolved-candidate.v1",
                "protocol_version": "current-validation-v1.3",
                "candidate_id": candidate_id,
            }
            path, digest = self._write_content_addressed_json(
                root,
                f"{self.ARTIFACT_SET_ROOT}/resolved_candidates",
                candidate_id,
                placeholder,
            )
            resolved_manifests[candidate_id] = {"path": path, "sha256": digest}
        shared_artifacts = {}
        for name in (
            "prompt_bundle",
            "schema_bundle",
            "semantic_guardrail",
            "http_retry",
            "vllm_launch",
            "execution_source_bundle",
        ):
            placeholder = {
                "protocol_version": "current-validation-v1.3",
                "manifest_kind": name,
            }
            path, digest = self._write_content_addressed_json(
                root,
                f"{self.ARTIFACT_SET_ROOT}/manifests",
                f"{name}_v1_3",
                placeholder,
            )
            shared_artifacts[name] = {"path": path, "sha256": digest}
        index = {
            "schema_version": "membind.h0.offline-artifacts.v2",
            "protocol_version": "current-validation-v1.3",
            "artifact_set_id": self.ARTIFACT_SET_ID,
            "execution_harness_revision": self.HARNESS_REVISION,
            "status": "offline_resolved_not_live_authorized",
            "live_h0_candidate_authorized": False,
            "shared_artifacts": shared_artifacts,
            "resolved_manifests": resolved_manifests,
            "unresolved_fields": [],
            "source_specs_immutable": True,
            "secrets_persisted": False,
        }
        index_path, index_sha = self._write_json(
            root,
            f"{self.ARTIFACT_SET_ROOT}/resolved_manifest_index_v1_3_harness_r2.json",
            index,
        )
        verification = {
            "schema_version": "membind.h0.offline-artifact-verification.v2",
            "protocol_version": "current-validation-v1.3",
            "artifact_set_id": self.ARTIFACT_SET_ID,
            "execution_harness_revision": self.HARNESS_REVISION,
            "status": "verified_offline_not_live_authorized",
            "index_path": index_path,
            "index_sha256": index_sha,
            "generated_json_file_count": 11,
            "binding_count": 10,
            "resolved_wrapper_count": 4,
            "source_spec_count": 4,
            "execution_source_count": 31,
            "secret_scan_passed": True,
            "live_eligible": False,
        }
        evidence: dict[str, dict[str, object]] = {}
        for ordinal, name in enumerate(
            ("latest_red", "latest_green", "latest_focused", "latest_full_regression"),
            start=1,
        ):
            relative = f"artifacts/tdd/h0_{name}_{ordinal:03d}.log"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"safe offline {name} evidence\n", encoding="ascii")
            evidence[name] = {
                "path": relative,
                "sha256": sha256_file(path),
                "test_count": ordinal,
            }
        source = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "H0",
            "status": "h0_protocol_accepted_harness_not_implemented",
            "current_action_scope": "h0_offline_tdd_and_harness_only",
            "stage_progress": {"h0_live_gate": "forbidden", "unrelated": "preserved"},
            "evidence": {"existing": "preserved"},
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
        }
        return source, verification, evidence

    def _offline(self, root: Path) -> dict:
        source, verification, evidence = self._fixture(root)
        return build_h0_offline_bound_state(
            source,
            root=root,
            manifest_verification=verification,
            tdd_evidence=evidence,
        )

    def _live_revoke_fixture(self, root: Path) -> tuple[dict, str, str, str]:
        attempt_id = "h0-q1-a-gate-order-001"
        checkpoint_relative = (
            f"artifacts/h0_runs/h0/checkpoints/{attempt_id}/index.json"
        )
        checkpoint_path = root / checkpoint_relative
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "membind.h0.checkpoint-index.v1",
                    "protocol_version": "current-validation-v1.3",
                    "stage_attempt_id": attempt_id,
                    "candidate_id": "Q1",
                    "phase": "H0-A",
                    "status": "running",
                }
            )
        )
        checkpoint_sha256 = sha256_file(checkpoint_path)
        scope = "h0_q1_a_live_only"
        source = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "H0",
            "status": scope,
            "current_action_scope": scope,
            "stage_progress": {"h0_live_gate": scope, "preserved": "yes"},
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "service_admin_authorized": True,
            "v3_smoke_003_authorized": True,
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-A",
                "resolved_manifest_index_path": "artifacts/h0/index.json",
                "resolved_manifest_index_sha256": "1" * 64,
                "resolved_candidate_manifest_path": "artifacts/h0/q1.json",
                "resolved_candidate_manifest_sha256": "2" * 64,
                "resolved_shared_base_manifest_path": "artifacts/h0/shared.json",
                "resolved_shared_base_manifest_sha256": "3" * 64,
            },
            "current_blocker": "late_discovered_pre_freeze_host_compatibility_failure",
            "unrelated": {"preserved": True},
        }
        return source, attempt_id, checkpoint_relative, checkpoint_sha256

    def test_offline_binding_persists_exact_manifests_and_tdd_evidence_but_denies_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, verification, evidence = self._fixture(root)
            original = deepcopy(source)

            state = build_h0_offline_bound_state(
                source,
                root=root,
                manifest_verification=verification,
                tdd_evidence=evidence,
            )

            self.assertEqual(source, original)
            self.assertEqual(state["status"], "h0_offline_verified_not_live_authorized")
            self.assertEqual(state["current_action_scope"], "h0_offline_verified_only")
            self.assertFalse(state["live_h0_candidate_authorized"])
            self.assertEqual(state["authorized_live_actions"], [])
            self.assertIsNone(state["authorized_h0_candidate_id"])
            self.assertNotIn("live_h0_authorization", state)
            self.assertEqual(state["stage_progress"]["h0_live_gate"], "forbidden")
            self.assertEqual(state["stage_progress"]["unrelated"], "preserved")
            prereq = state["h0_offline_live_prerequisites"]
            self.assertEqual(prereq["candidate_id"], "Q1")
            self.assertEqual(prereq["phase"], "H0-A")
            self.assertEqual(prereq["manifest_verification"], verification)
            self.assertEqual(prereq["tdd_evidence"], evidence)
            self.assertEqual(
                prereq["artifact_bindings"]["resolved_manifest_index_sha256"],
                verification["index_sha256"],
            )

    def test_historical_r2_h0_a_grant_is_rejected_by_current_r3_runtime_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            offline = self._offline(root)

            live = build_q1_h0_a_live_state(offline, root=root)

            self.assertFalse(offline["live_h0_candidate_authorized"])
            self.assertEqual(live["status"], "h0_q1_a_live_only")
            self.assertEqual(live["current_action_scope"], "h0_q1_a_live_only")
            self.assertEqual(live["stage_progress"]["h0_live_gate"], "h0_q1_a_live_only")
            self.assertTrue(live["live_h0_candidate_authorized"])
            self.assertEqual(live["authorized_live_actions"], ["h0_candidate"])
            self.assertEqual(live["authorized_h0_candidate_id"], "Q1")
            self.assertEqual(live["live_h0_authorization"]["phase"], "H0-A")

            state_path = root / "CURRENT_STATE.live.json"
            state_path.write_bytes(canonical_json_bytes(live))
            authorization = authorize_h0_live_entry(
                state_path=state_path, candidate_id="Q1", phase="H0-A"
            )
            with self.assertRaises(H0ManifestError):
                load_authorized_h0_runtime_identity(authorization, root=root)

    def test_offline_persist_and_live_dry_run_are_atomic_and_do_not_authorize_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, verification, evidence = self._fixture(root)
            state_path = root / "CURRENT_STATE.json"
            state_path.write_bytes(canonical_json_bytes(source))

            persisted = persist_h0_offline_bound_state(
                state_path,
                root=root,
                manifest_verification=verification,
                tdd_evidence=evidence,
            )
            self.assertEqual(state_path.read_bytes(), canonical_json_bytes(persisted))
            before = state_path.read_bytes()

            with self.assertRaises(H0StateTransitionError):
                transition_q1_h0_a_live(state_path, root=root, dry_run=True)
            self.assertEqual(state_path.read_bytes(), before)
            self.assertFalse(json.loads(before)["live_h0_candidate_authorized"])
            self.assertEqual(list(root.glob(".CURRENT_STATE.json.*.tmp")), [])

    def test_exact_offline_source_state_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, verification, evidence = self._fixture(root)
            invalid_updates = (
                {"protocol_version": "current-validation-v1.2"},
                {"current_stage": "V2-R"},
                {"live_h0_candidate_authorized": True},
                {"authorized_live_actions": ["h0_candidate"]},
                {"authorized_h0_candidate_id": "Q1"},
                {"stage_progress": {"h0_live_gate": "h0_q1_a_live_only"}},
            )
            for updates in invalid_updates:
                with self.subTest(updates=updates):
                    with self.assertRaises(H0StateTransitionError):
                        build_h0_offline_bound_state(
                            dict(source, **updates),
                            root=root,
                            manifest_verification=verification,
                            tdd_evidence=evidence,
                        )

    def test_manifest_verification_hash_and_target_phase_mismatch_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, verification, evidence = self._fixture(root)
            for field, bad in (
                ("protocol_version", "current-validation-v1.2"),
                ("artifact_set_id", "v1_3_harness_r1"),
                ("execution_harness_revision", 1),
                ("status", "unverified"),
                ("index_sha256", "f" * 64),
                ("generated_json_file_count", 10),
                ("binding_count", 9),
                ("execution_source_count", 30),
                ("secret_scan_passed", False),
                ("live_eligible", True),
            ):
                with self.subTest(field=field):
                    invalid = dict(verification, **{field: bad})
                    with self.assertRaises(H0StateTransitionError):
                        build_h0_offline_bound_state(
                            source,
                            root=root,
                            manifest_verification=invalid,
                            tdd_evidence=evidence,
                        )

            offline = self._offline(root)
            offline["h0_offline_live_prerequisites"]["phase"] = "H0-B"
            with self.assertRaises(H0StateTransitionError):
                build_q1_h0_a_live_state(offline, root=root)

    def test_escape_symlink_missing_full_regression_and_sensitive_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, verification, evidence = self._fixture(root)
            cases: list[dict[str, dict[str, object]]] = []
            escaped = deepcopy(evidence)
            escaped["latest_red"]["path"] = "../red.log"
            cases.append(escaped)
            missing_full = deepcopy(evidence)
            missing_full.pop("latest_full_regression")
            cases.append(missing_full)
            secret = deepcopy(evidence)
            secret["api_key"] = {"path": "secret", "sha256": "a" * 64, "test_count": 1}
            cases.append(secret)
            for invalid in cases:
                with self.subTest(keys=tuple(invalid)):
                    with self.assertRaises(H0StateTransitionError):
                        build_h0_offline_bound_state(
                            source,
                            root=root,
                            manifest_verification=verification,
                            tdd_evidence=invalid,
                        )

            legacy = dict(
                verification,
                schema_version="membind.h0.offline-artifact-verification.v1",
            )
            with self.assertRaises(H0StateTransitionError):
                build_h0_offline_bound_state(
                    source,
                    root=root,
                    manifest_verification=legacy,
                    tdd_evidence=evidence,
                )

            target = root / str(evidence["latest_green"]["path"])
            link = root / "artifacts/tdd/symlink.log"
            link.symlink_to(target)
            symlinked = deepcopy(evidence)
            symlinked["latest_green"]["path"] = link.relative_to(root).as_posix()
            symlinked["latest_green"]["sha256"] = sha256_file(target)
            with self.assertRaisesRegex(H0StateTransitionError, "symlink"):
                build_h0_offline_bound_state(
                    source,
                    root=root,
                    manifest_verification=verification,
                    tdd_evidence=symlinked,
                )

            state_dir = root / "state-files"
            state_dir.mkdir()
            state_file = state_dir / "CURRENT_STATE.json"
            state_file.write_bytes(canonical_json_bytes(source))
            (root / "state-link").symlink_to(state_dir, target_is_directory=True)
            with self.assertRaisesRegex(H0StateTransitionError, "symlink"):
                persist_h0_offline_bound_state(
                    root / "state-link/CURRENT_STATE.json",
                    root=root,
                    manifest_verification=verification,
                    tdd_evidence=evidence,
                )

            for unsafe_source in (
                dict(source, api_key="must-not-be-copied"),
                dict(source, note="never load .env here"),
                dict(source, raw_response={"private": "payload"}),
            ):
                with self.subTest(unsafe_source_key=tuple(unsafe_source)[-1]):
                    with self.assertRaisesRegex(H0StateTransitionError, "unsafe"):
                        build_h0_offline_bound_state(
                            unsafe_source,
                            root=root,
                            manifest_verification=verification,
                            tdd_evidence=evidence,
                        )

    def test_failed_persist_or_transition_leaves_original_bytes_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, verification, evidence = self._fixture(root)
            state_path = root / "CURRENT_STATE.json"
            state_path.write_bytes(canonical_json_bytes(source))
            original = state_path.read_bytes()
            bad_evidence = deepcopy(evidence)
            bad_evidence["latest_full_regression"]["sha256"] = "0" * 64

            with self.assertRaises(H0StateTransitionError):
                persist_h0_offline_bound_state(
                    state_path,
                    root=root,
                    manifest_verification=verification,
                    tdd_evidence=bad_evidence,
                )
            self.assertEqual(state_path.read_bytes(), original)

            offline = build_h0_offline_bound_state(
                source,
                root=root,
                manifest_verification=verification,
                tdd_evidence=evidence,
            )
            state_path.write_bytes(canonical_json_bytes(offline))
            offline_bytes = state_path.read_bytes()
            os.remove(root / str(evidence["latest_full_regression"]["path"]))
            with self.assertRaises((H0StateTransitionError, H0ManifestError, H0StateGateError)):
                transition_q1_h0_a_live(state_path, root=root, dry_run=False)
            self.assertEqual(state_path.read_bytes(), offline_bytes)

    def test_revoke_build_binds_violation_checkpoint_and_clears_every_live_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, attempt_id, checkpoint_path, checkpoint_sha256 = (
                self._live_revoke_fixture(root)
            )
            original = deepcopy(source)

            revoked = build_h0_live_authorization_revoked_state(
                source,
                root=root,
                candidate_id="Q1",
                phase="H0-A",
                stage_attempt_id=attempt_id,
                checkpoint_index_path=checkpoint_path,
                checkpoint_index_sha256=checkpoint_sha256,
            )

            self.assertEqual(source, original)
            self.assertEqual(revoked["status"], "h0_live_authorization_revoked")
            self.assertEqual(revoked["current_action_scope"], "h0_live_forbidden")
            self.assertEqual(
                revoked["current_blocker"], "h0_protocol_gate_order_violation"
            )
            self.assertEqual(revoked["stage_progress"]["h0_live_gate"], "forbidden")
            self.assertEqual(revoked["stage_progress"]["preserved"], "yes")
            self.assertFalse(revoked["live_h0_candidate_authorized"])
            self.assertEqual(revoked["authorized_live_actions"], [])
            self.assertIsNone(revoked["authorized_h0_candidate_id"])
            self.assertFalse(revoked["service_admin_authorized"])
            self.assertFalse(revoked["v3_smoke_003_authorized"])
            self.assertNotIn("live_h0_authorization", revoked)
            invalidation = revoked["h0_live_authorization_invalidation"]
            self.assertEqual(invalidation["reason"], "protocol_gate_order_violation")
            self.assertEqual(invalidation["candidate_id"], "Q1")
            self.assertEqual(invalidation["phase"], "H0-A")
            self.assertEqual(invalidation["stage_attempt_id"], attempt_id)
            self.assertEqual(invalidation["checkpoint_index_path"], checkpoint_path)
            self.assertEqual(
                invalidation["checkpoint_index_sha256"], checkpoint_sha256
            )
            self.assertFalse(invalidation["candidate_rerun_authorized"])
            self.assertFalse(invalidation["candidate_advance_authorized"])
            self.assertFalse(invalidation["live_transition_authorized"])
            self.assertEqual(revoked["unrelated"], {"preserved": True})

    def test_revoke_defaults_to_dry_run_then_commits_atomically_and_denies_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, attempt_id, checkpoint_path, checkpoint_sha256 = (
                self._live_revoke_fixture(root)
            )
            state_path = root / "CURRENT_STATE.json"
            state_path.write_bytes(canonical_json_bytes(source))
            before = state_path.read_bytes()
            kwargs = {
                "root": root,
                "candidate_id": "Q1",
                "phase": "H0-A",
                "stage_attempt_id": attempt_id,
                "checkpoint_index_path": checkpoint_path,
                "checkpoint_index_sha256": checkpoint_sha256,
            }

            preview = transition_h0_live_authorization_revoke(state_path, **kwargs)
            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(
                preview["h0_live_authorization_invalidation"]["reason"],
                "protocol_gate_order_violation",
            )

            committed = transition_h0_live_authorization_revoke(
                state_path, **kwargs, dry_run=False
            )
            self.assertEqual(state_path.read_bytes(), canonical_json_bytes(committed))
            with self.assertRaises(H0StateGateError):
                authorize_h0_live_entry(
                    state_path=state_path, candidate_id="Q1", phase="H0-A"
                )
            with self.assertRaises(H0StateTransitionError):
                transition_q1_h0_a_live(state_path, root=root, dry_run=True)

    def test_revoke_binding_mismatch_fails_without_mutating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, attempt_id, checkpoint_path, checkpoint_sha256 = (
                self._live_revoke_fixture(root)
            )
            state_path = root / "CURRENT_STATE.json"
            state_path.write_bytes(canonical_json_bytes(source))
            before = state_path.read_bytes()
            base = {
                "root": root,
                "candidate_id": "Q1",
                "phase": "H0-A",
                "stage_attempt_id": attempt_id,
                "checkpoint_index_path": checkpoint_path,
                "checkpoint_index_sha256": checkpoint_sha256,
                "dry_run": False,
            }
            mismatches = (
                {"candidate_id": "Q2"},
                {"phase": "H0-B"},
                {"stage_attempt_id": "different-attempt"},
                {"checkpoint_index_sha256": "0" * 64},
                {"checkpoint_index_path": "artifacts/h0_runs/not-the-attempt/index.json"},
            )
            for mismatch in mismatches:
                with self.subTest(mismatch=mismatch):
                    with self.assertRaises(H0StateTransitionError):
                        transition_h0_live_authorization_revoke(
                            state_path, **(base | mismatch)
                        )
                    self.assertEqual(state_path.read_bytes(), before)

    def test_revoke_commit_detects_concurrent_state_change_and_preserves_new_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, attempt_id, checkpoint_path, checkpoint_sha256 = (
                self._live_revoke_fixture(root)
            )
            state_path = root / "CURRENT_STATE.json"
            state_path.write_bytes(canonical_json_bytes(source))
            concurrent = deepcopy(source)
            concurrent["concurrent_operator_update"] = "must-be-preserved"
            concurrent_bytes = canonical_json_bytes(concurrent)
            real_builder = state_transition.build_h0_live_authorization_revoked_state
            build_count = 0

            def build_then_change(*args, **kwargs):
                nonlocal build_count
                result = real_builder(*args, **kwargs)
                build_count += 1
                if build_count == 1:
                    state_path.write_bytes(concurrent_bytes)
                return result

            with patch.object(
                state_transition,
                "build_h0_live_authorization_revoked_state",
                side_effect=build_then_change,
            ):
                with self.assertRaisesRegex(H0StateTransitionError, "state_changed"):
                    transition_h0_live_authorization_revoke(
                        state_path,
                        root=root,
                        candidate_id="Q1",
                        phase="H0-A",
                        stage_attempt_id=attempt_id,
                        checkpoint_index_path=checkpoint_path,
                        checkpoint_index_sha256=checkpoint_sha256,
                        dry_run=False,
                    )

            self.assertEqual(build_count, 2)
            self.assertEqual(state_path.read_bytes(), concurrent_bytes)


if __name__ == "__main__":
    import unittest

    unittest.main()
