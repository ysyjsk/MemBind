"""Offline contracts for the explicit Protocol v1.3 H0 control commands."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_control  # noqa: E402
from h0_runtime import H0InfrastructureError  # noqa: E402


class H0ControlCommandTests(TestCase):
    def test_h0_b_harness_recovery_uses_four_explicit_control_actions(self):
        verification = {
            "status": "verified_offline_not_live_authorized",
            "index_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r3/"
                "resolved_manifest_index_v1_3_harness_r3.json"
            ),
            "index_sha256": "1" * 64,
        }
        decision_path = (
            "artifacts/h0_protocol_repair/decisions/"
            "q1_h0_b_harness_compatibility_repair.json"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            evidence_path = root / "tdd.json"
            evidence = {
                name: {
                    "path": f"artifacts/tdd/{name}.log",
                    "sha256": "a" * 64,
                    "test_count": 1,
                }
                for name in (
                    "latest_red",
                    "latest_green",
                    "latest_focused",
                    "latest_full_regression",
                )
            }
            evidence_path.write_text(json.dumps(evidence), encoding="ascii")
            revoke = Mock(return_value={"status": "h0_b_harness_compatibility_failure_live_revoked"})
            writer = Mock(
                return_value={
                    "decision_path": decision_path,
                    "decision_sha256": "2" * 64,
                }
            )
            binder = Mock(
                return_value={
                    "status": "h0_b_harness_repair_verified_not_live_authorized"
                }
            )
            authorizer = Mock(return_value={"status": "h0_q1_b_live_only"})
            with (
                patch.object(
                    h0_control,
                    "verify_h0_offline_artifacts",
                    return_value=verification,
                ),
                patch.object(
                    h0_control,
                    "transition_h0_b_harness_revoke",
                    revoke,
                ),
                patch.object(
                    h0_control,
                    "write_h0_b_harness_repair_decision",
                    writer,
                ),
                patch.object(
                    h0_control,
                    "transition_h0_b_harness_repair_bound",
                    binder,
                ),
                patch.object(
                    h0_control,
                    "transition_h0_b_replacement_live",
                    authorizer,
                ),
            ):
                revoke_code, revoke_output, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "revoke-h0-b-harness",
                        "--state",
                        "CURRENT_STATE.json",
                        "--attempt-id",
                        "h0-q1-b-20260809-attempt-001",
                        "--checkpoint-index",
                        "artifacts/h0_runs/h0/checkpoints/old/index.json",
                        "--checkpoint-index-sha256",
                        "3" * 64,
                        "--failure-report",
                        "artifacts/diagnostics/failure.json",
                        "--failure-report-sha256",
                        "4" * 64,
                    ]
                )
                prepare_code, _, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "prepare-h0-b-harness-repair",
                        "--replacement-attempt-id",
                        "h0-q1-b-20260809-replacement-001",
                    ]
                )
                bind_code, _, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "bind-h0-b-harness-repair",
                        "--state",
                        "CURRENT_STATE.json",
                        "--tdd-evidence",
                        str(evidence_path),
                        "--decision",
                        decision_path,
                        "--decision-sha256",
                        "2" * 64,
                        "--commit",
                    ]
                )
                authorize_code, _, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "authorize-q1-b-replacement",
                        "--state",
                        "CURRENT_STATE.json",
                        "--commit",
                    ]
                )

        self.assertEqual(
            (revoke_code, prepare_code, bind_code, authorize_code),
            (0, 0, 0, 0),
        )
        revoke.assert_called_once_with(
            root / "CURRENT_STATE.json",
            root=root,
            stage_attempt_id="h0-q1-b-20260809-attempt-001",
            checkpoint_index_path="artifacts/h0_runs/h0/checkpoints/old/index.json",
            checkpoint_index_sha256="3" * 64,
            failure_report_path="artifacts/diagnostics/failure.json",
            failure_report_sha256="4" * 64,
            dry_run=True,
        )
        writer.assert_called_once_with(
            root=root,
            manifest_verification=verification,
            replacement_attempt_id="h0-q1-b-20260809-replacement-001",
        )
        binder.assert_called_once_with(
            root / "CURRENT_STATE.json",
            root=root,
            manifest_verification=verification,
            tdd_evidence=evidence,
            repair_decision_path=decision_path,
            repair_decision_sha256="2" * 64,
            dry_run=False,
        )
        authorizer.assert_called_once_with(
            root / "CURRENT_STATE.json", root=root, dry_run=False
        )
        self.assertIn('"committed": false', revoke_output)
        self.assertIn("h0_b_pre_workload_harness_compatibility_failure", revoke_output)

    def test_repair_prepare_bind_and_authorize_are_three_explicit_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            evidence_path = root / "tdd.json"
            evidence = {
                name: {"path": f"{name}.log", "sha256": "a" * 64, "test_count": 1}
                for name in (
                    "latest_red",
                    "latest_green",
                    "latest_focused",
                    "latest_full_regression",
                )
            }
            evidence_path.write_text(json.dumps(evidence), encoding="ascii")
            decision_path = (
                "artifacts/h0_protocol_repair/decisions/decision.json"
            )
            verification = {
                "status": "verified_offline_not_live_authorized",
                "index_path": "artifacts/h0_manifest_sets/v1_3_harness_r2/index.json",
                "index_sha256": "1" * 64,
            }
            with patch.object(
                h0_control,
                "verify_h0_offline_artifacts",
                return_value=verification,
            ), patch.object(
                h0_control,
                "write_h0_repair_decision",
                return_value={
                    "decision_path": decision_path,
                    "decision_sha256": "2" * 64,
                },
            ) as writer, patch.object(
                h0_control,
                "transition_h0_repair_bound",
                return_value={
                    "status": "h0_protocol_repair_verified_not_live_authorized",
                    "live_h0_candidate_authorized": False,
                },
            ) as binder, patch.object(
                h0_control,
                "transition_h0_replacement_live",
                return_value={
                    "status": "h0_q1_a_live_only",
                    "current_action_scope": "h0_q1_a_live_only",
                },
            ) as authorizer:
                prepare_code, _, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "prepare-repair",
                        "--replacement-attempt-id",
                        "replacement-001",
                    ]
                )
                bind_code, _, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "bind-repair",
                        "--state",
                        "state.json",
                        "--tdd-evidence",
                        str(evidence_path),
                        "--decision",
                        decision_path,
                        "--decision-sha256",
                        "2" * 64,
                        "--commit",
                    ]
                )
                authorize_code, _, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "authorize-q1-a-replacement",
                        "--state",
                        "state.json",
                        "--commit",
                    ]
                )

            self.assertEqual((prepare_code, bind_code, authorize_code), (0, 0, 0))
            writer.assert_called_once_with(
                root=root,
                manifest_verification=verification,
                replacement_attempt_id="replacement-001",
            )
            binder.assert_called_once_with(
                root / "state.json",
                root=root,
                manifest_verification=verification,
                tdd_evidence=evidence,
                repair_decision_path=decision_path,
                repair_decision_sha256="2" * 64,
                dry_run=False,
            )
            authorizer.assert_called_once_with(
                root / "state.json", root=root, dry_run=False
            )

    def test_advance_and_full_history_run_are_explicit_and_never_cross_call(self):
        runner = AsyncMock(
            return_value={
                "status": "stage_complete",
                "checkpoint_index_path": "h0/checkpoints/b/index.json",
                "checkpoint_index_sha256": "a" * 64,
            }
        )
        advance = Mock(return_value={"status": "h0_q1_b_live_only"})
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            h0_control, "execute_h0_full_history_live", runner
        ), patch.object(
            h0_control, "transition_h0_successor_phase_live", advance
        ):
            run_code, _, _ = self._invoke(
                [
                    "--root",
                    tmp,
                    "run-q1-b",
                    "--state",
                    "state.json",
                    "--attempt-id",
                    "b-attempt",
                ]
            )
            advance.assert_not_called()
            advance_code, _, _ = self._invoke(
                [
                    "--root",
                    tmp,
                    "advance-q1",
                    "--state",
                    "state.json",
                    "--completed-phase",
                    "H0-A",
                    "--attempt-id",
                    "a-attempt",
                    "--checkpoint-index",
                    "artifacts/h0_runs/h0/checkpoints/a-attempt/index.json",
                    "--checkpoint-index-sha256",
                    "b" * 64,
                    "--runtime-definition-sha256",
                    "c" * 64,
                    "--commit",
                ]
            )

        root = Path(tmp).resolve()
        self.assertEqual((run_code, advance_code), (0, 0))
        runner.assert_awaited_once_with(
            root=root,
            state_path=root / "state.json",
            artifacts_root=root / "artifacts/h0_runs",
            stage_attempt_id="b-attempt",
            candidate_id="Q1",
            phase="H0-B",
        )
        advance.assert_called_once_with(
            root / "state.json",
            root=root,
            completed_phase="H0-A",
            stage_attempt_id="a-attempt",
            checkpoint_index_path=(
                "artifacts/h0_runs/h0/checkpoints/a-attempt/index.json"
            ),
            checkpoint_index_sha256="b" * 64,
            runtime_definition_sha256="c" * 64,
            dry_run=False,
        )

    def _invoke(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = h0_control.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_offline_resolve_writes_then_verifies_without_state_or_live_runtime(self):
        order: list[str] = []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            h0_control,
            "write_h0_offline_artifacts",
            side_effect=lambda root: order.append("write")
            or {"index_path": "artifacts/h0/index.json", "index_sha256": "a" * 64},
        ), patch.object(
            h0_control,
            "verify_h0_offline_artifacts",
            side_effect=lambda root: order.append("verify")
            or {
                "status": "verified_offline_not_live_authorized",
                "index_path": "artifacts/h0/index.json",
                "index_sha256": "a" * 64,
                "live_eligible": False,
            },
        ), patch.object(
            h0_control,
            "transition_q1_h0_a_live",
            side_effect=AssertionError("must not authorize"),
        ), patch.object(
            h0_control,
            "execute_h0_a_live",
            side_effect=AssertionError("must not access live runtime"),
        ):
            code, output, error = self._invoke(
                ["--root", tmp, "offline-resolve"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(order, ["write", "verify"])
        self.assertIn("verified_offline_not_live_authorized", output)
        self.assertEqual(error, "")

    def test_bind_uses_explicit_tdd_evidence_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "tdd.json"
            evidence = {
                name: {"path": f"{name}.log", "sha256": "a" * 64, "test_count": 1}
                for name in (
                    "latest_red",
                    "latest_green",
                    "latest_focused",
                    "latest_full_regression",
                )
            }
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            binder = Mock(
                return_value={
                    "status": "h0_offline_verified_not_live_authorized",
                    "live_h0_candidate_authorized": False,
                    "authorized_live_actions": [],
                }
            )
            with patch.object(
                h0_control, "verify_and_persist_h0_offline_bound_state", binder
            ):
                code, output, error = self._invoke(
                    [
                        "--root",
                        tmp,
                        "bind",
                        "--state",
                        "CURRENT_STATE.json",
                        "--tdd-evidence",
                        str(evidence_path),
                    ]
                )

            self.assertEqual(code, 0)
            binder.assert_called_once_with(
                root / "CURRENT_STATE.json", root=root.resolve(), tdd_evidence=evidence
            )
            self.assertIn("h0_offline_verified_not_live_authorized", output)
            self.assertEqual(error, "")

    def test_authorize_defaults_to_dry_run_and_commit_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            transition = Mock(
                return_value={
                    "status": "h0_q1_a_live_only",
                    "current_action_scope": "h0_q1_a_live_only",
                }
            )
            with patch.object(h0_control, "transition_q1_h0_a_live", transition):
                dry_code, dry_output, _ = self._invoke(
                    ["--root", tmp, "authorize-q1-a", "--state", "state.json"]
                )
                commit_code, commit_output, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "authorize-q1-a",
                        "--state",
                        "state.json",
                        "--commit",
                    ]
                )

            self.assertEqual((dry_code, commit_code), (0, 0))
            self.assertEqual(
                [call.kwargs["dry_run"] for call in transition.call_args_list],
                [True, False],
            )
            self.assertIn('"committed": false', dry_output)
            self.assertIn('"committed": true', commit_output)

    def test_revoke_defaults_to_dry_run_and_binds_explicit_violation_evidence(self):
        revoked = Mock(
            return_value={
                "status": "h0_live_authorization_revoked",
                "current_action_scope": "h0_live_forbidden",
            }
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            h0_control, "transition_h0_live_authorization_revoke", revoked
        ):
            arguments = [
                "--root",
                tmp,
                "revoke-h0-live",
                "--state",
                "state.json",
                "--candidate-id",
                "Q1",
                "--phase",
                "H0-A",
                "--attempt-id",
                "attempt-001",
                "--checkpoint-index",
                "artifacts/h0_runs/h0/checkpoints/attempt-001/index.json",
                "--checkpoint-index-sha256",
                "a" * 64,
            ]
            dry_code, dry_output, dry_error = self._invoke(arguments)
            commit_code, commit_output, commit_error = self._invoke(
                [*arguments, "--commit"]
            )

        root = Path(tmp).resolve()
        self.assertEqual((dry_code, commit_code), (0, 0))
        self.assertEqual(
            [call.kwargs for call in revoked.call_args_list],
            [
                {
                    "root": root,
                    "candidate_id": "Q1",
                    "phase": "H0-A",
                    "stage_attempt_id": "attempt-001",
                    "checkpoint_index_path": (
                        "artifacts/h0_runs/h0/checkpoints/attempt-001/index.json"
                    ),
                    "checkpoint_index_sha256": "a" * 64,
                    "dry_run": True,
                },
                {
                    "root": root,
                    "candidate_id": "Q1",
                    "phase": "H0-A",
                    "stage_attempt_id": "attempt-001",
                    "checkpoint_index_path": (
                        "artifacts/h0_runs/h0/checkpoints/attempt-001/index.json"
                    ),
                    "checkpoint_index_sha256": "a" * 64,
                    "dry_run": False,
                },
            ],
        )
        self.assertIn('"committed": false', dry_output)
        self.assertIn('"committed": true', commit_output)
        self.assertIn("protocol_gate_order_violation", dry_output)
        self.assertIn('"candidate_rerun_authorized": false', commit_output)
        self.assertIn('"candidate_advance_authorized": false', commit_output)
        self.assertEqual(dry_error + commit_error, "")

    def test_run_never_transitions_and_maps_infrastructure_to_75_safely(self):
        private = "private transport detail"
        runner = AsyncMock(
            side_effect=H0InfrastructureError(f"vllm_unreachable: {private}")
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            h0_control, "execute_h0_a_live", runner
        ), patch.object(
            h0_control,
            "transition_q1_h0_a_live",
            side_effect=AssertionError("run must never authorize"),
        ):
            code, output, error = self._invoke(
                [
                    "--root",
                    tmp,
                    "run-q1-a",
                    "--state",
                    "state.json",
                    "--attempt-id",
                    "attempt-001",
                ]
            )

        self.assertEqual(code, 75)
        runner.assert_awaited_once()
        combined = output + error
        self.assertIn("vllm_unreachable", combined)
        self.assertNotIn(private, combined)

    def test_run_success_uses_root_anchored_paths_and_never_transitions(self):
        runner = AsyncMock(
            return_value={
                "status": "stage_complete",
                "checkpoint_index_path": "h0/checkpoints/attempt-001/index.json",
                "checkpoint_index_sha256": "a" * 64,
            }
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            h0_control, "execute_h0_a_live", runner
        ), patch.object(
            h0_control,
            "transition_q1_h0_a_live",
            side_effect=AssertionError("run must never authorize or advance"),
        ):
            code, output, error = self._invoke(
                [
                    "--root",
                    tmp,
                    "run-q1-a",
                    "--state",
                    "state.json",
                    "--attempt-id",
                    "attempt-001",
                ]
            )

        root = Path(tmp).resolve()
        self.assertEqual(code, 0)
        runner.assert_awaited_once_with(
            root=root,
            state_path=root / "state.json",
            artifacts_root=root / "artifacts/h0_runs",
            stage_attempt_id="attempt-001",
        )
        self.assertIn("stage_complete", output)
        self.assertEqual(error, "")

    def test_argument_failure_is_code_2_without_echoing_private_input(self):
        private = "private-operator-value-must-not-be-logged"
        with tempfile.TemporaryDirectory() as tmp:
            code, output, error = self._invoke(
                [
                    "--root",
                    tmp,
                    "run-q1-a",
                    "--attempt-id",
                    "attempt-001",
                    "--unknown",
                    private,
                ]
            )

        combined = output + error
        self.assertEqual(code, 2)
        self.assertIn("argument_error", combined)
        self.assertNotIn(private, combined)

    def test_run_rejects_state_or_artifacts_outside_experiment_root(self):
        runner = AsyncMock(
            return_value={"status": "stage_complete", "checkpoint_index_path": "x"}
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as outside,
            patch.object(h0_control, "execute_h0_a_live", runner),
        ):
            code, output, error = self._invoke(
                [
                    "--root",
                    tmp,
                    "run-q1-a",
                    "--state",
                    str(Path(outside) / "state.json"),
                    "--artifacts",
                    str(Path(outside) / "checkpoints"),
                    "--attempt-id",
                    "attempt-001",
                ]
            )

        self.assertEqual(code, 20)
        runner.assert_not_awaited()
        self.assertIn("control_input_invalid", output + error)


if __name__ == "__main__":
    import unittest

    unittest.main()
