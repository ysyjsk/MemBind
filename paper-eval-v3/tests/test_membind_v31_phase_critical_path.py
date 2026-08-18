"""TDD contract for the offline v3.1 phase critical-path analyzer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.phase_critical_path import (
    MemBindV31PhaseDiagnosticError,
    analyze_phase_critical_path,
)


def _write_wrapped(path: Path, rows: list[dict[str, object]], *, schema: str, key: str) -> None:
    for sequence, row in enumerate(rows):
        if key == "event":
            body = {"event_sequence": sequence, **row, "schema_version": schema}
            wrapper = {"event": body, "event_sha256": payload_sha256(body)}
        else:
            body = {"row": {"event_sequence": sequence, **row}, "schema_version": schema}
            wrapper = {"record": body, "record_sha256": payload_sha256(body)}
        path.write_text(
            path.read_text(encoding="utf-8")
            + json.dumps(wrapper, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )


def _write_queue(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("", encoding="utf-8")
    _write_wrapped(
        path,
        rows,
        schema="membind.paper-eval-v3.membind-v31-queue.v1",
        key="record",
    )


def _write_events(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("", encoding="utf-8")
    _write_wrapped(
        path,
        rows,
        schema="membind.paper-eval-v3.membind-v31-pilot-lifecycle.v1",
        key="event",
    )


def _write_llm(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("", encoding="utf-8")
    for sequence, row in enumerate(rows):
        record = {
            "row": {"event_sequence": sequence, **row},
            "schema_version": "membind.paper-eval-v3.membind-v31-pilot-llm.v1",
        }
        path.write_text(
            path.read_text(encoding="utf-8")
            + json.dumps(
                {"record": record, "record_sha256": payload_sha256(record)},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def test_phase_analyzer_separates_state_intervals_from_nested_request_work(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    events = tmp_path / "events.jsonl"
    llm = tmp_path / "llm.jsonl"
    _write_queue(
        queue,
        [
            {
                "event_type": "scheduler_state",
                "timestamp_ns": 0,
                "frontier_phase": "COMPILE_ACTIVE",
                "frontier_wait_reason": "COMPILE_IN_PROGRESS",
                "legal_ready_compile_count": 0,
                "prepared_rob_occupancy": 0,
                "active_compile_count": 1,
                "configured_limit": 2,
                "active_count": 1,
                "waiting_count": 0,
            },
            {
                "event_type": "scheduler_state",
                "timestamp_ns": 10,
                "frontier_phase": "BINDING",
                "frontier_wait_reason": "BIND_IN_PROGRESS",
                "legal_ready_compile_count": 1,
                "prepared_rob_occupancy": 1,
                "active_compile_count": 0,
                "configured_limit": 2,
                "active_count": 1,
                "waiting_count": 1,
            },
            {
                "event_type": "scheduler_state",
                "timestamp_ns": 20,
                "frontier_phase": "NO_SOURCE_ARRIVED",
                "frontier_wait_reason": "SOURCE_NOT_ARRIVED",
                "legal_ready_compile_count": 0,
                "prepared_rob_occupancy": 0,
                "active_compile_count": 0,
                "configured_limit": 2,
                "active_count": 0,
                "waiting_count": 0,
            },
        ],
    )
    _write_events(
        events,
        [
            {"event_type": "ARRIVAL", "timestamp_ns": 0, "source_sequence": 0},
            {"event_type": "PUBLICATION_DURABLE", "timestamp_ns": 30, "source_sequence": 0},
        ],
    )
    _write_llm(
        llm,
        [
            {
                "event_type": "llm_request_submitted",
                "timestamp_ns": 1,
                "request_id": "c0",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "token_count": 100,
            },
            {
                "event_type": "llm_request_start",
                "timestamp_ns": 2,
                "request_id": "c0",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
            },
            {
                "event_type": "llm_request_terminal",
                "timestamp_ns": 12,
                "request_id": "c0",
                "status": "ok",
            },
        ],
    )

    result = analyze_phase_critical_path(queue, events_path=events, llm_path=llm)

    assert result["status"] == "DIAGNOSTIC_ONLY"
    assert result["observation_window_ns"] == 30
    assert result["phase_time_ns"] == {
        "COMPILE_ACTIVE": 10,
        "BINDING": 10,
        "NO_SOURCE_ARRIVED": 10,
    }
    assert result["ready_work"]["legal_ready_duration_ns"] == 10
    assert result["ready_work"]["max_legal_ready_compile_count"] == 1
    assert result["ideal_overlap"]["lower_bound_ns"] == 20
    assert result["ideal_overlap"]["speedup_upper_bound"] == pytest.approx(1.5)
    assert result["request_overlap"]["compile_frontier_overlap_ns"] == 0


def test_phase_analyzer_does_not_call_transport_wait_work_ready(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    _write_queue(
        queue,
        [
            {
                "event_type": "scheduler_state",
                "timestamp_ns": 0,
                "frontier_phase": "COMPILE_ACTIVE",
                "frontier_wait_reason": "COMPILE_IN_PROGRESS",
                "legal_ready_compile_count": 0,
                "prepared_rob_occupancy": 0,
                "active_compile_count": 1,
                "configured_limit": 2,
                "active_count": 1,
                "waiting_count": 3,
            },
            {
                "event_type": "scheduler_state",
                "timestamp_ns": 10,
                "frontier_phase": "PUBLISHED",
                "frontier_wait_reason": "NONE",
                "legal_ready_compile_count": 0,
                "prepared_rob_occupancy": 0,
                "active_compile_count": 0,
                "configured_limit": 2,
                "active_count": 0,
                "waiting_count": 0,
            },
        ],
    )

    result = analyze_phase_critical_path(queue)

    assert result["admission"]["under_capacity_with_waiter_ns"] == 10
    assert result["ready_work"]["legal_ready_duration_ns"] == 0
    assert result["verdict"]["admission"] == "ADMISSION_UNDER_CAPACITY_WITH_WAITER_OBSERVED"
    assert result["verdict"]["ready_pool"] == "NO_LEGAL_READY_WORK_OBSERVED"


def test_phase_analyzer_fails_closed_on_invalid_queue_hash(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    _write_queue(
        queue,
        [
            {
                "event_type": "scheduler_state",
                "timestamp_ns": 0,
                "frontier_phase": "PUBLISHED",
                "frontier_wait_reason": "NONE",
                "legal_ready_compile_count": 0,
                "prepared_rob_occupancy": 0,
                "active_compile_count": 0,
                "configured_limit": 2,
                "active_count": 0,
                "waiting_count": 0,
            }
        ],
    )
    queue.write_text(queue.read_text(encoding="utf-8").replace('"timestamp_ns": 0', '"timestamp_ns": 1'), encoding="utf-8")

    with pytest.raises(MemBindV31PhaseDiagnosticError, match="record_hash_mismatch"):
        analyze_phase_critical_path(queue)
