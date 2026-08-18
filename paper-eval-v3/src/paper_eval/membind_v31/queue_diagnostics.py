"""Read-only analysis of optimization-lane scheduler/admission snapshots.

The analyzer deliberately keeps coordinator readiness and transport admission
as separate evidence surfaces.  A ready Compile slot is not inferred from an
LLM waiter, and an under-filled admission gate is not called a scheduler bug
unless a waiter is actually present.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from paper_eval.artifacts import payload_sha256, sha256_file


QUEUE_SCHEMA = "membind.paper-eval-v3.membind-v31-queue.v1"
SCHEDULER_SCHEMA = "membind.paper-eval-v3.membind-v31-scheduler-state.v1"
ADMISSION_SCHEMA = "membind.paper-eval-v3.membind-v31-admission-state.v1"
RESULT_SCHEMA = "membind.paper-eval-v3.membind-v31-queue-diagnostic.v1"

_CONTENT_FIELDS = {
    "body",
    "content",
    "episode",
    "messages",
    "payload",
    "prompt",
    "raw_artifact",
    "raw_response",
    "request",
    "response",
    "secret",
    "token",
}


class MemBindV31QueueDiagnosticError(ValueError):
    """The queue trace is malformed, unsafe, or internally inconsistent."""


def _fail(code: str) -> MemBindV31QueueDiagnosticError:
    return MemBindV31QueueDiagnosticError(code)


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _positive(value: object, code: str) -> int:
    selected = _nonnegative(value, code)
    if selected == 0:
        raise _fail(code)
    return selected


def _content_safe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _CONTENT_FIELDS:
                raise _fail("content_safe_violation")
            _content_safe(child)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _content_safe(child)
        return
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise _fail("content_safe_violation")


def _read_rows(path: Path) -> dict[str, list[dict[str, Any]]]:
    target = Path(path)
    if not target.is_file():
        raise _fail("trace_missing")
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("trace_unreadable") from None
    if not lines:
        raise _fail("trace_empty")

    producers: dict[str, list[dict[str, Any]]] = {
        "scheduler": [],
        "admission": [],
    }
    expected = {"scheduler": 0, "admission": 0}
    last_timestamp = {"scheduler": -1, "admission": -1}
    for line in lines:
        if not line:
            raise _fail("trace_blank_line")
        try:
            wrapper = json.loads(line)
        except json.JSONDecodeError:
            raise _fail("trace_json_invalid") from None
        if not isinstance(wrapper, Mapping) or set(wrapper) != {
            "record",
            "record_sha256",
        }:
            raise _fail("trace_envelope_invalid")
        record = wrapper.get("record")
        if (
            not isinstance(record, Mapping)
            or record.get("schema_version") != QUEUE_SCHEMA
            or wrapper.get("record_sha256") != payload_sha256(record)
        ):
            if isinstance(record, Mapping) and record.get("schema_version") == QUEUE_SCHEMA:
                raise _fail("record_hash_mismatch")
            raise _fail("trace_schema_invalid")
        row = record.get("row")
        if not isinstance(row, Mapping):
            raise _fail("trace_row_invalid")
        _content_safe(row)
        selected = dict(row)
        row_schema = selected.get("schema_version")
        event_type = selected.get("event_type")
        if row_schema == SCHEDULER_SCHEMA and event_type == "scheduler_state":
            producer = "scheduler"
        elif row_schema == ADMISSION_SCHEMA and event_type == "admission_snapshot":
            producer = "admission"
        else:
            raise _fail("producer_schema_invalid")
        if selected.get("event_sequence") != expected[producer]:
            raise _fail("event_sequence_invalid")
        timestamp = _nonnegative(selected.get("timestamp_ns"), "timestamp_invalid")
        if timestamp < last_timestamp[producer]:
            raise _fail("timestamp_not_monotonic")
        expected[producer] += 1
        last_timestamp[producer] = timestamp
        producers[producer].append(selected)
    return producers


def _intervals(rows: Sequence[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], int]]:
    return [
        (left, int(right["timestamp_ns"]) - int(left["timestamp_ns"]))
        for left, right in zip(rows, rows[1:])
        if int(right["timestamp_ns"]) > int(left["timestamp_ns"])
    ]


def _histogram(
    intervals: Sequence[tuple[Mapping[str, Any], int]], field: str
) -> dict[str, int]:
    result: dict[int, int] = defaultdict(int)
    for row, duration in intervals:
        value = _nonnegative(row.get(field), f"{field}_invalid")
        if duration > 0:
            result[value] += duration
    return {str(key): result[key] for key in sorted(result) if result[key] > 0}


def _scheduler_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    if not rows:
        return {
            "snapshot_count": 0,
            "ready_work_observable": False,
            "status": "NOT_OBSERVABLE",
        }
    intervals = _intervals(rows)
    worker_values: set[int] = set()
    max_prepared = 0
    for row in rows:
        workers = _positive(row.get("compile_workers"), "compile_workers_invalid")
        active = _nonnegative(row.get("active_compile_count"), "active_compile_count_invalid")
        ready = _nonnegative(
            row.get("legal_ready_compile_count"),
            "legal_ready_compile_count_invalid",
        )
        beyond = _nonnegative(
            row.get("arrived_beyond_lookahead_count"),
            "arrived_beyond_lookahead_count_invalid",
        )
        prepared = _nonnegative(
            row.get("prepared_rob_occupancy"),
            "prepared_rob_occupancy_invalid",
        )
        if active > workers:
            raise _fail("active_compile_exceeds_workers")
        worker_values.add(workers)
        max_prepared = max(max_prepared, prepared)
        if not isinstance(row.get("frontier_phase"), str) or not isinstance(
            row.get("frontier_wait_reason"), str
        ):
            raise _fail("frontier_state_invalid")
        del ready, beyond
    if len(worker_values) != 1:
        raise _fail("compile_workers_drift")
    workers = next(iter(worker_values))

    def duration_where(predicate: Any) -> int:
        return sum(duration for row, duration in intervals if predicate(row))

    phase_durations: dict[str, int] = defaultdict(int)
    wait_durations: dict[str, int] = defaultdict(int)
    for row, duration in intervals:
        phase_durations[str(row["frontier_phase"])] += duration
        wait_durations[str(row["frontier_wait_reason"])] += duration
    window = int(rows[-1]["timestamp_ns"]) - int(rows[0]["timestamp_ns"])
    return {
        "status": "OBSERVED",
        "snapshot_count": len(rows),
        "observation_window_ns": window,
        "compile_workers": workers,
        "ready_work_observable": True,
        "legal_ready_duration_ns": duration_where(
            lambda row: int(row["legal_ready_compile_count"]) > 0
        ),
        "work_conservation_candidate_duration_ns": duration_where(
            lambda row: int(row["legal_ready_compile_count"]) > 0
            and int(row["active_compile_count"]) < workers
        ),
        "window_limited_duration_ns": duration_where(
            lambda row: int(row["legal_ready_compile_count"]) == 0
            and int(row["active_compile_count"]) < workers
            and int(row["arrived_beyond_lookahead_count"]) > 0
        ),
        "arrived_beyond_window_duration_ns": duration_where(
            lambda row: int(row["arrived_beyond_lookahead_count"]) > 0
        ),
        "max_legal_ready_compile_count": max(
            int(row["legal_ready_compile_count"]) for row in rows
        ),
        "max_arrived_beyond_lookahead_count": max(
            int(row["arrived_beyond_lookahead_count"]) for row in rows
        ),
        "max_prepared_rob_occupancy": max_prepared,
        "active_compile_time_by_count_ns": _histogram(
            intervals, "active_compile_count"
        ),
        "prepared_rob_time_by_count_ns": _histogram(
            intervals, "prepared_rob_occupancy"
        ),
        "frontier_phase_time_ns": dict(sorted(phase_durations.items())),
        "frontier_wait_reason_time_ns": dict(sorted(wait_durations.items())),
    }


def _admission_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    if not rows:
        return {
            "snapshot_count": 0,
            "admission_state_observable": False,
            "status": "NOT_OBSERVABLE",
        }
    intervals = _intervals(rows)
    limit_values: set[int] = set()
    policies: set[str] = set()
    for row in rows:
        limit = _positive(row.get("configured_limit"), "configured_limit_invalid")
        active = _nonnegative(row.get("active_count"), "active_count_invalid")
        waiting = _nonnegative(row.get("waiting_count"), "waiting_count_invalid")
        active_compile = _nonnegative(
            row.get("active_compile_count"), "active_compile_count_invalid"
        )
        active_frontier = _nonnegative(
            row.get("active_frontier_count"), "active_frontier_count_invalid"
        )
        waiting_compile = _nonnegative(
            row.get("waiting_compile_count"), "waiting_compile_count_invalid"
        )
        waiting_frontier = _nonnegative(
            row.get("waiting_frontier_count"), "waiting_frontier_count_invalid"
        )
        if (
            active > limit
            or active_compile + active_frontier != active
            or waiting_compile + waiting_frontier != waiting
        ):
            raise _fail("admission_count_inconsistent")
        policy = row.get("policy")
        if not isinstance(policy, str) or not policy:
            raise _fail("admission_policy_invalid")
        limit_values.add(limit)
        policies.add(policy)
    if len(limit_values) != 1 or len(policies) != 1:
        raise _fail("admission_identity_drift")
    limit = next(iter(limit_values))

    def duration_where(predicate: Any) -> int:
        return sum(duration for row, duration in intervals if predicate(row))

    window = int(rows[-1]["timestamp_ns"]) - int(rows[0]["timestamp_ns"])
    return {
        "status": "OBSERVED",
        "snapshot_count": len(rows),
        "observation_window_ns": window,
        "configured_limit": limit,
        "policy": next(iter(policies)),
        "admission_state_observable": True,
        "under_capacity_with_waiter_duration_ns": duration_where(
            lambda row: int(row["active_count"]) < limit
            and int(row["waiting_count"]) > 0
        ),
        "under_capacity_without_waiter_duration_ns": duration_where(
            lambda row: 0 < int(row["active_count"]) < limit
            and int(row["waiting_count"]) == 0
        ),
        "frontier_waiter_under_capacity_duration_ns": duration_where(
            lambda row: int(row["active_count"]) < limit
            and int(row["waiting_frontier_count"]) > 0
        ),
        "compile_waiter_under_capacity_duration_ns": duration_where(
            lambda row: int(row["active_count"]) < limit
            and int(row["waiting_compile_count"]) > 0
        ),
        "active_time_by_count_ns": _histogram(intervals, "active_count"),
        "waiting_time_by_count_ns": _histogram(intervals, "waiting_count"),
        "max_active_count": max(int(row["active_count"]) for row in rows),
        "max_waiting_count": max(int(row["waiting_count"]) for row in rows),
    }


def analyze_queue_trace_file(path: Path) -> dict[str, object]:
    """Derive bounded scheduler and admission evidence from one queue trace."""

    target = Path(path)
    rows = _read_rows(target)
    body: dict[str, object] = {
        "schema_version": RESULT_SCHEMA,
        "status": "DIAGNOSTIC_ONLY",
        "merge_eligible": False,
        "source_trace": str(target),
        "source_trace_sha256": sha256_file(target),
        "scheduler": _scheduler_summary(rows["scheduler"]),
        "admission": _admission_summary(rows["admission"]),
        "interpretation_boundary": {
            "transport_wait_is_not_scheduler_ready_work": True,
            "ready_work_requires_scheduler_snapshot": True,
            "under_capacity_requires_waiter_for_admission_candidate": True,
        },
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


__all__ = [
    "MemBindV31QueueDiagnosticError",
    "analyze_queue_trace_file",
]
