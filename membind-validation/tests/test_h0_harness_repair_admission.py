"""Offline TDD contracts for the Q1/H0-B harness-repair decision.

The r2 attempt failed before any model workload.  These tests bind that exact
immutable evidence to one r3 whole-stage replacement without contacting a
model, embedding service, Neo4j, SSH, or reading project credentials.
"""

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
    build_h0_b_harness_repair_decision,
    verify_h0_b_harness_repair_decision,
    write_h0_b_harness_repair_decision,
)
from h0_runtime import (  # noqa: E402
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_file,
)


class H0BHarnessRepairAdmissionTests(TestCase):
    old_attempt_id = "h0-q1-b-20260809-attempt-001"
    replacement_attempt_id = "h0-q1-b-20260809-replacement-001"
    old_checkpoint_sha256 = (
        "fa6280ede4387775c719abd410478b5e1db358d840a10a69025c5a6cddd48896"
    )
    failure_report_sha256 = (
        "2bde8463ba862a13d4e3b580e3accc7ce0cf15f1eccdd923fee167eb91b7be31"
    )
    prior_index_sha256 = (
        "be31de29de13fb0d607570cbc1832c7df32fe83af51ec3ab31722ec036f172cf"
    )
    decision_relative = (
        "artifacts/h0_protocol_repair/decisions/"
        "q1_h0_b_harness_compatibility_repair.json"
    )

    def _copy(self, root: Path, relative: str) -> None:
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    def _fixture(self, root: Path) -> dict:
        self._copy(root, "artifacts/h0_manifest_sets/v1_3_harness_r2")
        self._copy(
            root,
            "artifacts/h0_runs/h0/checkpoints/h0-q1-b-20260809-attempt-001",
        )
        self._copy(
            root,
            "artifacts/diagnostics/h0_q1_b_preworkload_failure_report_20260809.json",
        )

        r2_index_path = (
            root
            / "artifacts/h0_manifest_sets/v1_3_harness_r2/"
            "resolved_manifest_index_v1_3_harness_r2.json"
        )
        self.assertEqual(sha256_file(r2_index_path), self.prior_index_sha256)
        r2_index = json.loads(r2_index_path.read_text(encoding="ascii"))
        old_q1_path = root / r2_index["resolved_manifests"]["Q1"]["path"]
        q1 = json.loads(old_q1_path.read_text(encoding="ascii"))

        r3_root = root / "artifacts/h0_manifest_sets/v1_3_harness_r3"
        q1_sha = canonical_json_sha256(q1)
        q1_relative = (
            "artifacts/h0_manifest_sets/v1_3_harness_r3/resolved_candidates/"
            f"Q1.{q1_sha}.json"
        )
        q1_path = root / q1_relative
        q1_path.parent.mkdir(parents=True, exist_ok=True)
        q1_path.write_bytes(canonical_json_bytes(q1))
        r3_index = {
            "schema_version": "membind.h0.offline-artifacts.v2",
            "protocol_version": "current-validation-v1.3",
            "artifact_set_id": "v1_3_harness_r3",
            "execution_harness_revision": 3,
            "status": "offline_resolved_not_live_authorized",
            "live_h0_candidate_authorized": False,
            "resolved_manifests": {
                "Q1": {"path": q1_relative, "sha256": q1_sha},
            },
            "source_specs_immutable": True,
            "unresolved_fields": [],
            "secrets_persisted": False,
        }
        index_relative = (
            "artifacts/h0_manifest_sets/v1_3_harness_r3/"
            "resolved_manifest_index_v1_3_harness_r3.json"
        )
        index_path = root / index_relative
        r3_root.mkdir(parents=True, exist_ok=True)
        index_path.write_bytes(canonical_json_bytes(r3_index))
        return {
            "schema_version": "membind.h0.offline-artifact-verification.v3",
            "protocol_version": "current-validation-v1.3",
            "artifact_set_id": "v1_3_harness_r3",
            "execution_harness_revision": 3,
            "status": "verified_offline_not_live_authorized",
            "index_path": index_relative,
            "index_sha256": sha256_file(index_path),
            "generated_json_file_count": 11,
            "binding_count": 10,
            "resolved_wrapper_count": 4,
            "source_spec_count": 4,
            "execution_source_count": 32,
            "secret_scan_passed": True,
            "live_eligible": False,
        }

    def _build(self, root: Path, verification: dict) -> dict:
        return build_h0_b_harness_repair_decision(
            root=root,
            manifest_verification=verification,
            replacement_attempt_id=self.replacement_attempt_id,
        )

    def _write(self, root: Path, verification: dict) -> dict:
        return write_h0_b_harness_repair_decision(
            root=root,
            manifest_verification=verification,
            replacement_attempt_id=self.replacement_attempt_id,
        )

    def test_build_is_deterministic_and_exactly_binds_zero_workload_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            first = self._build(root, verification)
            second = self._build(root, deepcopy(verification))

            self.assertEqual(first, second)
            self.assertEqual(
                first,
                {
                    "schema_version": (
                        "membind.h0.harness-compatibility-repair-decision.v1"
                    ),
                    "protocol_version": "current-validation-v1.3",
                    "status": (
                        "approved_one_shot_whole_stage_replacement_"
                        "not_live_authorized"
                    ),
                    "candidate_id": "Q1",
                    "phase": "H0-B",
                    "decision_result_blind": False,
                    "prior_model_workload_output_observed": False,
                    "repair_required_independent_of_model_output": True,
                    "repair_reason": "preworkload_harness_compatibility_failure",
                    "scientific_configuration_unchanged": True,
                    "one_shot_whole_stage_replacement": True,
                    "old_attempt_qualification_reusable": False,
                    "old_and_new_trial_counts_mergeable": False,
                    "invalidated_attempt": {
                        "stage_attempt_id": self.old_attempt_id,
                        "checkpoint_index_path": (
                            "artifacts/h0_runs/h0/checkpoints/"
                            f"{self.old_attempt_id}/index.json"
                        ),
                        "checkpoint_index_sha256": self.old_checkpoint_sha256,
                        "failure_report_path": (
                            "artifacts/diagnostics/"
                            "h0_q1_b_preworkload_failure_report_20260809.json"
                        ),
                        "failure_report_sha256": self.failure_report_sha256,
                        "logical_trial_count": 0,
                        "http_attempt_count": 0,
                        "source_checkpoint_count": 0,
                        "fresh_graph_count": 0,
                        "embedding_workload_request_count": 0,
                    },
                    "prior_execution_binding": {
                        "artifact_set_id": "v1_3_harness_r2",
                        "execution_harness_revision": 2,
                        "manifest_index_path": (
                            "artifacts/h0_manifest_sets/v1_3_harness_r2/"
                            "resolved_manifest_index_v1_3_harness_r2.json"
                        ),
                        "manifest_index_sha256": self.prior_index_sha256,
                    },
                    "repaired_execution_binding": {
                        "artifact_set_id": "v1_3_harness_r3",
                        "execution_harness_revision": 3,
                        "manifest_index_path": verification["index_path"],
                        "manifest_index_sha256": verification["index_sha256"],
                        "manifest_verification_sha256": canonical_json_sha256(
                            verification
                        ),
                    },
                    "replacement": {
                        "attempt_id": self.replacement_attempt_id,
                        "candidate_id": "Q1",
                        "phase": "H0-B",
                        "whole_stage": True,
                        "one_shot": True,
                        "old_attempt_id": self.old_attempt_id,
                        "old_attempt_trials_reused": False,
                        "live_authorized_by_this_artifact": False,
                    },
                    "secrets_persisted": False,
                    "raw_prompts_persisted": False,
                    "raw_responses_persisted": False,
                },
            )

    def test_write_is_atomic_immutable_exact_path_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            first = self._write(root, verification)
            path = root / self.decision_relative
            first_bytes = path.read_bytes()
            first_stat = path.stat()
            second = self._write(root, deepcopy(verification))

            self.assertEqual(first, second)
            self.assertEqual(first["decision_path"], self.decision_relative)
            self.assertEqual(first["decision_sha256"], sha256_file(path))
            self.assertEqual(path.read_bytes(), first_bytes)
            self.assertEqual(path.stat().st_ino, first_stat.st_ino)
            leftovers = list(path.parent.glob(".*h0-b-repair-*.tmp"))
            self.assertEqual(leftovers, [])

    def test_write_rejects_existing_mismatch_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            path = root / self.decision_relative
            path.parent.mkdir(parents=True, exist_ok=True)
            original = b"user-owned-mismatched-bytes\n"
            path.write_bytes(original)

            with self.assertRaises(H0RepairAdmissionError):
                self._write(root, verification)
            self.assertEqual(path.read_bytes(), original)

    def test_verify_returns_exact_20_field_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            written = self._write(root, verification)
            admission = verify_h0_b_harness_repair_decision(
                root=root,
                decision_path=written["decision_path"],
                decision_sha256=written["decision_sha256"],
                manifest_verification=verification,
            )
            self.assertEqual(
                set(admission),
                {
                    "schema_version",
                    "protocol_version",
                    "candidate_id",
                    "phase",
                    "decision_path",
                    "decision_sha256",
                    "decision_result_blind",
                    "prior_model_workload_output_observed",
                    "repair_required_independent_of_model_output",
                    "scientific_configuration_unchanged",
                    "one_shot_whole_stage_replacement",
                    "replacement_attempt_id",
                    "invalidated_stage_attempt_id",
                    "invalidated_checkpoint_index_sha256",
                    "failure_report_sha256",
                    "old_attempt_qualification_reusable",
                    "old_and_new_trial_counts_mergeable",
                    "prior_manifest_index_sha256",
                    "repaired_manifest_index_sha256",
                    "secrets_persisted",
                },
            )
            self.assertEqual(len(admission), 20)
            self.assertEqual(
                admission["schema_version"],
                "membind.h0.harness-repair-admission.v1",
            )
            self.assertEqual(admission["decision_path"], self.decision_relative)
            self.assertEqual(admission["decision_sha256"], written["decision_sha256"])
            self.assertEqual(
                admission["invalidated_checkpoint_index_sha256"],
                self.old_checkpoint_sha256,
            )
            self.assertEqual(
                admission["failure_report_sha256"], self.failure_report_sha256
            )
            self.assertEqual(
                admission["prior_manifest_index_sha256"], self.prior_index_sha256
            )
            self.assertEqual(
                admission["repaired_manifest_index_sha256"],
                verification["index_sha256"],
            )

    def test_verify_rejects_tamper_wrong_path_and_extra_field(self):
        mutations = (
            ("tamper", lambda value: value.__setitem__("decision_result_blind", True)),
            ("extra", lambda value: value.__setitem__("unexpected", False)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                verification = self._fixture(root)
                written = self._write(root, verification)
                path = root / written["decision_path"]
                value = json.loads(path.read_text(encoding="ascii"))
                mutate(value)
                path.write_bytes(canonical_json_bytes(value))
                with self.assertRaises(H0RepairAdmissionError):
                    verify_h0_b_harness_repair_decision(
                        root=root,
                        decision_path=written["decision_path"],
                        decision_sha256=sha256_file(path),
                        manifest_verification=verification,
                    )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            written = self._write(root, verification)
            wrong = root / "artifacts/h0_protocol_repair/decisions/wrong.json"
            wrong.write_bytes((root / written["decision_path"]).read_bytes())
            with self.assertRaises(H0RepairAdmissionError):
                verify_h0_b_harness_repair_decision(
                    root=root,
                    decision_path=wrong.relative_to(root).as_posix(),
                    decision_sha256=sha256_file(wrong),
                    manifest_verification=verification,
                )

    def test_rejects_r3_binding_drift_replacement_drift_and_scientific_drift(self):
        verification_mutations = (
            {"artifact_set_id": "v1_3_harness_r4"},
            {"execution_harness_revision": 4},
            {"index_path": "artifacts/h0_manifest_sets/v1_3_harness_r3/wrong.json"},
            {"index_sha256": "f" * 64},
        )
        for updates in verification_mutations:
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                verification = self._fixture(root) | updates
                with self.assertRaises(H0RepairAdmissionError):
                    self._build(root, verification)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            with self.assertRaises(H0RepairAdmissionError):
                build_h0_b_harness_repair_decision(
                    root=root,
                    manifest_verification=verification,
                    replacement_attempt_id="h0-q1-b-20260809-replacement-002",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            index_path = root / verification["index_path"]
            index = json.loads(index_path.read_text(encoding="ascii"))
            q1_path = root / index["resolved_manifests"]["Q1"]["path"]
            q1 = json.loads(q1_path.read_text(encoding="ascii"))
            q1["candidate_configuration"]["requested_max_tokens"] = 8192
            changed_sha = canonical_json_sha256(q1)
            changed_path = q1_path.with_name(f"Q1.{changed_sha}.json")
            changed_path.write_bytes(canonical_json_bytes(q1))
            q1_path.unlink()
            index["resolved_manifests"]["Q1"] = {
                "path": changed_path.relative_to(root).as_posix(),
                "sha256": changed_sha,
            }
            index_path.write_bytes(canonical_json_bytes(index))
            verification["index_sha256"] = sha256_file(index_path)
            with self.assertRaises(H0RepairAdmissionError):
                self._build(root, verification)

    def test_decision_and_admission_are_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = self._fixture(root)
            decision = self._build(root, verification)
            written = self._write(root, verification)
            admission = verify_h0_b_harness_repair_decision(
                root=root,
                decision_path=written["decision_path"],
                decision_sha256=written["decision_sha256"],
                manifest_verification=verification,
            )
            encoded = json.dumps(
                {"decision": decision, "admission": admission}, sort_keys=True
            ).casefold()
            for forbidden in (
                "api_key",
                "authorization",
                "bearer ",
                "raw_prompt\"",
                "raw_response\"",
                ".env",
                "gpt55_temporary",
            ):
                self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    import unittest

    unittest.main()
