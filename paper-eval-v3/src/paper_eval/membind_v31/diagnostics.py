"""Read-only diagnostics for explaining MemBind v3.1 scheduler behavior.

This module deliberately consumes only the public ``llm.jsonl`` telemetry
stream.  It never contacts a service, mutates a namespace, or produces a
formal result.  In particular, transport-level waiting is kept separate from
the scheduler's legal-ready set: the current trace schema does not expose
that set, so the analyzer reports the evidence boundary instead of guessing.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from paper_eval.artifacts import payload_sha256, sha256_file


TRACE_SCHEMA = "membind.paper-eval-v3.membind-v31-llm.v1"
DIAGNOSTIC_SCHEMA = "membind.paper-eval-v3.membind-v31-scheduler-diagnostic.v1"
_KINDS = ("COMPILE", "FRONTIER")
_REQUEST_EVENTS = {
    "llm_request_submitted",
    "llm_request_start",
    "llm_request_terminal",
}
_REGION_EVENTS = {"frontier_bind_region_start", "frontier_bind_region_end"}


class MemBindV31DiagnosticError(ValueError):
    """A malformed or internally inconsistent diagnostic trace."""


def _fail(code: str) -> MemBindV31DiagnosticError:
    return MemBindV31DiagnosticError(code)


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
    """Return the wall-clock union length of half-open intervals."""

    ordered = sorted((start, end) for start, end in intervals if end > start)
    if not ordered:
        return 0
    total = 0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    return total + current_end - current_start


def _read_rows(path: Path) -> list[dict[str, object]]:
    target = Path(path)
    if not target.is_file():
        raise _fail("trace_missing")
    rows: list[dict[str, object]] = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("trace_unreadable") from None
    for expected_sequence, line in enumerate(lines):
        if not line:
            raise _fail("trace_blank_line")
        try:
            wrapper = json.loads(line)
        except json.JSONDecodeError:
            raise _fail("trace_json_invalid") from None
        if not isinstance(wrapper, Mapping) or set(wrapper) != {"record", "record_sha256"}:
            raise _fail("trace_envelope_invalid")
        record = wrapper.get("record")
        if not isinstance(record, Mapping) or record.get("schema_version") != TRACE_SCHEMA:
            raise _fail("trace_schema_invalid")
        if wrapper.get("record_sha256") != payload_sha256(record):
            raise _fail("record_hash_mismatch")
        raw_row = record.get("row")
        if not isinstance(raw_row, Mapping):
            raise _fail("trace_row_invalid")
        row = dict(raw_row)
        if row.get("event_sequence") != expected_sequence:
            raise _fail("event_sequence_invalid")
        event_type = row.get("event_type")
        if event_type not in _REQUEST_EVENTS | _REGION_EVENTS:
            raise _fail("event_type_invalid")
        _nonnegative_int(row.get("timestamp_ns"), "timestamp_invalid")
        rows.append(row)
    return rows


def _request_identity(row: Mapping[str, object]) -> tuple[str, str, str, int]:
    request_id = row.get("request_id")
    kind = row.get("request_kind")
    stream_id = row.get("stream_id")
    source_sequence = row.get("source_sequence")
    if not isinstance(request_id, str) or not request_id:
        raise _fail("request_id_invalid")
    if kind not in _KINDS:
        raise _fail("request_kind_invalid")
    if not isinstance(stream_id, str) or not stream_id:
        raise _fail("stream_id_invalid")
    return request_id, str(kind), stream_id, _nonnegative_int(source_sequence, "source_sequence_invalid")


def _parse_requests(rows: Sequence[Mapping[str, object]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[tuple[int, int]]]]:
    requests: dict[str, dict[str, Any]] = {}
    regions: dict[str, list[tuple[int, int]]] = defaultdict(list)
    open_regions: dict[str, int] = {}
    for row in rows:
        event_type = row["event_type"]
        timestamp = _nonnegative_int(row["timestamp_ns"], "timestamp_invalid")
        if event_type in _REGION_EVENTS:
            stream = row.get("stream_id")
            sequence = _nonnegative_int(row.get("source_sequence"), "source_sequence_invalid")
            if not isinstance(stream, str) or not stream:
                raise _fail("stream_id_invalid")
            key = f"{stream}:{sequence}"
            if event_type == "frontier_bind_region_start":
                if key in open_regions:
                    raise _fail("region_duplicate_start")
                open_regions[key] = timestamp
            else:
                start = open_regions.pop(key, None)
                if start is None or timestamp < start:
                    raise _fail("region_end_invalid")
                regions[stream].append((start, timestamp))
            continue

        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise _fail("request_id_invalid")
        if event_type == "llm_request_submitted":
            if request_id in requests:
                raise _fail("request_duplicate")
            identity = _request_identity(row)
            token_count = _nonnegative_int(row.get("token_count"), "token_count_invalid")
            requests[request_id] = {
                "request_id": identity[0],
                "request_kind": identity[1],
                "stream_id": identity[2],
                "source_sequence": identity[3],
                "submitted_ns": timestamp,
                "token_count": token_count,
                "start_ns": None,
                "terminal_ns": None,
            }
            continue

        request = requests.get(request_id)
        if request is None:
            raise _fail("request_without_submission")
        if "request_kind" in row or "stream_id" in row or "source_sequence" in row:
            for field in ("request_kind", "stream_id", "source_sequence"):
                if field in row and row[field] != request[field]:
                    raise _fail("request_identity_mismatch")
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
    return requests, regions


def _active_occupancy(intervals: Sequence[tuple[int, int]], capacity: int) -> dict[str, object]:
    if not intervals:
        return {
            "service_window_ns": 0,
            "active_time_by_count_ns": {},
            "active_fraction_by_count": {},
            "observed_max_active": 0,
            "under_capacity_ns": 0,
        }
    points = sorted({point for start, end in intervals for point in (start, end)})
    durations: dict[int, int] = defaultdict(int)
    for left, right in zip(points, points[1:]):
        if right <= left:
            continue
        active = sum(start <= left < end for start, end in intervals)
        if active > 0:
            durations[active] += right - left
    window = points[-1] - points[0]
    return {
        "service_window_ns": window,
        "active_time_by_count_ns": {str(key): durations[key] for key in sorted(durations)},
        "active_fraction_by_count": {
            str(key): _finite_ratio(value, window) for key, value in sorted(durations.items())
        },
        "observed_max_active": max(durations, default=0),
        "under_capacity_ns": sum(value for key, value in durations.items() if key < capacity),
    }


def _transport_wait(requests: Mapping[str, Mapping[str, Any]], intervals: Sequence[tuple[int, int]], capacity: int) -> dict[str, int]:
    complete = [
        request
        for request in requests.values()
        if request["start_ns"] is not None and request["terminal_ns"] is not None
    ]
    waits = [
        (int(request["submitted_ns"]), int(request["start_ns"]))
        for request in complete
        if int(request["start_ns"]) > int(request["submitted_ns"])
    ]
    if not waits:
        return {"submitted_wait_sum_ns": 0, "under_capacity_ns": 0}
    points = sorted(
        {
            point
            for start, end in [*waits, *intervals]
            for point in (start, end)
        }
    )
    pending_deltas: dict[int, int] = defaultdict(int)
    active_deltas: dict[int, int] = defaultdict(int)
    for start, end in waits:
        pending_deltas[start] += 1
        pending_deltas[end] -= 1
    for start, end in intervals:
        active_deltas[start] += 1
        active_deltas[end] -= 1
    pending = 0
    active = 0
    under_capacity = 0
    for left, right in zip(points, points[1:]):
        pending += pending_deltas[left]
        active += active_deltas[left]
        if pending > 0 and active < capacity:
            under_capacity += right - left
    return {
        "submitted_wait_sum_ns": sum(end - start for start, end in waits),
        "under_capacity_ns": under_capacity,
    }


def analyze_llm_trace_file(path: Path, *, admission_capacity: int = 2) -> dict[str, object]:
    """Analyze one immutable LLM trace without inferring scheduler readiness."""

    if isinstance(admission_capacity, bool) or not isinstance(admission_capacity, int) or admission_capacity <= 0:
        raise _fail("admission_capacity_invalid")
    rows = _read_rows(Path(path))
    requests, regions = _parse_requests(rows)
    complete = [
        request
        for request in requests.values()
        if request["start_ns"] is not None and request["terminal_ns"] is not None
    ]
    counts = {kind: sum(request["request_kind"] == kind for request in requests.values()) for kind in _KINDS}
    tokens = {kind: sum(request["token_count"] for request in requests.values() if request["request_kind"] == kind) for kind in _KINDS}
    spans_by_kind: dict[str, list[tuple[int, int]]] = {
        kind: [
            (int(request["start_ns"]), int(request["terminal_ns"]))
            for request in complete
            if request["request_kind"] == kind
        ]
        for kind in _KINDS
    }
    span_sum = {kind: sum(end - start for start, end in spans_by_kind[kind]) for kind in _KINDS}
    interval_union = {kind: _interval_union(spans_by_kind[kind]) for kind in _KINDS}
    overlap = {kind: span_sum[kind] - interval_union[kind] for kind in _KINDS}
    total_requests = sum(counts.values())
    total_tokens = sum(tokens.values())
    total_span = sum(span_sum.values())
    total_union = _interval_union([interval for values in spans_by_kind.values() for interval in values])
    rho = {
        kind: {
            "request_fraction": _finite_ratio(counts[kind], total_requests),
            "prompt_fraction": _finite_ratio(tokens[kind], total_tokens),
            "service_span_fraction": _finite_ratio(span_sum[kind], total_span),
            "service_union_fraction": _finite_ratio(interval_union[kind], sum(interval_union.values())),
        }
        for kind in _KINDS
    }
    intervals = [interval for values in spans_by_kind.values() for interval in values]
    occupancy = _active_occupancy(intervals, admission_capacity)
    transport_wait = _transport_wait(requests, intervals, admission_capacity)
    complete_ids = {request["request_id"] for request in complete}
    body: dict[str, object] = {
        "schema_version": DIAGNOSTIC_SCHEMA,
        "trace_sha256": sha256_file(Path(path)),
        "admission_capacity": admission_capacity,
        # This artifact is never a formal result.  Trace completeness is
        # reported separately so a structurally complete diagnostic cannot be
        # mistaken for a method PASS.
        "status": "DIAGNOSTIC_ONLY",
        "trace_status": "COMPLETE_TRACE"
        if len(complete_ids) == len(requests)
        else "INCOMPLETE_TRACE",
        "request_count_by_kind": counts,
        "prompt_tokens_by_kind": tokens,
        "complete_request_count": len(complete),
        "incomplete_request_ids": sorted(
            request_id for request_id in requests if request_id not in complete_ids
        ),
        "service_span_sum_ns_by_kind": span_sum,
        "service_interval_union_ns_by_kind": interval_union,
        "service_overlap_excess_ns_by_kind": overlap,
        "service_interval_union_ns_total": total_union,
        "rho": rho,
        "occupancy": occupancy,
        "transport_wait": transport_wait,
        "frontier_bind_region_count": sum(len(value) for value in regions.values()),
        "frontier_bind_region_union_ns": _interval_union(
            [interval for values in regions.values() for interval in values]
        ),
        "evidence_boundary": {
            "ready_pool_observable": False,
            "frontier_wait_reason_observable": False,
            "conclusion": "READY_POOL_STARVATION_NOT_IDENTIFIABLE_FROM_LLM_TRACE",
        },
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


__all__ = [
    "DIAGNOSTIC_SCHEMA",
    "MemBindV31DiagnosticError",
    "TRACE_SCHEMA",
    "analyze_llm_trace_file",
]
