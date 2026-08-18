"""Deterministic analysis tests for optimization-lane scheduler telemetry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.queue_diagnostics import (
    MemBindV31QueueDiagnosticError,
    analyze_queue_trace_file,
)


QUEUE_SCHEMA = "membind.paper-eval-v3.membind-v31-queue.v1"
SCHEDULER_SCHEMA = "membind.paper-eval-v3.membind-v31-scheduler-state.v1"
ADMISSION_SCHEMA = "membind.paper-eval-v3.membind-v31-admission-state.v1"


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    lines = []
    for row in rows:
        record = {"schema_version": QUEUE_SCHEMA, "row": row}
        lines.append(
            json.dumps(
                {"record": record, "record_sha256": payload_sha256(record)},
                sort_keys=True,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _scheduler(
    sequence: int,
    timestamp: int,
    *,
    ready: int,
    beyond: int,
    active: int,
    prepared: int,
) -> dict[str, object]:
    return {
        "schema_version": SCHEDULER_SCHEMA,
        "event_type": "scheduler_state",
        "event_sequence": sequence,
        "reason": "FIXTURE",
        "timestamp_ns": timestamp,
        "lookahead": 4,
        "compile_workers": 2,
        "legal_ready_compile_count": ready,
        "arrived_beyond_lookahead_count": beyond,
        "active_compile_count": active,
        "prepared_rob_occupancy": prepared,
        "frontier_phase": "WAITING_FOR_COMPILE",
        "frontier_wait_reason": "COMPILE_IN_PROGRESS",
    }


def _admission(
    sequence: int,
    timestamp: int,
    *,
    active: int,
    waiting: int,
) -> dict[str, object]:
    return {
        "schema_version": ADMISSION_SCHEMA,
        "event_type": "admission_snapshot",
        "event_sequence": sequence,
        "reason": "FIXTURE",
        "timestamp_ns": timestamp,
        "configured_limit": 2,
        "active_count": active,
        "waiting_count": waiting,
        "active_compile_count": active,
        "active_frontier_count": 0,
        "waiting_compile_count": waiting,
        "waiting_frontier_count": 0,
        "frontier_bind_region_count": 0,
        "barrier_holds": False,
        "policy": "FRONTIER_FIRST_CACHE_AFFINITY",
    }


def test_queue_analyzer_separates_window_ready_and_admission_underfill(
    tmp_path: Path,
) -> None:
    path = tmp_path / "queue.jsonl"
    _write(
        path,
        [
            _scheduler(0, 0, ready=0, beyond=2, active=2, prepared=0),
            _admission(0, 0, active=2, waiting=0),
            _scheduler(1, 10, ready=1, beyond=1, active=1, prepared=1),
            _admission(1, 10, active=1, waiting=1),
            _scheduler(2, 20, ready=0, beyond=1, active=1, prepared=2),
            _admission(2, 20, active=1, waiting=0),
            _scheduler(3, 30, ready=0, beyond=0, active=0, prepared=0),
            _admission(3, 30, active=0, waiting=0),
        ],
    )

    result = analyze_queue_trace_file(path)

    assert result["status"] == "DIAGNOSTIC_ONLY"
    assert result["source_trace_sha256"]
    scheduler = result["scheduler"]
    assert scheduler["observation_window_ns"] == 30
    assert scheduler["ready_work_observable"] is True
    assert scheduler["legal_ready_duration_ns"] == 10
    assert scheduler["work_conservation_candidate_duration_ns"] == 10
    assert scheduler["window_limited_duration_ns"] == 10
    assert scheduler["arrived_beyond_window_duration_ns"] == 30
    assert scheduler["max_prepared_rob_occupancy"] == 2
    assert scheduler["active_compile_time_by_count_ns"] == {"1": 20, "2": 10}

    admission = result["admission"]
    assert admission["observation_window_ns"] == 30
    assert admission["under_capacity_with_waiter_duration_ns"] == 10
    assert admission["under_capacity_without_waiter_duration_ns"] == 10
    assert admission["active_time_by_count_ns"] == {"1": 20, "2": 10}


def test_queue_analyzer_fails_closed_on_hash_or_sequence_drift(tmp_path: Path) -> None:
    path = tmp_path / "queue.jsonl"
    _write(
        path,
        [
            _scheduler(0, 0, ready=0, beyond=0, active=1, prepared=0),
            _scheduler(2, 10, ready=0, beyond=0, active=0, prepared=0),
        ],
    )

    with pytest.raises(MemBindV31QueueDiagnosticError, match="event_sequence_invalid"):
        analyze_queue_trace_file(path)

    lines = path.read_text(encoding="utf-8").splitlines()
    wrapper = json.loads(lines[0])
    wrapper["record"]["row"]["active_compile_count"] = 9
    lines[0] = json.dumps(wrapper, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(MemBindV31QueueDiagnosticError, match="record_hash_mismatch"):
        analyze_queue_trace_file(path)


def test_queue_analyzer_rejects_content_bearing_fields(tmp_path: Path) -> None:
    path = tmp_path / "queue.jsonl"
    row = _scheduler(0, 0, ready=0, beyond=0, active=0, prepared=0)
    row["prompt"] = "private episode text"
    _write(path, [row])

    with pytest.raises(MemBindV31QueueDiagnosticError, match="content_safe_violation"):
        analyze_queue_trace_file(path)
