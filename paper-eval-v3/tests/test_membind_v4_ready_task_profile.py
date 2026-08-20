"""TDD contract for the sealed-trace ready-task opportunity profiler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v4.ready_task_profile import (
    MemBindV4ReadyTaskProfileError,
    analyze_ready_task_opportunities,
)


QUEUE_SCHEMA = "membind.paper-eval-v3.membind-v31-queue.v1"
SCHEDULER_SCHEMA = "membind.paper-eval-v3.membind-v31-scheduler-state.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.membind-v31-pilot-lifecycle.v1"
LLM_SCHEMA = "membind.paper-eval-v3.membind-v31-pilot-llm.v1"


def _write_queue(path: Path, rows: list[dict[str, object]]) -> None:
    lines: list[str] = []
    for sequence, row in enumerate(rows):
        selected = {
            "schema_version": SCHEDULER_SCHEMA,
            "event_type": "scheduler_state",
            "event_sequence": sequence,
            "reason": "FIXTURE",
            "stream_id": "07741c45",
            "trigger_source_sequence": None,
            "frontier_source_sequence": 0,
            "frontier_wait_reason": "NONE",
            "timestamp_ns": 0,
            "legal_ready_compile_count": 0,
            **row,
        }
        record = {"schema_version": QUEUE_SCHEMA, "row": selected}
        lines.append(
            json.dumps(
                {"record": record, "record_sha256": payload_sha256(record)},
                sort_keys=True,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_events(path: Path, rows: list[dict[str, object]]) -> None:
    lines: list[str] = []
    for sequence, row in enumerate(rows):
        event = {
            "schema_version": EVENT_SCHEMA,
            "event_sequence": sequence,
            "source_sha256": f"{'0' * 62}{int(row['source_sequence']):02d}",
            "telemetry": {},
            **row,
        }
        lines.append(
            json.dumps(
                {"event": event, "event_sha256": payload_sha256(event)},
                sort_keys=True,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_llm(path: Path, rows: list[dict[str, object]]) -> None:
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


def _lifecycle_rows() -> list[dict[str, object]]:
    times = {
        0: (0, 10, 20, 30, 40, 50),
        1: (2, 12, 22, 52, 62, 70),
        2: (4, 14, 24, 72, 82, 90),
    }
    names = (
        "ARRIVAL",
        "COMPILE_STARTED",
        "PREPARED_DURABLE",
        "BIND_STARTED",
        "COMMIT_RETURNED",
        "PUBLICATION_DURABLE",
    )
    return sorted(
        (
            {
                "event_type": name,
                "source_sequence": source,
                "timestamp_ns": timestamp,
            }
            for source, source_times in times.items()
            for name, timestamp in zip(names, source_times, strict=True)
        ),
        key=lambda row: (int(row["timestamp_ns"]), int(row["source_sequence"])),
    )


def _llm_rows() -> list[dict[str, object]]:
    requests = (
        ("c0", "COMPILE", 0, 10, 11, 19, 100),
        ("c1", "COMPILE", 1, 19, 20, 28, 200),
        ("b0", "FRONTIER", 0, 30, 31, 39, 300),
        ("b1", "FRONTIER", 1, 52, 53, 61, 400),
    )
    rows: list[dict[str, object]] = []
    for request_id, kind, source, submitted, start, terminal, tokens in requests:
        rows.extend(
            [
                {
                    "event_type": "llm_request_submitted",
                    "timestamp_ns": submitted,
                    "request_id": request_id,
                    "request_kind": kind,
                    "stream_id": "07741c45",
                    "source_sequence": source,
                    "token_count": tokens,
                },
                {
                    "event_type": "llm_request_start",
                    "timestamp_ns": start,
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
    rows.sort(key=lambda row: (int(row["timestamp_ns"]), str(row["event_type"])))
    return rows


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    queue = tmp_path / "queue.jsonl"
    events = tmp_path / "events.jsonl"
    llm = tmp_path / "llm.jsonl"
    _write_queue(
        queue,
        [
            {"timestamp_ns": 0, "frontier_phase": "WAITING_FOR_COMPILE", "legal_ready_compile_count": 1},
            {"timestamp_ns": 2, "frontier_phase": "WAITING_FOR_COMPILE", "legal_ready_compile_count": 2},
            {"timestamp_ns": 4, "frontier_phase": "WAITING_FOR_COMPILE", "legal_ready_compile_count": 3},
            {"timestamp_ns": 10, "frontier_phase": "COMPILE_ACTIVE", "legal_ready_compile_count": 2},
            {"timestamp_ns": 12, "frontier_phase": "COMPILE_ACTIVE", "legal_ready_compile_count": 1},
            {"timestamp_ns": 14, "frontier_phase": "COMPILE_ACTIVE", "legal_ready_compile_count": 0},
            {"timestamp_ns": 20, "frontier_phase": "READY_TO_BIND", "legal_ready_compile_count": 0},
            {"timestamp_ns": 30, "frontier_phase": "BINDING", "legal_ready_compile_count": 0},
            {"timestamp_ns": 90, "frontier_phase": "PUBLISHED", "legal_ready_compile_count": 0},
        ],
    )
    _write_events(events, _lifecycle_rows())
    _write_llm(llm, _llm_rows())
    return queue, events, llm


def test_profile_measures_selectable_and_workflow_ready_width(tmp_path: Path) -> None:
    queue, events, llm = _paths(tmp_path)

    result = analyze_ready_task_opportunities(queue, events, llm)

    assert result["status"] == "DIAGNOSTIC_ONLY"
    assert result["source_file_sha256s"] == {
        "events": result["source_file_sha256s"]["events"],
        "llm": result["source_file_sha256s"]["llm"],
        "queue": result["source_file_sha256s"]["queue"],
    }
    selectable = result["scheduler_selectable"]
    assert selectable["peak_ready_width"] == 3
    assert selectable["peak_same_type_ready_width"] == 3
    assert selectable["operator_resolution"] == "WORKFLOW_STAGE_ONLY"
    assert selectable["duration_ns_at_ready_width_ge"]["2"] == 10
    assert selectable["duration_ns_at_same_type_width_ge"]["2"] == 10
    assert selectable["time_fraction_at_ready_width_ge"]["2"] == pytest.approx(
        10 / 90
    )
    assert selectable["time_fraction_at_same_type_width_ge"]["2"] == pytest.approx(
        10 / 90
    )

    workflow = result["workflow_ready"]
    assert workflow["peak_ready_width"] == 3
    assert workflow["peak_same_type_ready_width"] == 3
    assert workflow["peak_llm_heavy_ready_width"] == 2
    # Workflow readiness includes both Compile and the serial frontier Bind
    # intervals; the scheduler aggregate is the separate 10 ns view above.
    assert workflow["duration_ns_at_ready_width_ge"]["2"] == 40
    assert workflow["time_fraction_at_ready_width_ge"]["2"] == pytest.approx(
        40 / 90
    )
    assert workflow["time_fraction_at_llm_heavy_ready_width_ge"][
        "2"
    ] == pytest.approx(16 / 90)
    assert result["fine_grained_operator_profile"]["status"] == "NOT_OBSERVABLE"


def test_profile_reports_residence_operator_source_and_llm_annotation(tmp_path: Path) -> None:
    queue, events, llm = _paths(tmp_path)

    result = analyze_ready_task_opportunities(queue, events, llm)

    residence = result["ready_residence"]
    assert residence["task_count"] == 6
    assert residence["mean_ns"] == pytest.approx(118 / 6)
    assert residence["p95_ns"] == pytest.approx(43.5)
    assert residence["by_operator"]["COMPILE"]["mean_ns"] == 10
    assert residence["by_operator"]["BIND"]["mean_ns"] == pytest.approx(88 / 3)

    tasks = {
        (row["source_id"], row["operator_type"]): row for row in result["tasks"]
    }
    assert tasks[(0, "COMPILE")]["llm_heavy"] is True
    assert tasks[(0, "COMPILE")]["llm_request_count"] == 1
    assert tasks[(0, "COMPILE")]["llm_input_tokens"] == 100
    assert tasks[(2, "COMPILE")]["llm_heavy"] is False
    assert tasks[(0, "BIND")]["frontier_critical"] is True
    assert tasks[(1, "COMPILE")]["frontier_critical"] is False


def test_profile_measures_critical_ready_overlap_with_noncritical_llm(tmp_path: Path) -> None:
    queue, events, llm = _paths(tmp_path)

    result = analyze_ready_task_opportunities(queue, events, llm)

    critical = result["critical_path"]
    assert critical["critical_ready_residence_ns"] == 88
    assert critical["critical_ready_overlap_with_noncritical_llm_ns"] == 8
    assert critical["causality"] == "OVERLAP_ONLY_NOT_CAUSAL_BLOCKING"


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("queue_hash", "record_hash_mismatch"),
        ("negative_width", "legal_ready_compile_count_invalid"),
        ("missing_lifecycle", "source_lifecycle_invalid"),
        ("orphan_llm_start", "llm_request_without_submission"),
    ],
)
def test_profile_fails_closed_on_malformed_sealed_traces(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    queue, events, llm = _paths(tmp_path)
    if mutation == "queue_hash":
        queue.write_text(
            queue.read_text(encoding="utf-8").replace(
                '"legal_ready_compile_count": 1',
                '"legal_ready_compile_count": 9',
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "negative_width":
        rows = json.loads(queue.read_text(encoding="utf-8").splitlines()[0])
        rows["record"]["row"]["legal_ready_compile_count"] = -1
        rows["record_sha256"] = payload_sha256(rows["record"])
        lines = queue.read_text(encoding="utf-8").splitlines()
        lines[0] = json.dumps(rows, sort_keys=True)
        queue.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation == "missing_lifecycle":
        lines = events.read_text(encoding="utf-8").splitlines()
        events.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    else:
        _write_llm(
            llm,
            [
                {
                    "event_type": "llm_request_start",
                    "timestamp_ns": 1,
                    "request_id": "orphan",
                }
            ],
        )

    with pytest.raises(MemBindV4ReadyTaskProfileError, match=error):
        analyze_ready_task_opportunities(queue, events, llm)
