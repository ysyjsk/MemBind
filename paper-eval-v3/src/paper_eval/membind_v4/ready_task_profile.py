"""Offline ready-task opportunity profiling for the MemBind v4 lane.

This module is deliberately diagnostic-only.  It reads sealed v3.1 queue,
lifecycle, and LLM traces, reconstructs dependency readiness, and reports
where a scheduler could have had a choice.  It never changes an arrival trace,
starts a provider, or infers fine-grained Graphiti operators that the trace
does not identify.

There are two related but distinct views:

``scheduler_selectable``
    The aggregate legal-ready Compile counter emitted by the scheduler, plus
    the one frontier Bind task represented by ``READY_TO_BIND``.

``workflow_ready``
    Per-source dependency readiness reconstructed from ARRIVAL,
    PREPARED_DURABLE, COMPILE_STARTED, and BIND_STARTED lifecycle events.
    This view exposes task-level residence and LLM annotations, but it is not
    allowed to overwrite the scheduler's sealed aggregate evidence.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.membind_v31.phase_critical_path import (
    _interval_union,
    _intersection_union,
    _read_events,
    _read_llm,
    _read_queue,
)


READY_TASK_PROFILE_SCHEMA = (
    "membind.paper-eval-v4.ready-task-opportunity-profile.v1"
)

_LIFECYCLE_EVENTS = {
    "ARRIVAL": "arrival",
    "arrival": "arrival",
    "COMPILE_STARTED": "compile_started",
    "compile_start": "compile_started",
    "PREPARED_DURABLE": "prepared_durable",
    "prepared_durable": "prepared_durable",
    "BIND_STARTED": "bind_started",
    "bind_start": "bind_started",
    "COMMIT_RETURNED": "commit_returned",
    "commit_returned": "commit_returned",
    "PUBLICATION_DURABLE": "publication_durable",
    "publication_durable": "publication_durable",
}

_REQUIRED_LIFECYCLE = (
    "arrival",
    "compile_started",
    "prepared_durable",
    "bind_started",
    "commit_returned",
    "publication_durable",
)

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

_THRESHOLDS = (2, 4, 8)


class MemBindV4ReadyTaskProfileError(ValueError):
    """A sealed trace cannot support a trustworthy ready-task profile."""


def _fail(code: str) -> MemBindV4ReadyTaskProfileError:
    return MemBindV4ReadyTaskProfileError(code)


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _finite_nonnegative(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(code)
    selected = float(value)
    if not math.isfinite(selected) or selected < 0:
        raise _fail(code)
    return selected


def _percentile(values: Sequence[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left = int(math.floor(position))
    right = int(math.ceil(position))
    if left == right:
        return float(ordered[left])
    weight = position - left
    return float(ordered[left] + (ordered[right] - ordered[left]) * weight)


def _duration_thresholds(
    intervals: Sequence[tuple[int, int, int]],
) -> dict[str, int]:
    return {
        str(threshold): _interval_union(
            (start, end)
            for start, end, width in intervals
            if width >= threshold
        )
        for threshold in _THRESHOLDS
    }


def _time_fractions(
    durations: Mapping[str, int], observation_window_ns: int
) -> dict[str, float | None]:
    return {
        threshold: (
            duration / observation_window_ns
            if observation_window_ns > 0
            else None
        )
        for threshold, duration in durations.items()
    }


def _max_width(intervals: Sequence[tuple[int, int, int]]) -> int:
    return max((width for _start, _end, width in intervals), default=0)


def _same_type_intervals(
    intervals: Sequence[tuple[int, int, str]],
) -> tuple[list[tuple[int, int, int]], dict[str, list[tuple[int, int, int]]]]:
    """Return total width and a width series for each coarse workflow type."""

    boundaries = sorted(
        {
            boundary
            for start, end, _kind in intervals
            for boundary in (start, end)
        }
    )
    total: list[tuple[int, int, int]] = []
    by_kind: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for start, end in zip(boundaries, boundaries[1:]):
        if end <= start:
            continue
        active = [
            kind
            for left, right, kind in intervals
            if left <= start < right
        ]
        total.append((start, end, len(active)))
        counts = Counter(active)
        for kind, count in counts.items():
            by_kind[kind].append((start, end, count))
        for kind in {kind for _left, _right, kind in intervals} - set(counts):
            by_kind.setdefault(kind, []).append((start, end, 0))
    return total, dict(by_kind)


def _width_summary(
    intervals: Sequence[tuple[int, int, int]],
    *,
    observation_window_ns: int,
    same_type: Mapping[str, Sequence[tuple[int, int, int]]] | None = None,
) -> dict[str, object]:
    ready_durations = _duration_thresholds(intervals)
    summary: dict[str, object] = {
        "peak_ready_width": _max_width(intervals),
        "duration_ns_at_ready_width_ge": ready_durations,
        "time_fraction_at_ready_width_ge": _time_fractions(
            ready_durations, observation_window_ns
        ),
        "time_fraction_definition": (
            "threshold duration divided by the shared observation_window_ns"
        ),
    }
    if same_type is None:
        same_type_durations = _duration_thresholds(intervals)
        summary["peak_same_type_ready_width"] = _max_width(intervals)
        summary["duration_ns_at_same_type_width_ge"] = same_type_durations
        summary["time_fraction_at_same_type_width_ge"] = _time_fractions(
            same_type_durations, observation_window_ns
        )
        summary["same_type_width_by_operator"] = {}
    else:
        per_kind = {
            kind: {
                "peak_width": _max_width(rows),
                "duration_ns_at_width_ge": _duration_thresholds(rows),
            }
            for kind, rows in sorted(same_type.items())
        }
        summary["peak_same_type_ready_width"] = max(
            (int(row["peak_width"]) for row in per_kind.values()),
            default=0,
        )
        same_type_durations = {
            str(threshold): _interval_union(
                (start, end)
                for rows in same_type.values()
                for start, end, width in rows
                if width >= threshold
            )
            for threshold in _THRESHOLDS
        }
        summary["duration_ns_at_same_type_width_ge"] = same_type_durations
        summary["time_fraction_at_same_type_width_ge"] = _time_fractions(
            same_type_durations, observation_window_ns
        )
        summary["same_type_width_by_operator"] = per_kind
    return summary


def _validate_scheduler_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    selected = [dict(row) for row in rows if row.get("event_type") == "scheduler_state"]
    if not selected:
        raise _fail("scheduler_empty")
    previous = -1
    for row in selected:
        sequence = _nonnegative_int(row.get("event_sequence"), "queue_event_sequence_invalid")
        if sequence <= previous:
            raise _fail("queue_event_sequence_invalid")
        previous = sequence
        timestamp = _nonnegative_int(row.get("timestamp_ns"), "timestamp_invalid")
        if row.get("frontier_phase") not in _SCHEDULER_PHASES:
            raise _fail("frontier_phase_invalid")
        _nonnegative_int(
            row.get("legal_ready_compile_count"),
            "legal_ready_compile_count_invalid",
        )
        _nonnegative_int(
            row.get("frontier_source_sequence"),
            "frontier_source_sequence_invalid",
        )
        if timestamp < 0:
            raise _fail("timestamp_invalid")
    selected.sort(key=lambda row: int(row["timestamp_ns"]))
    if any(
        int(right["timestamp_ns"]) < int(left["timestamp_ns"])
        for left, right in zip(selected, selected[1:])
    ):
        raise _fail("scheduler_time_order_invalid")
    return selected


def _validate_lifecycle(rows: Sequence[Mapping[str, object]]) -> dict[int, dict[str, int]]:
    by_source: dict[int, dict[str, int]] = defaultdict(dict)
    previous_sequence = -1
    for row in rows:
        sequence = _nonnegative_int(row.get("event_sequence"), "event_sequence_invalid")
        if sequence <= previous_sequence:
            raise _fail("event_sequence_invalid")
        previous_sequence = sequence
        source = _nonnegative_int(row.get("source_sequence"), "source_sequence_invalid")
        timestamp = _nonnegative_int(row.get("timestamp_ns"), "timestamp_invalid")
        event = _LIFECYCLE_EVENTS.get(row.get("event_type"))
        if event is None:
            raise _fail("lifecycle_event_type_invalid")
        if event in by_source[source]:
            raise _fail("source_lifecycle_duplicate")
        by_source[source][event] = timestamp
    if not by_source:
        raise _fail("source_lifecycle_empty")
    for source, values in by_source.items():
        if set(values) != set(_REQUIRED_LIFECYCLE):
            raise _fail("source_lifecycle_invalid")
        ordered = [values[key] for key in _REQUIRED_LIFECYCLE]
        if any(right < left for left, right in zip(ordered, ordered[1:])):
            raise _fail("source_lifecycle_time_order_invalid")
    return {source: dict(values) for source, values in sorted(by_source.items())}


def _validate_contiguous_sources(source_rows: Mapping[int, Mapping[str, int]]) -> None:
    sources = sorted(source_rows)
    if sources != list(range(len(sources))):
        raise _fail("source_sequence_coverage_invalid")


def _task_records(
    lifecycle: Mapping[int, Mapping[str, int]],
    llm_requests: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[int, str], list[Mapping[str, object]]] = defaultdict(list)
    for request in llm_requests:
        source = _nonnegative_int(request.get("source_sequence"), "source_sequence_invalid")
        kind = request.get("request_kind")
        if kind not in {"COMPILE", "FRONTIER"}:
            raise _fail("request_kind_invalid")
        grouped[(source, "COMPILE" if kind == "COMPILE" else "BIND")].append(request)

    tasks: list[dict[str, object]] = []
    for source, values in lifecycle.items():
        definitions = (
            ("COMPILE", "arrival", "compile_started", "prepared_durable", False),
            ("BIND", "prepared_durable", "bind_started", "commit_returned", True),
        )
        for kind, ready_key, start_key, end_key, critical in definitions:
            requests = grouped.get((source, kind), [])
            spans = [
                (int(request["start_ns"]), int(request["terminal_ns"]))
                for request in requests
                if request.get("start_ns") is not None
                and request.get("terminal_ns") is not None
            ]
            ready_at = values[ready_key]
            started = values[start_key]
            ended = values[end_key]
            if started < ready_at:
                raise _fail("task_ready_order_invalid")
            if ended < started:
                raise _fail("task_service_time_order_invalid")
            task = {
                "task_id": f"{kind.lower()}:{source}",
                "source_id": source,
                "operator_type": kind,
                "operator_granularity": "WORKFLOW_STAGE",
                "ready_at_ns": ready_at,
                "started_at_ns": started,
                "ended_at_ns": ended,
                "ready_wait_ns": started - ready_at,
                "ready_residence_ns": started - ready_at,
                "service_ns": ended - started,
                "frontier_critical": critical,
                "llm_heavy": bool(requests),
                "llm_request_count": len(requests),
                "llm_input_tokens": sum(int(request["token_count"]) for request in requests),
                "llm_service_ns": _interval_union(spans),
            }
            tasks.append(task)
    return tasks


def _task_summary(tasks: Sequence[Mapping[str, object]]) -> dict[str, object]:
    waits = [int(task["ready_wait_ns"]) for task in tasks]
    by_operator: dict[str, dict[str, object]] = {}
    for kind in sorted({str(task["operator_type"]) for task in tasks}):
        values = [
            int(task["ready_wait_ns"])
            for task in tasks
            if task["operator_type"] == kind
        ]
        by_operator[kind] = {
            "task_count": len(values),
            "total_ns": sum(values),
            "mean_ns": sum(values) / len(values) if values else None,
            "p50_ns": _percentile(values, 0.50),
            "p95_ns": _percentile(values, 0.95),
            "max_ns": max(values, default=None),
        }
    return {
        "task_count": len(tasks),
        "total_ns": sum(waits),
        "mean_ns": sum(waits) / len(waits) if waits else None,
        "p50_ns": _percentile(waits, 0.50),
        "p95_ns": _percentile(waits, 0.95),
        "max_ns": max(waits, default=None),
        "by_operator": by_operator,
        "definition": "dependency-ready timestamp to actual task dispatch",
    }


def _workflow_intervals(
    tasks: Sequence[Mapping[str, object]],
) -> tuple[
    list[tuple[int, int, int]],
    dict[str, list[tuple[int, int, int]]],
    list[tuple[int, int, int]],
]:
    typed = [
        (int(task["ready_at_ns"]), int(task["started_at_ns"]), str(task["operator_type"]))
        for task in tasks
        if int(task["started_at_ns"]) > int(task["ready_at_ns"])
    ]
    total, by_type = _same_type_intervals(typed)
    heavy_typed = [
        (
            int(task["ready_at_ns"]),
            int(task["started_at_ns"]),
            str(task["operator_type"]),
        )
        for task in tasks
        if int(task["started_at_ns"]) > int(task["ready_at_ns"])
        and bool(task["llm_heavy"])
    ]
    heavy_total, _ = _same_type_intervals(heavy_typed)
    return total, by_type, heavy_total


def _timeline(
    scheduler: Sequence[Mapping[str, object]],
    observation_end_ns: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, row in enumerate(scheduler):
        start = int(row["timestamp_ns"])
        end = (
            int(scheduler[index + 1]["timestamp_ns"])
            if index + 1 < len(scheduler)
            else observation_end_ns
        )
        if end < start:
            raise _fail("scheduler_time_order_invalid")
        compile_width = int(row["legal_ready_compile_count"])
        bind_width = 1 if row.get("frontier_phase") == "READY_TO_BIND" else 0
        rows.append(
            {
                "event_sequence": int(row["event_sequence"]),
                "start_ns": start,
                "end_ns": end,
                "frontier_source_id": int(row["frontier_source_sequence"]),
                "frontier_phase": row["frontier_phase"],
                "compile_ready_width": compile_width,
                "bind_ready_width": bind_width,
                "ready_width": compile_width + bind_width,
                "same_type_ready_width": max(compile_width, bind_width),
            }
        )
    return rows


def _coarse_profile(
    timeline: Sequence[Mapping[str, object]], *, observation_window_ns: int
) -> dict[str, object]:
    total_intervals = [
        (int(row["start_ns"]), int(row["end_ns"]), int(row["ready_width"]))
        for row in timeline
    ]
    coarse_same_type = {
        "COMPILE": [
            (
                int(row["start_ns"]),
                int(row["end_ns"]),
                int(row["compile_ready_width"]),
            )
            for row in timeline
        ],
        "BIND": [
            (
                int(row["start_ns"]),
                int(row["end_ns"]),
                int(row["bind_ready_width"]),
            )
            for row in timeline
        ],
    }
    summary = _width_summary(
        total_intervals,
        observation_window_ns=observation_window_ns,
        same_type=coarse_same_type,
    )
    summary.update(
        {
            "operator_resolution": "WORKFLOW_STAGE_ONLY",
            "operator_types_observed": ["COMPILE", "BIND"],
            "compile_ready_width_peak": max(
                (int(row["compile_ready_width"]) for row in timeline),
                default=0,
            ),
            "bind_ready_width_peak": max(
                (int(row["bind_ready_width"]) for row in timeline),
                default=0,
            ),
            "same_type_ready_width_definition": (
                "maximum width within one observable workflow stage, not a fine-grained operator"
            ),
        }
    )
    return summary


def _critical_profile(
    tasks: Sequence[Mapping[str, object]],
    llm_spans: Mapping[str, Sequence[tuple[int, int]]],
) -> dict[str, object]:
    critical = [
        task
        for task in tasks
        if bool(task["frontier_critical"])
        and int(task["ready_wait_ns"]) > 0
    ]
    critical_intervals = [
        (int(task["ready_at_ns"]), int(task["started_at_ns"]))
        for task in critical
    ]
    noncritical_intervals = [
        (int(task["ready_at_ns"]), int(task["started_at_ns"]))
        for task in tasks
        if not bool(task["frontier_critical"])
        and int(task["ready_wait_ns"]) > 0
    ]
    blocked_overlap = _intersection_union(critical_intervals, noncritical_intervals)
    noncritical_llm = [
        span
        for kind, spans in llm_spans.items()
        if kind != "FRONTIER"
        for span in spans
    ]
    llm_overlap = _intersection_union(critical_intervals, noncritical_llm)
    return {
        "critical_task_count": len(critical),
        "critical_ready_residence_ns": sum(
            int(task["ready_wait_ns"]) for task in critical
        ),
        "critical_ready_overlap_with_noncritical_ready_ns": blocked_overlap,
        "critical_ready_overlap_with_noncritical_llm_ns": llm_overlap,
        "causality": "OVERLAP_ONLY_NOT_CAUSAL_BLOCKING",
        "definition": (
            "critical readiness is the serial frontier Bind dependency interval; "
            "overlap does not prove that another task caused the wait"
        ),
    }


def analyze_ready_task_opportunities(
    queue_path: Path,
    events_path: Path,
    llm_path: Path,
) -> dict[str, object]:
    """Reconstruct ready-task opportunity metrics from sealed local traces."""

    queue_target = Path(queue_path)
    events_target = Path(events_path)
    llm_target = Path(llm_path)
    try:
        queue_rows = _validate_scheduler_rows(_read_queue(queue_target))
        lifecycle_rows = _read_events(events_target)
        llm = _read_llm(llm_target)
    except MemBindV4ReadyTaskProfileError:
        raise
    except Exception as error:
        code = str(error) or "trace_read_failed"
        if code == "request_without_submission":
            code = "llm_request_without_submission"
        raise _fail(code) from None
    lifecycle = _validate_lifecycle(lifecycle_rows)
    _validate_contiguous_sources(lifecycle)
    # The v3.1 phase parser intentionally exposes aggregate spans.  Re-read
    # request rows through a small content-safe projection for task mapping.
    # This keeps raw prompts and responses out of the profile artifact.
    request_rows: list[dict[str, object]] = []
    raw_lines = llm_target.read_text(encoding="utf-8").splitlines()
    for line in raw_lines:
        wrapper = json.loads(line)
        row = wrapper["record"]["row"]
        if row.get("event_type") != "llm_request_submitted":
            continue
        request_rows.append(
            {
                "request_id": row["request_id"],
                "request_kind": row["request_kind"],
                "source_sequence": row["source_sequence"],
                "token_count": row["token_count"],
                "start_ns": None,
                "terminal_ns": None,
            }
        )
    by_request = {str(row["request_id"]): row for row in request_rows}
    for line in raw_lines:
        wrapper = json.loads(line)
        row = wrapper["record"]["row"]
        request_id = row.get("request_id")
        if request_id not in by_request:
            continue
        if row.get("event_type") == "llm_request_start":
            by_request[str(request_id)]["start_ns"] = row["timestamp_ns"]
        elif row.get("event_type") == "llm_request_terminal":
            by_request[str(request_id)]["terminal_ns"] = row["timestamp_ns"]
    request_rows = list(by_request.values())
    for request in request_rows:
        if request.get("start_ns") is None or request.get("terminal_ns") is None:
            raise _fail("llm_request_incomplete")
        if int(request["terminal_ns"]) < int(request["start_ns"]):
            raise _fail("llm_request_time_order_invalid")
        if int(request["source_sequence"]) not in lifecycle:
            raise _fail("llm_source_unknown")
    tasks = _task_records(lifecycle, request_rows)
    timeline_end = max(
        [
            int(row["timestamp_ns"])
            for row in queue_rows
        ]
        + [int(row["timestamp_ns"]) for row in lifecycle_rows]
        + [
            int(request["terminal_ns"])
            for request in request_rows
            if isinstance(request.get("terminal_ns"), int)
        ]
    )
    timeline = _timeline(queue_rows, timeline_end)
    observation_start = min(int(row["start_ns"]) for row in timeline)
    observation_window = max(0, timeline_end - observation_start)
    scheduler_profile = _coarse_profile(
        timeline, observation_window_ns=observation_window
    )
    workflow_intervals, workflow_by_type, heavy_intervals = _workflow_intervals(tasks)
    workflow_profile = _width_summary(
        workflow_intervals,
        observation_window_ns=observation_window,
        same_type=workflow_by_type,
    )
    heavy_durations = _duration_thresholds(heavy_intervals)
    workflow_profile.update(
        {
            "peak_llm_heavy_ready_width": _max_width(heavy_intervals),
            "duration_ns_at_llm_heavy_ready_width_ge": heavy_durations,
            "time_fraction_at_llm_heavy_ready_width_ge": _time_fractions(
                heavy_durations, observation_window
            ),
            "readiness_definition": "dependency readiness reconstructed from lifecycle events",
        }
    )
    spans_by_kind = llm.get("spans_by_kind", {})
    if not isinstance(spans_by_kind, Mapping):
        spans_by_kind = {}
    span_mapping = {
        str(kind): tuple(
            (int(span[0]), int(span[1]))
            for span in spans
            if isinstance(span, Sequence) and len(span) == 2
        )
        for kind, spans in spans_by_kind.items()
        if isinstance(spans, Sequence)
    }
    critical_profile = _critical_profile(tasks, span_mapping)
    result: dict[str, object] = {
        "schema_version": READY_TASK_PROFILE_SCHEMA,
        "status": "DIAGNOSTIC_ONLY",
        "source_file_sha256s": {
            "queue": sha256_file(queue_target),
            "events": sha256_file(events_target),
            "llm": sha256_file(llm_target),
        },
        "observation_start_ns": observation_start,
        "observation_end_ns": timeline_end,
        "observation_window_ns": observation_window,
        "scheduler_selectable": scheduler_profile,
        "workflow_ready": workflow_profile,
        "ready_residence": _task_summary(tasks),
        "tasks": tasks,
        "timeline": timeline,
        "critical_path": critical_profile,
        "fine_grained_operator_profile": {
            "status": "NOT_OBSERVABLE",
            "operator_resolution": "WORKFLOW_STAGE_ONLY",
            "reason": (
                "sealed traces expose request kind and lifecycle stage, but no "
                "EntityExtract/EdgeExtract/NodeResolve member IDs"
            ),
            "unavailable_fields": [
                "operator_type_within_compile_or_bind",
                "ready_task_member_ids",
                "fine_grained_same_type_ready_width",
            ],
        },
        "backend_shape": {
            "request_kind_spans_observed": sorted(span_mapping),
            "compile_request_count": len(
                [row for row in request_rows if row["request_kind"] == "COMPILE"]
            ),
            "frontier_request_count": len(
                [row for row in request_rows if row["request_kind"] == "FRONTIER"]
            ),
            "interpretation": "request-kind level only; no vLLM batch internals inferred",
        },
        "decision": {
            "scheduler_choice_observed": scheduler_profile["peak_ready_width"] >= 2,
            "same_type_choice_observed": scheduler_profile["peak_same_type_ready_width"] >= 2,
            "workflow_choice_observed": workflow_profile["peak_ready_width"] >= 2,
            "llm_heavy_choice_observed": workflow_profile["peak_llm_heavy_ready_width"] >= 2,
            "critical_path_overlap_observed": critical_profile[
                "critical_ready_overlap_with_noncritical_ready_ns"
            ]
            > 0,
            "backend_speedup_proven": False,
            "claim_boundary": "OFFLINE_OPPORTUNITY_PROFILE_ONLY",
        },
    }
    result["payload_sha256"] = payload_sha256(result)
    return result


def render_ready_task_profile_report(result: Mapping[str, object]) -> str:
    """Render a concise content-safe report."""

    scheduler = result.get("scheduler_selectable", {})
    workflow = result.get("workflow_ready", {})
    residence = result.get("ready_residence", {})
    critical = result.get("critical_path", {})
    fine = result.get("fine_grained_operator_profile", {})
    decision = result.get("decision", {})
    lines = [
        "# MemBind v4 Ready-Task Opportunity Profile",
        "",
        "> Diagnostic-only offline replay; no scheduler, arrival trace, or backend was changed.",
        "",
        "## Observable Width",
        "",
        f"- scheduler peak ready width: `{scheduler.get('peak_ready_width')}`",
        f"- scheduler peak same-type width: `{scheduler.get('peak_same_type_ready_width')}`",
        f"- workflow peak dependency-ready width: `{workflow.get('peak_ready_width')}`",
        f"- workflow peak LLM-heavy ready width: `{workflow.get('peak_llm_heavy_ready_width')}`",
        f"- ready width >= 2 duration: `{scheduler.get('duration_ns_at_ready_width_ge', {}).get('2')}` ns",
        f"- P(scheduler ready width >= 2): `{scheduler.get('time_fraction_at_ready_width_ge', {}).get('2')}`",
        f"- P(scheduler same-type width >= 2): `{scheduler.get('time_fraction_at_same_type_width_ge', {}).get('2')}`",
        f"- P(workflow ready width >= 2): `{workflow.get('time_fraction_at_ready_width_ge', {}).get('2')}`",
        f"- P(LLM-heavy ready width >= 2): `{workflow.get('time_fraction_at_llm_heavy_ready_width_ge', {}).get('2')}`",
        "",
        "## Waiting and Critical Path",
        "",
        f"- ready-task count: `{residence.get('task_count')}`",
        f"- mean dependency-ready wait: `{residence.get('mean_ns')}` ns",
        f"- critical ready residence: `{critical.get('critical_ready_residence_ns')}` ns",
        f"- critical/noncritical ready overlap: `{critical.get('critical_ready_overlap_with_noncritical_ready_ns')}` ns",
        f"- critical/noncritical LLM overlap: `{critical.get('critical_ready_overlap_with_noncritical_llm_ns')}` ns",
        f"- causality boundary: `{critical.get('causality')}`",
        "",
        "## Observability Boundary",
        "",
        f"- fine-grained operator profile: `{fine.get('status')}`",
        f"- scheduler choice observed: `{decision.get('scheduler_choice_observed')}`",
        f"- same-type choice observed: `{decision.get('same_type_choice_observed')}`",
        f"- backend speedup proven: `{decision.get('backend_speedup_proven')}`",
        "- offline direction: `"
        + (
            "NO_SCHEDULING_OPPORTUNITY_OBSERVED"
            if not decision.get("scheduler_choice_observed")
            and not decision.get("workflow_choice_observed")
            else "OPPORTUNITY_OBSERVED_REQUIRES_CONTROLLED_SCHEDULER_STUDY"
        )
        + "`",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "READY_TASK_PROFILE_SCHEMA",
    "MemBindV4ReadyTaskProfileError",
    "analyze_ready_task_opportunities",
    "render_ready_task_profile_report",
]
