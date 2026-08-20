"""Fail-closed reducer for the V4-MSEG-Q0 qualification gate."""

from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from typing import Any

from paper_eval.artifacts import payload_sha256


class Q0QualificationError(ValueError):
    """Q0 inputs are malformed and cannot support a scientific comparison."""


def _fail(code: str) -> Q0QualificationError:
    return Q0QualificationError(code)


_CAUSAL_FIELDS = {
    "operator_role",
    "operator_id",
    "parent_bind_id",
    "parent_operator_id",
    "operator_phase",
}
_ENVELOPE_FIELDS = (
    "history_id",
    "source_count",
    "compile_workers",
    "lookahead",
    "bind_workers",
    "global_llm_admission_k",
    "policy",
    "shared_execution_envelope_sha256",
    "source_manifest_sha256",
    "arrival_trace_sha256",
)


def _rows(value: object, code: str) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise _fail(code)
    selected = []
    for row in value:
        if not isinstance(row, dict):
            raise _fail(code)
        selected.append(dict(row))
    return selected


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _request_summary(rows: list[dict[str, object]]) -> dict[str, Any]:
    submitted = [row for row in rows if row.get("event_type") == "llm_request_submitted"]
    starts = {
        str(row.get("request_id")): row
        for row in rows
        if row.get("event_type") == "llm_request_start"
    }
    terminals = {
        str(row.get("request_id")): row
        for row in rows
        if row.get("event_type") == "llm_request_terminal"
    }
    durations = []
    for row in submitted:
        request_id = str(row.get("request_id"))
        start = starts.get(request_id)
        terminal = terminals.get(request_id)
        if start is not None and terminal is not None:
            try:
                durations.append(int(terminal["timestamp_ns"]) - int(start["timestamp_ns"]))
            except (KeyError, TypeError, ValueError):
                pass
    token_total = sum(
        int(row["token_count"])
        for row in submitted
        if isinstance(row.get("token_count"), int)
        and not isinstance(row.get("token_count"), bool)
    )
    return {
        "submitted": submitted,
        "count": len(submitted),
        "kind_counts": dict(
            sorted(Counter(str(row.get("request_kind")) for row in submitted).items())
        ),
        "input_token_count": token_total,
        "service_latency_ns": {
            "count": len(durations),
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
        },
        "starts": starts,
        "terminals": terminals,
    }


def _causal_coverage(
    rows: list[dict[str, object]],
    operator_events: list[dict[str, object]],
) -> tuple[dict[str, object], list[str]]:
    reasons: list[str] = []
    submitted = [row for row in rows if row.get("event_type") == "llm_request_submitted"]
    enters = Counter(
        str(event.get("operator_id"))
        for event in operator_events
        if event.get("event_type") == "operator_enter"
    )
    exits = Counter(
        str(event.get("operator_id"))
        for event in operator_events
        if event.get("event_type") == "operator_exit"
    )
    effects = [
        event
        for event in operator_events
        if event.get("event_type") == "operator_effect"
    ]
    effect_counts = Counter(str(event.get("operator_id")) for event in effects)
    if not enters:
        reasons.append("operator_trace_empty")
    for operator_id, count in enters.items():
        if count != 1 or exits[operator_id] != 1:
            reasons.append("operator_span_cardinality_invalid")
        if effect_counts[operator_id] != 1:
            reasons.append("operator_effect_coverage_incomplete")
    spans: dict[str, dict[str, dict[str, object]]] = {}
    for event in operator_events:
        operator_id = event.get("operator_id")
        event_type = event.get("event_type")
        if not isinstance(operator_id, str):
            reasons.append("operator_identity_missing")
            continue
        if event_type in {"operator_enter", "operator_exit", "operator_effect"}:
            spans.setdefault(operator_id, {})[str(event_type)] = event

    complete_metadata = 0
    contained = 0
    for row in submitted:
        if not _CAUSAL_FIELDS <= set(row):
            reasons.append("causal_metadata_incomplete")
            continue
        complete_metadata += 1
        operator_id = str(row["operator_id"])
        span = spans.get(operator_id, {})
        enter = span.get("operator_enter")
        exit_event = span.get("operator_exit")
        if enter is None or exit_event is None:
            reasons.append("operator_span_missing")
            continue
        try:
            timestamp = int(row["timestamp_ns"])
            enter_ns = int(enter["operator_enter_ns"])
            end_ns = int(exit_event["operator_end_ns"])
        except (KeyError, TypeError, ValueError):
            reasons.append("operator_span_timing_missing")
            continue
        if enter_ns <= timestamp <= end_ns:
            contained += 1
        else:
            reasons.append("request_outside_operator_span")

    response_rows = [
        row for row in rows if row.get("event_type") == "llm_transport_response"
    ]
    response_metadata = sum(_CAUSAL_FIELDS <= set(row) for row in response_rows)
    read_statuses = {row.get("read_scope") for row in effects}
    if effects and read_statuses != {"NOT_OBSERVABLE"}:
        reasons.append("read_scope_claim_exceeds_observation")
    if effects and any(row.get("read_scope_complete") is not False for row in effects):
        reasons.append("read_scope_completeness_invalid")
    if effects and any(row.get("effect_scope_complete") is not True for row in effects):
        reasons.append("effect_scope_completeness_invalid")
    if effects and any(not isinstance(row.get("persistent_write"), bool) for row in effects):
        reasons.append("persistent_write_telemetry_invalid")
    if submitted and (
        len(response_rows) != len(submitted)
        or response_metadata != len(response_rows)
    ):
        reasons.append("transport_response_correlation_incomplete")
    coverage = {
        "submitted_count": len(submitted),
        "submitted_with_complete_metadata": complete_metadata,
        "submitted_coverage_fraction": (
            complete_metadata / len(submitted) if submitted else 0.0
        ),
        "span_contained_count": contained,
        "span_containment_fraction": contained / len(submitted) if submitted else 0.0,
        "transport_response_count": len(response_rows),
        "transport_response_with_metadata": response_metadata,
        "transport_response_coverage_fraction": (
            response_metadata / len(response_rows) if response_rows else None
        ),
        "operator_count": sum(enters.values()),
        "effect_count": len(effects),
    }
    return coverage, sorted(set(reasons))


def _envelope_parity(
    baseline_manifest: dict[str, object], q0_manifest: dict[str, object]
) -> tuple[bool, dict[str, object]]:
    comparison = {
        field: {
            "baseline": baseline_manifest.get(field),
            "q0": q0_manifest.get(field),
            "equal": baseline_manifest.get(field) == q0_manifest.get(field),
        }
        for field in _ENVELOPE_FIELDS
    }
    return all(item["equal"] for item in comparison.values()), comparison


def reduce_q0_qualification(
    *,
    baseline_result: dict[str, object],
    q0_result: dict[str, object],
    baseline_manifest: dict[str, object],
    q0_manifest: dict[str, object],
    baseline_request_rows: list[dict[str, object]],
    q0_request_rows: list[dict[str, object]],
    operator_events: list[dict[str, object]],
    baseline_state: dict[str, object],
    q0_state: dict[str, object],
) -> dict[str, object]:
    """Reduce one diagnostic measurement without modifying sealed inputs."""

    baseline_rows = _rows(baseline_request_rows, "baseline_rows_invalid")
    q0_rows = _rows(q0_request_rows, "q0_rows_invalid")
    baseline = _request_summary(baseline_rows)
    candidate = _request_summary(q0_rows)
    events = _rows(operator_events, "operator_events_invalid")
    envelope_equal, envelope = _envelope_parity(baseline_manifest, q0_manifest)
    coverage, blocking_reasons = _causal_coverage(q0_rows, events)

    if candidate["count"] != baseline["count"]:
        blocking_reasons.append("request_count_parity_failed")
    if candidate["kind_counts"] != baseline["kind_counts"]:
        blocking_reasons.append("request_kind_parity_failed")
    token_parity = candidate["input_token_count"] == baseline["input_token_count"]
    if not token_parity:
        blocking_reasons.append("semantic_input_token_parity_failed")
    publication_parity = (
        baseline_result.get("publication_source_sequences")
        == q0_result.get("publication_source_sequences")
    )
    if not publication_parity:
        blocking_reasons.append("publication_order_parity_failed")
    state_parity = baseline_state == q0_state
    if not state_parity:
        blocking_reasons.append("published_state_parity_failed")
    if not envelope_equal:
        blocking_reasons.append("execution_envelope_drift")
    if q0_result.get("direct_violation_count") != 0:
        blocking_reasons.append("correctness_violation")
    if q0_result.get("observed_max_inflight") != q0_manifest.get("global_llm_admission_k"):
        blocking_reasons.append("admission_k_observation_invalid")
    if coverage["submitted_coverage_fraction"] != 1.0:
        blocking_reasons.append("causal_metadata_incomplete")
    if coverage["span_containment_fraction"] != 1.0:
        blocking_reasons.append("operator_span_correlation_incomplete")
    if not events:
        blocking_reasons.append("operator_trace_empty")

    baseline_makespan = baseline_result.get("performance", {}).get("makespan_ns")
    q0_makespan = q0_result.get("performance", {}).get("makespan_ns")
    delta = None
    delta_fraction = None
    if isinstance(baseline_makespan, int) and isinstance(q0_makespan, int):
        delta = q0_makespan - baseline_makespan
        delta_fraction = delta / baseline_makespan if baseline_makespan else None

    reasons = sorted(set(str(reason) for reason in blocking_reasons))
    passed = not reasons
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.mseg-q0-qualification.v1",
        "status": "PASS_INSTRUMENTATION_QUALIFICATION"
        if passed
        else "FAIL_INSTRUMENTATION_QUALIFICATION",
        "execution_policy_changed": False,
        "request_count_parity": candidate["count"] == baseline["count"],
        "baseline_request_count": baseline["count"],
        "q0_request_count": candidate["count"],
        "request_kind_parity": candidate["kind_counts"] == baseline["kind_counts"],
        "semantic_input_token_parity": token_parity,
        "baseline_input_tokens": baseline["input_token_count"],
        "q0_input_tokens": candidate["input_token_count"],
        "publication_order_parity": publication_parity,
        "published_state_parity": state_parity,
        "execution_envelope_parity": envelope,
        "causal_correlation": coverage,
        "effect_telemetry": {
            "effect_count": coverage["effect_count"],
            "read_scope_status": "NOT_OBSERVABLE",
            "read_scope_claimed": False,
        },
        "timing_comparison": {
            "baseline_makespan_ns": baseline_makespan,
            "q0_makespan_ns": q0_makespan,
            "delta_ns": delta,
            "delta_fraction": delta_fraction,
            "baseline_service_latency": baseline["service_latency_ns"],
            "q0_service_latency": candidate["service_latency_ns"],
            "interpretation": "DESCRIPTIVE_NOT_CAUSAL_OVERHEAD_ESTIMATE",
        },
        "blocking_reasons": reasons,
        "post_q0_action": "RECONSTRUCT_MSEG_AND_RUN_O1_O4"
        if passed
        else "STOP_V4_FINE_GRAINED",
    }
    return {**body, "payload_sha256": payload_sha256(body)}


__all__ = ["Q0QualificationError", "reduce_q0_qualification"]
