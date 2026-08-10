"""Atomic transition contracts for the H0-B harness recovery builders.

The tests use only canonical temporary state files and injected pure builders.
They exercise persistence mechanics without loading credentials or contacting
construction, embedding, Neo4j, or SSH services.
"""

from __future__ import annotations

import tempfile
import sys
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_harness_recovery import (
    H0HarnessRecoveryError,
    _default_decision_verifier,
    _validate_r3_verification_shape,
    transition_h0_b_harness_repair_bound,
    transition_h0_b_harness_revoke,
    transition_h0_b_replacement_live,
)
from h0_runtime import H0StateGateError, authorize_h0_live_entry, canonical_json_bytes


class H0BHarnessRecoveryTransitionTests(TestCase):
    def _source(self) -> dict:
        return {"protocol_version": "current-validation-v1.3", "marker": "source"}

    def _forbidden(self, marker: str = "derived") -> dict:
        return {
            "protocol_version": "current-validation-v1.3",
            "marker": marker,
            "stage_progress": {"h0_live_gate": "forbidden"},
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
        }

    def _live(self, marker: str = "derived") -> dict:
        return {
            "protocol_version": "current-validation-v1.3",
            "marker": marker,
            "stage_progress": {"h0_live_gate": "h0_q1_b_live_only"},
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "live_h0_authorization": {"candidate_id": "Q1", "phase": "H0-B"},
        }

    def _write_state(self, root: Path, value: dict | None = None) -> Path:
        path = root / "CURRENT_STATE.json"
        path.write_bytes(canonical_json_bytes(value or self._source()))
        return path

    def _revoke_kwargs(self, root: Path, builder: Mock) -> dict:
        return {
            "root": root,
            "stage_attempt_id": "h0-q1-b-failed-001",
            "checkpoint_index_path": "artifacts/checkpoints/failed/index.json",
            "checkpoint_index_sha256": "1" * 64,
            "failure_report_path": "artifacts/diagnostics/failure.json",
            "failure_report_sha256": "2" * 64,
            "state_builder": builder,
        }

    def _repair_kwargs(self, root: Path, builder: Mock) -> dict:
        return {
            "root": root,
            "manifest_verification": {"kind": "verification"},
            "tdd_evidence": {"kind": "tdd"},
            "repair_decision_path": "artifacts/decisions/repair.json",
            "repair_decision_sha256": "3" * 64,
            "state_builder": builder,
            "manifest_validator": Mock(name="manifest_validator"),
            "tdd_validator": Mock(name="tdd_validator"),
            "repair_decision_verifier": Mock(name="repair_decision_verifier"),
        }

    def _live_kwargs(self, root: Path, builder: Mock, preview: Mock) -> dict:
        return {
            "root": root,
            "state_builder": builder,
            "preview_validator": preview,
            "manifest_validator": Mock(name="manifest_validator"),
            "tdd_validator": Mock(name="tdd_validator"),
            "repair_decision_verifier": Mock(name="repair_decision_verifier"),
        }

    def test_revoke_defaults_to_zero_write_dry_run_then_commits_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = self._write_state(root)
            before = path.read_bytes()
            target = self._forbidden()
            builder = Mock(return_value=deepcopy(target))
            kwargs = self._revoke_kwargs(root, builder)

            with patch("h0_harness_recovery._atomic_write", wraps=None) as atomic:
                preview = transition_h0_b_harness_revoke(path, **kwargs)
                atomic.assert_not_called()
            self.assertEqual(preview, target)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((root / ".CURRENT_STATE.json.transition.lock").exists())

            from h0_state_transition import _atomic_write as real_atomic_write

            with patch(
                "h0_harness_recovery._atomic_write",
                wraps=real_atomic_write,
            ) as atomic:
                committed = transition_h0_b_harness_revoke(
                    path, **kwargs, dry_run=False
                )
                atomic.assert_called_once()
            self.assertEqual(committed, target)
            self.assertEqual(path.read_bytes(), canonical_json_bytes(target))
            self.assertEqual(builder.call_count, 3)
            with self.assertRaises(H0StateGateError):
                authorize_h0_live_entry(
                    state_path=path, candidate_id="Q1", phase="H0-B"
                )

    def test_repair_bound_commits_once_and_rejects_injected_live_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = self._write_state(root)
            before = path.read_bytes()
            target = self._forbidden()
            builder = Mock(return_value=deepcopy(target))
            kwargs = self._repair_kwargs(root, builder)
            from h0_state_transition import _atomic_write as real_atomic_write

            preview = transition_h0_b_harness_repair_bound(path, **kwargs)
            self.assertEqual(preview, target)
            self.assertEqual(path.read_bytes(), before)

            with patch(
                "h0_harness_recovery._atomic_write",
                wraps=real_atomic_write,
            ) as atomic:
                committed = transition_h0_b_harness_repair_bound(
                    path, **kwargs, dry_run=False
                )
                atomic.assert_called_once()
            self.assertEqual(committed, target)
            self.assertEqual(path.read_bytes(), canonical_json_bytes(target))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = self._write_state(root)
            before = path.read_bytes()
            builder = Mock(return_value=self._live())
            with self.assertRaisesRegex(H0HarnessRecoveryError, "live_forbidden"):
                transition_h0_b_harness_repair_bound(
                    path, **self._repair_kwargs(root, builder)
                )
            self.assertEqual(path.read_bytes(), before)

    def test_replacement_preview_uses_both_runtime_validators_before_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = self._write_state(root)
            before = path.read_bytes()
            target = self._live()
            builder = Mock(return_value=deepcopy(target))
            order: list[str] = []

            def authorize(**kwargs):
                order.append("authorize")
                preview_path = Path(kwargs["state_path"])
                self.assertTrue(preview_path.is_file())
                self.assertEqual(preview_path.read_bytes(), canonical_json_bytes(target))
                self.assertEqual(kwargs["candidate_id"], "Q1")
                self.assertEqual(kwargs["phase"], "H0-B")
                return {"authorized": True}

            def load_identity(authorization, *, root):
                order.append("identity")
                self.assertEqual(authorization, {"authorized": True})
                return {"loaded": True}

            from h0_state_transition import _atomic_write as real_atomic_write

            def atomic_write(target_path, state):
                order.append("write")
                real_atomic_write(target_path, state)

            with (
                patch("h0_harness_recovery.authorize_h0_live_entry", side_effect=authorize),
                patch(
                    "h0_harness_recovery.load_authorized_h0_runtime_identity",
                    side_effect=load_identity,
                ),
                patch("h0_harness_recovery._atomic_write", side_effect=atomic_write),
            ):
                dry = transition_h0_b_replacement_live(
                    path,
                    root=root,
                    state_builder=builder,
                )
                self.assertEqual(order, ["authorize", "identity"])
                self.assertEqual(path.read_bytes(), before)
                order.clear()
                committed = transition_h0_b_replacement_live(
                    path,
                    root=root,
                    state_builder=builder,
                    dry_run=False,
                )

            self.assertEqual(dry, target)
            self.assertEqual(committed, target)
            self.assertEqual(order, ["authorize", "identity", "write"])
            self.assertEqual(path.read_bytes(), canonical_json_bytes(target))
            self.assertEqual(list(root.glob(".h0-b-live-preview.*")), [])

    def test_replacement_preview_failure_precedes_write_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = self._write_state(root)
            before = path.read_bytes()
            builder = Mock(return_value=self._live())
            preview = Mock(side_effect=RuntimeError("private preview detail"))
            with (
                patch("h0_harness_recovery._atomic_write") as atomic,
                self.assertRaisesRegex(
                    H0HarnessRecoveryError, "preview_validation_failed"
                ),
            ):
                transition_h0_b_replacement_live(
                    path,
                    **self._live_kwargs(root, builder, preview),
                    dry_run=False,
                )
            atomic.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_all_transitions_reject_evidence_drift_without_writing(self):
        cases = ("revoke", "repair", "live")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                path = self._write_state(root)
                before = path.read_bytes()
                first = self._live("first") if case == "live" else self._forbidden("first")
                second = self._live("second") if case == "live" else self._forbidden("second")
                builder = Mock(side_effect=[deepcopy(first), deepcopy(second)])
                preview = Mock()
                if case == "revoke":
                    call = transition_h0_b_harness_revoke
                    kwargs = self._revoke_kwargs(root, builder)
                elif case == "repair":
                    call = transition_h0_b_harness_repair_bound
                    kwargs = self._repair_kwargs(root, builder)
                else:
                    call = transition_h0_b_replacement_live
                    kwargs = self._live_kwargs(root, builder, preview)
                with (
                    patch("h0_harness_recovery._atomic_write") as atomic,
                    self.assertRaisesRegex(H0HarnessRecoveryError, "evidence_changed"),
                ):
                    call(path, **kwargs, dry_run=False)
                atomic.assert_not_called()
                preview.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_all_transitions_preserve_concurrent_state_drift(self):
        cases = ("revoke", "repair", "live")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                path = self._write_state(root)
                concurrent = {**self._source(), "operator_update": "preserve"}
                concurrent_bytes = canonical_json_bytes(concurrent)
                target = self._live() if case == "live" else self._forbidden()

                def build_then_change(*_args, **_kwargs):
                    path.write_bytes(concurrent_bytes)
                    return deepcopy(target)

                builder = Mock(side_effect=build_then_change)
                preview = Mock()
                if case == "revoke":
                    call = transition_h0_b_harness_revoke
                    kwargs = self._revoke_kwargs(root, builder)
                elif case == "repair":
                    call = transition_h0_b_harness_repair_bound
                    kwargs = self._repair_kwargs(root, builder)
                else:
                    call = transition_h0_b_replacement_live
                    kwargs = self._live_kwargs(root, builder, preview)
                with (
                    patch("h0_harness_recovery._atomic_write") as atomic,
                    self.assertRaisesRegex(H0HarnessRecoveryError, "state_changed"),
                ):
                    call(path, **kwargs, dry_run=False)
                atomic.assert_not_called()
                preview.assert_not_called()
                self.assertEqual(path.read_bytes(), concurrent_bytes)

    def test_root_containment_and_canonical_snapshot_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            root = Path(tmp).resolve()
            outside = self._write_state(Path(out).resolve())
            outside_before = outside.read_bytes()
            builder = Mock(return_value=self._forbidden())
            with self.assertRaises(Exception):
                transition_h0_b_harness_revoke(
                    outside, **self._revoke_kwargs(root, builder)
                )
            self.assertEqual(outside.read_bytes(), outside_before)
            builder.assert_not_called()

            noncanonical = root / "CURRENT_STATE.json"
            noncanonical.write_text('{"marker": "source"}\n', encoding="ascii")
            before = noncanonical.read_bytes()
            with self.assertRaises(Exception):
                transition_h0_b_harness_revoke(
                    noncanonical, **self._revoke_kwargs(root, builder)
                )
            self.assertEqual(noncanonical.read_bytes(), before)
            builder.assert_not_called()

    def test_dry_run_requires_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = self._write_state(root)
            before = path.read_bytes()
            builder = Mock(return_value=self._forbidden())
            with self.assertRaisesRegex(H0HarnessRecoveryError, "dry_run_not_boolean"):
                transition_h0_b_harness_revoke(
                    path,
                    **self._revoke_kwargs(root, builder),
                    dry_run=1,
                )
            self.assertEqual(path.read_bytes(), before)
            builder.assert_not_called()

    def test_default_decision_verifier_uses_reproducible_decision_contract(self):
        expected = {"schema_version": "membind.h0.harness-repair-admission.v1"}
        verification = {"artifact_set_id": "v1_3_harness_r3"}
        with patch(
            "h0_harness_recovery.verify_h0_b_harness_repair_decision",
            return_value=expected,
        ) as verifier:
            observed = _default_decision_verifier(
                root=Path("/offline/root"),
                decision_path="artifacts/h0_protocol_repair/decisions/repair.json",
                decision_sha256="a" * 64,
                manifest_verification=verification,
            )

        self.assertEqual(observed, expected)
        verifier.assert_called_once_with(
            root=Path("/offline/root"),
            decision_path="artifacts/h0_protocol_repair/decisions/repair.json",
            decision_sha256="a" * 64,
            manifest_verification=verification,
        )

    def test_default_builders_compose_across_all_three_atomic_transitions(self):
        from tests.test_h0_harness_recovery import H0BHarnessRecoveryTests

        fixture = H0BHarnessRecoveryTests(
            "test_exact_r2_failure_revocation_clears_stale_gate_and_preserves_h0_a"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            evidence = fixture._failure_evidence(root)
            path = self._write_state(root, fixture._live_r2_state())

            revoked = transition_h0_b_harness_revoke(
                path,
                root=root,
                stage_attempt_id=fixture.old_attempt_id,
                checkpoint_index_path=evidence["checkpoint_index_path"],
                checkpoint_index_sha256=evidence["checkpoint_index_sha256"],
                failure_report_path=evidence["failure_report_path"],
                failure_report_sha256=evidence["failure_report_sha256"],
                dry_run=False,
            )
            self.assertEqual(
                revoked["status"],
                "h0_b_harness_compatibility_failure_live_revoked",
            )

            verification, tdd, admission, manifest, tdd_validator, repair = (
                fixture._validators(evidence)
            )
            bound = transition_h0_b_harness_repair_bound(
                path,
                root=root,
                manifest_verification=verification,
                tdd_evidence=tdd,
                repair_decision_path=admission["decision_path"],
                repair_decision_sha256=admission["decision_sha256"],
                manifest_validator=manifest,
                tdd_validator=tdd_validator,
                repair_decision_verifier=repair,
                dry_run=False,
            )
            self.assertEqual(
                bound["status"],
                "h0_b_harness_repair_verified_not_live_authorized",
            )

            preview = Mock()
            live = transition_h0_b_replacement_live(
                path,
                root=root,
                manifest_validator=manifest,
                tdd_validator=tdd_validator,
                repair_decision_verifier=repair,
                preview_validator=preview,
                dry_run=False,
            )
            self.assertEqual(live["status"], "h0_q1_b_live_only")
            self.assertEqual(
                live["live_h0_authorization"]["authorized_stage_attempt_id"],
                fixture.replacement_attempt_id,
            )
            self.assertEqual(path.read_bytes(), canonical_json_bytes(live))
            preview.assert_called_once()

    def test_r3_verification_freezes_exactly_32_execution_sources(self):
        verification = {
            "schema_version": "membind.h0.offline-artifact-verification.v3",
            "protocol_version": "current-validation-v1.3",
            "artifact_set_id": "v1_3_harness_r3",
            "execution_harness_revision": 3,
            "status": "verified_offline_not_live_authorized",
            "index_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r3/"
                "resolved_manifest_index_v1_3_harness_r3.json"
            ),
            "index_sha256": "4" * 64,
            "generated_json_file_count": 11,
            "binding_count": 10,
            "resolved_wrapper_count": 4,
            "source_spec_count": 4,
            "execution_source_count": 32,
            "secret_scan_passed": True,
            "live_eligible": False,
        }
        self.assertEqual(
            _validate_r3_verification_shape(verification), verification
        )
        for count in (31, 33):
            with self.subTest(count=count), self.assertRaises(
                H0HarnessRecoveryError
            ):
                _validate_r3_verification_shape(
                    {**verification, "execution_source_count": count}
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
