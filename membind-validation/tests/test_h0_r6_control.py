"""Control-plane contracts for the four explicit R6 recovery actions."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_control  # noqa: E402
from h0_repair_admission import H0_B_R6_DECISION_PATH  # noqa: E402


class H0R6ControlTests(TestCase):
    def _invoke(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = h0_control.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_r6_recovery_uses_four_explicit_non_live_then_live_actions(self):
        verification = {
            "status": "verified_offline_not_live_authorized",
            "artifact_set_id": "v1_3_harness_r6",
            "execution_harness_revision": 6,
            "index_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r6/"
                "resolved_manifest_index_v1_3_harness_r6.json"
            ),
            "index_sha256": "6" * 64,
            "source_bundle_sha256": "7" * 64,
        }
        classification = {"scientific_status": "infrastructure_interrupted"}
        admission = {"schema_version": "membind.h0.r6-recovery-admission.v1"}
        bindings = {
            "resolved_manifest_index_path": verification["index_path"],
            "resolved_manifest_index_sha256": verification["index_sha256"],
            "resolved_candidate_manifest_path": "candidate.json",
            "resolved_candidate_manifest_sha256": "1" * 64,
            "resolved_shared_base_manifest_path": "shared.json",
            "resolved_shared_base_manifest_sha256": "2" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            evidence_path = root / "r6-tdd.json"
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
            decision_path = root / H0_B_R6_DECISION_PATH
            decision_path.parent.mkdir(parents=True)
            decision_path.write_text("{}", encoding="ascii")

            verifier = Mock(return_value=verification)
            classify = Mock(return_value=classification)
            writer = Mock(
                return_value={
                    "decision_path": H0_B_R6_DECISION_PATH,
                    "decision_sha256": "8" * 64,
                }
            )
            decision_verifier = Mock(return_value=admission)
            binding_builder = Mock(return_value=bindings)
            revoke = Mock(
                return_value={"status": "h0_b_r6_recovery_required_live_revoked"}
            )
            binder = Mock(
                return_value={
                    "status": "h0_b_r6_recovery_verified_not_live_authorized"
                }
            )
            authorizer = Mock(return_value={"status": "h0_q1_b_live_only"})
            with (
                patch.object(h0_control, "verify_h0_offline_artifacts", verifier),
                patch.object(h0_control, "_r6_classification", classify),
                patch.object(h0_control, "write_h0_b_r6_recovery_decision", writer),
                patch.object(
                    h0_control,
                    "verify_h0_b_r6_recovery_decision",
                    decision_verifier,
                ),
                patch.object(h0_control, "_r6_artifact_bindings", binding_builder),
                patch.object(h0_control, "transition_h0_b_r6_recovery_revoke", revoke),
                patch.object(h0_control, "transition_h0_b_r6_recovery_bound", binder),
                patch.object(h0_control, "transition_h0_b_r6_recovery_live", authorizer),
            ):
                prepare_code, _, _ = self._invoke(
                    ["--root", tmp, "prepare-h0-b-r6-recovery"]
                )
                revoke_code, revoke_output, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "revoke-h0-b-r6-recovery",
                        "--state",
                        "CURRENT_STATE.json",
                    ]
                )
                bind_code, bind_output, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "bind-h0-b-r6-recovery",
                        "--state",
                        "CURRENT_STATE.json",
                        "--tdd-evidence",
                        str(evidence_path),
                        "--decision",
                        H0_B_R6_DECISION_PATH,
                        "--decision-sha256",
                        "8" * 64,
                        "--commit",
                    ]
                )
                authorize_code, authorize_output, _ = self._invoke(
                    [
                        "--root",
                        tmp,
                        "authorize-q1-b-r6-replacement",
                        "--state",
                        "CURRENT_STATE.json",
                        "--commit",
                    ]
                )

        self.assertEqual(
            (prepare_code, revoke_code, bind_code, authorize_code),
            (0, 0, 0, 0),
        )
        self.assertEqual(verifier.call_count, 2)
        self.assertEqual(classify.call_count, 3)
        writer.assert_called_once_with(
            root=root,
            classification=classification,
            manifest_verification=verification,
        )
        revoke.assert_called_once_with(
            root / "CURRENT_STATE.json",
            root=root,
            classification=classification,
            dry_run=True,
        )
        decision_verifier.assert_called_once_with(
            root=root,
            decision_path=H0_B_R6_DECISION_PATH,
            decision_sha256="8" * 64,
            classification=classification,
            manifest_verification=verification,
        )
        binding_builder.assert_called_once_with(root, verification)
        binder.assert_called_once_with(
            root / "CURRENT_STATE.json",
            root=root,
            manifest_verification=verification,
            tdd_evidence=evidence,
            r6_recovery_admission=admission,
            artifact_bindings=bindings,
            dry_run=False,
        )
        authorizer.assert_called_once_with(
            root / "CURRENT_STATE.json",
            root=root,
            dry_run=False,
        )
        self.assertIn('"committed": false', revoke_output)
        self.assertIn('"committed": true', bind_output)
        self.assertIn('"committed": true', authorize_output)


if __name__ == "__main__":
    import unittest

    unittest.main()
