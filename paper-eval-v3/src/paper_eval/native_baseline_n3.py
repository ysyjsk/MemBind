"""Pure-offline N3 reduction for the fixed Native U0 development screen.

The reducer treats sanitized Level-0 rows as its source of truth, rebuilds
Level-1/Level-2 projections, and uses sealed durable summaries only as exact
cross-checks.  It never contacts Graphiti, model services, or Neo4j. Histories
are the descriptive aggregation unit; episode rows are intentionally not
pooled as independent experimental replicates.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .artifacts import payload_sha256
from .native_baseline_runner import (
    DEVELOPMENT_HISTORIES,
    build_native_baseline_plan,
    verify_checkpoint,
    verify_history_result,
)
from .unified_observability import (
    PRIMARY_METRICS,
    SECONDARY_METRICS,
    ObservabilityIdentity,
    aggregate_history_metrics,
    derive_episode_metrics,
    project_operation_views,
    validate_observability_record,
    validate_raw_quality_evidence,
)


NATIVE_BASELINE_N3_SCHEMA = "membind.paper-eval-v3.native-baseline-n3.v1"
_GOODPUT_UNIT = "episodes_per_second"
_SERIAL_BACKLOG_STATUS = "NOT_APPLICABLE_SERIAL_BASELINE"
_RAW_ROW_STREAMS = (
    "spans",
    "events",
    "llm",
    "embedding",
    "db",
    "graph_work",
    "queue",
    "per_episode",
)
_LIFECYCLE_EVENTS = ("intent", "service_start", "publication", "terminal")


def _median(values: Sequence[int | float]) -> int | float:
    """Return a deterministic median without an ambiguous top-level import."""

    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _nonnegative_number(value: Any, field: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{field} must be a finite nonnegative number")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _bounded_fraction(value: Any, field: str) -> float:
    number = _nonnegative_number(value, field)
    if number > 1:
        raise ValueError(f"{field} must be in [0, 1]")
    return float(number)


def _numeric_total(value: Mapping[str, Any], field: str) -> float:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    total = 0.0
    for name, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{field}.{name} must be numeric")
        if not math.isfinite(float(raw)):
            raise ValueError(f"{field}.{name} must be finite")
        total += max(0.0, float(raw))
    return total


def _descriptive(values: Sequence[int | float]) -> dict[str, int | float]:
    if len(values) != len(DEVELOPMENT_HISTORIES):
        raise ValueError("macro description requires the fixed four histories")
    return {
        "history_count": len(values),
        "mean": sum(values) / len(values),
        "median": _median(values),
        "min": min(values),
        "max": max(values),
    }


def _require_rows(raw_rows: Mapping[str, Any], stream: str) -> list[Mapping[str, Any]]:
    value = raw_rows.get(stream)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"raw {stream} rows must be a sequence")
    if any(not isinstance(row, Mapping) for row in value):
        raise ValueError(f"raw {stream} row must be an object")
    return list(value)


def _check_identity(
    row: Mapping[str, Any],
    *,
    expected: Any,
    source_sequence: int,
) -> None:
    required = {
        "run_id": expected.run_id,
        "history_id": expected.history_id,
        "question_id": expected.history_id,
        "episode_id": f"{expected.history_id}:{source_sequence}",
        "source_sequence": source_sequence,
        "method": "U0",
        "repeat_id": 0,
    }
    if any(row.get(name) != value for name, value in required.items()):
        raise ValueError("raw row common identity mismatch")


def _group_observability_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected: Any,
    expected_sequences: Sequence[int],
) -> dict[int, list[dict[str, Any]]]:
    grouped = {sequence: [] for sequence in expected_sequences}
    for raw in rows:
        row = validate_observability_record(raw)
        sequence = row.get("source_sequence")
        if sequence not in grouped:
            raise ValueError("raw source coverage contains an unexpected sequence")
        _check_identity(row, expected=expected, source_sequence=sequence)
        grouped[sequence].append(row)
    return grouped


def _singletons(
    grouped: Mapping[int, Sequence[dict[str, Any]]],
    *,
    label: str,
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for sequence, rows in grouped.items():
        if len(rows) != 1:
            raise ValueError(f"source sequence requires one unique {label} row")
        result[sequence] = dict(rows[0])
    return result


def _per_episode_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected: Any,
    expected_sequences: Sequence[int],
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw in rows:
        row = validate_raw_quality_evidence(raw)
        identity = row.get("identity")
        if not isinstance(identity, Mapping):
            raise ValueError("durable per_episode identity is missing")
        sequence = identity.get("source_sequence")
        if sequence not in expected_sequences:
            raise ValueError("durable per_episode source coverage mismatch")
        _check_identity(identity, expected=expected, source_sequence=sequence)
        if sequence in result:
            raise ValueError("source sequence requires one unique per_episode row")
        result[sequence] = row
    if tuple(sorted(result)) != tuple(expected_sequences):
        raise ValueError("durable per_episode source coverage mismatch")
    return result


def _projection_without_stream(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result.pop("stream", None)
    return result


def _canonical_history_aggregate(
    *,
    expected: Any,
    checkpoint: Mapping[str, Any],
    result: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild Level-1 and Level-2 solely from validated Level-0 rows."""

    raw_rows = evidence.get("raw_rows")
    if not isinstance(raw_rows, Mapping):
        raise ValueError("complete history evidence requires raw_rows")
    for stream in _RAW_ROW_STREAMS:
        if stream not in raw_rows:
            raise ValueError(f"complete history evidence is missing raw {stream}")
    expected_sequences = list(checkpoint["expected_sequences"])

    grouped = {
        stream: _group_observability_rows(
            _require_rows(raw_rows, stream),
            expected=expected,
            expected_sequences=expected_sequences,
        )
        for stream in _RAW_ROW_STREAMS
        if stream != "per_episode"
    }
    durable_episode = _per_episode_rows(
        _require_rows(raw_rows, "per_episode"),
        expected=expected,
        expected_sequences=expected_sequences,
    )
    queue_rows = _singletons(grouped["queue"], label="queue")
    graph_rows = _singletons(grouped["graph_work"], label="graph_work")

    derived_episode: list[dict[str, Any]] = []
    previous_graph: tuple[int, int] | None = None
    for sequence in expected_sequences:
        spans = grouped["spans"][sequence]
        if not spans:
            raise ValueError("raw spans source coverage mismatch")
        roots = [span for span in spans if span.get("phase") == "add-episode"]
        if len(roots) != 1:
            raise ValueError("source sequence requires exactly one add-episode root")
        for span in spans:
            start = _nonnegative_int(span.get("start_ns"), "span.start_ns")
            end = _nonnegative_int(span.get("end_ns"), "span.end_ns")
            if end < start or span.get("duration_ns") != end - start:
                raise ValueError("span duration is inconsistent")

        lifecycle: dict[str, dict[str, Any]] = {}
        for event in grouped["events"][sequence]:
            event_type = event.get("event_type")
            if event_type not in _LIFECYCLE_EVENTS or event_type in lifecycle:
                raise ValueError("source sequence lifecycle events are not unique")
            lifecycle[event_type] = event
        if tuple(sorted(lifecycle)) != tuple(sorted(_LIFECYCLE_EVENTS)):
            raise ValueError("source sequence lifecycle coverage is incomplete")
        if lifecycle["terminal"].get("status") != "published":
            raise ValueError("terminal lifecycle outcome is not published")

        queue = queue_rows[sequence]
        if queue.get("queue_status") != _SERIAL_BACKLOG_STATUS:
            raise ValueError("serial queue status is invalid")
        event_timestamp_fields = {
            "intent": "enqueue_ts_ns",
            "service_start": "service_start_ts_ns",
            "publication": "publication_ts_ns",
            "terminal": "terminal_ts_ns",
        }
        for event_type, queue_field in event_timestamp_fields.items():
            if lifecycle[event_type].get("timestamp_ns") != queue.get(queue_field):
                raise ValueError("event/queue timestamp mismatch")

        graph = graph_rows[sequence]
        graph_counts = {
            name: _nonnegative_int(graph.get(name), f"graph_work.{name}")
            for name in (
                "nodes_before",
                "nodes_after",
                "relationships_before",
                "relationships_after",
            )
        }
        current_before = (
            graph_counts["nodes_before"],
            graph_counts["relationships_before"],
        )
        if previous_graph is None and current_before != (0, 0):
            raise ValueError("fresh graph prefix must start empty")
        if previous_graph is not None and current_before != previous_graph:
            raise ValueError("graph prefix continuity mismatch")
        previous_graph = (
            graph_counts["nodes_after"],
            graph_counts["relationships_after"],
        )

        views = project_operation_views(spans)
        for stream in ("llm", "embedding", "db"):
            expected_sidecar = [
                _projection_without_stream(row) for row in views[stream]
            ]
            actual_sidecar = [
                _projection_without_stream(row)
                for row in grouped[stream][sequence]
            ]
            if actual_sidecar != expected_sidecar:
                raise ValueError(f"{stream} sidecar projection mismatch")

        identity = ObservabilityIdentity(
            run_id=expected.run_id,
            history_id=expected.history_id,
            question_id=expected.history_id,
            episode_id=f"{expected.history_id}:{sequence}",
            source_sequence=sequence,
            method="U0",
            repeat_id=0,
        )
        canonical = derive_episode_metrics(
            identity=identity,
            spans=spans,
            queue_event=queue,
            graph_work=graph_counts,
        )
        if durable_episode[sequence] != canonical:
            raise ValueError("durable per_episode row differs from Level-0 derivation")
        derived_episode.append(canonical)

    final_namespace = result.get("final_namespace_observation")
    if not isinstance(final_namespace, Mapping):
        raise ValueError("final namespace observation is missing")
    observed_final = (
        _nonnegative_int(final_namespace.get("node_count"), "final_namespace.node_count"),
        _nonnegative_int(
            final_namespace.get("relationship_count"),
            "final_namespace.relationship_count",
        ),
    )
    if previous_graph != observed_final:
        raise ValueError("final namespace counts differ from Level-0 graph state")

    quality = result.get("quality")
    if not isinstance(quality, Mapping):
        raise ValueError("history quality is missing")
    validate_raw_quality_evidence(quality)
    retrieval = quality.get("retrieval")
    if not isinstance(retrieval, Mapping):
        raise ValueError("history retrieval quality is missing")
    quality_projection = {
        "qa_accuracy": _bounded_fraction(quality.get("qa_accuracy"), "qa_accuracy"),
        "evidence_recall_at_10": _bounded_fraction(
            retrieval.get("evidence_recall_at_10"), "evidence_recall_at_10"
        ),
    }
    return aggregate_history_metrics(
        identity=ObservabilityIdentity(
            run_id=expected.run_id,
            history_id=expected.history_id,
            question_id=expected.history_id,
            episode_id=f"{expected.history_id}:0",
            source_sequence=0,
            method="U0",
            repeat_id=0,
        ),
        episode_metrics=derived_episode,
        quality=quality_projection,
        direct_violations=0,
        serial_baseline=True,
    )


def _unsealed_report(
    *,
    run_id: str,
    eligibility: bool,
    decision: str | None,
    ineligibility_reasons: Sequence[str],
    decision_reasons: Sequence[str],
    per_history: Sequence[Mapping[str, Any]],
    macro_descriptive: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": NATIVE_BASELINE_N3_SCHEMA,
        "run_id": run_id,
        "method": "U0",
        "repeat_id": 0,
        "aggregation_unit": "history_macro_equal_weight",
        "target_histories": list(DEVELOPMENT_HISTORIES),
        "eligibility": eligibility,
        "ineligibility_reasons": list(ineligibility_reasons),
        "decision": decision,
        "decision_reasons": list(decision_reasons),
        "successful_goodput_unit": _GOODPUT_UNIT,
        "per_history": [dict(row) for row in per_history],
        "macro_descriptive": dict(macro_descriptive or {}),
        "secondary_metrics": {
            "p99_freshness_ns": (
                dict(macro_descriptive["p99_freshness_ns"])
                if macro_descriptive and "p99_freshness_ns" in macro_descriptive
                else None
            ),
            "max_backlog": None,
            "max_backlog_status": _SERIAL_BACKLOG_STATUS,
        },
        "scientific_scope": "DESCRIPTIVE_DEVELOPMENT_SCREEN_ONLY",
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def reduce_native_baseline_n3(
    *,
    run_id: str,
    history_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify and reduce the fixed ordered-four U0 history evidence.

    Missing or still-running evidence is an ordinary ineligible state and
    cannot authorize a decision.  Structurally invalid, reordered, unsealed,
    or tampered evidence fails closed with ``ValueError``.
    """

    plan = build_native_baseline_plan(run_id)
    if isinstance(history_evidence, (str, bytes)) or not isinstance(
        history_evidence, Sequence
    ):
        raise ValueError("history_evidence must be a sequence")
    if len(history_evidence) != len(DEVELOPMENT_HISTORIES):
        return _unsealed_report(
            run_id=run_id,
            eligibility=False,
            decision=None,
            ineligibility_reasons=["FIXED_FOUR_HISTORY_EVIDENCE_INCOMPLETE"],
            decision_reasons=[],
            per_history=[],
        )

    observed_order: list[str] = []
    for item in history_evidence:
        if not isinstance(item, Mapping):
            raise ValueError("history evidence row must be an object")
        checkpoint = item.get("checkpoint")
        result = item.get("history_result")
        if not isinstance(checkpoint, Mapping) or not isinstance(result, Mapping):
            raise ValueError("history evidence requires checkpoint and history_result")
        checkpoint_history = checkpoint.get("history_id")
        result_history = result.get("history_id")
        if checkpoint_history != result_history:
            raise ValueError("checkpoint/result history identity mismatch")
        observed_order.append(str(result_history))
    if tuple(observed_order) != DEVELOPMENT_HISTORIES:
        raise ValueError("history evidence must use the fixed ordered four histories")

    verified_rows: list[
        tuple[Any, dict[str, Any], dict[str, Any], Mapping[str, Any]]
    ] = []
    incomplete: list[str] = []
    for expected, evidence in zip(plan.histories, history_evidence, strict=True):
        checkpoint = verify_checkpoint(evidence["checkpoint"])
        if (
            checkpoint["run_id"] != expected.run_id
            or checkpoint["history_id"] != expected.history_id
            or checkpoint["namespace"] != expected.namespace
        ):
            raise ValueError("checkpoint does not match the fixed Native plan")
        result = verify_history_result(
            evidence["history_result"], expected_plan=expected
        )
        complete_prefix = (
            checkpoint["completed_sequences"] == checkpoint["expected_sequences"]
        )
        if checkpoint["status"] != "completed" or not complete_prefix:
            incomplete.append(f"CHECKPOINT_INCOMPLETE:{expected.history_id}")
        verified_rows.append((expected, checkpoint, result, evidence))
    if incomplete:
        return _unsealed_report(
            run_id=run_id,
            eligibility=False,
            decision=None,
            ineligibility_reasons=incomplete,
            decision_reasons=[],
            per_history=[],
        )

    per_history: list[dict[str, Any]] = []
    decision_reasons: list[str] = []
    for expected, checkpoint, result, evidence in verified_rows:
        aggregate = _canonical_history_aggregate(
            expected=expected,
            checkpoint=checkpoint,
            result=result,
            evidence=evidence,
        )
        durable_aggregate = result["aggregate"]
        durable_metrics = durable_aggregate.get("metrics")
        if not isinstance(durable_metrics, Mapping):
            raise ValueError("history aggregate metrics are missing")
        if durable_metrics.get("successful_goodput_unit") != _GOODPUT_UNIT:
            raise ValueError("successful goodput unit must be episodes_per_second")
        if (
            durable_metrics.get("max_backlog") is not None
            or durable_metrics.get("max_backlog_status") != _SERIAL_BACKLOG_STATUS
        ):
            raise ValueError("serial max_backlog must remain not applicable")
        if durable_aggregate != aggregate:
            raise ValueError("durable history aggregate differs from Level-0 derivation")
        metrics = aggregate.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ValueError("history aggregate metrics are missing")
        missing_primary = [name for name in PRIMARY_METRICS if name not in metrics]
        missing_secondary = [
            name for name in SECONDARY_METRICS if name not in metrics
        ]
        if missing_primary or missing_secondary:
            raise ValueError("history aggregate metric vector is incomplete")

        qa = _bounded_fraction(metrics["qa_accuracy"], "qa_accuracy")
        recall = _bounded_fraction(
            metrics["evidence_recall_at_10"], "evidence_recall_at_10"
        )
        direct_violations = _nonnegative_int(
            metrics["direct_violations"], "direct_violations"
        )
        p95 = _nonnegative_int(metrics["p95_freshness_ns"], "p95_freshness_ns")
        p99 = _nonnegative_int(metrics["p99_freshness_ns"], "p99_freshness_ns")
        if p99 < p95:
            raise ValueError("p99_freshness_ns precedes p95_freshness_ns")
        goodput = _nonnegative_number(
            metrics["successful_goodput"], "successful_goodput"
        )
        makespan = _nonnegative_int(metrics["makespan_ns"], "makespan_ns")
        if metrics.get("successful_goodput_unit") != _GOODPUT_UNIT:
            raise ValueError("successful goodput unit must be episodes_per_second")
        if (
            metrics.get("max_backlog") is not None
            or metrics.get("max_backlog_status") != _SERIAL_BACKLOG_STATUS
        ):
            raise ValueError("serial max_backlog must remain not applicable")

        expected_episode_count = len(checkpoint["expected_sequences"])
        episode_count = _nonnegative_int(
            aggregate.get("episode_count"), "aggregate.episode_count"
        )
        namespace = result.get("final_namespace_observation")
        if not isinstance(namespace, Mapping):
            raise ValueError("final namespace observation is missing")
        if (
            episode_count != expected_episode_count
            or namespace.get("episode_count") != expected_episode_count
            or namespace.get("episode_names_match_expected") is not True
        ):
            raise ValueError("history completion accounting is inconsistent")

        quality = result["quality"]
        retrieval = quality.get("retrieval") if isinstance(quality, Mapping) else None
        if not isinstance(retrieval, Mapping):
            raise ValueError("history retrieval quality is missing")
        if quality.get("qa_accuracy") != metrics["qa_accuracy"] or retrieval.get(
            "evidence_recall_at_10"
        ) != metrics["evidence_recall_at_10"]:
            raise ValueError("quality and aggregate metric vectors disagree")

        graph_total = _numeric_total(aggregate.get("graph_work"), "graph_work")
        work_total = _numeric_total(aggregate.get("work_volume"), "work_volume")
        history_id = expected.history_id
        if quality.get("status") != "SUCCESS":
            decision_reasons.append(f"QUALITY_NOT_SUCCESS:{history_id}")
        if direct_violations != 0:
            decision_reasons.append(f"DIRECT_VIOLATIONS_PRESENT:{history_id}")
        if graph_total <= 0:
            decision_reasons.append(f"GRAPH_WORK_EMPTY:{history_id}")
        if work_total <= 0:
            decision_reasons.append(f"SYSTEM_WORK_EMPTY:{history_id}")

        per_history.append(
            {
                "history_id": history_id,
                "episode_count": episode_count,
                "headline_metrics": {
                    "qa_accuracy": qa,
                    "evidence_recall_at_10": recall,
                    "direct_violations": direct_violations,
                    "p95_freshness_ns": p95,
                    "successful_goodput": goodput,
                    "makespan_ns": makespan,
                },
                "secondary_metrics": {
                    "p99_freshness_ns": p99,
                    "max_backlog": None,
                    "max_backlog_status": _SERIAL_BACKLOG_STATUS,
                },
                "quality_status": quality.get("status"),
                "work_volume": dict(aggregate["work_volume"]),
                "graph_work": dict(aggregate["graph_work"]),
                "graph_work_total": graph_total,
                "system_work_total": work_total,
            }
        )

    if not any(row["headline_metrics"]["qa_accuracy"] > 0 for row in per_history):
        decision_reasons.append("ALL_QA_ACCURACY_ZERO")
    if not any(
        row["headline_metrics"]["evidence_recall_at_10"] > 0
        for row in per_history
    ):
        decision_reasons.append("ALL_EVIDENCE_RECALL_AT_10_ZERO")

    macro_names = (*PRIMARY_METRICS, "p99_freshness_ns")
    macro_descriptive = {
        name: _descriptive(
            [
                (
                    row["headline_metrics"][name]
                    if name in row["headline_metrics"]
                    else row["secondary_metrics"][name]
                )
                for row in per_history
            ]
        )
        for name in macro_names
    }
    decision = (
        "HEALTHY_FOR_NEXT_BASELINE"
        if not decision_reasons
        else "DIAGNOSE_BEFORE_METHODS"
    )
    return _unsealed_report(
        run_id=run_id,
        eligibility=True,
        decision=decision,
        ineligibility_reasons=[],
        decision_reasons=decision_reasons,
        per_history=per_history,
        macro_descriptive=macro_descriptive,
    )


__all__ = ["NATIVE_BASELINE_N3_SCHEMA", "reduce_native_baseline_n3"]
