"""Offline TDD contracts for the disclosed one-shot Q1/H0-A repair."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_repair_admission import (  # noqa: E402
    H0RepairAdmissionError,
    build_h0_repair_decision,
    verify_h0_repair_decision,
    write_h0_repair_decision,
)
from h0_runtime import (  # noqa: E402
    H0CheckpointStore,
    H0StateGateError,
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_file,
)


class H0RepairAdmissionTests(TestCase):
    replacement_id = "h0-q1-a-20260809-replacement-001"

    def _copy(self, root: Path, relative: str) -> None:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)

    def _fixture(self, root: Path) -> dict:
        for relative in (
            "artifacts/diagnostics/h0_q1_a_protocol_invalidation_20260809.json",
            "artifacts/diagnostics/h0_q1_a_result_20260809.json",
            "artifacts/h0/resolved_manifest_index_v1_3.json",
            "artifacts/h0/resolved_candidates/Q1.4a76ab5c1abb91e86f36787b7bc78bda12f000964e38c0b200925e86859056bb.json",
            "artifacts/h0/manifests/semantic_guardrail_v1_3.9ce33ca7764cc061dfe3399ca3494471d120a750302b09aa18579c6fb4d4a6e1.json",
            "artifacts/h0_runs/h0/checkpoints/h0-q1-a-20260809-attempt-001/index.json",
        ):
            self._copy(root, relative)

        legacy_candidate = json.loads(
            (root / "artifacts/h0/resolved_candidates/Q1.4a76ab5c1abb91e86f36787b7bc78bda12f000964e38c0b200925e86859056bb.json").read_text(
                encoding="ascii"
            )
        )
        r2_candidate = deepcopy(legacy_candidate)
        r2_candidate["artifact_set_id"] = "v1_3_harness_r2"
        r2_candidate["execution_harness_revision"] = 2
        candidate_sha = canonical_json_sha256(r2_candidate)
        candidate_relative = (
            "artifacts/h0_manifest_sets/v1_3_harness_r2/resolved_candidates/"
            f"Q1.{candidate_sha}.json"
        )
        candidate_path = root / candidate_relative
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_bytes(canonical_json_bytes(r2_candidate))
        index = {
            "schema_version": "membind.h0.offline-artifacts.v2",
            "protocol_version": "current-validation-v1.3",
            "artifact_set_id": "v1_3_harness_r2",
            "execution_harness_revision": 2,
            "status": "offline_resolved_not_live_authorized",
            "live_h0_candidate_authorized": False,
            "resolved_manifests": {
                "Q1": {"path": candidate_relative, "sha256": candidate_sha},
            },
            "source_specs_immutable": True,
            "unresolved_fields": [],
            "secrets_persisted": False,
        }
        index_relative = (
            "artifacts/h0_manifest_sets/v1_3_harness_r2/"
            "resolved_manifest_index_v1_3_harness_r2.json"
        )
        index_path = root / index_relative
        index_path.write_bytes(canonical_json_bytes(index))
        return {
            "schema_version": "membind.h0.offline-artifact-verification.v2",
            "protocol_version": "current-validation-v1.3",
            "artifact_set_id": "v1_3_harness_r2",
            "execution_harness_revision": 2,
            "status": "verified_offline_not_live_authorized",
            "index_path": index_relative,
            "index_sha256": sha256_file(index_path),
            "generated_json_file_count": 10,
            "binding_count": 9,
            "resolved_wrapper_count": 4,
            "source_spec_count": 4,
            "secret_scan_passed": True,
            "live_eligible": False,
        }

    def test_build_write_verify_discloses_nonblind_result_and_unchanged_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            decision = build_h0_repair_decision(
                root=root,
                manifest_verification=verification,
                replacement_attempt_id=self.replacement_id,
            )
            self.assertFalse(decision["decision_result_blind"])
            self.assertTrue(decision["prior_technical_outcome_observed"])
            self.assertEqual(decision["prior_observation"]["logical_trial_count"], 3)
            self.assertEqual(decision["prior_observation"]["http_attempt_count"], 3)
            self.assertTrue(decision["repair_required_independent_of_output"])
            self.assertFalse(decision["old_attempt_qualification_reusable"])
            self.assertFalse(decision["old_and_new_trial_counts_mergeable"])
            self.assertEqual(decision["candidate_order"], ["Q1", "Q2", "Q3"])
            self.assertEqual(decision["replacement"]["attempt_id"], self.replacement_id)

            written = write_h0_repair_decision(
                root=root,
                manifest_verification=verification,
                replacement_attempt_id=self.replacement_id,
            )
            admission = verify_h0_repair_decision(
                root=root,
                decision_path=written["decision_path"],
                decision_sha256=written["decision_sha256"],
                manifest_verification=verification,
            )
            self.assertEqual(admission["schema_version"], "membind.h0.repair-admission.v1")
            self.assertEqual(admission["replacement_attempt_id"], self.replacement_id)
            self.assertFalse(admission["decision_result_blind"])
            self.assertTrue(admission["one_shot_replacement"])
            encoded = json.dumps({"decision": decision, "admission": admission})
            self.assertNotIn("api_key", encoded.casefold())
            self.assertNotIn("authorization", encoded.casefold())

    def test_rejects_tamper_manifest_drift_and_second_decision_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            written = write_h0_repair_decision(
                root=root,
                manifest_verification=verification,
                replacement_attempt_id=self.replacement_id,
            )
            decision_path = root / written["decision_path"]
            decision = json.loads(decision_path.read_text(encoding="ascii"))
            decision["decision_result_blind"] = True
            decision_path.write_bytes(canonical_json_bytes(decision))
            with self.assertRaises(H0RepairAdmissionError):
                verify_h0_repair_decision(
                    root=root,
                    decision_path=written["decision_path"],
                    decision_sha256=sha256_file(decision_path),
                    manifest_verification=verification,
                )

            verification = verification | {"index_sha256": "f" * 64}
            with self.assertRaises(H0RepairAdmissionError):
                build_h0_repair_decision(
                    root=root,
                    manifest_verification=verification,
                    replacement_attempt_id=self.replacement_id,
                )

    def test_checkpoint_admission_allows_exact_replacement_once_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            source_attempt = ROOT / "artifacts/h0_runs/h0/checkpoints/h0-q1-a-20260809-attempt-001"
            target_attempt = root / "runs/h0/checkpoints/h0-q1-a-20260809-attempt-001"
            target_attempt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_attempt, target_attempt)

            written = write_h0_repair_decision(
                root=root,
                manifest_verification=verification,
                replacement_attempt_id=self.replacement_id,
            )
            admission = verify_h0_repair_decision(
                root=root,
                decision_path=written["decision_path"],
                decision_sha256=written["decision_sha256"],
                manifest_verification=verification,
            )

            with self.assertRaises(H0StateGateError):
                H0CheckpointStore(
                    root=root / "runs",
                    stage_attempt_id="unapproved-replacement",
                    candidate_id="Q1",
                    phase="H0-A",
                )

            replacement = H0CheckpointStore(
                root=root / "runs",
                stage_attempt_id=self.replacement_id,
                candidate_id="Q1",
                phase="H0-A",
                repair_admission=admission,
            )
            self.assertTrue(replacement.index["protocol_repair_replacement"])
            self.assertEqual(replacement.index["prior_matching_attempt_count"], 1)
            replacement.mark_candidate_failure("test_terminal", "e" * 64)

            with self.assertRaises(H0StateGateError):
                H0CheckpointStore(
                    root=root / "runs",
                    stage_attempt_id="second-replacement",
                    candidate_id="Q1",
                    phase="H0-A",
                    repair_admission=admission,
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
