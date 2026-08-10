"""Segmented, checkpointed orchestration for the offline-defined H0 stages.

The executor owns stage progress semantics only. Model, database, and artifact I/O
are injected by callers, which keeps this module independently testable and prevents
it from bypassing the live state gate.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence


PROGRESS_SCHEMA_VERSION = "membind.h0.progress.v1"
_STAGE_GRANULARITY = {
    "H0-A": "logical_trial",
    "H0-B": "source_sequence",
    "H0-C": "source_sequence",
}
_HEX_DIGITS = frozenset("0123456789abcdef")


def _require_sha256(value: object, field: str) -> str:
    digest = str(value)
    if len(digest) != 64 or any(character not in _HEX_DIGITS for character in digest):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _require_count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class Segment:
    """One durable H0 checkpoint unit with content-addressed input evidence."""

    segment_id: str
    history_id: str
    source_sequence: int
    logical_trial_index: int | None
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not self.segment_id:
            raise ValueError("segment_id must be nonempty")
        if not self.history_id:
            raise ValueError("history_id must be nonempty")
        if isinstance(self.source_sequence, bool) or self.source_sequence < 0:
            raise ValueError("source_sequence must be a non-negative integer")
        if self.logical_trial_index is not None and (
            isinstance(self.logical_trial_index, bool) or self.logical_trial_index < 0
        ):
            raise ValueError("logical_trial_index must be a non-negative integer or None")
        _require_sha256(self.evidence_sha256, "evidence_sha256")


class H0InfrastructureFailure(RuntimeError):
    """Explicit infrastructure classification backed by sanitized evidence."""

    def __init__(
        self,
        failure_code: str,
        *,
        evidence_sha256: str,
        logical_call_count: int = 0,
        http_attempt_count: int = 0,
        retry_count: int = 0,
    ) -> None:
        if not failure_code or not failure_code.replace("_", "").isalnum():
            raise ValueError("failure_code must be a nonempty identifier")
        super().__init__(failure_code)
        self.failure_code = failure_code
        self.evidence_sha256 = _require_sha256(
            evidence_sha256, "infrastructure failure evidence_sha256"
        )
        self.logical_call_count = _require_count(
            logical_call_count, "infrastructure failure logical_call_count"
        )
        self.http_attempt_count = _require_count(
            http_attempt_count, "infrastructure failure http_attempt_count"
        )
        self.retry_count = _require_count(
            retry_count, "infrastructure failure retry_count"
        )


CheckpointWriter = Callable[[dict[str, Any]], None]
SegmentRunner = Callable[[Segment], Awaitable[Mapping[str, Any]]]


class H0SegmentedExecutor:
    """Execute exactly one H0 stage attempt without partial-attempt reuse."""

    def __init__(
        self,
        *,
        attempt_id: str,
        candidate_id: str,
        stage: str,
        segments: Sequence[Segment],
        checkpoint_writer: CheckpointWriter,
        prior_attempt: Mapping[str, Any] | None = None,
    ) -> None:
        if not attempt_id:
            raise ValueError("attempt_id must be nonempty")
        if not candidate_id:
            raise ValueError("candidate_id must be nonempty")
        if stage not in _STAGE_GRANULARITY:
            raise ValueError(f"unsupported H0 stage: {stage}")
        if not callable(checkpoint_writer):
            raise TypeError("checkpoint_writer must be callable")

        frozen_segments = tuple(segments)
        if not frozen_segments:
            raise ValueError("segments must be nonempty")
        segment_ids = [segment.segment_id for segment in frozen_segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("segment_id values must be unique within an attempt")
        if stage == "H0-A" and any(
            segment.logical_trial_index is None for segment in frozen_segments
        ):
            raise ValueError("H0-A segments require logical_trial_index")
        if stage in {"H0-B", "H0-C"} and any(
            segment.logical_trial_index is not None for segment in frozen_segments
        ):
            raise ValueError(f"{stage} segments must be source-sequence checkpoints")
        checkpoint_keys = [
            (
                segment.history_id,
                segment.source_sequence,
                segment.logical_trial_index if stage == "H0-A" else None,
            )
            for segment in frozen_segments
        ]
        if len(checkpoint_keys) != len(set(checkpoint_keys)):
            raise ValueError(f"{stage} checkpoint keys must be unique")

        if prior_attempt is not None:
            if str(prior_attempt.get("stage")) != stage:
                raise ValueError("prior attempt stage does not match rerun stage")
            if str(prior_attempt.get("attempt_id")) == attempt_id:
                raise ValueError(
                    "infrastructure recovery requires a new attempt_id and whole-stage rerun"
                )

        self.attempt_id = attempt_id
        self.candidate_id = candidate_id
        self.stage = stage
        self.segments = frozen_segments
        self.checkpoint_writer = checkpoint_writer
        # Prior evidence is deliberately not retained: it is provenance for a separate
        # failed attempt and cannot contribute to this attempt's qualification result.
        self.prior_attempt_id = (
            str(prior_attempt.get("attempt_id")) if prior_attempt is not None else None
        )

    def _event(
        self,
        event_type: str,
        segment: Segment,
        *,
        completed_segment_count: int,
        logical_call_count: int,
        http_attempt_count: int,
        retry_count: int,
        evidence_sha256: str,
        failure_code: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "event_type": event_type,
            "attempt_id": self.attempt_id,
            "candidate_id": self.candidate_id,
            "stage": self.stage,
            "checkpoint_granularity": _STAGE_GRANULARITY[self.stage],
            "segment_id": segment.segment_id,
            "history_id": segment.history_id,
            "source_sequence": segment.source_sequence,
            "logical_trial_index": segment.logical_trial_index,
            "completed_segment_count": completed_segment_count,
            "total_segment_count": len(self.segments),
            "cumulative_logical_call_count": logical_call_count,
            "cumulative_http_attempt_count": http_attempt_count,
            "cumulative_retry_count": retry_count,
            "evidence_sha256": _require_sha256(
                evidence_sha256, "progress evidence_sha256"
            ),
        }
        if failure_code is not None:
            event["failure_code"] = failure_code
        return event

    async def run(self, run_segment: SegmentRunner) -> dict[str, Any]:
        """Run all segments from the beginning and checkpoint each successful unit."""

        if not callable(run_segment):
            raise TypeError("run_segment must be callable")

        completed: list[str] = []
        evidence: list[str] = []
        cumulative_logical_calls = 0
        cumulative_http_attempts = 0
        cumulative_retries = 0

        for segment in self.segments:
            self.checkpoint_writer(
                self._event(
                    "segment_started",
                    segment,
                    completed_segment_count=len(completed),
                    logical_call_count=cumulative_logical_calls,
                    http_attempt_count=cumulative_http_attempts,
                    retry_count=cumulative_retries,
                    evidence_sha256=segment.evidence_sha256,
                )
            )
            try:
                outcome = await run_segment(segment)
            except H0InfrastructureFailure as exc:
                cumulative_logical_calls += exc.logical_call_count
                cumulative_http_attempts += exc.http_attempt_count
                cumulative_retries += exc.retry_count
                failure_evidence = [*evidence, exc.evidence_sha256]
                self.checkpoint_writer(
                    self._event(
                        "stage_infrastructure_failure",
                        segment,
                        completed_segment_count=len(completed),
                        logical_call_count=cumulative_logical_calls,
                        http_attempt_count=cumulative_http_attempts,
                        retry_count=cumulative_retries,
                        evidence_sha256=exc.evidence_sha256,
                        failure_code=exc.failure_code,
                    )
                )
                return {
                    "schema_version": PROGRESS_SCHEMA_VERSION,
                    "attempt_id": self.attempt_id,
                    "candidate_id": self.candidate_id,
                    "stage": self.stage,
                    "status": "infrastructure_failure",
                    "failure_code": exc.failure_code,
                    "completed_segment_ids": completed,
                    "preserved_evidence_sha256": failure_evidence,
                    "cumulative_logical_call_count": cumulative_logical_calls,
                    "cumulative_http_attempt_count": cumulative_http_attempts,
                    "cumulative_retry_count": cumulative_retries,
                    "partial_qualification_reusable": False,
                    "requires_whole_stage_rerun": True,
                    "candidate_advance_allowed": False,
                }
            except Exception:
                self.checkpoint_writer(
                    self._event(
                        "stage_candidate_failure",
                        segment,
                        completed_segment_count=len(completed),
                        logical_call_count=cumulative_logical_calls,
                        http_attempt_count=cumulative_http_attempts,
                        retry_count=cumulative_retries,
                        evidence_sha256=segment.evidence_sha256,
                        failure_code="candidate_execution_failure",
                    )
                )
                raise

            logical_calls = _require_count(
                outcome.get("logical_call_count"), "logical_call_count"
            )
            http_attempts = _require_count(
                outcome.get("http_attempt_count"), "http_attempt_count"
            )
            retries = _require_count(outcome.get("retry_count"), "retry_count")
            ledger_sha256 = _require_sha256(
                outcome.get("ledger_sha256"), "ledger_sha256"
            )
            failure_code: str | None = None
            failure_message: str | None = None
            if http_attempts != logical_calls + retries:
                failure_code = "logical_http_retry_count_mismatch"
                failure_message = "logical/HTTP/retry counts are inconsistent"
            elif retries != 0:
                failure_code = "candidate_induced_retry"
                failure_message = "candidate-induced retry cannot qualify"
            elif self.stage == "H0-A" and logical_calls != 1:
                failure_code = "h0_a_not_one_logical_trial"
                failure_message = "H0-A checkpoint must contain one logical trial"
            if failure_code is not None:
                cumulative_logical_calls += logical_calls
                cumulative_http_attempts += http_attempts
                cumulative_retries += retries
                self.checkpoint_writer(
                    self._event(
                        "stage_candidate_failure",
                        segment,
                        completed_segment_count=len(completed),
                        logical_call_count=cumulative_logical_calls,
                        http_attempt_count=cumulative_http_attempts,
                        retry_count=cumulative_retries,
                        evidence_sha256=ledger_sha256,
                        failure_code=failure_code,
                    )
                )
                raise ValueError(failure_message)
            cumulative_logical_calls += logical_calls
            cumulative_http_attempts += http_attempts
            cumulative_retries += retries
            completed.append(segment.segment_id)
            evidence.append(ledger_sha256)
            self.checkpoint_writer(
                self._event(
                    "checkpoint",
                    segment,
                    completed_segment_count=len(completed),
                    logical_call_count=cumulative_logical_calls,
                    http_attempt_count=cumulative_http_attempts,
                    retry_count=cumulative_retries,
                    evidence_sha256=ledger_sha256,
                )
            )

        final_segment = self.segments[-1]
        self.checkpoint_writer(
            self._event(
                "stage_complete",
                final_segment,
                completed_segment_count=len(completed),
                logical_call_count=cumulative_logical_calls,
                http_attempt_count=cumulative_http_attempts,
                retry_count=cumulative_retries,
                evidence_sha256=evidence[-1],
            )
        )
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "attempt_id": self.attempt_id,
            "candidate_id": self.candidate_id,
            "stage": self.stage,
            "status": "stage_complete",
            "completed_segment_ids": completed,
            "preserved_evidence_sha256": evidence,
            "cumulative_logical_call_count": cumulative_logical_calls,
            "cumulative_http_attempt_count": cumulative_http_attempts,
            "cumulative_retry_count": cumulative_retries,
            "partial_qualification_reusable": True,
            "requires_whole_stage_rerun": False,
            "candidate_advance_allowed": True,
        }
