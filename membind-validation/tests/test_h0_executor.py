"""Offline contracts for segmented H0 execution and durable progress events."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_executor import (  # noqa: E402
    H0InfrastructureFailure,
    H0SegmentedExecutor,
    Segment,
)


class MemoryCheckpointWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(json.loads(json.dumps(event)))


class H0SegmentedExecutorTests(IsolatedAsyncioTestCase):
    def test_stage_checkpoint_keys_must_be_unique(self):
        with self.assertRaisesRegex(ValueError, "checkpoint keys must be unique"):
            H0SegmentedExecutor(
                attempt_id="h0-a-duplicate",
                candidate_id="Q1",
                stage="H0-A",
                segments=[
                    Segment("trial-a", "history", 0, 0, "a" * 64),
                    Segment("trial-b", "history", 0, 0, "b" * 64),
                ],
                checkpoint_writer=MemoryCheckpointWriter(),
            )

        with self.assertRaisesRegex(ValueError, "checkpoint keys must be unique"):
            H0SegmentedExecutor(
                attempt_id="h0-b-duplicate",
                candidate_id="Q1",
                stage="H0-B",
                segments=[
                    Segment("source-a", "history", 0, None, "a" * 64),
                    Segment("source-b", "history", 0, None, "b" * 64),
                ],
                checkpoint_writer=MemoryCheckpointWriter(),
            )

    async def test_h0_a_checkpoints_after_every_logical_trial(self):
        writer = MemoryCheckpointWriter()
        calls: list[str] = []

        async def run_segment(segment: Segment) -> dict:
            calls.append(segment.segment_id)
            return {
                "logical_call_count": 1,
                "http_attempt_count": 1,
                "retry_count": 0,
                "ledger_sha256": segment.evidence_sha256,
            }

        result = await H0SegmentedExecutor(
            attempt_id="h0-attempt-001",
            candidate_id="Q1",
            stage="H0-A",
            segments=[
                Segment("trial-0", "07741c45", 0, 0, "a" * 64),
                Segment("trial-1", "07741c45", 0, 1, "b" * 64),
                Segment("trial-2", "07741c45", 0, 2, "c" * 64),
            ],
            checkpoint_writer=writer,
        ).run(run_segment)

        checkpoints = [e for e in writer.events if e["event_type"] == "checkpoint"]
        self.assertEqual(calls, ["trial-0", "trial-1", "trial-2"])
        self.assertEqual([e["segment_id"] for e in checkpoints], calls)
        self.assertEqual(
            [e["checkpoint_granularity"] for e in checkpoints],
            ["logical_trial"] * 3,
        )
        self.assertEqual(
            [e["cumulative_logical_call_count"] for e in checkpoints],
            [1, 2, 3],
        )
        self.assertEqual(
            [e["cumulative_http_attempt_count"] for e in checkpoints],
            [1, 2, 3],
        )
        self.assertEqual(
            [e["cumulative_retry_count"] for e in checkpoints],
            [0, 0, 0],
        )
        self.assertEqual(result["status"], "stage_complete")
        self.assertTrue(result["partial_qualification_reusable"])

    async def test_h0_b_and_c_checkpoint_once_per_source_sequence(self):
        for stage in ("H0-B", "H0-C"):
            with self.subTest(stage=stage):
                writer = MemoryCheckpointWriter()
                segments = [
                    Segment("source-0", "07741c45", 0, None, "a" * 64),
                    Segment("source-1", "07741c45", 1, None, "b" * 64),
                ]

                async def run_segment(segment: Segment) -> dict:
                    return {
                        "logical_call_count": 1,
                        "http_attempt_count": 1,
                        "retry_count": 0,
                        "ledger_sha256": segment.evidence_sha256,
                    }

                await H0SegmentedExecutor(
                    attempt_id=f"{stage}-attempt-001",
                    candidate_id="Q1",
                    stage=stage,
                    segments=segments,
                    checkpoint_writer=writer,
                ).run(run_segment)

                checkpoints = [
                    event
                    for event in writer.events
                    if event["event_type"] == "checkpoint"
                ]
                self.assertEqual(len(checkpoints), 2)
                self.assertEqual(
                    [event["source_sequence"] for event in checkpoints], [0, 1]
                )
                self.assertEqual(
                    {event["checkpoint_granularity"] for event in checkpoints},
                    {"source_sequence"},
                )

    async def test_infrastructure_failure_preserves_partial_evidence_but_forces_new_attempt(self):
        writer = MemoryCheckpointWriter()
        calls: list[str] = []
        segments = [
            Segment("source-0", "07741c45", 0, None, "a" * 64),
            Segment("source-1", "07741c45", 1, None, "b" * 64),
            Segment("source-2", "07741c45", 2, None, "c" * 64),
        ]

        async def run_segment(segment: Segment) -> dict:
            calls.append(segment.segment_id)
            if segment.source_sequence == 1:
                raise H0InfrastructureFailure(
                    "vllm_unreachable",
                    evidence_sha256="f" * 64,
                    logical_call_count=1,
                    http_attempt_count=1,
                    retry_count=0,
                )
            return {
                "logical_call_count": 3,
                "http_attempt_count": 3,
                "retry_count": 0,
                "ledger_sha256": segment.evidence_sha256,
            }

        result = await H0SegmentedExecutor(
            attempt_id="h0-attempt-failed",
            candidate_id="Q1",
            stage="H0-B",
            segments=segments,
            checkpoint_writer=writer,
        ).run(run_segment)

        self.assertEqual(result["status"], "infrastructure_failure")
        self.assertEqual(result["failure_code"], "vllm_unreachable")
        self.assertFalse(result["partial_qualification_reusable"])
        self.assertTrue(result["requires_whole_stage_rerun"])
        self.assertEqual(result["completed_segment_ids"], ["source-0"])
        self.assertEqual(result["preserved_evidence_sha256"], ["a" * 64, "f" * 64])
        self.assertEqual(result["cumulative_logical_call_count"], 4)
        self.assertEqual(result["cumulative_http_attempt_count"], 4)
        self.assertEqual(result["cumulative_retry_count"], 0)
        self.assertFalse(result["candidate_advance_allowed"])
        self.assertEqual(calls, ["source-0", "source-1"])
        self.assertEqual(
            writer.events[-1]["event_type"], "stage_infrastructure_failure"
        )
        self.assertEqual(writer.events[-1]["cumulative_logical_call_count"], 4)
        self.assertEqual(writer.events[-1]["cumulative_http_attempt_count"], 4)
        self.assertEqual(writer.events[-1]["cumulative_retry_count"], 0)
        self.assertNotIn("resume_segment_id", result)

    async def test_rerun_rejects_same_attempt_id_and_never_combines_partial_attempts(self):
        prior = {
            "attempt_id": "h0-attempt-old",
            "stage": "H0-C",
            "status": "infrastructure_failure",
            "partial_qualification_reusable": False,
            "completed_segment_ids": ["source-0"],
            "preserved_evidence_sha256": ["a" * 64],
        }
        segments = [
            Segment("source-0", "a", 0, None, "b" * 64),
            Segment("source-1", "b", 1, None, "c" * 64),
        ]

        with self.assertRaisesRegex(ValueError, "new attempt_id"):
            H0SegmentedExecutor(
                attempt_id="h0-attempt-old",
                candidate_id="Q1",
                stage="H0-C",
                segments=segments,
                checkpoint_writer=MemoryCheckpointWriter(),
                prior_attempt=prior,
            )

        calls: list[str] = []

        async def run_segment(segment: Segment) -> dict:
            calls.append(segment.segment_id)
            return {
                "logical_call_count": 1,
                "http_attempt_count": 1,
                "retry_count": 0,
                "ledger_sha256": segment.evidence_sha256,
            }

        result = await H0SegmentedExecutor(
            attempt_id="h0-attempt-new",
            candidate_id="Q1",
            stage="H0-C",
            segments=segments,
            checkpoint_writer=MemoryCheckpointWriter(),
            prior_attempt=prior,
        ).run(run_segment)

        self.assertEqual(calls, ["source-0", "source-1"])
        self.assertEqual(result["attempt_id"], "h0-attempt-new")
        self.assertEqual(result["completed_segment_ids"], calls)
        self.assertEqual(result["preserved_evidence_sha256"], ["b" * 64, "c" * 64])
        self.assertNotIn("a" * 64, json.dumps(result))

    async def test_progress_events_are_detailed_and_sanitized(self):
        writer = MemoryCheckpointWriter()
        private_prompt = "private prompt must never persist"
        private_response = "private response must never persist"

        async def run_segment(segment: Segment) -> dict:
            return {
                "logical_call_count": 1,
                "http_attempt_count": 1,
                "retry_count": 0,
                "ledger_sha256": segment.evidence_sha256,
                "raw_prompt": private_prompt,
                "raw_response": private_response,
                "authorization": "Bearer secret",
                "api_key": "secret",
            }

        await H0SegmentedExecutor(
            attempt_id="h0-attempt-safe",
            candidate_id="Q1",
            stage="H0-A",
            segments=[Segment("trial-0", "07741c45", 0, 0, "d" * 64)],
            checkpoint_writer=writer,
        ).run(run_segment)

        encoded = json.dumps(writer.events, sort_keys=True)
        self.assertNotIn(private_prompt, encoded)
        self.assertNotIn(private_response, encoded)
        self.assertNotIn("Bearer secret", encoded)
        self.assertNotIn('"api_key"', encoded)
        for event in writer.events:
            self.assertEqual(event["schema_version"], "membind.h0.progress.v1")
            self.assertEqual(event["attempt_id"], "h0-attempt-safe")
            self.assertEqual(event["candidate_id"], "Q1")
            self.assertEqual(event["stage"], "H0-A")
            self.assertIn("segment_id", event)
            self.assertIn("cumulative_logical_call_count", event)
            self.assertIn("cumulative_http_attempt_count", event)
            self.assertIn("cumulative_retry_count", event)
            self.assertIn("evidence_sha256", event)

    async def test_retry_count_and_h0_a_single_logical_trial_are_fail_closed(self):
        cases = (
            {
                "logical_call_count": 1,
                "http_attempt_count": 2,
                "retry_count": 1,
                "ledger_sha256": "a" * 64,
            },
            {
                "logical_call_count": 2,
                "http_attempt_count": 2,
                "retry_count": 0,
                "ledger_sha256": "a" * 64,
            },
        )
        for outcome in cases:
            with self.subTest(outcome=outcome):
                writer = MemoryCheckpointWriter()

                async def run_segment(_segment: Segment) -> dict:
                    return outcome

                executor = H0SegmentedExecutor(
                    attempt_id="h0-attempt-invalid-counts",
                    candidate_id="Q1",
                    stage="H0-A",
                    segments=[Segment("trial-0", "07741c45", 0, 0, "a" * 64)],
                    checkpoint_writer=writer,
                )
                with self.assertRaisesRegex(ValueError, "H0-A|retry"):
                    await executor.run(run_segment)

    async def test_unexpected_exception_is_not_misclassified_as_infrastructure(self):
        async def run_segment(_segment: Segment) -> dict:
            raise RuntimeError("candidate parse failure")

        executor = H0SegmentedExecutor(
            attempt_id="h0-attempt-error",
            candidate_id="Q1",
            stage="H0-A",
            segments=[Segment("trial-0", "07741c45", 0, 0, "a" * 64)],
            checkpoint_writer=MemoryCheckpointWriter(),
        )

        with self.assertRaisesRegex(RuntimeError, "candidate parse failure"):
            await executor.run(run_segment)


if __name__ == "__main__":
    import unittest

    unittest.main()
