"""Offline cross-layer parallelism diagnosis for the stopped MemBind v4 lane.

The diagnostic distinguishes work that is merely outstanding, work waiting in
the coarse workflow ready pool, active workflow stages, and requests visible
to the LLM admission boundary.  Client request spans are never relabelled as
vLLM batch membership or GPU execution.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.membind_v31.phase_critical_path import _read_events, _read_queue
from paper_eval.membind_v4.ready_task_profile import (
    analyze_ready_task_opportunities,
)


PARALLELISM_FUNNEL_SCHEMA = (
    "membind.paper-eval-v4.parallelism-funnel-diagnostic.v1"
)
_THRESHOLDS = (1, 2, 4, 8, 16)
_REQUEST_KINDS = ("COMPILE", "FRONTIER")
_ADMISSION_COUNT_FIELDS = (
    "active_count",
    "waiting_count",
    "active_compile_count",
    "active_frontier_count",
    "waiting_compile_count",
    "waiting_frontier_count",
)
_ALIGNED_LIFECYCLE_EVENTS = (
    "ARRIVAL",
    "ENQUEUED",
    "SERVICE_STARTED",
    "PUBLICATION_DURABLE",
)


class MemBindV4ParallelismFunnelError(ValueError):
    """A sealed trace cannot support a trustworthy parallelism funnel."""


def _fail(code: str) -> MemBindV4ParallelismFunnelError:
    return MemBindV4ParallelismFunnelError(code)


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


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


def _span_profile(
    spans: Sequence[tuple[int, int]],
    *,
    observation_start_ns: int,
    observation_end_ns: int,
) -> dict[str, object]:
    if observation_end_ns < observation_start_ns:
        raise _fail("observation_window_invalid")
    selected: list[tuple[int, int]] = []
    for start, end in spans:
        if start < observation_start_ns or end > observation_end_ns or end < start:
            raise _fail("span_outside_observation_window")
        if end > start:
            selected.append((start, end))
    boundaries = sorted(
        {
            observation_start_ns,
            observation_end_ns,
            *(boundary for span in selected for boundary in span),
        }
    )
    intervals = [
        (
            start,
            end,
            sum(left <= start < right for left, right in selected),
        )
        for start, end in zip(boundaries, boundaries[1:])
        if end > start
    ]
    window = observation_end_ns - observation_start_ns
    durations = {
        str(threshold): sum(
            end - start
            for start, end, width in intervals
            if width >= threshold
        )
        for threshold in _THRESHOLDS
    }
    area = sum((end - start) * width for start, end, width in intervals)
    return {
        "span_count": len(selected),
        "peak_width": max((width for _start, _end, width in intervals), default=0),
        "mean_width": area / window if window > 0 else None,
        "duration_ns_at_width_ge": durations,
        "time_fraction_at_width_ge": {
            threshold: duration / window if window > 0 else None
            for threshold, duration in durations.items()
        },
        "observation_start_ns": observation_start_ns,
        "observation_end_ns": observation_end_ns,
        "observation_window_ns": window,
        "interval_semantics": "half-open [start_ns, end_ns)",
    }


def _wait_summary(
    requests: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    waits = [int(request["start_ns"]) - int(request["submitted_ns"]) for request in requests]
    by_kind: dict[str, object] = {}
    for kind in _REQUEST_KINDS:
        selected = [
            int(request["start_ns"]) - int(request["submitted_ns"])
            for request in requests
            if request["request_kind"] == kind
        ]
        by_kind[kind] = {
            "request_count": len(selected),
            "mean_ns": sum(selected) / len(selected) if selected else None,
            "p50_ns": _percentile(selected, 0.50),
            "p95_ns": _percentile(selected, 0.95),
            "max_ns": max(selected, default=None),
        }
    return {
        "request_count": len(waits),
        "mean_ns": sum(waits) / len(waits) if waits else None,
        "p50_ns": _percentile(waits, 0.50),
        "p95_ns": _percentile(waits, 0.95),
        "max_ns": max(waits, default=None),
        "by_request_kind": by_kind,
        "definition": "llm_request_submitted to llm_request_start",
    }


def _request_rows(path: Path) -> list[dict[str, object]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("llm_trace_unreadable") from None
    requests: dict[str, dict[str, object]] = {}
    for line in lines:
        try:
            wrapper = json.loads(line)
            row = wrapper["record"]["row"]
        except (KeyError, TypeError, json.JSONDecodeError):
            raise _fail("llm_trace_projection_invalid") from None
        event_type = row.get("event_type")
        if event_type == "llm_request_submitted":
            request_id = row.get("request_id")
            if not isinstance(request_id, str) or request_id in requests:
                raise _fail("llm_request_identity_invalid")
            kind = row.get("request_kind")
            if kind not in _REQUEST_KINDS:
                raise _fail("llm_request_kind_invalid")
            requests[request_id] = {
                "request_id": request_id,
                "request_kind": kind,
                "source_sequence": _nonnegative_int(
                    row.get("source_sequence"), "llm_source_sequence_invalid"
                ),
                "submitted_ns": _nonnegative_int(
                    row.get("timestamp_ns"), "llm_timestamp_invalid"
                ),
                "start_ns": None,
                "terminal_ns": None,
            }
        elif event_type in {"llm_request_start", "llm_request_terminal"}:
            request_id = row.get("request_id")
            request = requests.get(request_id) if isinstance(request_id, str) else None
            if request is None:
                raise _fail("llm_request_without_submission")
            field = "start_ns" if event_type == "llm_request_start" else "terminal_ns"
            if request[field] is not None:
                raise _fail("llm_request_event_duplicate")
            request[field] = _nonnegative_int(
                row.get("timestamp_ns"), "llm_timestamp_invalid"
            )
        elif event_type not in {
            "frontier_bind_region_start",
            "frontier_bind_region_end",
        }:
            raise _fail("llm_event_type_invalid")
    result = list(requests.values())
    for request in result:
        submitted = int(request["submitted_ns"])
        start = request.get("start_ns")
        terminal = request.get("terminal_ns")
        if not isinstance(start, int) or not isinstance(terminal, int):
            raise _fail("llm_request_incomplete")
        if start < submitted or terminal < start:
            raise _fail("llm_request_time_order_invalid")
    return sorted(
        result,
        key=lambda request: (
            int(request["submitted_ns"]),
            str(request["request_id"]),
        ),
    )


def _admission_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    selected = [
        dict(row) for row in rows if row.get("event_type") == "admission_snapshot"
    ]
    if not selected:
        raise _fail("admission_snapshot_empty")
    previous_timestamp = -1
    configured_limit: int | None = None
    for row in selected:
        timestamp = _nonnegative_int(
            row.get("timestamp_ns"), "admission_timestamp_invalid"
        )
        if timestamp < previous_timestamp:
            raise _fail("admission_timestamp_order_invalid")
        previous_timestamp = timestamp
        counts = {
            field: _nonnegative_int(
                row.get(field), f"admission_{field}_invalid"
            )
            for field in _ADMISSION_COUNT_FIELDS
        }
        if counts["active_count"] != (
            counts["active_compile_count"] + counts["active_frontier_count"]
        ):
            raise _fail("admission_active_count_inconsistent")
        if counts["waiting_count"] != (
            counts["waiting_compile_count"] + counts["waiting_frontier_count"]
        ):
            raise _fail("admission_waiting_count_inconsistent")
        limit = _nonnegative_int(
            row.get("configured_limit"), "admission_limit_invalid"
        )
        if limit != 2 or counts["active_count"] > limit:
            raise _fail("admission_limit_invalid")
        if configured_limit is not None and configured_limit != limit:
            raise _fail("admission_limit_drift")
        configured_limit = limit
    return selected


def _snapshot_profile(
    rows: Sequence[Mapping[str, object]], observation_end_ns: int
) -> dict[str, object]:
    first = int(rows[0]["timestamp_ns"])
    if observation_end_ns < first:
        raise _fail("admission_observation_window_invalid")
    durations: dict[str, dict[str, int]] = {}
    for field in ("active_count", "waiting_count"):
        durations[field] = {
            str(threshold): sum(
                (
                    int(rows[index + 1]["timestamp_ns"])
                    if index + 1 < len(rows)
                    else observation_end_ns
                )
                - int(row["timestamp_ns"])
                for index, row in enumerate(rows)
                if int(row[field]) >= threshold
            )
            for threshold in _THRESHOLDS
        }
    return {
        "snapshot_count": len(rows),
        "configured_limit": 2,
        "peak_active_count": max(int(row["active_count"]) for row in rows),
        "peak_waiting_count": max(int(row["waiting_count"]) for row in rows),
        "peak_pending_count": max(
            int(row["active_count"]) + int(row["waiting_count"])
            for row in rows
        ),
        "peak_active_compile_count": max(
            int(row["active_compile_count"]) for row in rows
        ),
        "peak_active_frontier_count": max(
            int(row["active_frontier_count"]) for row in rows
        ),
        "peak_waiting_compile_count": max(
            int(row["waiting_compile_count"]) for row in rows
        ),
        "peak_waiting_frontier_count": max(
            int(row["waiting_frontier_count"]) for row in rows
        ),
        "duration_ns_at_active_count_ge": durations["active_count"],
        "duration_ns_at_waiting_count_ge": durations["waiting_count"],
        "observation_start_ns": first,
        "observation_end_ns": observation_end_ns,
        "observation_window_ns": observation_end_ns - first,
        "interpretation": "sealed admission-controller snapshots",
    }


def _with_interpretation(
    profile: Mapping[str, object], interpretation: str
) -> dict[str, object]:
    return {**profile, "interpretation": interpretation}


def _read_aligned_events(path: Path) -> dict[int, dict[str, object]]:
    """Read the content-safe APC-aligned lifecycle projection."""

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("aligned_events_unreadable") from None
    by_source: dict[int, dict[str, object]] = {}
    previous_sequence = -1
    for line in lines:
        try:
            wrapper = json.loads(line)
            event = wrapper["event"]
        except (KeyError, TypeError, json.JSONDecodeError):
            raise _fail("aligned_event_projection_invalid") from None
        if not isinstance(event, Mapping):
            raise _fail("aligned_event_projection_invalid")
        if wrapper.get("event_sha256") != payload_sha256(event):
            raise _fail("aligned_event_hash_mismatch")
        if not str(event.get("schema_version", "")).endswith(
            "membind-v1-aligned-block-event.v1"
        ):
            raise _fail("aligned_event_schema_invalid")
        sequence = _nonnegative_int(
            event.get("event_sequence"), "aligned_event_sequence_invalid"
        )
        if sequence <= previous_sequence:
            raise _fail("aligned_event_sequence_invalid")
        previous_sequence = sequence
        source = _nonnegative_int(
            event.get("source_sequence"), "aligned_source_sequence_invalid"
        )
        event_type = event.get("event_type")
        if event_type not in _ALIGNED_LIFECYCLE_EVENTS:
            raise _fail("aligned_event_type_invalid")
        timestamp = _nonnegative_int(
            event.get("timestamp_ns"), "aligned_timestamp_invalid"
        )
        source_hash = event.get("source_sha256")
        if not isinstance(source_hash, str) or len(source_hash) != 64:
            raise _fail("aligned_source_hash_invalid")
        row = by_source.setdefault(
            source,
            {"source_sha256": source_hash, "timestamps": {}},
        )
        if row["source_sha256"] != source_hash:
            raise _fail("aligned_source_hash_drift")
        timestamps = row["timestamps"]
        assert isinstance(timestamps, dict)
        if event_type in timestamps:
            raise _fail("aligned_source_event_duplicate")
        timestamps[event_type] = timestamp
    if not by_source:
        raise _fail("aligned_events_empty")
    sources = sorted(by_source)
    if sources != list(range(len(sources))):
        raise _fail("aligned_source_sequence_coverage_invalid")
    for row in by_source.values():
        timestamps = row["timestamps"]
        assert isinstance(timestamps, Mapping)
        if set(timestamps) != set(_ALIGNED_LIFECYCLE_EVENTS):
            raise _fail("aligned_source_lifecycle_invalid")
        ordered = [int(timestamps[key]) for key in _ALIGNED_LIFECYCLE_EVENTS]
        if any(right < left for left, right in zip(ordered, ordered[1:])):
            raise _fail("aligned_source_lifecycle_order_invalid")
    return by_source


def _read_registered_result(path: Path) -> dict[str, object]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("aligned_result_unreadable") from None
    if not isinstance(document, Mapping):
        raise _fail("aligned_result_invalid")
    digest = document.get("payload_sha256")
    body = {key: value for key, value in document.items() if key != "payload_sha256"}
    if not isinstance(digest, str) or digest != payload_sha256(body):
        raise _fail("aligned_result_hash_mismatch")
    performance = document.get("performance")
    if not isinstance(performance, Mapping):
        raise _fail("aligned_result_performance_invalid")
    selected = {
        key: performance.get(key)
        for key in (
            "episode_count",
            "max_outstanding_backlog",
            "max_waiting_queue_depth",
            "makespan_ns",
            "goodput_episodes_per_second",
            "p95_freshness_ns",
        )
        if key in performance
    }
    return {
        "method": document.get("method"),
        "history_id": document.get("history_id"),
        "source_count": document.get("episode_count"),
        "status": document.get("status"),
        "result_payload_sha256": digest,
        "performance": selected,
    }


def analyze_aligned_baseline_prefix(
    events_path: Path,
    *,
    prefix_count: int,
    registered_result_path: Path | None = None,
) -> dict[str, object]:
    """Recompute prefix-safe baseline queue metrics from sealed events.

    The registered baseline result remains full-run evidence.  Prefix values
    are recomputed only from the first ``prefix_count`` sources and are never
    presented as a replacement result artifact.
    """

    if isinstance(prefix_count, bool) or not isinstance(prefix_count, int):
        raise _fail("aligned_prefix_count_invalid")
    by_source = _read_aligned_events(Path(events_path))
    if prefix_count <= 0 or prefix_count > len(by_source):
        raise _fail("aligned_prefix_count_invalid")
    prefix = {source: by_source[source] for source in range(prefix_count)}
    timestamps = {
        source: row["timestamps"] for source, row in prefix.items()
    }
    assert all(isinstance(value, Mapping) for value in timestamps.values())
    observation_start = min(int(value["ARRIVAL"]) for value in timestamps.values())
    observation_end = max(
        int(value["PUBLICATION_DURABLE"]) for value in timestamps.values()
    )
    outstanding = _with_interpretation(
        _span_profile(
            [
                (int(value["ARRIVAL"]), int(value["PUBLICATION_DURABLE"]))
                for value in timestamps.values()
            ],
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        ),
        "ARRIVAL to PUBLICATION_DURABLE",
    )
    service_wait = _with_interpretation(
        _span_profile(
            [
                (int(value["ARRIVAL"]), int(value["SERVICE_STARTED"]))
                for value in timestamps.values()
            ],
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        ),
        "ARRIVAL to SERVICE_STARTED; same queue-delay basis as registered result",
    )
    enqueue_wait = _with_interpretation(
        _span_profile(
            [
                (int(value["ARRIVAL"]), int(value["ENQUEUED"]))
                for value in timestamps.values()
            ],
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        ),
        "ARRIVAL to ENQUEUED; enqueue-stage wait only",
    )
    queue_delays = [
        int(value["SERVICE_STARTED"]) - int(value["ARRIVAL"])
        for value in timestamps.values()
    ]
    source_rows = [
        {
            "source_sequence": source,
            "source_sha256": prefix[source]["source_sha256"],
            "arrival_offset_ns": int(timestamps[source]["ARRIVAL"])
            - observation_start,
            "queue_delay_ns": int(timestamps[source]["SERVICE_STARTED"])
            - int(timestamps[source]["ARRIVAL"]),
        }
        for source in range(prefix_count)
    ]
    result: dict[str, object] = {
        "events_file_sha256": sha256_file(Path(events_path)),
        "full_source_count": len(by_source),
        "prefix_source_count": prefix_count,
        "prefix_source_sequences": list(range(prefix_count)),
        "prefix_source_sha256s": [
            str(prefix[source]["source_sha256"]) for source in range(prefix_count)
        ],
        "observation_start_ns": observation_start,
        "observation_end_ns": observation_end,
        "observation_window_ns": observation_end - observation_start,
        "source_outstanding": outstanding,
        "arrival_to_service_start_waiting": service_wait,
        "arrival_to_enqueue_waiting": enqueue_wait,
        "mean_queue_delay_ns": sum(queue_delays) / len(queue_delays),
        "max_queue_delay_ns": max(queue_delays),
        "per_source": source_rows,
        "prefix_censored": len(by_source) > prefix_count,
    }
    if registered_result_path is not None:
        result["registered_full_run"] = _read_registered_result(
            Path(registered_result_path)
        )
    result["payload_sha256"] = payload_sha256(result)
    return result


def analyze_parallelism_funnel(
    queue_path: Path,
    events_path: Path,
    llm_path: Path,
) -> dict[str, object]:
    """Reconstruct source, workflow, and client-visible LLM concurrency."""

    queue_target = Path(queue_path)
    events_target = Path(events_path)
    llm_target = Path(llm_path)
    try:
        ready = analyze_ready_task_opportunities(
            queue_target, events_target, llm_target
        )
        queue_rows = _read_queue(queue_target)
        event_rows = _read_events(events_target)
    except MemBindV4ParallelismFunnelError:
        raise
    except Exception as error:
        raise _fail(str(error) or "sealed_trace_invalid") from None
    requests = _request_rows(llm_target)
    admissions = _admission_rows(queue_rows)

    lifecycle: dict[int, dict[str, int]] = defaultdict(dict)
    for row in event_rows:
        lifecycle[int(row["source_sequence"])][str(row["event_type"])] = int(
            row["timestamp_ns"]
        )
    if not lifecycle:
        raise _fail("lifecycle_empty")
    observation_start = min(values["ARRIVAL"] for values in lifecycle.values())
    observation_end = max(
        values["PUBLICATION_DURABLE"] for values in lifecycle.values()
    )
    if any(
        int(request["source_sequence"]) not in lifecycle for request in requests
    ):
        raise _fail("llm_source_unknown")

    source_spans = [
        (values["ARRIVAL"], values["PUBLICATION_DURABLE"])
        for values in lifecycle.values()
    ]
    compile_dispatch_wait = [
        (values["ARRIVAL"], values["COMPILE_STARTED"])
        for values in lifecycle.values()
    ]
    tasks = ready["tasks"]
    assert isinstance(tasks, Sequence)
    active_spans = [
        (int(task["started_at_ns"]), int(task["ended_at_ns"]))
        for task in tasks
        if isinstance(task, Mapping)
    ]
    active_by_kind = {
        kind: _span_profile(
            [
                (int(task["started_at_ns"]), int(task["ended_at_ns"]))
                for task in tasks
                if isinstance(task, Mapping) and task["operator_type"] == kind
            ],
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        )
        for kind in ("COMPILE", "BIND")
    }

    pending_spans = [
        (int(request["submitted_ns"]), int(request["terminal_ns"]))
        for request in requests
    ]
    waiting_spans = [
        (int(request["submitted_ns"]), int(request["start_ns"]))
        for request in requests
    ]
    running_spans = [
        (int(request["start_ns"]), int(request["terminal_ns"]))
        for request in requests
    ]
    if pending_spans and (
        min(start for start, _end in pending_spans) < observation_start
        or max(end for _start, end in pending_spans) > observation_end
    ):
        raise _fail("llm_span_outside_lifecycle")

    source_outstanding = _with_interpretation(
        _span_profile(
            source_spans,
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        ),
        "ARRIVAL to PUBLICATION_DURABLE",
    )
    workflow_active = _with_interpretation(
        _span_profile(
            active_spans,
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        ),
        "dispatched workflow stages; active work is not a scheduler choice",
    )
    workflow_active["by_stage"] = active_by_kind
    ready_workflow = ready["workflow_ready"]
    assert isinstance(ready_workflow, Mapping)
    ready_scheduler = ready["scheduler_selectable"]
    assert isinstance(ready_scheduler, Mapping)
    workflow_ready_waiting = {
        "peak_width": ready_workflow["peak_ready_width"],
        "peak_same_type_width": ready_workflow["peak_same_type_ready_width"],
        "duration_ns_at_width_ge": ready_workflow[
            "duration_ns_at_ready_width_ge"
        ],
        "time_fraction_at_width_ge": ready_workflow[
            "time_fraction_at_ready_width_ge"
        ],
        "interpretation": "dependency-ready to dispatch; excludes active stages",
    }

    request_kind_counts: dict[int, dict[str, int]] = defaultdict(
        lambda: {kind: 0 for kind in _REQUEST_KINDS}
    )
    for request in requests:
        request_kind_counts[int(request["source_sequence"])][
            str(request["request_kind"])
        ] += 1
    per_source_requests = [
        {
            "source_sequence": source,
            "compile_request_count": counts["COMPILE"],
            "frontier_request_count": counts["FRONTIER"],
            "total_request_count": sum(counts.values()),
        }
        for source, counts in sorted(request_kind_counts.items())
    ]

    llm_pending = _with_interpretation(
        _span_profile(
            pending_spans,
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        ),
        "submitted but not terminal at the client admission boundary",
    )
    llm_waiting = _with_interpretation(
        _span_profile(
            waiting_spans,
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        ),
        "submitted but not started at the client admission boundary",
    )
    llm_running = _with_interpretation(
        _span_profile(
            running_spans,
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        ),
        "client-observed request span, not GPU execution",
    )
    snapshot_profile = _snapshot_profile(admissions, observation_end)

    stage_choice = bool(
        int(ready_scheduler["peak_ready_width"]) >= 2
        or int(ready_workflow["peak_ready_width"]) >= 2
    )
    source_backlog = int(source_outstanding["peak_width"]) >= 2
    llm_pressure = bool(
        int(llm_waiting["peak_width"]) >= 2
        or int(snapshot_profile["peak_waiting_count"]) >= 2
    )
    if not stage_choice and llm_pressure:
        classification = "COARSE_READY_POOL_NO_CHOICE_WITH_INTERNAL_LLM_FANOUT"
        terminal = "NO_STAGE_SCHEDULER_CHOICE_LLM_ADMISSION_BACKLOG_OBSERVED"
    elif not source_backlog and not llm_pressure:
        classification = "PREFIX_SOURCE_CONCURRENCY_NOT_OBSERVED"
        terminal = "NO_PARALLELISM_OPPORTUNITY_OBSERVED"
    else:
        classification = "MIXED_LAYER_CONCURRENCY_REQUIRES_BOUNDARY_SPECIFIC_CLAIMS"
        terminal = "NO_STAGE_SCHEDULER_AUTHORIZATION"

    result: dict[str, object] = {
        "schema_version": PARALLELISM_FUNNEL_SCHEMA,
        "status": "DIAGNOSTIC_ONLY",
        "source_file_sha256s": {
            "queue": sha256_file(queue_target),
            "events": sha256_file(events_target),
            "llm": sha256_file(llm_target),
        },
        "ready_profile_payload_sha256": ready["payload_sha256"],
        "observation_start_ns": observation_start,
        "observation_end_ns": observation_end,
        "observation_window_ns": observation_end - observation_start,
        "source_outstanding": source_outstanding,
        "source_waiting_for_compile_dispatch": _with_interpretation(
            _span_profile(
                compile_dispatch_wait,
                observation_start_ns=observation_start,
                observation_end_ns=observation_end,
            ),
            "ARRIVAL to COMPILE_STARTED",
        ),
        "workflow_ready_waiting": workflow_ready_waiting,
        "scheduler_selectable_ready": {
            "peak_width": ready_scheduler["peak_ready_width"],
            "peak_same_type_width": ready_scheduler[
                "peak_same_type_ready_width"
            ],
            "duration_ns_at_width_ge": ready_scheduler[
                "duration_ns_at_ready_width_ge"
            ],
        },
        "workflow_active": workflow_active,
        "llm_request_pending": llm_pending,
        "llm_admission_waiting": llm_waiting,
        "llm_client_running": llm_running,
        "llm_admission_wait_summary": _wait_summary(requests),
        "admission_snapshots": snapshot_profile,
        "llm_request_inventory": {
            "request_count": len(requests),
            "compile_request_count": sum(
                request["request_kind"] == "COMPILE" for request in requests
            ),
            "frontier_request_count": sum(
                request["request_kind"] == "FRONTIER" for request in requests
            ),
            "per_source": per_source_requests,
        },
        "backend_internal": {
            "status": "NOT_OBSERVABLE",
            "unavailable_fields": [
                "vllm_batch_membership",
                "gpu_execution_width",
                "fine_grained_operator_identity",
            ],
        },
        "decision": {
            "backend_bottleneck_proven": False,
            "coarse_stage_scheduler_authorized": stage_choice,
            "end_to_end_parallelism_collapse_proven": False,
            "llm_admission_pressure_observed": llm_pressure,
            "root_cause_classification": classification,
            "source_backlog_observed": source_backlog,
            "terminal": terminal,
            "workload_too_sparse_proven": False,
        },
        "claim_boundary": {
            "active_is_not_ready_waiting": True,
            "client_running_is_not_gpu_execution": True,
            "prefix_censoring_requires_separate_audit": True,
        },
    }
    result["payload_sha256"] = payload_sha256(result)
    return result


def render_parallelism_funnel_report(result: Mapping[str, object]) -> str:
    """Render the content-safe funnel and its claim boundaries."""

    source = result.get("source_outstanding", {})
    ready = result.get("workflow_ready_waiting", {})
    active = result.get("workflow_active", {})
    pending = result.get("llm_request_pending", {})
    waiting = result.get("llm_admission_waiting", {})
    running = result.get("llm_client_running", {})
    snapshots = result.get("admission_snapshots", {})
    decision = result.get("decision", {})
    lines = [
        "# MemBind v4 Cross-Layer Parallelism Funnel",
        "",
        "> Diagnostic-only replay. No scheduler or live mechanism was added.",
        "",
        "## Width Funnel",
        "",
        "| Layer | Peak width | Interpretation |",
        "|---|---:|---|",
        f"| Source outstanding | {source.get('peak_width')} | ARRIVAL to publication |",
        f"| Workflow ready-waiting | {ready.get('peak_width')} | Legal but not dispatched |",
        f"| Workflow active | {active.get('peak_width')} | Already dispatched |",
        f"| LLM request pending | {pending.get('peak_width')} | Submitted to terminal |",
        f"| LLM admission waiting | {waiting.get('peak_width')} | Submitted to start |",
        f"| LLM client running | {running.get('peak_width')} | Not GPU execution |",
        f"| Admission snapshot waiting | {snapshots.get('peak_waiting_count')} | Controller state |",
        f"| Admission snapshot active | {snapshots.get('peak_active_count')} | Controller state |",
        "| vLLM/GPU internal | NOT_OBSERVABLE | No batch membership trace |",
        "",
        "## Decision",
        "",
        f"- root-cause classification: `{decision.get('root_cause_classification')}`",
        f"- terminal: `{decision.get('terminal')}`",
        f"- coarse scheduler authorized: `{decision.get('coarse_stage_scheduler_authorized')}`",
        f"- backend bottleneck proven: `{decision.get('backend_bottleneck_proven')}`",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "PARALLELISM_FUNNEL_SCHEMA",
    "MemBindV4ParallelismFunnelError",
    "analyze_aligned_baseline_prefix",
    "analyze_parallelism_funnel",
    "render_parallelism_funnel_report",
]
