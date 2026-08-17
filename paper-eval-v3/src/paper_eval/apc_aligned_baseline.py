"""Pure contracts for the APC-aligned three-baseline development run.

This lane is deliberately independent from historical baseline artifacts and
from the MemBind method lane.  It freezes relative open-loop arrival offsets,
balanced sequential block order, lifecycle metrics, and the already-defined
source/publication/visibility/temporal-provenance correctness categories.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from paper_eval.artifacts import payload_sha256


SCHEMA = "membind.paper-eval-v3.apc-aligned-baseline-plan.v1"
APC_BASELINE_METHODS = ("U0-aligned", "A0-aligned", "P(C=2)-aligned")
APC_BASELINE_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
_METHOD_SLUG = {"U0-aligned": "u0", "A0-aligned": "a0", "P(C=2)-aligned": "pc2"}
_METHOD_ORDERS = (
    ("U0-aligned", "A0-aligned", "P(C=2)-aligned"),
    ("A0-aligned", "P(C=2)-aligned", "U0-aligned"),
    ("P(C=2)-aligned", "U0-aligned", "A0-aligned"),
    ("U0-aligned", "P(C=2)-aligned", "A0-aligned"),
)
_RUN_ID = re.compile(r"^apc-baseline-[a-z0-9][a-z0-9-]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def cache_salt_for_block(run_id: str, block_index: int) -> str:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run id invalid")
    index = _nonnegative_int(block_index, "block index invalid")
    return f"mb-{payload_sha256({'run_id': run_id, 'block_index': index})[:32]}"


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(code)
    return value


def _sources(value: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or tuple(value) != APC_BASELINE_HISTORIES:
        raise ValueError("source inventory invalid")
    result: dict[str, list[str]] = {}
    for history_id in APC_BASELINE_HISTORIES:
        raw = value.get(history_id)
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
            raise ValueError("source inventory invalid")
        selected = [_sha(item, "source identity invalid") for item in raw]
        if len(set(selected)) != len(selected):
            raise ValueError("source identity duplicate")
        result[history_id] = selected
    return result


def build_apc_aligned_baseline_plan(
    *,
    run_id: str,
    history_source_sha256s: Mapping[str, Sequence[str]],
    interarrival_ns: int,
    execution_envelope_sha256: str,
    service_reference_ns: int,
    normalized_offered_load: float,
) -> dict[str, Any]:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run id invalid")
    interval = _nonnegative_int(interarrival_ns, "interarrival invalid")
    service = _nonnegative_int(service_reference_ns, "service reference invalid")
    if (
        isinstance(normalized_offered_load, bool)
        or not isinstance(normalized_offered_load, (int, float))
        or not math.isfinite(float(normalized_offered_load))
        or float(normalized_offered_load) <= 0
    ):
        raise ValueError("offered load invalid")
    if round(service / float(normalized_offered_load)) != interval:
        raise ValueError("interarrival derivation invalid")
    sources = _sources(history_source_sha256s)
    envelope = _sha(execution_envelope_sha256, "execution envelope invalid")
    traces: dict[str, dict[str, object]] = {}
    for history_id in APC_BASELINE_HISTORIES:
        body = {
            "history_id": history_id,
            "interarrival_ns": interval,
            "arrival_offsets_ns": [index * interval for index in range(len(sources[history_id]))],
        }
        traces[history_id] = {**body, "history_arrival_trace_sha256": payload_sha256(body)}
    arrival_trace_sha = payload_sha256(traces)
    source_manifest_sha = payload_sha256(sources)
    blocks: list[dict[str, object]] = []
    for history_index, history_id in enumerate(APC_BASELINE_HISTORIES):
        for position, method in enumerate(_METHOD_ORDERS[history_index]):
            blocks.append(
                {
                    "block_index": len(blocks),
                    "run_id": run_id,
                    "aligned_run_id": run_id,
                    "method": method,
                    "method_position": position,
                    "history_id": history_id,
                    "source_count": len(sources[history_id]),
                    "namespace": f"pev3-{run_id}-{_METHOD_SLUG[method]}-{history_id}",
                    "source_manifest_sha256": source_manifest_sha,
                    "arrival_trace_sha256": arrival_trace_sha,
                    "history_arrival_trace_sha256": traces[history_id][
                        "history_arrival_trace_sha256"
                    ],
                    "execution_envelope_sha256": envelope,
                    "shared_execution_envelope_sha256": envelope,
                    "global_llm_admission_k": 2,
                    "cache_salt_sha256": payload_sha256(
                        {"cache_salt": cache_salt_for_block(run_id, len(blocks))}
                    ),
                }
            )
    plan: dict[str, Any] = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "aligned_run_id": run_id,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "methods": list(APC_BASELINE_METHODS),
        "histories": list(APC_BASELINE_HISTORIES),
        "history_source_sha256s": sources,
        "source_manifest_sha256": source_manifest_sha,
        "interarrival_ns": interval,
        "service_reference_ns": service,
        "normalized_offered_load": float(normalized_offered_load),
        "arrival_traces": traces,
        "arrival_trace_sha256": arrival_trace_sha,
        "execution_envelope_sha256": envelope,
        "shared_execution_envelope_sha256": envelope,
        "global_llm_admission_k": 2,
        "apc_cache_policy": "HOT_ENGINE_COLD_CROSS_BLOCK_NATURAL_WITHIN_BLOCK",
        "method_orders": [list(value) for value in _METHOD_ORDERS],
        "blocks": blocks,
    }
    plan["payload_sha256"] = payload_sha256(plan)
    return plan


def verify_apc_aligned_baseline_plan(value: Mapping[str, object]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA:
        raise ValueError("plan invalid")
    candidate = deepcopy(dict(value))
    expected = build_apc_aligned_baseline_plan(
        run_id=candidate.get("run_id"),
        history_source_sha256s=candidate.get("history_source_sha256s"),
        interarrival_ns=candidate.get("interarrival_ns"),
        execution_envelope_sha256=candidate.get("execution_envelope_sha256"),
        service_reference_ns=candidate.get("service_reference_ns"),
        normalized_offered_load=candidate.get("normalized_offered_load"),
    )
    if candidate != expected:
        raise ValueError("plan identity drift")
    return candidate


def _nearest_rank(values: Sequence[int], probability: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def derive_apc_aligned_performance(
    lifecycle_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if isinstance(lifecycle_rows, (str, bytes)) or not isinstance(lifecycle_rows, Sequence):
        raise ValueError("lifecycle invalid")
    rows: list[dict[str, int]] = []
    for expected_sequence, raw in enumerate(lifecycle_rows):
        if not isinstance(raw, Mapping) or raw.get("source_sequence") != expected_sequence:
            raise ValueError("lifecycle source coverage invalid")
        arrival = _nonnegative_int(raw.get("arrival_timestamp_ns"), "arrival invalid")
        enqueue = _nonnegative_int(raw.get("enqueue_timestamp_ns"), "enqueue invalid")
        start = _nonnegative_int(raw.get("service_start_timestamp_ns"), "service start invalid")
        publication = _nonnegative_int(raw.get("publication_timestamp_ns"), "publication invalid")
        caller_return = _nonnegative_int(
            raw.get("caller_return_timestamp_ns", publication), "caller return invalid"
        )
        if not arrival <= enqueue <= start <= publication or not arrival <= caller_return <= publication:
            raise ValueError("lifecycle timestamp order invalid")
        rows.append(
            {
                "source_sequence": expected_sequence,
                "arrival_timestamp_ns": arrival,
                "enqueue_timestamp_ns": enqueue,
                "service_start_timestamp_ns": start,
                "publication_timestamp_ns": publication,
                "caller_return_timestamp_ns": caller_return,
                "queue_delay_ns": start - arrival,
                "service_latency_ns": publication - start,
                "freshness_ns": publication - arrival,
                "caller_blocking_ns": caller_return - arrival,
                "post_return_stale_window_ns": publication - caller_return,
            }
        )
    if not rows:
        raise ValueError("lifecycle empty")
    timestamps = sorted(
        {
            value
            for row in rows
            for value in (
                row["arrival_timestamp_ns"],
                row["service_start_timestamp_ns"],
                row["publication_timestamp_ns"],
            )
        }
    )
    outstanding = [
        sum(
            row["arrival_timestamp_ns"] <= timestamp < row["publication_timestamp_ns"]
            for row in rows
        )
        for timestamp in timestamps
    ]
    waiting = [
        sum(
            row["arrival_timestamp_ns"] <= timestamp < row["service_start_timestamp_ns"]
            for row in rows
        )
        for timestamp in timestamps
    ]
    freshness = [row["freshness_ns"] for row in rows]
    makespan = max(row["publication_timestamp_ns"] for row in rows) - min(
        row["arrival_timestamp_ns"] for row in rows
    )
    if makespan <= 0:
        raise ValueError("makespan invalid")
    return {
        "episode_count": len(rows),
        "p95_freshness_ns": _nearest_rank(freshness, 0.95),
        "p99_freshness_ns": _nearest_rank(freshness, 0.99),
        "makespan_ns": makespan,
        "goodput_episodes_per_second": len(rows) * 1_000_000_000 / makespan,
        "max_outstanding_backlog": max(outstanding),
        "max_waiting_queue_depth": max(waiting),
        "per_source": rows,
    }


def lifecycle_rows_from_events(
    events: Sequence[Mapping[str, object]], *, method: str, source_count: int
) -> list[dict[str, int]]:
    if method not in APC_BASELINE_METHODS:
        raise ValueError("method invalid")
    count = _nonnegative_int(source_count, "source count invalid")
    if count < 1 or isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise ValueError("lifecycle events invalid")
    by_source: dict[int, dict[str, Mapping[str, object]]] = {
        sequence: {} for sequence in range(count)
    }
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("lifecycle event invalid")
        source = event.get("source_sequence")
        kind = event.get("event_type")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source not in by_source
            or kind not in {"ARRIVAL", "ENQUEUED", "SERVICE_STARTED", "PUBLICATION_DURABLE"}
            or kind in by_source[source]
        ):
            raise ValueError("lifecycle coverage invalid")
        by_source[source][str(kind)] = event
    rows: list[dict[str, int]] = []
    for source in range(count):
        selected = by_source[source]
        if set(selected) != {"ARRIVAL", "ENQUEUED", "SERVICE_STARTED", "PUBLICATION_DURABLE"}:
            raise ValueError("lifecycle coverage invalid")
        arrival = _nonnegative_int(selected["ARRIVAL"].get("timestamp_ns"), "timestamp invalid")
        enqueue = _nonnegative_int(selected["ENQUEUED"].get("timestamp_ns"), "timestamp invalid")
        start = _nonnegative_int(selected["SERVICE_STARTED"].get("timestamp_ns"), "timestamp invalid")
        publication = _nonnegative_int(selected["PUBLICATION_DURABLE"].get("timestamp_ns"), "timestamp invalid")
        if method == "A0-aligned":
            telemetry = selected["ENQUEUED"].get("telemetry")
            if not isinstance(telemetry, Mapping):
                raise ValueError("A0 caller return missing")
            caller = _nonnegative_int(telemetry.get("caller_return_timestamp_ns"), "A0 caller return missing")
        else:
            caller = publication
        rows.append(
            {
                "source_sequence": source,
                "arrival_timestamp_ns": arrival,
                "enqueue_timestamp_ns": enqueue,
                "service_start_timestamp_ns": start,
                "publication_timestamp_ns": publication,
                "caller_return_timestamp_ns": caller,
            }
        )
    return rows


_GRAPH_COUNT_FIELDS = (
    "lost_episodic_count",
    "duplicate_episodic_count",
    "unexpected_episodic_count",
    "episodic_namespace_escape_count",
    "entity_namespace_escape_count",
    "relation_namespace_escape_count",
    "endpoint_escape_count",
    "provenance_dangling_count",
    "provenance_cross_namespace_count",
    "valid_invalid_reversal_count",
)


def summarize_direct_violations(
    *,
    expected_source_count: int,
    publication_source_sequences: Sequence[int],
    visibility_by_source: Mapping[int, bool],
    graph_counts: Mapping[str, object],
) -> dict[str, object]:
    count = _nonnegative_int(expected_source_count, "expected source count invalid")
    if count < 1:
        raise ValueError("expected source count invalid")
    expected = tuple(range(count))
    if isinstance(publication_source_sequences, (str, bytes)) or not isinstance(
        publication_source_sequences, Sequence
    ):
        raise ValueError("publication inventory invalid")
    publications = tuple(publication_source_sequences)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in publications):
        raise ValueError("publication inventory invalid")
    if not isinstance(visibility_by_source, Mapping) or set(visibility_by_source) != set(expected):
        raise ValueError("visibility coverage invalid")
    if any(not isinstance(value, bool) for value in visibility_by_source.values()):
        raise ValueError("visibility coverage invalid")
    if not isinstance(graph_counts, Mapping) or set(graph_counts) != set(_GRAPH_COUNT_FIELDS):
        raise ValueError("graph observation invalid")
    graph = {
        field: _nonnegative_int(graph_counts.get(field), "graph observation invalid")
        for field in _GRAPH_COUNT_FIELDS
    }
    observed = Counter(publications)
    lost_publication = sum(1 for source in expected if observed[source] == 0)
    duplicate_publication = sum(max(0, observed[source] - 1) for source in expected)
    unexpected_publication = sum(value for source, value in observed.items() if source not in expected)
    lost = lost_publication + graph["lost_episodic_count"]
    duplicate = (
        duplicate_publication
        + unexpected_publication
        + graph["duplicate_episodic_count"]
        + graph["unexpected_episodic_count"]
    )
    order = int(publications != expected)
    visibility = sum(not visibility_by_source[source] for source in expected)
    temporal_provenance = sum(
        graph[field]
        for field in (
            "episodic_namespace_escape_count",
            "entity_namespace_escape_count",
            "relation_namespace_escape_count",
            "endpoint_escape_count",
            "provenance_dangling_count",
            "provenance_cross_namespace_count",
            "valid_invalid_reversal_count",
        )
    )
    counts = {
        "lost_or_missing_source_count": lost,
        "duplicate_source_or_publication_count": duplicate,
        "source_publication_order_violation_count": order,
        "visibility_publication_violation_count": visibility,
        "temporal_provenance_hard_violation_count": temporal_provenance,
    }
    return {
        "checker_status": "MEASURED",
        "counts": counts,
        "direct_violations_total": sum(counts.values()),
        "violated_category_count": sum(value > 0 for value in counts.values()),
        "graph_observation_counts": graph,
    }


__all__ = [
    "APC_BASELINE_HISTORIES",
    "APC_BASELINE_METHODS",
    "SCHEMA",
    "build_apc_aligned_baseline_plan",
    "cache_salt_for_block",
    "derive_apc_aligned_performance",
    "lifecycle_rows_from_events",
    "summarize_direct_violations",
    "verify_apc_aligned_baseline_plan",
]
