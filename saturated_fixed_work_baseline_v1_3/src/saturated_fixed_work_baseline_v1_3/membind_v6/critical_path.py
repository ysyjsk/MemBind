"""Provider-free critical-path reduction for sealed V5/V6 traces.

The reducer consumes only sanitized event journals and native trace envelopes.
It never imports Graphiti or opens a service connection.  Interval totals are
kept as attribution evidence; only the ordered native interval chain is used
for the makespan decomposition, so overlapping child spans are never added to
the critical path.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


class CriticalPathError(ValueError):
    """The selected trace cannot satisfy the V6 reduction contract."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CriticalPathError(f"missing evidence: {path.name}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CriticalPathError(f"invalid JSONL: {path.name}:{line_number}") from exc
        if not isinstance(row, dict):
            raise CriticalPathError(f"JSON object required: {path.name}:{line_number}")
        rows.append(row)
    return rows


def _number(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CriticalPathError(f"{key} must be an integer")
    return int(value)


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _integer_median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def reduce_history_artifact(root: str | Path) -> dict[str, Any]:
    """Recompute the V5 native critical path from one sealed history root."""

    history_root = Path(root)
    raw = _read_jsonl(history_root / "raw_events.jsonl")
    native_rows = [row for row in raw if row.get("event") == "NATIVE_INTERVAL"]
    durable_rows = [row for row in raw if row.get("event") == "PUBLICATION_DURABLE"]
    starts = [row for row in raw if row.get("event") == "FORMAL_START"]
    stops = [row for row in raw if row.get("event") == "TIMER_STOP"]
    if len(starts) != 1 or len(stops) != 1:
        raise CriticalPathError("exactly one FORMAL_START and TIMER_STOP are required")
    if not native_rows:
        raise CriticalPathError("native interval coverage is empty")

    native_by_source: dict[int, dict[str, Any]] = {}
    for row in native_rows:
        sequence = _number(row, "source_sequence")
        if sequence in native_by_source:
            raise CriticalPathError(f"duplicate native interval for source {sequence}")
        start = _number(row, "start_ns")
        end = _number(row, "end_ns")
        if start > end:
            raise CriticalPathError(f"native interval has negative duration: {sequence}")
        native_by_source[sequence] = {"source_sequence": sequence, "start_ns": start, "end_ns": end}
    source_count = len(native_by_source)
    expected_sequences = list(range(source_count))
    if sorted(native_by_source) != expected_sequences:
        raise CriticalPathError("native interval coverage is not contiguous")

    durable_sequences = [_number(row, "source_sequence") for row in durable_rows]
    if durable_sequences != expected_sequences:
        if durable_sequences and max(durable_sequences) >= source_count:
            raise CriticalPathError("native interval coverage does not match durable frontier")
        raise CriticalPathError("durable frontier must advance contiguously from source zero")

    ordered_native = [native_by_source[index] for index in expected_sequences]
    for previous, current in zip(ordered_native, ordered_native[1:]):
        if current["start_ns"] < previous["end_ns"]:
            raise CriticalPathError("native intervals overlap; ordered publication is not serial")

    timer_start = _number(starts[0], "monotonic_ns")
    timer_stop = _number(stops[0], "timer_stop_ns")
    if timer_stop < timer_start:
        raise CriticalPathError("timer stop precedes timer start")
    build_makespan = timer_stop - timer_start

    first_native_start = ordered_native[0]["start_ns"]
    last_native_end = ordered_native[-1]["end_ns"]
    prefix_prepare = first_native_start - timer_start
    native_occupied = sum(row["end_ns"] - row["start_ns"] for row in ordered_native)
    inter_native_gap = sum(
        max(0, current["start_ns"] - previous["end_ns"])
        for previous, current in zip(ordered_native, ordered_native[1:])
    )
    post_native_tail = timer_stop - last_native_end
    residual = build_makespan - (prefix_prepare + native_occupied + inter_native_gap + post_native_tail)
    if residual != 0:
        raise CriticalPathError(f"critical-path decomposition residual is non-zero: {residual}")

    prepared_at = {
        _number(row, "source_sequence"): _number(row, "monotonic_ns")
        for row in raw
        if row.get("event") == "PREPARED"
    }
    future_slack = [
        native_by_source[sequence]["start_ns"] - prepared_at[sequence]
        for sequence in expected_sequences[1:]
        if sequence in prepared_at
    ]

    phase_durations: dict[str, int] = defaultdict(int)
    phase_counts: dict[str, int] = defaultdict(int)
    try:
        trace_rows = _read_jsonl(history_root / "native_trace.jsonl")
    except CriticalPathError:
        trace_rows = []
    trace_sequences = sorted(_number(row, "source_sequence") for row in trace_rows)
    if trace_rows and trace_sequences != expected_sequences:
        raise CriticalPathError("native trace envelope coverage is not contiguous")
    for envelope in trace_rows:
        spans = envelope.get("spans")
        if not isinstance(spans, list):
            raise CriticalPathError("native trace envelope spans must be a list")
        for span in spans:
            if not isinstance(span, Mapping) or not isinstance(span.get("phase"), str):
                continue
            duration = span.get("duration_ns")
            if isinstance(duration, int) and duration >= 0:
                phase = str(span["phase"])
                phase_durations[phase] += duration
                phase_counts[phase] += 1

    return {
        "schema_version": "membind.v6.critical-path.v1",
        "history_id": str(history_root.name),
        "source_count": source_count,
        "durable_frontier": source_count - 1,
        "timer": {
            "timer_start_ns": timer_start,
            "timer_stop_ns": timer_stop,
            "build_makespan_ns": build_makespan,
            "reconstructed_from_events": True,
        },
        "critical_path": {
            "source0_prepare_to_first_native_ns": prefix_prepare,
            "native_chain_ns": native_occupied,
            "inter_native_gap_ns": inter_native_gap,
            "post_native_tail_ns": post_native_tail,
            "decomposition_residual_ns": residual,
            "first_native_start_ns": first_native_start,
            "last_native_end_ns": last_native_end,
        },
        "future_ready_slack_ns": {
            "count": len(future_slack),
            "min": min(future_slack) if future_slack else None,
            "median": _integer_median(future_slack),
            "p95": _percentile(future_slack, 0.95),
        },
        "phase_attribution_method": "span_totals_for_attribution_only",
        "phase_attribution": {
            phase: {
                "span_count": phase_counts[phase],
                "total_duration_ns": phase_durations[phase],
                "overlap_safe": False,
            }
            for phase in sorted(phase_counts)
        },
    }


__all__ = ["CriticalPathError", "reduce_history_artifact"]
