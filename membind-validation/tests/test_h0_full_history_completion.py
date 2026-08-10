"""Offline terminal-completion contracts for Q1/H0-B -> H0-C admission."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_full_history_completion import (  # noqa: E402
    H0FullHistoryCompletionValidationError,
    validate_h0_b_terminal_completion,
)
from h0_runtime import H0CheckpointStore, canonical_json_sha256, sha256_file  # noqa: E402


class H0FullHistoryCompletionTests(TestCase):
    runtime_sha = "6" * 64
    prior_sha = "7" * 64

    def _ledger(self, attempt_id: str, count: int) -> dict:
        trials = []
        attempts = []
        for index in range(count):
            logical_id = f"logical-{index:04d}"
            http_id = f"http-{index:04d}"
            trials.append(
                {
                    "logical_trial_id": logical_id,
                    "candidate_id": "Q1",
                    "call_key": f"07741c45:{index}:safe-call",
                    "repeated_trial_index": 0,
                    "statistically_independent": False,
                    "attempt_ids": [http_id],
                }
            )
            attempts.append(
                {
                    "http_attempt_id": http_id,
                    "logical_trial_id": logical_id,
                    "retry_index": 0,
                    "retry_same_logical_trial": False,
                    "completed": True,
                    "http_status": 200,
                    "http_200": True,
                    "finish_reason": "stop",
                    "finish_non_length": True,
                    "json_parse_success": True,
                    "pydantic_validation_success": True,
                    "semantic_utility_success": True,
                    "failure_class": None,
                }
            )
        return {
            "schema_version": "membind.h0.attempt-ledger.v1",
            "protocol_version": "current-validation-v1.3",
            "stage_attempt_id": attempt_id,
            "logical_trials": trials,
            "http_attempts": attempts,
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }

    def _runtime_evidence(self, graph_count: int) -> dict:
        return {
            "fresh_graph_count": graph_count,
            "closed_graph_count": graph_count,
            "embedding_workload_request_count": graph_count,
            "cross_encoder_rank_call_count": 0,
            "histories": [],
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }

    def _fixture(self, root: Path) -> tuple[dict, H0CheckpointStore]:
        attempt_id = "h0-q1-b-qualified-001"
        artifacts = root / "artifacts/h0_runs"
        store = H0CheckpointStore(
            root=artifacts,
            stage_attempt_id=attempt_id,
            candidate_id="Q1",
            phase="H0-B",
        )
        prior_payload = {
            "schema_version": "membind.h0.prior-phase-terminal-completion.v1",
            "qualified": True,
            "candidate_id": "Q1",
            "phase": "H0-A",
            "stage_attempt_id": "h0-q1-a-qualified-002",
            "terminal_result_sha256": self.prior_sha,
            "secrets_persisted": False,
        }
        store.record_segment(
            "prior_phase_completion",
            "qualified",
            prior_payload,
        )
        readiness_checks = (
            "vllm_version",
            "served_model",
            "health",
            "construction_ready",
            "embedding_ready",
            "neo4j_ready",
            "authorization_recheck",
        )
        for ordinal, check in enumerate(readiness_checks):
            store.record_segment(
                "stage_readiness_check",
                f"{ordinal:03d}-{check}",
                {
                    "schema_version": "membind.h0.stage-readiness-event.v1",
                    "stage_attempt_id": attempt_id,
                    "candidate_id": "Q1",
                    "phase": "H0-B",
                    "check": check,
                    "qualified": True,
                    "secrets_persisted": False,
                },
            )
        readiness = {
            "schema_version": "membind.h0.stage-readiness.v1",
            "stage_attempt_id": attempt_id,
            "candidate_id": "Q1",
            "phase": "H0-B",
            "status": "ready",
            "construction_readiness_count": 1,
            "embedding_readiness_count": 1,
            "neo4j_readiness_count": 1,
            "authorization_recheck_count": 1,
            "generation_requests": 0,
            "embedding_request_count": 0,
            "per_history_warmup_count": 0,
            "secrets_persisted": False,
        }
        store.record_segment("stage_readiness_result", "ready", readiness)

        for stage in (
            "corpus_ready",
            "history_factory_ready",
            "graph_construction_started",
            "graph_construction_ready",
        ):
            store.record_segment(
                "preworkload_progress",
                stage,
                {
                    "schema_version": "membind.h0.preworkload-progress.v1",
                    "protocol_version": "current-validation-v1.3",
                    "stage_attempt_id": attempt_id,
                    "candidate_id": "Q1",
                    "phase": "H0-B",
                    "stage": stage,
                    "question_id": (
                        "07741c45" if stage.startswith("graph_construction_") else None
                    ),
                    "generation_request_count": 0,
                    "embedding_request_count": 0,
                    "secrets_persisted": False,
                },
            )

        ledger = self._ledger(attempt_id, 49)
        for sequence in range(49):
            source_ledger = self._ledger(attempt_id, sequence + 1)
            store.record_segment(
                "source_sequence",
                f"07741c45-{sequence:03d}",
                {
                    "phase_checkpoint": {
                        "schema_version": "membind.h0.phase-checkpoint.v1",
                        "stage_attempt_id": attempt_id,
                        "question_id": "07741c45",
                        "source_sequence": sequence,
                        "logical_call_count": 1,
                        "http_attempt_count": 1,
                        "retry_count": 0,
                        "final_stage_checks_passed": sequence == 48,
                        "secrets_persisted": False,
                    },
                    "attempt_ledger": source_ledger,
                    "runtime_evidence": self._runtime_evidence(1),
                    "runtime_definition_sha256": self.runtime_sha,
                    "prior_phase_completion_sha256": canonical_json_sha256(prior_payload),
                },
            )
        history_result = {
            "schema_version": "membind.h0.full-history-evidence.v1",
            "stage_attempt_id": attempt_id,
            "question_id": "07741c45",
            "qualified": True,
            "semantic_records": [
                {
                    "call_key": "07741c45:0:safe-call",
                    "response_model_name": "SafeModel",
                    "entity_count": 1,
                    "distinct_normalized_entity_name_count": 1,
                    "semantic_payload_sha256": "8" * 64,
                    "failure_codes": [],
                    "qualified": True,
                }
            ],
        }
        store.record_segment(
            "history_result",
            "07741c45",
            {
                "history_result": history_result,
                "history_result_sha256": canonical_json_sha256(history_result),
                "attempt_ledger": ledger,
                "runtime_evidence": self._runtime_evidence(1),
                "runtime_definition_sha256": self.runtime_sha,
            },
        )
        phase_result = {
            "schema_version": "membind.h0.full-history-phase-result.v1",
            "stage_attempt_id": attempt_id,
            "phase": "H0-B",
            "qualified": True,
            "completed_history_count": 1,
            "completed_histories": [
                {
                    "question_id": "07741c45",
                    "evidence_sha256": canonical_json_sha256(history_result),
                }
            ],
            "combined_semantic_record_count": 1,
            "combined_semantic_projection_sha256": "9" * 64,
            "semantic_stage_sha256": "a" * 64,
            "partial_qualification_reusable": True,
        }
        terminal_hash = canonical_json_sha256(phase_result)
        store.record_segment(
            "stage_result",
            "qualified",
            {
                "phase_result": phase_result,
                "phase_result_sha256": terminal_hash,
                "attempt_ledger": ledger,
                "runtime_evidence": self._runtime_evidence(1),
                "runtime_definition_sha256": self.runtime_sha,
                "prior_phase_completion_sha256": canonical_json_sha256(prior_payload),
                "stage_readiness_sha256": canonical_json_sha256(readiness),
            },
        )
        terminal = store.mark_stage_complete(terminal_hash)
        return terminal, store

    def _validate(self, root: Path, terminal: dict, store: H0CheckpointStore) -> dict:
        relative = (root / "artifacts/h0_runs" / terminal["checkpoint_index_path"]).relative_to(root)
        return validate_h0_b_terminal_completion(
            root=root,
            stage_attempt_id=store.stage_attempt_id,
            checkpoint_index_path=relative.as_posix(),
            checkpoint_index_sha256=sha256_file(store.index_path),
            candidate_id="Q1",
            runtime_definition_sha256=self.runtime_sha,
        )

    def test_accepts_only_complete_content_addressed_h0_b_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            terminal, store = self._fixture(root)
            result = self._validate(root, terminal, store)
            self.assertEqual(result["status"], "qualified_terminal_completion")
            self.assertEqual(result["phase"], "H0-B")
            self.assertEqual(result["source_checkpoint_count"], 49)
            self.assertEqual(result["completed_history_count"], 1)
            self.assertEqual(result["runtime_definition_sha256"], self.runtime_sha)

    def test_rejects_tampering_unindexed_files_runtime_drift_and_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            terminal, store = self._fixture(root)
            extra = store.directory / "unindexed.json"
            extra.write_text("{}\n", encoding="ascii")
            with self.assertRaises(H0FullHistoryCompletionValidationError):
                self._validate(root, terminal, store)
            extra.unlink()

            with self.assertRaises(H0FullHistoryCompletionValidationError):
                validate_h0_b_terminal_completion(
                    root=root,
                    stage_attempt_id=store.stage_attempt_id,
                    checkpoint_index_path=store.index_path.relative_to(root).as_posix(),
                    checkpoint_index_sha256=sha256_file(store.index_path),
                    candidate_id="Q1",
                    runtime_definition_sha256="f" * 64,
                )

            terminal_entry = store.index["segments"][-1]
            terminal_path = root / "artifacts/h0_runs" / terminal_entry["artifact_path"]
            artifact = json.loads(terminal_path.read_text(encoding="ascii"))
            artifact["payload"]["attempt_ledger"]["http_attempts"][0]["retry_index"] = 1
            terminal_path.write_text(json.dumps(artifact, sort_keys=True), encoding="ascii")
            with self.assertRaises(H0FullHistoryCompletionValidationError):
                validate_h0_b_terminal_completion(
                    root=root,
                    stage_attempt_id=store.stage_attempt_id,
                    checkpoint_index_path=store.index_path.relative_to(root).as_posix(),
                    checkpoint_index_sha256=sha256_file(store.index_path),
                    candidate_id="Q1",
                    runtime_definition_sha256=self.runtime_sha,
                )

    def test_rejects_missing_or_nonfinal_source_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _terminal, store = self._fixture(root)
            source_entry = next(
                entry
                for entry in store.index["segments"]
                if entry["segment_kind"] == "source_sequence"
                and entry["segment_id"] == "07741c45-000"
            )
            source_path = root / "artifacts/h0_runs" / source_entry["artifact_path"]
            artifact = json.loads(source_path.read_text(encoding="ascii"))
            artifact["payload"]["phase_checkpoint"]["final_stage_checks_passed"] = True
            encoded = json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
            source_path.write_text(encoded, encoding="ascii")
            source_entry["artifact_sha256"] = sha256_file(source_path)
            store.index_path.write_text(
                json.dumps(store.index, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                encoding="ascii",
            )
            with self.assertRaises(H0FullHistoryCompletionValidationError):
                validate_h0_b_terminal_completion(
                    root=root,
                    stage_attempt_id=store.stage_attempt_id,
                    checkpoint_index_path=store.index_path.relative_to(root).as_posix(),
                    checkpoint_index_sha256=sha256_file(store.index_path),
                    candidate_id="Q1",
                    runtime_definition_sha256=self.runtime_sha,
                )

    def test_rejects_semantically_tampered_preworkload_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            terminal, store = self._fixture(root)
            progress_entry = next(
                entry
                for entry in store.index["segments"]
                if entry["segment_kind"] == "preworkload_progress"
                and entry["segment_id"] == "graph_construction_ready"
            )
            progress_path = (
                root / "artifacts/h0_runs" / progress_entry["artifact_path"]
            )
            artifact = json.loads(progress_path.read_text(encoding="ascii"))
            artifact["payload"]["generation_request_count"] = 1
            encoded = (
                json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
            )
            progress_path.write_text(encoded, encoding="ascii")
            progress_digest = sha256_file(progress_path)
            name_parts = progress_path.name.split(".")
            name_parts[-2] = progress_digest
            readdressed_path = progress_path.with_name(".".join(name_parts))
            progress_path.rename(readdressed_path)
            progress_entry["artifact_sha256"] = progress_digest
            progress_entry["artifact_path"] = (
                Path(progress_entry["artifact_path"])
                .with_name(readdressed_path.name)
                .as_posix()
            )
            store.index_path.write_text(
                json.dumps(store.index, ensure_ascii=True, indent=2, sort_keys=True)
                + "\n",
                encoding="ascii",
            )

            with self.assertRaisesRegex(
                H0FullHistoryCompletionValidationError,
                "preworkload_progress_mismatch",
            ):
                self._validate(root, terminal, store)


if __name__ == "__main__":
    import unittest

    unittest.main()
