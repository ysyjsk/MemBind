"""Offline TDD contracts for prior-phase terminal completion validation.

Every checkpoint in this module is synthesized below a temporary root.  The
tests never inspect credentials, live services, or the repository's real run
artifacts.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_completion import (  # noqa: E402
    H0CompletionValidationError,
    validate_h0_prior_phase_terminal_completion,
)
from h0_runtime import canonical_json_sha256  # noqa: E402


def _checkpoint_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


class H0CompletionValidatorTests(TestCase):
    attempt_id = "h0-q1-a-complete-001"
    runtime_definition_sha256 = "d" * 64

    def _fixture(
        self,
        root: Path,
        *,
        phase_result_overrides: dict | None = None,
        terminal_result_override: str | None = None,
        terminal_position: int = 7,
        extra_terminal: bool = False,
    ) -> tuple[str, str, dict]:
        artifact_prefix = Path("artifacts/h0_runs")
        attempt_relative = Path("h0/checkpoints") / self.attempt_id
        attempt_root = root / artifact_prefix / attempt_relative
        attempt_root.mkdir(parents=True)
        phase_result = {
            "schema_version": "membind.h0.phase-result.v1",
            "stage_attempt_id": self.attempt_id,
            "phase": "H0-A",
            "question_id": "07741c45",
            "qualified": True,
            "logical_call_count": 3,
            "http_attempt_count": 3,
            "semantic_record_count": 3,
            "retry_count": 0,
            "secrets_persisted": False,
        }
        phase_result.update(phase_result_overrides or {})
        phase_result_sha256 = canonical_json_sha256(phase_result)
        terminal_payload = {
            "phase_result": phase_result,
            "phase_result_sha256": phase_result_sha256,
            "attempt_ledger": {
                "schema_version": "membind.h0.attempt-ledger.v1",
                "stage_attempt_id": self.attempt_id,
                "logical_trials": [
                    {
                        "candidate_id": "Q1",
                        "repeated_trial_index": index,
                    }
                    for index in range(3)
                ],
                "http_attempts": [
                    {
                        "candidate_id": "Q1",
                        "retry_index": 0,
                        "http_200": True,
                        "semantic_utility_success": True,
                    }
                    for _ in range(3)
                ],
                "secrets_persisted": False,
                "raw_prompts_persisted": False,
                "raw_responses_persisted": False,
            },
            "runtime_evidence": {
                "fresh_client_count": 3,
                "db_calls": 0,
                "embedding_calls": 0,
                "tokenize_events": [[], [], []],
                "wire_events": [[], [], []],
                "secrets_persisted": False,
                "raw_prompts_persisted": False,
                "raw_responses_persisted": False,
            },
            "runtime_definition_sha256": self.runtime_definition_sha256,
        }
        specifications = [
            ("readiness_check", "000-vllm_version", {"check": "vllm_version"}),
            ("readiness_check", "001-served_model", {"check": "served_model"}),
            ("readiness_check", "002-health", {"check": "health"}),
            ("readiness_result", "ready", {"status": "ready"}),
            ("logical_trial", "trial-000", {"repeated_trial_index": 0}),
            ("logical_trial", "trial-001", {"repeated_trial_index": 1}),
            ("logical_trial", "trial-002", {"repeated_trial_index": 2}),
            ("stage_result", "qualified", terminal_payload),
        ]
        terminal = specifications.pop()
        specifications.insert(terminal_position, terminal)
        if extra_terminal:
            specifications.append(("stage_result", "qualified-extra", terminal_payload))

        entries: list[dict] = []
        for ordinal, (kind, segment_id, payload) in enumerate(specifications):
            artifact = {
                "schema_version": "membind.h0.checkpoint-segment.v1",
                "protocol_version": "current-validation-v1.3",
                "stage_attempt_id": self.attempt_id,
                "segment_ordinal": ordinal,
                "segment_kind": kind,
                "segment_id": segment_id,
                "payload": payload,
                "secrets_persisted": False,
                "raw_prompts_persisted": False,
                "raw_responses_persisted": False,
            }
            encoded = _checkpoint_bytes(artifact)
            digest = hashlib.sha256(encoded).hexdigest()
            filename = f"{ordinal:06d}.{kind}.{segment_id}.{digest}.json"
            (attempt_root / filename).write_bytes(encoded)
            entries.append(
                {
                    "segment_ordinal": ordinal,
                    "segment_kind": kind,
                    "segment_id": segment_id,
                    "artifact_path": (attempt_relative / filename).as_posix(),
                    "artifact_sha256": digest,
                }
            )
        index = {
            "schema_version": "membind.h0.checkpoint-index.v1",
            "protocol_version": "current-validation-v1.3",
            "stage_attempt_id": self.attempt_id,
            "candidate_id": "Q1",
            "phase": "H0-A",
            "status": "stage_complete",
            "segments": entries,
            "terminal_result_sha256": (
                terminal_result_override or phase_result_sha256
            ),
            "candidate_advance_allowed": True,
            "partial_qualification_reusable": True,
            "requires_whole_stage_rerun": False,
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }
        index_relative = artifact_prefix / attempt_relative / "index.json"
        index_encoded = _checkpoint_bytes(index)
        (root / index_relative).write_bytes(index_encoded)
        return (
            index_relative.as_posix(),
            hashlib.sha256(index_encoded).hexdigest(),
            index,
        )

    def _validate(self, root: Path, path: str, digest: str) -> dict:
        return validate_h0_prior_phase_terminal_completion(
            root=root,
            stage_attempt_id=self.attempt_id,
            checkpoint_index_path=path,
            checkpoint_index_sha256=digest,
            candidate_id="Q1",
            phase="H0-A",
            runtime_definition_sha256=self.runtime_definition_sha256,
        )

    def test_valid_canonical_q1_h0_a_completion_returns_only_bound_safe_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, digest, index = self._fixture(root)

            result = self._validate(root, path, digest)

            self.assertEqual(result["status"], "qualified_terminal_completion")
            self.assertEqual(result["candidate_id"], "Q1")
            self.assertEqual(result["phase"], "H0-A")
            self.assertEqual(result["stage_attempt_id"], self.attempt_id)
            self.assertEqual(result["checkpoint_index_path"], path)
            self.assertEqual(result["checkpoint_index_sha256"], digest)
            self.assertEqual(
                result["terminal_result_sha256"], index["terminal_result_sha256"]
            )
            self.assertEqual(
                result["runtime_definition_sha256"],
                self.runtime_definition_sha256,
            )
            self.assertTrue(result["qualified"])
            self.assertNotIn("phase_result", result)
            self.assertFalse(result["raw_prompts_persisted"])
            self.assertFalse(result["raw_responses_persisted"])

    def test_terminal_hash_chain_metrics_and_runtime_definition_are_exact(self):
        cases = (
            ({"terminal_result_override": "0" * 64}, self.runtime_definition_sha256),
            ({"phase_result_overrides": {"logical_call_count": 2}}, self.runtime_definition_sha256),
            ({}, "e" * 64),
        )
        for fixture_options, expected_runtime in cases:
            with self.subTest(options=fixture_options, runtime=expected_runtime):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path, digest, _ = self._fixture(root, **fixture_options)
                    with self.assertRaises(H0CompletionValidationError):
                        validate_h0_prior_phase_terminal_completion(
                            root=root,
                            stage_attempt_id=self.attempt_id,
                            checkpoint_index_path=path,
                            checkpoint_index_sha256=digest,
                            candidate_id="Q1",
                            phase="H0-A",
                            runtime_definition_sha256=expected_runtime,
                        )

    def test_terminal_must_be_last_and_unique(self):
        for options in ({"terminal_position": 6}, {"extra_terminal": True}):
            with self.subTest(options=options):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path, digest, _ = self._fixture(root, **options)
                    with self.assertRaises(H0CompletionValidationError):
                        self._validate(root, path, digest)

    def test_rejects_segment_reorder_path_escape_hash_drift_and_noncanonical_index(self):
        mutations = ("reorder", "escape", "hash_drift", "noncanonical")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path, digest, index = self._fixture(root)
                    index_path = root / path
                    if mutation == "reorder":
                        index["segments"][0], index["segments"][1] = (
                            index["segments"][1],
                            index["segments"][0],
                        )
                        encoded = _checkpoint_bytes(index)
                        index_path.write_bytes(encoded)
                        digest = hashlib.sha256(encoded).hexdigest()
                    elif mutation == "escape":
                        index["segments"][0]["artifact_path"] = "../outside.json"
                        encoded = _checkpoint_bytes(index)
                        index_path.write_bytes(encoded)
                        digest = hashlib.sha256(encoded).hexdigest()
                    elif mutation == "hash_drift":
                        segment_path = root / "artifacts/h0_runs" / index["segments"][0]["artifact_path"]
                        segment_path.write_bytes(segment_path.read_bytes() + b" ")
                    else:
                        encoded = json.dumps(index, sort_keys=True).encode("ascii")
                        index_path.write_bytes(encoded)
                        digest = hashlib.sha256(encoded).hexdigest()
                    with self.assertRaises(H0CompletionValidationError):
                        self._validate(root, path, digest)

    def test_rejects_index_and_segment_symlinks_even_when_hashes_match(self):
        for target_kind in ("index", "segment"):
            with self.subTest(target_kind=target_kind):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path, digest, index = self._fixture(root)
                    target = root / path
                    if target_kind == "segment":
                        target = (
                            root
                            / "artifacts/h0_runs"
                            / index["segments"][0]["artifact_path"]
                        )
                    backing = root / f"{target_kind}-backing.json"
                    target.replace(backing)
                    target.symlink_to(backing)
                    with self.assertRaises(H0CompletionValidationError):
                        self._validate(root, path, digest)

    def test_known_protocol_invalidated_real_attempt_can_never_qualify(self):
        with self.assertRaisesRegex(
            H0CompletionValidationError, "protocol_invalidated_attempt"
        ):
            validate_h0_prior_phase_terminal_completion(
                root=ROOT,
                stage_attempt_id="h0-q1-a-20260809-attempt-001",
                checkpoint_index_path=(
                    "artifacts/h0_runs/h0/checkpoints/"
                    "h0-q1-a-20260809-attempt-001/index.json"
                ),
                checkpoint_index_sha256=(
                    "127c81b39ccd705d7c67dc936e953992d5be97f4065fd56f3655db52d12ad309"
                ),
                candidate_id="Q1",
                phase="H0-A",
                runtime_definition_sha256=(
                    "fb74c0186e7e45542fd0f3a7dc746ac49e17ca62bb259d193caace2013c84d9a"
                ),
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
