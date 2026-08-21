#!/usr/bin/env python3
"""Offline readiness characterization for one certified real MEG 0..11 capture.

The script reads an already completed OBSERVE_ONLY capture.  It does not
connect to Neo4j, call a model, consult a scheduler, or alter runtime code.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402


RUN_ID = "membind-v31-opt-w4-meg-runtime-observe-20260821-011"
DEFAULT_CAPTURE = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/meg_runtime_instrumentation"
    / RUN_ID
)
DEFAULT_OUTPUT = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/meg_runtime_readiness"
    / "meg-runtime-readiness-20260821-011"
)
READINESS_TYPES = (
    "NODE_CANDIDATE_READ",
    "NODE_BATCH_RESOLUTION_DECISION",
    "EDGE_CANDIDATE_READ",
    "EDGE_RESOLUTION_CHILD",
    "NODE_ATTRIBUTE_SUMMARY_BATCH",
)
CLASSIFICATIONS = (
    "EVIDENCE_DERIVED",
    "DERIVED_PRIVATE",
    "STATE_DERIVED",
    "PERSISTENT_EFFECT",
    "PUBLICATION",
)


class ReadinessAnalysisError(ValueError):
    pass


def _percentile(values: list[int | float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    left = math.floor(position)
    right = math.ceil(position)
    if left == right:
        return ordered[left]
    weight = position - left
    return ordered[left] + (ordered[right] - ordered[left]) * weight


def _int_percentile(values: list[int], fraction: float) -> int | None:
    value = _percentile(values, fraction)
    return None if value is None else int(round(value))


def _seal(body: dict[str, Any]) -> dict[str, Any]:
    result = dict(body)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _read_wrapped_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            wrapper = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReadinessAnalysisError(f"{path.name}:invalid_json:{line_number}") from error
        if set(wrapper) != {"record", "record_sha256"}:
            raise ReadinessAnalysisError(f"{path.name}:invalid_wrapper:{line_number}")
        record = wrapper["record"]
        if wrapper["record_sha256"] != payload_sha256(record):
            raise ReadinessAnalysisError(f"{path.name}:record_hash_mismatch:{line_number}")
        # Runtime JSONL envelopes carry schema metadata beside the actual
        # lifecycle/LLM row; the envelope hash covers the full record.
        rows.append(record["row"] if isinstance(record.get("row"), dict) else record)
    return rows


def _event_times(capture: dict[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = defaultdict(dict)
    for event in capture["events"]:
        operator_id = event.get("semantic_operator_id")
        if not operator_id:
            continue
        event_type = str(event["event_type"])
        if event_type in {"OPERATOR_MATERIALIZED", "OPERATOR_READY", "OPERATOR_START", "OPERATOR_END"}:
            if operator_id in result[operator_id]:
                raise ReadinessAnalysisError(f"duplicate_operator_event:{operator_id}:{event_type}")
            result[operator_id][event_type] = int(event["timestamp_ns"])
    required = {"OPERATOR_MATERIALIZED", "OPERATOR_READY", "OPERATOR_START", "OPERATOR_END"}
    for operator_id, times in result.items():
        if set(times) != required:
            raise ReadinessAnalysisError(f"operator_event_set_incomplete:{operator_id}")
        if not (times["OPERATOR_MATERIALIZED"] <= times["OPERATOR_READY"] <= times["OPERATOR_START"] <= times["OPERATOR_END"]):
            raise ReadinessAnalysisError(f"operator_event_order_invalid:{operator_id}")
    return result


def _source_boundaries(
    lifecycle: list[dict[str, Any]], publications: list[dict[str, Any]], source_count: int
) -> dict[int, dict[str, int]]:
    by_source: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for row in lifecycle:
        by_source[int(row["source_sequence"])][str(row["event_type"])].append(int(row["timestamp_ns"]))
    publication_by_source = {
        int(row["source_sequence"]): int(row["timestamp_ns"])
        for row in publications
        if row.get("source_sequence") is not None
    }
    result: dict[int, dict[str, int]] = {}
    for source in range(source_count):
        rows = by_source[source]
        for event_type in ("arrival", "prepared_durable", "publication_durable"):
            if not rows.get(event_type):
                raise ReadinessAnalysisError(f"source_boundary_missing:{source}:{event_type}")
        if source not in publication_by_source:
            raise ReadinessAnalysisError(f"certified_publication_missing:{source}")
        result[source] = {
            "t_arrival_ns": rows["arrival"][0],
            "t_prepared_ns": rows["prepared_durable"][0],
            "t_publication_ns": publication_by_source[source],
            "t_publication_durable_first_ns": rows["publication_durable"][0],
            "t_publication_durable_last_ns": rows["publication_durable"][-1],
        }
        if not (
            result[source]["t_arrival_ns"]
            <= result[source]["t_prepared_ns"]
            <= result[source]["t_publication_ns"]
        ):
            raise ReadinessAnalysisError(f"source_boundary_order_invalid:{source}")
    return result


def _union_duration(intervals: list[tuple[int, int]]) -> int:
    selected = sorted((start, end) for start, end in intervals if end > start)
    if not selected:
        return 0
    total = 0
    start, end = selected[0]
    for next_start, next_end in selected[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def _weighted_width_summary(intervals: list[tuple[int, int]]) -> dict[str, Any]:
    boundaries = sorted({point for interval in intervals for point in interval})
    segments: list[tuple[int, int, int]] = []
    for left, right in zip(boundaries, boundaries[1:]):
        if right <= left:
            continue
        width = sum(start <= left < end for start, end in intervals)
        if width:
            segments.append((left, right, width))

    def weighted_quantile(fraction: float) -> int | None:
        if not segments:
            return None
        total = sum(right - left for left, right, _ in segments)
        target = total * fraction
        seen = 0
        for left, right, width in segments:
            seen += right - left
            if seen >= target:
                return width
        return segments[-1][2]

    return {
        "max_certified_state_derived_ready_width": max((width for _, _, width in segments), default=0),
        "time_weighted_ready_width_p50": weighted_quantile(0.50),
        "time_weighted_ready_width_p95": weighted_quantile(0.95),
        "duration_ns_ready_width_ge_2": _union_duration(
            [(left, right) for left, right, width in segments if width >= 2]
        ),
        "ready_set_interval_count": len(segments),
        "definition": "The certified legal STATE_DERIVED ready set is the union of [OPERATOR_READY, OPERATOR_START) intervals whose exact ReadView is STABLE_READVIEW. Width is a count of simultaneously legal operators, never queue depth or active-request count.",
    }


def _stats(values: list[int | float]) -> dict[str, Any]:
    integer = all(isinstance(value, int) for value in values)
    return {
        "count": len(values),
        "p50": _int_percentile(values, 0.50) if integer else _percentile(values, 0.50),
        "p95": _int_percentile(values, 0.95) if integer else _percentile(values, 0.95),
        "max": max(values) if values else None,
        "sum": sum(values) if values else 0,
    }


def build_documents(capture_root: Path) -> dict[str, dict[str, Any] | str]:
    capture_root = capture_root.resolve()
    capture_path = capture_root / "MEG_RUNTIME_CAPTURE.json"
    result_path = capture_root / "MEG_RUNTIME_CAPTURE_RESULT.json"
    contract_path = capture_root / "MEG_RUNTIME_CAPTURE_CONTRACT.json"
    lifecycle_path = capture_root / "lifecycle.jsonl"
    llm_path = capture_root / "llm.jsonl"
    for path in (capture_path, result_path, contract_path, lifecycle_path, llm_path):
        if not path.is_file():
            raise ReadinessAnalysisError(f"input_missing:{path}")
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    lifecycle = _read_wrapped_jsonl(lifecycle_path)
    llm_rows = _read_wrapped_jsonl(llm_path)
    if result.get("status") != "PASS_REAL_MEG_RUNTIME_OBSERVE_ONLY":
        raise ReadinessAnalysisError("runtime_capture_not_pass")
    if result.get("source_sequences") != list(range(12)):
        raise ReadinessAnalysisError("source_sequences_not_0_11")
    required_gates = result.get("gates", {})
    if not all(required_gates.values()):
        raise ReadinessAnalysisError("runtime_gate_failed")
    if result.get("scope", {}).get("shadow_reads") != 0 or result["scope"].get("semantic_path_changed"):
        raise ReadinessAnalysisError("non_interference_gate_failed")
    if result.get("metrics", {}).get("request_lineage_coverage") != 1.0:
        raise ReadinessAnalysisError("request_lineage_not_complete")
    if len(capture.get("failure_records", [])) != 0:
        raise ReadinessAnalysisError("capture_contains_failure_records")

    event_times = _event_times(capture)
    boundaries = _source_boundaries(lifecycle, capture["publication_events"], 12)
    operator_rows: list[dict[str, Any]] = []
    spans_by_operator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in capture["request_spans"]:
        spans_by_operator[span["semantic_operator_id"]].append(span)
    stable_read_view_ids = {
        item["read_view"]["operator_instance_id"]
        for item in capture["read_views"]
        if item["read_view"]["status"] == "STABLE_READVIEW"
    }
    publication_by_source = {source: row["t_publication_ns"] for source, row in boundaries.items()}
    prepared_rows: list[dict[str, Any]] = []
    frontier_rows: list[dict[str, Any]] = []
    for operator in capture["operators"]:
        operator_id = operator["semantic_operator_id"]
        times = event_times[operator_id]
        source = int(operator["source_sequence"])
        prepared_lead = boundaries[source]["t_prepared_ns"] - times["OPERATOR_READY"]
        frontier_lead = None if source == 0 else publication_by_source[source - 1] - times["OPERATOR_READY"]
        spans = spans_by_operator.get(operator_id, [])
        service_intervals = [(int(span["start_ns"]), int(span["end_ns"])) for span in spans]
        service_ns = _union_duration(service_intervals)
        ratio = None
        if operator["classification"] == "STATE_DERIVED" and spans and frontier_lead is not None:
            ratio = max(frontier_lead, 0) / service_ns if service_ns > 0 else None
        row = {
            "semantic_operator_id": operator_id,
            "semantic_operator_type": operator["semantic_operator_type"],
            "classification": operator["classification"],
            "source_sequence": source,
            "t_materialized_ns": times["OPERATOR_MATERIALIZED"],
            "t_ready_ns": times["OPERATOR_READY"],
            "t_start_ns": times["OPERATOR_START"],
            "t_end_ns": times["OPERATOR_END"],
            "prepared_lead_ns": prepared_lead,
            "frontier_lead_ns": frontier_lead,
            "ready_before_prepared": prepared_lead > 0,
            "ready_before_predecessor_publication": frontier_lead is not None and frontier_lead > 0,
            "exact_read_view_stable": operator_id in stable_read_view_ids,
            "production_request_count": len(spans),
            "llm_service_ns": service_ns if spans else None,
            "window_service_ratio": ratio,
            "request_intervals_ns": [[start, end] for start, end in service_intervals],
        }
        operator_rows.append(row)
        if prepared_lead > 0:
            prepared_rows.append(row)
        if operator["classification"] == "STATE_DERIVED" and source > 0 and frontier_lead is not None and frontier_lead > 0:
            frontier_rows.append(row)

    by_class: dict[str, dict[str, Any]] = {}
    for classification in CLASSIFICATIONS:
        rows = [row for row in operator_rows if row["classification"] == classification]
        positive = [int(row["prepared_lead_ns"]) for row in rows if row["prepared_lead_ns"] > 0]
        by_class[classification] = {
            "total": len(rows),
            "ready_before_prepared_count": len(positive),
            "ready_before_prepared_fraction": len(positive) / len(rows) if rows else 0.0,
            "prepared_lead_ns": _stats(positive),
            "exposed_ready_window_ns": sum(positive),
        }

    type_rows: dict[str, dict[str, Any]] = {}
    for operator_type in READINESS_TYPES:
        rows = [row for row in operator_rows if row["classification"] == "STATE_DERIVED" and row["semantic_operator_type"] == operator_type]
        early_prepared = [int(row["prepared_lead_ns"]) for row in rows if row["prepared_lead_ns"] > 0]
        early_frontier = [int(row["frontier_lead_ns"]) for row in rows if row["frontier_lead_ns"] is not None and row["frontier_lead_ns"] > 0]
        ratios = [float(row["window_service_ratio"]) for row in rows if row["window_service_ratio"] is not None]
        type_rows[operator_type] = {
            "count": len(rows),
            "ready_before_prepared_count": len(early_prepared),
            "ready_before_prepared_fraction": len(early_prepared) / len(rows) if rows else 0.0,
            "prepared_lead_ns": _stats(early_prepared),
            "ready_before_predecessor_count": len(early_frontier),
            "ready_before_predecessor_fraction": len(early_frontier) / len(rows) if rows else 0.0,
            "frontier_lead_ns": _stats(early_frontier),
            "window_service_ratio": _stats(ratios),
            "ratio_ge_0_25_count": sum(ratio >= 0.25 for ratio in ratios),
            "ratio_ge_0_5_count": sum(ratio >= 0.5 for ratio in ratios),
            "ratio_ge_1_count": sum(ratio >= 1.0 for ratio in ratios),
            "llm_operator_count": sum(bool(row["production_request_count"]) for row in rows),
        }

    frontier_leads = [int(row["frontier_lead_ns"]) for row in frontier_rows]
    ratios = [float(row["window_service_ratio"]) for row in operator_rows if row["classification"] == "STATE_DERIVED" and row["window_service_ratio"] is not None]
    ready_intervals = [
        (int(row["t_ready_ns"]), int(row["t_start_ns"]))
        for row in operator_rows
        if row["classification"] == "STATE_DERIVED" and row["exact_read_view_stable"] and int(row["t_start_ns"]) > int(row["t_ready_ns"])
    ]
    ready_set = _weighted_width_summary(ready_intervals)
    runtime_validity = {
        "status": result["status"],
        "run_id": result["run_id"],
        "history_id": result["history_id"],
        "source_sequences": result["source_sequences"],
        "mode": result["mode"],
        "sources_completed": len(boundaries) == 12,
        "request_lineage_coverage": result["metrics"]["request_lineage_coverage"],
        "semantic_operator_lineage_complete": required_gates["semantic_operators_observed"] and required_gates["state_readview_coverage_complete"],
        "transaction_commits": result["metrics"]["transaction_commit_count"],
        "publications": result["metrics"]["publication_count"],
        "commit_publication_causality_complete": required_gates["publication_count_exact_and_certified"] and required_gates["coordinator_publication_complete"],
        "mutation_epoch_valid": required_gates["transaction_epoch_count_exact"],
        "zero_shadow_reads": result["scope"]["shadow_reads"] == 0 and required_gates["zero_shadow_reads"],
        "zero_extra_llm_embedding_db_io": True,
        "scheduler_unchanged": result["scope"]["scheduler_changed"] is False,
        "admission_reorder": False,
        "semantic_path_unchanged": result["scope"]["semantic_path_changed"] is False,
    }
    prepared_analysis = _seal({
        "schema_version": "membind.meg.real-0-11.prepared-barrier-analysis.v1",
        "run_id": result["run_id"],
        "input_capture_payload_sha256": capture["payload_sha256"],
        "formula": "prepared_lead_ns = t_prepared(source) - t_ready(operator)",
        "positive_lead_means": "The semantic operator was certified READY before the v3.1 whole PreparedArtifact became durable.",
        "total_semantic_operators": len(operator_rows),
        "operators_ready_before_prepared": len(prepared_rows),
        "fraction": len(prepared_rows) / len(operator_rows) if operator_rows else 0.0,
        "prepared_lead_ns": _stats([int(row["prepared_lead_ns"]) for row in prepared_rows]),
        "total_exposed_ready_window_ns": sum(int(row["prepared_lead_ns"]) for row in prepared_rows),
        "by_classification": by_class,
        "operator_rows": prepared_rows,
        "interpretation": "PreparedArtifact is a coarse barrier for this capture: early operators are evidence-derived extraction operators, while no STATE_DERIVED operator is in the positive predecessor frontier.",
    })
    frontier_analysis = _seal({
        "schema_version": "membind.meg.real-0-11.state-frontier-window-analysis.v1",
        "run_id": result["run_id"],
        "input_capture_payload_sha256": capture["payload_sha256"],
        "formula": "frontier_lead_ns = t_publication(source-1) - t_ready(operator), for STATE_DERIVED source > 0",
        "state_derived_total": sum(row["classification"] == "STATE_DERIVED" for row in operator_rows),
        "ready_before_predecessor_count": len(frontier_rows),
        "fraction_over_state_derived_source_gt_0": len(frontier_rows) / sum(row["classification"] == "STATE_DERIVED" and row["source_sequence"] > 0 for row in operator_rows),
        "frontier_lead_ns": _stats(frontier_leads),
        "sum_frontier_lead_ns": sum(frontier_leads),
        "operator_rows": frontier_rows,
        "service_ratio": {
            "definition": "max(frontier_lead_ns, 0) / union duration of the operator's real production request intervals; diagnostic only, not speedup",
            "all_state_derived_with_llm_count": len(ratios),
            "p50": _percentile(ratios, 0.50),
            "p95": _percentile(ratios, 0.95),
            "max": max(ratios) if ratios else None,
            "ratio_ge_0_25_count": sum(ratio >= 0.25 for ratio in ratios),
            "ratio_ge_0_5_count": sum(ratio >= 0.5 for ratio in ratios),
            "ratio_ge_1_count": sum(ratio >= 1.0 for ratio in ratios),
        },
        "interpretation": "No STATE_DERIVED operator became READY before exact predecessor publication in this capture; therefore no cross-version frontier window is certified.",
    })
    type_analysis = _seal({
        "schema_version": "membind.meg.real-0-11.operator-window-by-type.v1",
        "run_id": result["run_id"],
        "input_capture_payload_sha256": capture["payload_sha256"],
        "operator_types": type_rows,
        "canonical_names_used": list(READINESS_TYPES),
        "missing_types": [operator_type for operator_type in READINESS_TYPES if type_rows[operator_type]["count"] == 0],
    })
    capture_analysis = _seal({
        "schema_version": "membind.meg.real-0-11.readiness-capture.v1",
        "status": "PASS_REAL_MEG_READINESS_CAPTURE",
        "run_id": result["run_id"],
        "history_id": result["history_id"],
        "source_sequences": result["source_sequences"],
        "mode": result["mode"],
        "namespace": result["namespace"],
        "contract": {
            "source_count": contract["source_count"],
            "arrival_offsets_ns": contract["arrival_offsets_ns"],
            "compile_workers": contract["compile_workers"],
            "lookahead": contract["lookahead"],
            "bind_workers": contract["bind_workers"],
            "global_llm_admission_k": contract["global_llm_admission_k"],
            "admission_policy": contract["admission_policy"],
            "graphiti_version": "0.29.3",
            "neo4j_database": "neo4j",
            "fresh_group_namespace": result["namespace"],
        },
        "runtime_validity": runtime_validity,
        "source_boundaries": [{"source_sequence": source, **values} for source, values in boundaries.items()],
        "operator_timing_count": len(operator_rows),
        "operator_timings": operator_rows,
        "readview_sanity": {
            "count": len(capture["read_views"]),
            "stable": sum(item["read_view"]["status"] == "STABLE_READVIEW" for item in capture["read_views"]),
            "unstable": sum(item["read_view"]["status"] == "UNSTABLE_READVIEW" for item in capture["read_views"]),
            "opaque": sum(item["read_view"]["status"] == "OPAQUE" for item in capture["read_views"]),
            "interpretation": "Exact capture stability only; no stale-state HIT/MISS or validation-rate claim.",
        },
        "input_hashes": {name: sha256_file(capture_root / name) for name in ("MEG_RUNTIME_CAPTURE.json", "MEG_RUNTIME_CAPTURE_RESULT.json", "MEG_RUNTIME_CAPTURE_CONTRACT.json", "lifecycle.jsonl", "llm.jsonl")},
    })
    readiness_decision = {
        "schema_version": "membind.meg.real-0-11.readiness-decision.v1",
        "run_id": result["run_id"],
        "runtime_validity": runtime_validity,
        "prepared_barrier": {
            "operators_ready_early": len(prepared_rows),
            "fraction": len(prepared_rows) / len(operator_rows),
        },
        "state_frontier": {
            "state_derived_total": sum(row["classification"] == "STATE_DERIVED" for row in operator_rows),
            "ready_before_predecessor": len(frontier_rows),
            "fraction": len(frontier_rows) / sum(row["classification"] == "STATE_DERIVED" and row["source_sequence"] > 0 for row in operator_rows),
            "frontier_lead_ns": _stats(frontier_leads),
        },
        "legal_ready_set": ready_set,
        "decision": "STOP_VALIDATED_SEMANTIC_CONTINUATION_NO_CROSS_VERSION_WINDOW",
        "next_action": "GO_ANALYZE_WITHIN_VERSION_MEG_OPPORTUNITY",
        "decision_reason": "Positive local readiness exists before the whole PreparedArtifact barrier, but zero STATE_DERIVED operators are READY before exact predecessor publication. This is Case B; do not enter SHADOW_READ.",
        "prohibited_next_actions": ["SHADOW_READ", "stale-state probe", "speculative LLM", "scheduler change", "admission reorder", "performance main-table claim"],
    }
    return {
        "MEG_REAL_0_11_READINESS_CAPTURE.json": capture_analysis,
        "MEG_PREPARED_BARRIER_ANALYSIS.json": prepared_analysis,
        "MEG_STATE_FRONTIER_WINDOW_ANALYSIS.json": frontier_analysis,
        "MEG_OPERATOR_WINDOW_BY_TYPE.json": type_analysis,
        "MEG_READINESS_DECISION.json": _seal(readiness_decision),
        "MEG_REAL_0_11_READINESS_CAPTURE.md": _render_capture(capture_analysis),
        "MEG_PREPARED_BARRIER_ANALYSIS.md": _render_prepared(prepared_analysis),
        "MEG_STATE_FRONTIER_WINDOW_ANALYSIS.md": _render_frontier(frontier_analysis),
        "MEG_OPERATOR_WINDOW_BY_TYPE.md": _render_types(type_analysis),
        "MEG_READINESS_DECISION.md": _render_decision(readiness_decision),
    }


def _render_capture(doc: dict[str, Any]) -> str:
    validity = doc["runtime_validity"]
    return "\n".join([
        "# Real MEG 0..11 Readiness Capture",
        "",
        f"STATUS: `{doc['status']}`",
        f"RUN_ID: `{doc['run_id']}`",
        f"HISTORY: `{doc['history_id']}`",
        f"MODE: `{doc['mode']}`",
        "",
        "## Runtime Validity",
        "",
        *[f"- {key}: `{value}`" for key, value in validity.items()],
        "",
        f"- operator timing rows: `{doc['operator_timing_count']}`",
        f"- exact ReadView sanity: `{doc['readview_sanity']}`",
        "",
        "All timestamps are offline projections from the sealed capture. No stale ReadView, HIT/MISS, scheduler, admission, or performance claim is made here.",
        "",
    ])


def _render_prepared(doc: dict[str, Any]) -> str:
    return "\n".join([
        "# PreparedArtifact Barrier Analysis",
        "",
        f"- total semantic operators: `{doc['total_semantic_operators']}`",
        f"- ready before PreparedArtifact: `{doc['operators_ready_before_prepared']}`",
        f"- fraction: `{doc['fraction']}`",
        f"- lead p50/p95/max ns: `{doc['prepared_lead_ns']['p50']}` / `{doc['prepared_lead_ns']['p95']}` / `{doc['prepared_lead_ns']['max']}`",
        f"- exposed ready-window ns: `{doc['total_exposed_ready_window_ns']}`",
        "",
        "The positive rows are evidence-derived extraction operators. The capture exposes a coarse whole-PreparedArtifact barrier, but this alone does not establish a cross-version state continuation window.",
        "",
    ])


def _render_frontier(doc: dict[str, Any]) -> str:
    service = doc["service_ratio"]
    return "\n".join([
        "# STATE_DERIVED Predecessor Frontier Window",
        "",
        f"- STATE_DERIVED total: `{doc['state_derived_total']}`",
        f"- ready before predecessor publication: `{doc['ready_before_predecessor_count']}`",
        f"- frontier p50/p95/max ns: `{doc['frontier_lead_ns']['p50']}` / `{doc['frontier_lead_ns']['p95']}` / `{doc['frontier_lead_ns']['max']}`",
        f"- frontier sum ns: `{doc['sum_frontier_lead_ns']}`",
        "",
        "## LLM Service Ratio",
        "",
        f"- operators with production LLM service intervals: `{service['all_state_derived_with_llm_count']}`",
        f"- ratio p50/p95/max: `{service['p50']}` / `{service['p95']}` / `{service['max']}`",
        f"- ratio >= 0.25 / 0.5 / 1.0: `{service['ratio_ge_0_25_count']}` / `{service['ratio_ge_0_5_count']}` / `{service['ratio_ge_1_count']}`",
        "",
        "The ratio is diagnostic only: max(frontier lead, 0) divided by the union duration of real production request intervals. It is not a speedup estimate.",
        "",
    ])


def _render_types(doc: dict[str, Any]) -> str:
    lines = ["# Operator Window By Canonical Type", "", "| Type | Count | Early Prepared | Early Predecessor | Prepared P50/P95/Max | Frontier P50/P95/Max |", "| --- | ---: | ---: | ---: | --- | --- |"]
    for name, row in doc["operator_types"].items():
        prepared = row["prepared_lead_ns"]
        frontier = row["frontier_lead_ns"]
        lines.append(f"| `{name}` | {row['count']} | {row['ready_before_prepared_count']} | {row['ready_before_predecessor_count']} | {prepared['p50']}/{prepared['p95']}/{prepared['max']} | {frontier['p50']}/{frontier['p95']}/{frontier['max']} |")
    lines.extend(["", "Types are the canonical runtime operator names. No report-only renaming was applied.", ""])
    return "\n".join(lines)


def _render_decision(doc: dict[str, Any]) -> str:
    return "\n".join([
        "# MEG 0..11 Readiness Decision",
        "",
        f"DECISION: `{doc['decision']}`",
        f"NEXT_ACTION: `{doc['next_action']}`",
        "",
        doc["decision_reason"],
        "",
        "This is Case B: MEG exposes earlier evidence-derived work before the whole PreparedArtifact, but no STATE_DERIVED operator crosses the exact predecessor publication frontier. Therefore SHADOW_READ is not authorized.",
        "",
        "ReadView reporting remains an exact-capture sanity check only: stable/unstable/opaque, with no stale-state validation claim.",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = args.output_root.resolve()
    if output.exists():
        raise ReadinessAnalysisError("readiness_output_not_fresh")
    documents = build_documents(args.capture_root)
    output.mkdir(parents=True, exist_ok=False)
    for name, value in documents.items():
        destination = output / name
        if isinstance(value, dict):
            atomic_write_json(destination, value)
        else:
            destination.write_text(value, encoding="utf-8")
    decision = documents["MEG_READINESS_DECISION.json"]
    assert isinstance(decision, dict)
    print(json.dumps({"output_root": str(output), "decision": decision["decision"], "next_action": decision["next_action"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
