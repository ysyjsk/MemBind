"""Offline phase/critical-path diagnostics for MemBind v3.1.

This module reads immutable ``queue.jsonl``, ``events.jsonl`` and
``llm.jsonl`` artifacts only.  It separates scheduler-state intervals from
nested LLM service spans, so a service-work fraction is never presented as a
wall-clock critical-path measurement.  Results are permanently diagnostic
only and cannot make a failed or pilot run mergeable.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from paper_eval.artifacts import payload_sha256, sha256_file


PHASE_DIAGNOSTIC_SCHEMA = (
    "membind.paper-eval-v3.membind-v31-phase-critical-path.v1"
)
_QUEUE_SCHEMA = "membind.paper-eval-v3.membind-v31-queue.v1"
_EVENT_SCHEMA = "membind.paper-eval-v3.membind-v31-pilot-lifecycle.v1"
_LLM_SCHEMA_SUFFIX = "-llm.v1"
_SCHEDULER_PHASES = {
    "COMPILE_ACTIVE",
    "BINDING",
    "BIND_DISPATCHED",
    "READY_TO_BIND",
    "WAITING_FOR_COMPILE",
    "NO_SOURCE_ARRIVED",
    "PUBLISHED",
    "TERMINAL",
}


class MemBindV31PhaseDiagnosticError(ValueError):
    """A phase diagnostic artifact is malformed or internally inconsistent."""


def _fail(code: str) -> MemBindV31PhaseDiagnosticError:
    return MemBindV31PhaseDiagnosticError(code)


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _finite_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def _interval_union(intervals: Sequence[tuple[int, int]]) -> int:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    if not ordered:
        return 0
    total = 0
    left, right = ordered[0]
    for start, end in ordered[1:]:
        if start <= right:
            right = max(right, end)
        else:
            total += right - left
            left, right = start, end
    return total + right - left


def _intersection_union(
    left: Sequence[tuple[int, int]], right: Sequence[tuple[int, int]]
) -> int:
    intersections: list[tuple[int, int]] = []
    for left_start, left_end in left:
        for right_start, right_end in right:
            start = max(left_start, right_start)
            end = min(left_end, right_end)
            if end > start:
                intersections.append((start, end))
    return _interval_union(intersections)


def _read_json_lines(path: Path) -> list[dict[str, object]]:
    target = Path(path)
    if not target.is_file():
        raise _fail("trace_missing")
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("trace_unreadable") from None
    rows: list[dict[str, object]] = []
    for line in lines:
        if not line:
            raise _fail("trace_blank_line")
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise _fail("trace_json_invalid") from None
        if not isinstance(value, Mapping):
            raise _fail("trace_envelope_invalid")
        rows.append(dict(value))
    return rows


def _read_queue(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    expected_by_type: dict[str, int] = defaultdict(int)
    for wrapper in _read_json_lines(path):
        if set(wrapper) != {"record", "record_sha256"}:
            raise _fail("queue_envelope_invalid")
        record = wrapper.get("record")
        if not isinstance(record, Mapping) or record.get("schema_version") != _QUEUE_SCHEMA:
            raise _fail("queue_schema_invalid")
        if wrapper.get("record_sha256") != payload_sha256(record):
            raise _fail("record_hash_mismatch")
        row = record.get("row")
        if not isinstance(row, Mapping):
            raise _fail("queue_row_invalid")
        selected = dict(row)
        event_type = selected.get("event_type")
        if event_type not in {"scheduler_state", "admission_snapshot"}:
            raise _fail("queue_event_type_invalid")
        if selected.get("event_sequence") != expected_by_type[event_type]:
            raise _fail("queue_event_sequence_invalid")
        expected_by_type[event_type] += 1
        _nonnegative_int(selected.get("timestamp_ns"), "timestamp_invalid")
        rows.append(selected)
    if not rows:
        raise _fail("queue_empty")
    return rows


def _read_events(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for wrapper in _read_json_lines(path):
        if set(wrapper) != {"event", "event_sha256"}:
            raise _fail("events_envelope_invalid")
        event = wrapper.get("event")
        if not isinstance(event, Mapping) or event.get("schema_version") != _EVENT_SCHEMA:
            raise _fail("events_schema_invalid")
        if wrapper.get("event_sha256") != payload_sha256(event):
            raise _fail("event_hash_mismatch")
        selected = dict(event)
        _nonnegative_int(selected.get("event_sequence"), "event_sequence_invalid")
        _nonnegative_int(selected.get("timestamp_ns"), "timestamp_invalid")
        rows.append(selected)
    return rows


def _read_llm(path: Path) -> dict[str, object]:
    """Parse both the original and pilot LLM trace schema without exposing content."""

    requests: dict[str, dict[str, Any]] = {}
    regions: list[tuple[int, int]] = []
    open_regions: dict[str, int] = {}
    expected_sequence = 0
    for wrapper in _read_json_lines(path):
        if set(wrapper) != {"record", "record_sha256"}:
            raise _fail("llm_envelope_invalid")
        record = wrapper.get("record")
        if not isinstance(record, Mapping) or not str(record.get("schema_version", "")).endswith(
            _LLM_SCHEMA_SUFFIX
        ):
            raise _fail("llm_schema_invalid")
        if wrapper.get("record_sha256") != payload_sha256(record):
            raise _fail("record_hash_mismatch")
        row = record.get("row")
        if not isinstance(row, Mapping) or row.get("event_sequence") != expected_sequence:
            raise _fail("llm_event_sequence_invalid")
        expected_sequence += 1
        selected = dict(row)
        timestamp = _nonnegative_int(selected.get("timestamp_ns"), "timestamp_invalid")
        event_type = selected.get("event_type")
        if event_type == "frontier_bind_region_start":
            stream = selected.get("stream_id")
            sequence = _nonnegative_int(selected.get("source_sequence"), "source_sequence_invalid")
            if not isinstance(stream, str) or not stream:
                raise _fail("stream_id_invalid")
            key = f"{stream}:{sequence}"
            if key in open_regions:
                raise _fail("region_duplicate_start")
            open_regions[key] = timestamp
            continue
        if event_type == "frontier_bind_region_end":
            stream = selected.get("stream_id")
            sequence = _nonnegative_int(selected.get("source_sequence"), "source_sequence_invalid")
            if not isinstance(stream, str) or not stream:
                raise _fail("stream_id_invalid")
            key = f"{stream}:{sequence}"
            start = open_regions.pop(key, None)
            if start is None or timestamp < start:
                raise _fail("region_end_invalid")
            regions.append((start, timestamp))
            continue
        if event_type not in {
            "llm_request_submitted",
            "llm_request_start",
            "llm_request_terminal",
        }:
            raise _fail("llm_event_type_invalid")
        request_id = selected.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise _fail("request_id_invalid")
        if event_type == "llm_request_submitted":
            kind = selected.get("request_kind")
            stream = selected.get("stream_id")
            sequence = _nonnegative_int(selected.get("source_sequence"), "source_sequence_invalid")
            if kind not in {"COMPILE", "FRONTIER"}:
                raise _fail("request_kind_invalid")
            if not isinstance(stream, str) or not stream:
                raise _fail("stream_id_invalid")
            if request_id in requests:
                raise _fail("request_duplicate")
            requests[request_id] = {
                "request_id": request_id,
                "request_kind": kind,
                "stream_id": stream,
                "source_sequence": sequence,
                "submitted_ns": timestamp,
                "token_count": _nonnegative_int(selected.get("token_count"), "token_count_invalid"),
                "start_ns": None,
                "terminal_ns": None,
            }
            continue
        request = requests.get(request_id)
        if request is None:
            raise _fail("request_without_submission")
        if event_type == "llm_request_start":
            if request["start_ns"] is not None or timestamp < request["submitted_ns"]:
                raise _fail("request_start_invalid")
            request["start_ns"] = timestamp
        else:
            if request["terminal_ns"] is not None:
                raise _fail("request_duplicate_terminal")
            if request["start_ns"] is not None and timestamp < request["start_ns"]:
                raise _fail("request_terminal_invalid")
            request["terminal_ns"] = timestamp
    if open_regions:
        raise _fail("region_unclosed")
    complete = [
        request
        for request in requests.values()
        if request["start_ns"] is not None and request["terminal_ns"] is not None
    ]
    spans_by_kind = {
        kind: [
            (int(request["start_ns"]), int(request["terminal_ns"]))
            for request in complete
            if request["request_kind"] == kind
        ]
        for kind in ("COMPILE", "FRONTIER")
    }
    return {
        "request_count": len(requests),
        "complete_request_count": len(complete),
        "incomplete_request_count": len(requests) - len(complete),
        "token_count_by_kind": {
            kind: sum(
                int(request["token_count"])
                for request in requests.values()
                if request["request_kind"] == kind
            )
            for kind in ("COMPILE", "FRONTIER")
        },
        "span_sum_ns_by_kind": {
            kind: sum(end - start for start, end in spans_by_kind[kind])
            for kind in spans_by_kind
        },
        "interval_union_ns_by_kind": {
            kind: _interval_union(spans_by_kind[kind]) for kind in spans_by_kind
        },
        "spans_by_kind": spans_by_kind,
        "region_union_ns": _interval_union(regions),
        "region_count": len(regions),
        "max_timestamp_ns": max(
            [
                int(request["terminal_ns"])
                for request in complete
            ]
            + [end for _start, end in regions],
            default=0,
        ),
    }


def _phase_intervals(
    scheduler_rows: Sequence[Mapping[str, object]], observation_end_ns: int
) -> tuple[dict[str, int], dict[str, list[tuple[int, int]]], dict[str, int]]:
    ordered = sorted(scheduler_rows, key=lambda row: int(row["timestamp_ns"]))
    if not ordered:
        raise _fail("scheduler_empty")
    intervals_by_phase: dict[str, list[tuple[int, int]]] = defaultdict(list)
    ready_intervals: list[tuple[int, int]] = []
    rob_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for index, row in enumerate(ordered):
        start = _nonnegative_int(row.get("timestamp_ns"), "timestamp_invalid")
        end = (
            _nonnegative_int(ordered[index + 1].get("timestamp_ns"), "timestamp_invalid")
            if index + 1 < len(ordered)
            else observation_end_ns
        )
        if end < start:
            raise _fail("scheduler_time_order_invalid")
        phase = row.get("frontier_phase")
        if phase not in _SCHEDULER_PHASES:
            raise _fail("frontier_phase_invalid")
        if end <= start:
            continue
        intervals_by_phase[str(phase)].append((start, end))
        ready_count = _nonnegative_int(row.get("legal_ready_compile_count"), "ready_count_invalid")
        if ready_count > 0:
            ready_intervals.append((start, end))
        occupancy = _nonnegative_int(row.get("prepared_rob_occupancy"), "rob_occupancy_invalid")
        rob_intervals[str(occupancy)].append((start, end))
    phase_time = {
        phase: _interval_union(intervals) for phase, intervals in sorted(intervals_by_phase.items())
    }
    rob_time = {
        count: _interval_union(intervals) for count, intervals in sorted(rob_intervals.items())
    }
    return phase_time, {"ready": ready_intervals}, rob_time


def _admission_summary(
    queue_rows: Sequence[Mapping[str, object]], capacity: int, observation_end_ns: int
) -> dict[str, object]:
    rows = [row for row in queue_rows if row.get("event_type") == "admission_snapshot"]
    if not rows:
        rows = [row for row in queue_rows if row.get("event_type") == "scheduler_state"]
    ordered = sorted(rows, key=lambda row: int(row["timestamp_ns"]))
    with_waiter = 0
    without_waiter = 0
    for index, row in enumerate(ordered):
        start = _nonnegative_int(row.get("timestamp_ns"), "timestamp_invalid")
        end = (
            _nonnegative_int(ordered[index + 1].get("timestamp_ns"), "timestamp_invalid")
            if index + 1 < len(ordered)
            else observation_end_ns
        )
        duration = max(0, end - start)
        active = _nonnegative_int(row.get("active_count"), "active_count_invalid")
        waiting = _nonnegative_int(row.get("waiting_count"), "waiting_count_invalid")
        if active < capacity:
            if waiting > 0:
                with_waiter += duration
            else:
                without_waiter += duration
    return {
        "capacity": capacity,
        "snapshot_count": len(ordered),
        "under_capacity_with_waiter_ns": with_waiter,
        "under_capacity_without_waiter_ns": without_waiter,
    }


def _event_summary(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    counts: dict[str, int] = defaultdict(int)
    arrivals: list[int] = []
    publications: list[int] = []
    for event in events:
        event_type = event.get("event_type")
        counts[str(event_type)] += 1
        timestamp = _nonnegative_int(event.get("timestamp_ns"), "timestamp_invalid")
        if event_type == "ARRIVAL":
            arrivals.append(timestamp)
        elif event_type == "PUBLICATION_DURABLE":
            publications.append(timestamp)
    return {
        "event_count": len(events),
        "event_counts": dict(sorted(counts.items())),
        "arrival_count": len(arrivals),
        "publication_count": len(publications),
        "lifecycle_makespan_ns": (
            max(publications) - min(arrivals) if arrivals and publications else None
        ),
    }


def analyze_phase_critical_path(
    queue_path: Path,
    *,
    events_path: Path | None = None,
    llm_path: Path | None = None,
    admission_capacity: int | None = None,
) -> dict[str, object]:
    """Recompute phase exposure and structural overlap bounds from immutable traces."""

    if admission_capacity is not None and (
        isinstance(admission_capacity, bool)
        or not isinstance(admission_capacity, int)
        or admission_capacity <= 0
    ):
        raise _fail("admission_capacity_invalid")
    queue_rows = _read_queue(Path(queue_path))
    scheduler_rows = [row for row in queue_rows if row.get("event_type") == "scheduler_state"]
    if not scheduler_rows:
        raise _fail("scheduler_empty")
    event_rows = _read_events(Path(events_path)) if events_path is not None else []
    llm = _read_llm(Path(llm_path)) if llm_path is not None else None
    timestamps = [int(row["timestamp_ns"]) for row in queue_rows]
    timestamps.extend(int(row["timestamp_ns"]) for row in event_rows)
    if llm is not None:
        timestamps.append(int(llm["max_timestamp_ns"]))
    start_ns = min(timestamps)
    end_ns = max(timestamps)
    observation_window_ns = end_ns - start_ns
    phase_time, ready_intervals, rob_time = _phase_intervals(
        scheduler_rows, end_ns
    )
    phase_time = {phase: value for phase, value in phase_time.items() if value > 0}
    ready_duration = _interval_union(ready_intervals["ready"])
    max_ready = max(
        _nonnegative_int(row.get("legal_ready_compile_count"), "ready_count_invalid")
        for row in scheduler_rows
    )
    compile_phase_ns = phase_time.get("COMPILE_ACTIVE", 0)
    bind_phase_ns = phase_time.get("BINDING", 0)
    no_source_ns = phase_time.get("NO_SOURCE_ARRIVED", 0)
    lower_bound = no_source_ns + max(compile_phase_ns, bind_phase_ns)
    ideal_overlap = {
        "compile_phase_ns": compile_phase_ns,
        "bind_phase_ns": bind_phase_ns,
        "no_source_phase_ns": no_source_ns,
        "lower_bound_ns": lower_bound,
        "speedup_upper_bound": (
            observation_window_ns / lower_bound if lower_bound > 0 else None
        ),
        "interpretation": "STRUCTURAL_OVERLAP_BOUND_NOT_OBSERVED_SPEEDUP",
    }
    capacity = admission_capacity
    if capacity is None:
        capacity_values = [
            row.get("configured_limit")
            for row in queue_rows
            if isinstance(row.get("configured_limit"), int)
        ]
        capacity = capacity_values[0] if capacity_values else 2
    admission = _admission_summary(queue_rows, capacity, end_ns)
    result: dict[str, object] = {
        "schema_version": PHASE_DIAGNOSTIC_SCHEMA,
        "status": "DIAGNOSTIC_ONLY",
        "queue_trace_sha256": sha256_file(Path(queue_path)),
        "events_trace_sha256": sha256_file(Path(events_path)) if events_path is not None else None,
        "llm_trace_sha256": sha256_file(Path(llm_path)) if llm_path is not None else None,
        "observation_start_ns": start_ns,
        "observation_end_ns": end_ns,
        "observation_window_ns": observation_window_ns,
        "phase_time_ns": phase_time,
        "phase_fraction": {
            phase: _finite_ratio(value, observation_window_ns)
            for phase, value in sorted(phase_time.items())
        },
        "ready_work": {
            "legal_ready_duration_ns": ready_duration,
            "legal_ready_fraction": _finite_ratio(ready_duration, observation_window_ns),
            "max_legal_ready_compile_count": max_ready,
            "prepared_rob_time_by_count_ns": rob_time,
            "ready_work_observable": True,
        },
        "admission": admission,
        "ideal_overlap": ideal_overlap,
        "event_summary": _event_summary(event_rows) if event_rows else None,
        "request_overlap": None,
        "verdict": {},
    }
    if llm is not None:
        compile_spans = llm["spans_by_kind"]["COMPILE"]
        frontier_spans = llm["spans_by_kind"]["FRONTIER"]
        result["request_overlap"] = {
            "compile_frontier_overlap_ns": _intersection_union(compile_spans, frontier_spans),
            "compile_interval_union_ns": llm["interval_union_ns_by_kind"]["COMPILE"],
            "frontier_interval_union_ns": llm["interval_union_ns_by_kind"]["FRONTIER"],
            "compile_service_span_sum_ns": llm["span_sum_ns_by_kind"]["COMPILE"],
            "frontier_service_span_sum_ns": llm["span_sum_ns_by_kind"]["FRONTIER"],
            "request_count": llm["request_count"],
            "complete_request_count": llm["complete_request_count"],
            "incomplete_request_count": llm["incomplete_request_count"],
            "token_count_by_kind": llm["token_count_by_kind"],
            "region_count": llm["region_count"],
            "region_union_ns": llm["region_union_ns"],
        }
    admission_status = (
        "ADMISSION_UNDER_CAPACITY_WITH_WAITER_OBSERVED"
        if admission["under_capacity_with_waiter_ns"] > 0
        else "NO_ADMISSION_UNDER_CAPACITY_WITH_WAITER_OBSERVED"
    )
    if max_ready == 0:
        ready_status = "NO_LEGAL_READY_WORK_OBSERVED"
    elif max_ready == 1:
        ready_status = "SINGLE_LEGAL_READY_WORK_ONLY_OBSERVED"
    else:
        ready_status = "MULTIPLE_LEGAL_READY_WORK_OBSERVED"
    if bind_phase_ns > compile_phase_ns:
        bind_status = "BIND_PHASE_LONGER_THAN_COMPILE_PHASE"
    elif bind_phase_ns > 0:
        bind_status = "BIND_PHASE_NOT_LONGER_THAN_COMPILE_PHASE"
    else:
        bind_status = "BIND_PHASE_NOT_OBSERVABLE"
    result["verdict"] = {
        "admission": admission_status,
        "ready_pool": ready_status,
        "bind_phase": bind_status,
        "scientific_claim": "DIAGNOSTIC_ONLY_REQUIRES_ALIGNED_FORMAL_CONTROL",
    }
    result["payload_sha256"] = payload_sha256(result)
    return result


def render_phase_critical_path_report(result: Mapping[str, object]) -> str:
    """Render a compact, content-safe Markdown report from a sealed result."""

    phase_time = result.get("phase_time_ns", {})
    phase_fraction = result.get("phase_fraction", {})
    rows = [
        "# MemBind v3.1 Phase Critical-Path Diagnostic",
        "",
        "> Diagnostic-only offline analysis; not a formal performance result.",
        "",
        f"- observation window: `{result.get('observation_window_ns')} ns`",
        f"- queue trace: `{result.get('queue_trace_sha256')}`",
        "",
        "## Phase Exposure",
        "",
        "| Phase | Time (ns) | Fraction |",
        "| --- | ---: | ---: |",
    ]
    for phase in sorted(phase_time):
        fraction = phase_fraction.get(phase)
        rows.append(f"| `{phase}` | {phase_time[phase]} | {fraction:.6f} |" if isinstance(fraction, float) else f"| `{phase}` | {phase_time[phase]} | n/a |")
    ready = result.get("ready_work", {})
    admission = result.get("admission", {})
    ideal = result.get("ideal_overlap", {})
    verdict = result.get("verdict", {})
    rows.extend(
        [
            "",
            "## Diagnostic Findings",
            "",
            f"- legal-ready duration: `{ready.get('legal_ready_duration_ns')} ns`",
            f"- max legal-ready Compile count: `{ready.get('max_legal_ready_compile_count')}`",
            f"- under-capacity with waiter: `{admission.get('under_capacity_with_waiter_ns')} ns`",
            f"- structural overlap lower bound: `{ideal.get('lower_bound_ns')} ns`",
            f"- structural speedup upper bound: `{ideal.get('speedup_upper_bound')}`",
            "",
            "## Verdict",
            "",
            f"- admission: `{verdict.get('admission')}`",
            f"- ready pool: `{verdict.get('ready_pool')}`",
            f"- Bind phase: `{verdict.get('bind_phase')}`",
            f"- claim boundary: `{verdict.get('scientific_claim')}`",
            "",
        ]
    )
    return "\n".join(rows)


__all__ = [
    "MemBindV31PhaseDiagnosticError",
    "PHASE_DIAGNOSTIC_SCHEMA",
    "analyze_phase_critical_path",
    "render_phase_critical_path_report",
]
