"""Measured speculation-window recovery and critical-path credit bounds."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence


WINDOW_SCHEMA = "membind.dvsr.speculation-window.v1"
WINDOW_RECOVERY_SCHEMA = "membind.dvsr.frozen-v6-window-recovery.v1"


def _measured(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def compute_speculation_window(
    *,
    artifact_ready_ns: int | None,
    old_snapshot_ready_ns: int | None,
    old_snapshot_close_ns: int | None,
    authoritative_need_ns: int | None,
    removable_operator_cp_ns: int | None,
) -> dict[str, Any]:
    """Apply the real ready/need and old-snapshot launch constraints.

    ``old_snapshot_close_ns`` is the predecessor publication start.  Starting
    after that boundary would either observe the new state or mix snapshots;
    ordered publication is never delayed to create an artificial window.
    """

    values = {
        "artifact_ready_ns": artifact_ready_ns,
        "old_snapshot_ready_ns": old_snapshot_ready_ns,
        "old_snapshot_close_ns": old_snapshot_close_ns,
        "authoritative_need_ns": authoritative_need_ns,
        "removable_operator_cp_ns": removable_operator_cp_ns,
    }
    missing = sorted(name for name, value in values.items() if value is None)
    if missing:
        return {
            "schema_version": WINDOW_SCHEMA,
            "status": "MISSING_FIELD",
            "missing_fields": missing,
            "effective_ready_ns": None,
            "speculation_window_ns": None,
            "maximum_hideable_cp_ns": None,
        }
    measured = {name: _measured(value, name) for name, value in values.items()}
    ready = max(measured["artifact_ready_ns"], measured["old_snapshot_ready_ns"])
    if measured["old_snapshot_close_ns"] < measured["old_snapshot_ready_ns"]:
        raise ValueError("old snapshot close precedes old snapshot readiness")
    if measured["authoritative_need_ns"] < ready:
        raise ValueError("authoritative need precedes effective readiness")
    if ready >= measured["old_snapshot_close_ns"]:
        return {
            "schema_version": WINDOW_SCHEMA,
            "status": "INELIGIBLE_CROSS_SNAPSHOT_LAUNCH",
            "reason": "prepared_artifact_not_ready_before_old_snapshot_close",
            **measured,
            "effective_ready_ns": ready,
            "speculation_window_ns": 0,
            "maximum_hideable_cp_ns": 0,
        }
    window = max(0, measured["authoritative_need_ns"] - ready)
    return {
        "schema_version": WINDOW_SCHEMA,
        "status": "COMPLETE",
        **measured,
        "effective_ready_ns": ready,
        "speculation_window_ns": window,
        "maximum_hideable_cp_ns": min(window, measured["removable_operator_cp_ns"]),
    }


def bound_hidden_cp_components(
    *,
    reuse_hidden_cp_ns: int,
    reconvergence_saved_descendant_cp_ns: int,
    maximum_hideable_cp_ns: int,
) -> dict[str, int]:
    """Conservatively allocate one shared window to non-overlapping credits."""

    reuse = _measured(reuse_hidden_cp_ns, "reuse_hidden_cp_ns")
    reconvergence = _measured(
        reconvergence_saved_descendant_cp_ns,
        "reconvergence_saved_descendant_cp_ns",
    )
    cap = _measured(maximum_hideable_cp_ns, "maximum_hideable_cp_ns")
    bounded_reuse = min(reuse, cap)
    remaining = max(0, cap - bounded_reuse)
    bounded_reconvergence = min(reconvergence, remaining)
    credited = bounded_reuse + bounded_reconvergence
    return {
        "reuse_hidden_cp_ns": bounded_reuse,
        "reconvergence_saved_descendant_cp_ns": bounded_reconvergence,
        "window_bounded_hidden_cp_ns": credited,
        "uncredited_due_to_window_ns": reuse + reconvergence - credited,
    }


def _single_trace_timestamp(
    capture: Mapping[str, Any] | None,
    *,
    phase: str,
    field: str,
) -> int | None:
    if not isinstance(capture, Mapping):
        return None
    trace = capture.get("trace")
    if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes, bytearray)):
        return None
    values: list[int] = []
    for row in trace:
        if not isinstance(row, Mapping) or row.get("phase") != phase or row.get("status") != "ok":
            continue
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        values.append(value)
    return values[0] if len(values) == 1 else None


def compute_pair_window_from_observer_evidence(
    *,
    source_sequence: int,
    old_capture: Mapping[str, Any],
    fresh_capture: Mapping[str, Any],
    formal_start_ns: int | None,
    previous_durable_ns: int | None,
    predecessor_publication_start_ns: int | None,
    removable_operator_cp_ns: int,
) -> dict[str, Any]:
    """Reduce a paired observer record to measured ready/need window clocks.

    The prepared artifact is ready only after the OLD build-to-seam capture
    closes.  ``node-resolution.start_ns`` in the fresh trace is the
    authoritative need boundary.  The predecessor publication start is a
    hard launch cutoff, so this helper never turns a late observer into a
    fabricated overlap opportunity.
    """

    if isinstance(source_sequence, bool) or not isinstance(source_sequence, int) or source_sequence < 1:
        raise ValueError("source_sequence must be a positive integer")
    artifact_ready = old_capture.get("end_ns") if isinstance(old_capture, Mapping) else None
    if isinstance(artifact_ready, bool) or not isinstance(artifact_ready, int) or artifact_ready < 0:
        artifact_ready = None
    old_snapshot_ready = formal_start_ns if source_sequence == 1 else previous_durable_ns
    authoritative_need = _single_trace_timestamp(
        fresh_capture,
        phase="node-resolution",
        field="start_ns",
    )
    values = {
        "artifact_ready_ns": artifact_ready,
        "old_snapshot_ready_ns": old_snapshot_ready,
        "old_snapshot_close_ns": predecessor_publication_start_ns,
        "authoritative_need_ns": authoritative_need,
        "removable_operator_cp_ns": removable_operator_cp_ns,
    }
    result = compute_speculation_window(**values)
    result.update(
        {
            "source_sequence": source_sequence,
            "ready_event": "OLD.capture.end_ns",
            "need_event": "FRESH_NATIVE.node-resolution.start_ns",
            "close_event": "predecessor.publication.start_ns",
        }
    )
    return result


def _event_values(
    rows: Sequence[Mapping[str, Any]],
    *,
    event: str,
) -> dict[int, list[int]]:
    result: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        if row.get("event") != event:
            continue
        source = row.get("source_sequence")
        stamp = row.get("monotonic_ns")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source < 0
            or isinstance(stamp, bool)
            or not isinstance(stamp, int)
            or stamp < 0
        ):
            continue
        result[source].append(stamp)
    return dict(result)


def _span_values(
    envelopes: Sequence[Mapping[str, Any]],
    *,
    phase: str,
    field: str,
) -> dict[int, list[int]]:
    result: dict[int, list[int]] = defaultdict(list)
    for envelope in envelopes:
        source = envelope.get("source_sequence")
        spans = envelope.get("spans")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source < 0
            or not isinstance(spans, Sequence)
            or isinstance(spans, (str, bytes, bytearray))
        ):
            continue
        for span in spans:
            if not isinstance(span, Mapping) or span.get("phase") != phase or span.get("status") != "ok":
                continue
            stamp = span.get(field)
            if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0:
                continue
            result[source].append(stamp)
    return dict(result)


def _unique(values: Mapping[int, list[int]], source: int) -> int | None:
    selected = values.get(source, ())
    return selected[0] if len(selected) == 1 else None


def recover_frozen_v6_window_fields(
    *,
    frontier_events: Sequence[Mapping[str, Any]],
    native_trace_envelopes: Sequence[Mapping[str, Any]],
    raw_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Recover window clocks from existing sealed Frozen-V6 evidence only."""

    ready = _event_values(frontier_events, event="PREPARE_READY")
    durable = _event_values(frontier_events, event="PUBLICATION_DURABLE")
    need = _span_values(
        native_trace_envelopes,
        phase="node-resolution",
        field="start_ns",
    )
    publication_start = _span_values(
        native_trace_envelopes,
        phase="publication",
        field="start_ns",
    )
    formal_start_values = [
        row.get("monotonic_ns")
        for row in raw_events
        if row.get("event") == "FORMAL_START"
        and isinstance(row.get("monotonic_ns"), int)
        and not isinstance(row.get("monotonic_ns"), bool)
        and int(row["monotonic_ns"]) >= 0
    ]
    formal_start = formal_start_values[0] if len(formal_start_values) == 1 else None
    rows: list[dict[str, Any]] = []
    for source in sorted(sequence for sequence in ready if sequence >= 1):
        artifact_ready = _unique(ready, source)
        authoritative_need = _unique(need, source)
        old_snapshot_close = _unique(publication_start, source - 1)
        old_snapshot_ready = (
            formal_start if source == 1 else _unique(durable, source - 2)
        )
        fields = {
            "artifact_ready_ns": artifact_ready,
            "old_snapshot_ready_ns": old_snapshot_ready,
            "old_snapshot_close_ns": old_snapshot_close,
            "authoritative_need_ns": authoritative_need,
        }
        missing = sorted(name for name, value in fields.items() if value is None)
        row: dict[str, Any] = {
            "source_sequence": source,
            **fields,
            "need_event": "node-resolution.start_ns",
            "ready_event": "PREPARE_READY.monotonic_ns",
        }
        if missing:
            row.update(
                {
                    "field_status": "MISSING_FIELD",
                    "missing_fields": missing,
                    "cross_snapshot_launch_eligible": False,
                    "raw_window_ns": None,
                }
            )
        else:
            effective_ready = max(int(artifact_ready), int(old_snapshot_ready))
            eligible = effective_ready < int(old_snapshot_close)
            row.update(
                {
                    "field_status": "COMPLETE",
                    "cross_snapshot_launch_eligible": eligible,
                    "raw_window_ns": max(0, int(authoritative_need) - effective_ready)
                    if eligible
                    else 0,
                }
            )
        rows.append(row)
    complete = sum(row["field_status"] == "COMPLETE" for row in rows)
    eligible = sum(row["cross_snapshot_launch_eligible"] is True for row in rows)
    return {
        "schema_version": WINDOW_RECOVERY_SCHEMA,
        "status": "COMPLETE" if rows and complete == len(rows) else "MISSING_FIELD",
        "source_pair_count": len(rows),
        "complete_pair_count": complete,
        "cross_snapshot_launch_eligible_count": eligible,
        "rows": rows,
        "non_claims": [
            "raw_window_ns is not operator CP",
            "raw_window_ns is not serial observer duration",
            "MaximumHideableCP still requires measured removable operator CP",
            "snapshot-consistent read completion must be proven separately",
        ],
    }


__all__ = [
    "WINDOW_RECOVERY_SCHEMA",
    "WINDOW_SCHEMA",
    "bound_hidden_cp_components",
    "compute_pair_window_from_observer_evidence",
    "compute_speculation_window",
    "recover_frozen_v6_window_fields",
]
