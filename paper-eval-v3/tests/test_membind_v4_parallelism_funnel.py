"""TDD contracts for the cross-layer parallelism funnel diagnostic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v4.parallelism_funnel import (
    MemBindV4ParallelismFunnelError,
    analyze_parallelism_funnel,
)


QUEUE_SCHEMA = "membind.paper-eval-v3.membind-v31-queue.v1"
SCHEDULER_SCHEMA = "membind.paper-eval-v3.membind-v31-scheduler-state.v1"
ADMISSION_SCHEMA = "membind.paper-eval-v3.membind-v31-admission-state.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.membind-v31-pilot-lifecycle.v1"
LLM_SCHEMA = "membind.paper-eval-v3.membind-v31-pilot-llm.v1"


def _seal_queue(path: Path, rows: list[dict[str, object]]) -> None:
    sequences = {"scheduler_state": 0, "admission_snapshot": 0}
    lines: list[str] = []
    for row in sorted(rows, key=lambda item: int(item["timestamp_ns"])):
        event_type = str(row["event_type"])
        selected = {
            "event_sequence": sequences[event_type],
            **row,
        }
        sequences[event_type] += 1
        record = {"schema_version": QUEUE_SCHEMA, "row": selected}
        lines.append(
            json.dumps(
                {"record": record, "record_sha256": payload_sha256(record)},
                sort_keys=True,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _scheduler(timestamp: int, phase: str, ready: int) -> dict[str, object]:
    return {
        "schema_version": SCHEDULER_SCHEMA,
        "event_type": "scheduler_state",
        "reason": "FIXTURE",
        "stream_id": "07741c45",
        "trigger_source_sequence": None,
        "frontier_source_sequence": 0,
        "frontier_phase": phase,
        "frontier_wait_reason": "NONE",
        "timestamp_ns": timestamp,
        "legal_ready_compile_count": ready,
    }


def _admission(
    timestamp: int,
    *,
    active_compile: int = 0,
    active_frontier: int = 0,
    waiting_compile: int = 0,
    waiting_frontier: int = 0,
) -> dict[str, object]:
    return {
        "schema_version": ADMISSION_SCHEMA,
        "event_type": "admission_snapshot",
        "reason": "FIXTURE",
        "timestamp_ns": timestamp,
        "active_count": active_compile + active_frontier,
        "waiting_count": waiting_compile + waiting_frontier,
        "active_compile_count": active_compile,
        "active_frontier_count": active_frontier,
        "waiting_compile_count": waiting_compile,
        "waiting_frontier_count": waiting_frontier,
        "configured_limit": 2,
        "policy": "CACHE_AFFINE",
        "barrier_holds": False,
        "frontier_bind_region_count": 0,
        "frontier_transport_phase": "OUTSIDE_FRONTIER_REGION",
    }


def _seal_events(path: Path) -> None:
    names = (
        "ARRIVAL",
        "COMPILE_STARTED",
        "PREPARED_DURABLE",
        "BIND_STARTED",
        "COMMIT_RETURNED",
        "PUBLICATION_DURABLE",
    )
    times = {
        0: (0, 2, 10, 12, 25, 26),
        1: (5, 6, 15, 26, 35, 36),
    }
    rows = sorted(
        (
            (timestamp, source, event_type)
            for source, values in times.items()
            for event_type, timestamp in zip(names, values, strict=True)
        ),
        key=lambda item: (item[0], item[1]),
    )
    lines: list[str] = []
    for sequence, (timestamp, source, event_type) in enumerate(rows):
        event = {
            "schema_version": EVENT_SCHEMA,
            "event_sequence": sequence,
            "event_type": event_type,
            "source_sequence": source,
            "source_sha256": f"{source + 1:064x}",
            "timestamp_ns": timestamp,
            "telemetry": {},
        }
        lines.append(
            json.dumps(
                {"event": event, "event_sha256": payload_sha256(event)},
                sort_keys=True,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seal_llm(path: Path) -> None:
    requests = (
        ("c0", "COMPILE", 0, 2, 2, 10),
        ("c1", "COMPILE", 1, 6, 6, 15),
        ("f0", "FRONTIER", 0, 12, 12, 20),
        ("f1", "FRONTIER", 0, 13, 20, 24),
        ("f2", "FRONTIER", 0, 14, 20, 25),
    )
    rows: list[dict[str, object]] = []
    for request_id, kind, source, submitted, started, terminal in requests:
        rows.extend(
            [
                {
                    "event_type": "llm_request_submitted",
                    "timestamp_ns": submitted,
                    "request_id": request_id,
                    "request_kind": kind,
                    "stream_id": "07741c45",
                    "source_sequence": source,
                    "token_count": 10,
                },
                {
                    "event_type": "llm_request_start",
                    "timestamp_ns": started,
                    "request_id": request_id,
                },
                {
                    "event_type": "llm_request_terminal",
                    "timestamp_ns": terminal,
                    "request_id": request_id,
                    "status": "ok",
                    "error_class": None,
                },
            ]
        )
    event_order = {
        "llm_request_submitted": 0,
        "llm_request_start": 1,
        "llm_request_terminal": 2,
    }
    rows.sort(
        key=lambda row: (
            int(row["timestamp_ns"]),
            event_order[str(row["event_type"])],
        )
    )
    lines: list[str] = []
    for sequence, row in enumerate(rows):
        record = {
            "schema_version": LLM_SCHEMA,
            "row": {"event_sequence": sequence, **row},
        }
        lines.append(
            json.dumps(
                {"record": record, "record_sha256": payload_sha256(record)},
                sort_keys=True,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    queue = tmp_path / "queue.jsonl"
    events = tmp_path / "events.jsonl"
    llm = tmp_path / "llm.jsonl"
    _seal_queue(
        queue,
        [
            _scheduler(0, "WAITING_FOR_COMPILE", 1),
            _scheduler(2, "COMPILE_ACTIVE", 0),
            _scheduler(10, "READY_TO_BIND", 0),
            _scheduler(12, "BINDING", 0),
            _scheduler(36, "PUBLISHED", 0),
            _admission(2, active_compile=1),
            _admission(6, active_compile=2),
            _admission(12, active_frontier=1),
            _admission(14, active_frontier=1, waiting_frontier=2),
            _admission(20, active_frontier=2),
            _admission(25),
        ],
    )
    _seal_events(events)
    _seal_llm(llm)
    return queue, events, llm


def test_funnel_separates_ready_waiting_active_and_llm_pressure(
    tmp_path: Path,
) -> None:
    queue, events, llm = _fixture(tmp_path)

    result = analyze_parallelism_funnel(queue, events, llm)

    assert result["source_outstanding"]["peak_width"] == 2
    assert result["workflow_ready_waiting"]["peak_width"] == 1
    assert result["workflow_active"]["peak_width"] == 2
    assert result["llm_request_pending"]["peak_width"] == 4
    assert result["llm_admission_waiting"]["peak_width"] == 2
    assert result["llm_client_running"]["peak_width"] == 2
    assert result["admission_snapshots"]["peak_waiting_count"] == 2
    assert result["admission_snapshots"]["peak_active_count"] == 2
    assert result["decision"] == {
        "backend_bottleneck_proven": False,
        "coarse_stage_scheduler_authorized": False,
        "end_to_end_parallelism_collapse_proven": False,
        "llm_admission_pressure_observed": True,
        "root_cause_classification": (
            "COARSE_READY_POOL_NO_CHOICE_WITH_INTERNAL_LLM_FANOUT"
        ),
        "source_backlog_observed": True,
        "terminal": (
            "NO_STAGE_SCHEDULER_CHOICE_LLM_ADMISSION_BACKLOG_OBSERVED"
        ),
        "workload_too_sparse_proven": False,
    }


def test_funnel_keeps_backend_and_operator_internals_unobservable(
    tmp_path: Path,
) -> None:
    queue, events, llm = _fixture(tmp_path)

    result = analyze_parallelism_funnel(queue, events, llm)

    assert result["backend_internal"] == {
        "status": "NOT_OBSERVABLE",
        "unavailable_fields": [
            "vllm_batch_membership",
            "gpu_execution_width",
            "fine_grained_operator_identity",
        ],
    }
    assert result["llm_client_running"]["interpretation"] == (
        "client-observed request span, not GPU execution"
    )


def test_funnel_fails_closed_on_inconsistent_admission_snapshot(
    tmp_path: Path,
) -> None:
    queue, events, llm = _fixture(tmp_path)
    wrappers = [json.loads(line) for line in queue.read_text().splitlines()]
    target = next(
        wrapper
        for wrapper in wrappers
        if wrapper["record"]["row"]["event_type"] == "admission_snapshot"
    )
    target["record"]["row"]["active_count"] = 2
    target["record_sha256"] = payload_sha256(target["record"])
    queue.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in wrappers) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        MemBindV4ParallelismFunnelError,
        match="admission_active_count_inconsistent",
    ):
        analyze_parallelism_funnel(queue, events, llm)
