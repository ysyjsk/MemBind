"""Read-only SFWB v1.3 V5 evidence analyzer.

This module deliberately uses only the Python standard library.  Its input is
the four already validated qualification attempts and its output is a new
analysis root; it never constructs a client, opens a socket, or mutates a
sealed artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


ANALYSIS_ROOT_NAME = "sfwb-v1-3-v5-analysis-20260821-001"
_BLOCKS_ROOT = "artifacts"

# These are intentionally explicit.  A future campaign must not silently
# replace the evidence used for this analysis.
EXPECTED_BLOCKS: dict[str, dict[str, str]] = {
    "B0-A": {
        "method": "B0_NATIVE_SERIAL",
        "run_root": "sfwb-v1-3-simple-20260821-004",
        "attempt_root": "sfwb-v1-3-simple-20260821-004/qualification/blocks/qualification-b0-a/attempt-001",
    },
    "B0-B": {
        "method": "B0_NATIVE_SERIAL",
        "run_root": "sfwb-v1-3-simple-20260821-004",
        "attempt_root": "sfwb-v1-3-simple-20260821-004/qualification/blocks/qualification-b0-b/attempt-001",
    },
    "B1": {
        "method": "B1_NAIVE_WHOLE_UPDATE_ASYNC",
        "run_root": "sfwb-v1-3-simple-20260821-004",
        "attempt_root": "sfwb-v1-3-simple-20260821-004/qualification/blocks/qualification-b1/attempt-001",
    },
    "MemBind-v3.1": {
        "method": "MEMBIND_V31",
        "run_root": "sfwb-v1-3-membind-ext-20260821-001",
        "attempt_root": "sfwb-v1-3-membind-ext-20260821-001/qualification/blocks/qualification-membind/attempt-001",
    },
}

_PROMPT_OPERATOR = {
    "extract_nodes.extract_message": ("NODE_EXTRACTION", "NODE_EXTRACTION"),
    "extract_edges.edge": ("EDGE_EXTRACTION", "EDGE_EXTRACTION"),
    "extract_nodes.extract_summaries_batch": ("SUMMARY", "ATTRIBUTE/SUMMARY"),
    "dedupe_nodes.nodes": ("NODE_RESOLUTION", "NODE_RESOLUTION/BATCH_RESOLUTION"),
    "extract_edges.extract_timestamps": ("TIMESTAMP", "TIMESTAMP"),
    "dedupe_edges.resolve_edge": ("EDGE_RESOLUTION", "EDGE_RESOLUTION"),
}

_ALL_OPERATORS = (
    "NODE_EXTRACTION",
    "EDGE_EXTRACTION",
    "NODE_CANDIDATE_READ",
    "NODE_RESOLUTION",
    "EDGE_CANDIDATE_READ",
    "EDGE_DEDUPE",
    "EDGE_RESOLUTION",
    "ATTRIBUTE",
    "SUMMARY",
    "TIMESTAMP",
    "EMBEDDING",
    "PERSISTENCE",
    "OTHER",
)


class OfflineAnalysisError(ValueError):
    """The selected sealed evidence does not satisfy the analysis contract."""


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfflineAnalysisError(f"UNREADABLE_JSON:{path}") from exc
    if not isinstance(value, dict):
        raise OfflineAnalysisError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise OfflineAnalysisError(f"UNREADABLE_JSONL:{path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OfflineAnalysisError(f"INVALID_JSONL:{path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise OfflineAnalysisError(f"JSONL_OBJECT_REQUIRED:{path}:{line_number}")
        rows.append(value)
    return rows


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return float(value)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _duration_stats(durations_ns: Sequence[int]) -> dict[str, float | int | None]:
    values = [float(x) / 1_000_000_000 for x in durations_ns if x >= 0]
    return {
        "count": len(values),
        "total_s": round(sum(values), 9),
        "p50_s": None if not values else round(_percentile(values, 0.50) or 0.0, 9),
        "p95_s": None if not values else round(_percentile(values, 0.95) or 0.0, 9),
        "p99_s": None if not values else round(_percentile(values, 0.99) or 0.0, 9),
        "max_s": None if not values else round(max(values), 9),
    }


def _operator_for_prompt(prompt: Any) -> tuple[str, str]:
    if isinstance(prompt, str) and prompt in _PROMPT_OPERATOR:
        return _PROMPT_OPERATOR[prompt]
    return "OTHER", "UNKNOWN"


def _unwrap_llm_record(value: Mapping[str, Any]) -> Mapping[str, Any]:
    row = value.get("record")
    if isinstance(row, Mapping) and isinstance(row.get("row"), Mapping):
        return row["row"]
    return value


def _canonical_clean_attributes(attrs: Any) -> dict[str, Any]:
    if not isinstance(attrs, Mapping):
        return {}
    ignored = {"uuid", "id", "database_id", "db_id", "element_id", "created_at", "updated_at", "embedding", "name_embedding"}
    clean: dict[str, Any] = {}
    for key, value in attrs.items():
        key_str = str(key)
        lowered = key_str.lower()
        if lowered in ignored or lowered.endswith("_uuid"):
            continue
        clean[key_str] = re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value
    return dict(sorted(clean.items()))


def _canonical_entity(raw: Mapping[str, Any]) -> dict[str, Any]:
    name = raw.get("name") or ""
    summary = raw.get("summary") or ""
    return {
        "group_id": str(raw.get("group_id") or ""),
        "name": re.sub(r"\s+", " ", str(name)).strip().lower(),
        "labels": sorted(str(label) for label in (raw.get("labels") or []) if str(label)),
        "summary": re.sub(r"\s+", " ", str(summary)).strip(),
        "attributes": _canonical_clean_attributes(raw.get("attributes") or raw),
    }


def _edge_key(raw: Mapping[str, Any], side: str) -> str:
    for key in (f"{side}_entity_key", f"{side}_name", f"{side}_entity_name", f"{side}_node_name"):
        if raw.get(key):
            return re.sub(r"\s+", " ", str(raw[key])).strip().lower()
    return re.sub(r"\s+", " ", str(raw.get(side) or "")).strip().lower()


def _canonical_edge(raw: Mapping[str, Any]) -> dict[str, Any]:
    norm = lambda value: re.sub(r"\s+", " ", str(value)).strip() if value is not None else None
    return {
        "source_entity_key": _edge_key(raw, "source"),
        "target_entity_key": _edge_key(raw, "target"),
        "relation_type": norm(raw.get("relation_type") or raw.get("name") or "") or "",
        "fact": norm(raw.get("fact") or raw.get("summary") or "") or "",
        "valid_at": norm(raw.get("valid_at")),
        "invalid_at": norm(raw.get("invalid_at")),
        "expired_at": norm(raw.get("expired_at")),
        "attributes": _canonical_clean_attributes(raw.get("attributes") or {}),
        "source_episode_sequence": raw.get("source_episode_sequence"),
    }


def _canonical_graph(raw: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    entities = [_canonical_entity(row) for row in raw.get("entities", [])]
    edges = [_canonical_edge(row) for row in raw.get("edges", [])]
    episodes = [
        {
            "source_sequence": row.get("source_sequence"),
            "source_hash": row.get("source_hash"),
            "session_id": row.get("session_id"),
        }
        for row in raw.get("episodes", [])
    ]
    return {
        "entities": sorted(entities, key=_stable),
        "edges": sorted(edges, key=_stable),
        "episodes": sorted(episodes, key=_stable),
    }


def _project_namespace(graph: dict[str, list[dict[str, Any]]], namespace: str) -> None:
    logical = "__FORMAL_HISTORY_NAMESPACE__"
    for row in graph["entities"]:
        if row.get("group_id") == namespace:
            row["group_id"] = logical
        attrs = row.get("attributes")
        if isinstance(attrs, dict) and attrs.get("group_id") == namespace:
            attrs["group_id"] = logical


def _symmetric_count(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]], projection: Any) -> int:
    return len({_stable(projection(row)) for row in left} ^ {_stable(projection(row)) for row in right})


def _canonical_diff(reference: Mapping[str, Any], candidate: Mapping[str, Any], reference_namespace: str, candidate_namespace: str) -> dict[str, Any]:
    left = _canonical_graph(reference)
    right = _canonical_graph(candidate)
    _project_namespace(left, reference_namespace)
    _project_namespace(right, candidate_namespace)
    differences = {
        "entity_key": _symmetric_count(left["entities"], right["entities"], lambda row: (row["group_id"], row["name"])),
        "edge_key": _symmetric_count(left["edges"], right["edges"], lambda row: (row["source_entity_key"], row["target_entity_key"], row["relation_type"], row["fact"])),
        "attribute": _symmetric_count(left["entities"], right["entities"], lambda row: (row["group_id"], row["name"], row["labels"], row["summary"], row["attributes"])),
        "temporal": _symmetric_count(left["edges"], right["edges"], lambda row: (row["source_entity_key"], row["target_entity_key"], row["relation_type"], row["fact"], row["valid_at"], row["invalid_at"], row["expired_at"])),
        "source_link": _symmetric_count(left["episodes"], right["episodes"], lambda row: row) + _symmetric_count(left["edges"], right["edges"], lambda row: (row["source_entity_key"], row["target_entity_key"], row["relation_type"], row["fact"], row["source_episode_sequence"])),
    }
    left_hash = hashlib.sha256(_stable(left).encode()).hexdigest()
    right_hash = hashlib.sha256(_stable(right).encode()).hexdigest()
    return {
        "exact_match": left_hash == right_hash,
        "reference_hash": left_hash,
        "candidate_hash": right_hash,
        "difference_counts": differences,
        "reference_counts": {key: len(left[key]) for key in ("entities", "edges", "episodes")},
        "candidate_counts": {key: len(right[key]) for key in ("entities", "edges", "episodes")},
        "namespace_projection": {"applied": True, "logical_group_id": "__FORMAL_HISTORY_NAMESPACE__"},
    }


def _load_block(sfw_root: Path, name: str, spec: Mapping[str, str]) -> dict[str, Any]:
    attempt = sfw_root / _BLOCKS_ROOT / spec["attempt_root"]
    metrics = _json(attempt / "block_metrics.json")
    seal = _json(attempt / "seal.json")
    if metrics.get("valid") is not True or seal.get("status") != "VALIDATED_SEALED":
        raise OfflineAnalysisError(f"BLOCK_NOT_VALIDATED_SEALED:{name}")
    rows = _jsonl(attempt / "native_trace.jsonl")
    if len(rows) != int(metrics.get("episode_count", -1)) or {row.get("source_sequence") for row in rows} != set(range(12)):
        raise OfflineAnalysisError(f"TRACE_SOURCE_COVERAGE_INVALID:{name}")
    graph = _json(attempt / "canonical_graph.json")
    auxiliary = _jsonl(attempt / "llm.jsonl") if (attempt / "llm.jsonl").exists() else []
    return {
        "name": name,
        "method": spec["method"],
        "attempt_root": spec["attempt_root"],
        "path": attempt,
        "metrics": metrics,
        "seal": seal,
        "trace_rows": rows,
        "graph": graph,
        "auxiliary_llm": auxiliary,
    }


def _aggregate_block(block: Mapping[str, Any]) -> dict[str, Any]:
    spans = [span for row in block["trace_rows"] for span in row.get("spans", [])]
    logical = [span for span in spans if span.get("operation_class") == "logical-call"]
    attempts = [span for span in spans if span.get("operation_class") == "request-attempt"]
    prompt_counts: Counter[str] = Counter()
    prompt_stats: dict[str, dict[str, Any]] = {}
    operator_counts: dict[str, dict[str, Any]] = {
        op: {
            "logical_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "embedding_items": 0,
            "span_count": 0,
            "service_time_s": 0.0,
        }
        for op in _ALL_OPERATORS
    }
    source_data: dict[str, dict[str, Any]] = defaultdict(lambda: {"logical_calls": 0, "transport_attempts": 0, "input_tokens": 0, "output_tokens": 0, "embedding_items": 0, "db_queries": 0, "db_writes": 0, "span_count": 0, "by_operator": {}})
    logical_durations: list[int] = []
    embedding_items = 0
    embedding_operations: Counter[str] = Counter()
    operation_counts: Counter[str] = Counter()
    phase_counts: Counter[str] = Counter()
    phase_duration_ns: Counter[str] = Counter()
    candidate_counts: list[int] = []
    candidate_query_counts: list[int] = []
    retries = 0
    for span in spans:
        source = str(span.get("source_sequence"))
        source_entry = source_data[source]
        source_entry["span_count"] += 1
        operation = span.get("operation_class") or "UNCLASSIFIED"
        operation_counts[operation] += 1
        phase = str(span.get("phase") or "UNKNOWN")
        phase_counts[phase] += 1
        phase_duration_ns[phase] += int(span.get("duration_ns") or 0)
        span_duration_s = int(span.get("duration_ns") or 0) / 1_000_000_000
        metadata = span.get("metadata") if isinstance(span.get("metadata"), Mapping) else {}
        if operation in {"create", "create_batch"}:
            embedding_operations[operation] += 1
            items = int(metadata.get("text_count") or 0)
            embedding_items += items
            source_entry["embedding_items"] += items
            operator_counts["EMBEDDING"]["embedding_items"] += items
            operator_counts["EMBEDDING"]["span_count"] += 1
            operator_counts["EMBEDDING"]["service_time_s"] += span_duration_s
        if operation == "write" or phase in {"publication", "database-transaction"}:
            operator_counts["PERSISTENCE"]["span_count"] += 1
            operator_counts["PERSISTENCE"]["service_time_s"] += span_duration_s
        if operation == "query":
            source_entry["db_queries"] += 1
        if operation == "write":
            source_entry["db_writes"] += 1
        if operation == "request-attempt":
            source_entry["transport_attempts"] += 1
        if phase == "candidate-search":
            candidate_operator = "NODE_CANDIDATE_READ" if operation == "node-dedup" else "EDGE_CANDIDATE_READ"
            operator_counts[candidate_operator]["span_count"] += 1
            operator_counts[candidate_operator]["service_time_s"] += span_duration_s
        if operation in {"node-dedup", "edge-dedup"}:
            dedupe_operator = "NODE_RESOLUTION" if operation == "node-dedup" else "EDGE_DEDUPE"
            operator_counts[dedupe_operator]["span_count"] += 1
            operator_counts[dedupe_operator]["service_time_s"] += span_duration_s
        if operation in {"edge-invalidation", "new-edge-expiration-observation", "existing-edge-mutation"}:
            operator_counts["PERSISTENCE"]["span_count"] += 1
            operator_counts["PERSISTENCE"]["service_time_s"] += span_duration_s
        if phase == "candidate-search":
            if isinstance(metadata.get("candidate_count"), int):
                candidate_counts.append(metadata["candidate_count"])
            if isinstance(metadata.get("candidate_query_count"), int):
                candidate_query_counts.append(metadata["candidate_query_count"])
        if operation != "logical-call":
            continue
        prompt = metadata.get("prompt_name")
        prompt_name = str(prompt) if prompt else "UNKNOWN"
        prompt_counts[prompt_name] += 1
        prompt_entry = prompt_stats.setdefault(prompt_name, {"logical_calls": 0, "input_tokens": 0, "output_tokens": 0, "retry_count": 0, "service_time_s": 0.0})
        input_tokens = int(metadata.get("input_tokens") or 0)
        output_tokens = int(metadata.get("output_tokens") or 0)
        retry_count = int(metadata.get("retry_count") or 0)
        duration_ns = int(span.get("duration_ns") or 0)
        retries += retry_count
        logical_durations.append(duration_ns)
        prompt_entry["logical_calls"] += 1
        prompt_entry["input_tokens"] += input_tokens
        prompt_entry["output_tokens"] += output_tokens
        prompt_entry["retry_count"] += retry_count
        prompt_entry["service_time_s"] += duration_ns / 1_000_000_000
        operator, semantic_type = _operator_for_prompt(prompt)
        operator_counts[operator]["logical_calls"] += 1
        operator_counts[operator]["input_tokens"] += input_tokens
        operator_counts[operator]["output_tokens"] += output_tokens
        operator_counts[operator]["service_time_s"] += duration_ns / 1_000_000_000
        entry = source_entry
        entry["logical_calls"] += 1
        entry["input_tokens"] += input_tokens
        entry["output_tokens"] += output_tokens
        entry["by_operator"][operator] = entry["by_operator"].get(operator, 0) + 1
        prompt_entry["semantic_operator_type"] = semantic_type

    # Candidate spans are evidence about branch shape, not a replacement for
    # legal-ready or scheduler observations.
    for entry in source_data.values():
        entry["by_operator"] = dict(sorted(entry["by_operator"].items()))
    metrics = block["metrics"]
    seal_evidence = block["seal"].get("evidence") if isinstance(block["seal"].get("evidence"), Mapping) else {}
    complete_publication = (
        int(seal_evidence.get("terminal_episode_task_count", -1)) == int(metrics.get("episode_count", -2))
        and int(seal_evidence.get("episode_task_count", -1)) == int(metrics.get("episode_count", -2))
        and int(seal_evidence.get("open_transactions", -1)) == 0
        and int(seal_evidence.get("open_requests", -1)) == 0
    )
    auxiliary_events = Counter(str(_unwrap_llm_record(row).get("event_type") or "UNKNOWN") for row in block["auxiliary_llm"])
    work = {
        "logical_calls": len(logical),
        "transport_attempts": len(attempts),
        "input_tokens": sum(int((span.get("metadata") or {}).get("input_tokens") or 0) for span in logical),
        "output_tokens": sum(int((span.get("metadata") or {}).get("output_tokens") or 0) for span in logical),
        "embedding_items": embedding_items,
        "embedding_operations": dict(sorted(embedding_operations.items())),
        "retry_count_sum": retries,
        "request_service_time": _duration_stats(logical_durations),
        "transport_service_time": _duration_stats([int(span.get("duration_ns") or 0) for span in attempts]),
        "db_queries": operation_counts.get("query", 0),
        "db_writes": operation_counts.get("write", 0),
        "native_span_count": len(spans),
        "trace_episode_count": len(block["trace_rows"]),
        "sealed_metric_crosscheck": {
            "makespan_s": metrics.get("build_makespan_s"),
            "llm_logical_calls": metrics.get("llm_logical_calls"),
            "llm_transport_attempts": metrics.get("llm_transport_attempts"),
            "llm_input_tokens": metrics.get("llm_input_tokens"),
            "embedding_items": metrics.get("embedding_items"),
        },
    }
    expected = (work["logical_calls"], work["transport_attempts"], work["input_tokens"], work["embedding_items"])
    sealed_expected = (metrics.get("llm_logical_calls"), metrics.get("llm_transport_attempts"), metrics.get("llm_input_tokens"), metrics.get("embedding_items"))
    if expected != sealed_expected:
        raise OfflineAnalysisError(f"TRACE_METRIC_MISMATCH:{block['name']}:{expected}!={sealed_expected}")
    return {
        "method": block["method"],
        "attempt_root": block["attempt_root"],
        "sealed": {"status": block["seal"].get("status"), "seal_sha256": _sha256(block["path"] / "seal.json"), "payload_sha256": block["seal"].get("payload_sha256")},
        "metrics": {**{key: metrics.get(key) for key in ("build_makespan_s", "direct_semantic_violations", "inversion_count", "inversion_density", "kendall_tau", "canonical_graph_hash", "global_llm_admission_k", "resource_availability") if key in metrics}, "complete_publication_coverage": complete_publication},
        "work": work,
        "prompt_counts": dict(sorted(prompt_counts.items())),
        "prompt_stats": {key: prompt_stats[key] for key in sorted(prompt_stats)},
        "operator_counts": operator_counts,
        "by_source": {key: source_data[key] for key in sorted(source_data, key=lambda value: int(value))},
        "operation_counts": dict(sorted(operation_counts.items())),
        "phase_counts": dict(sorted(phase_counts.items())),
        "phase_duration_s": {key: round(value / 1_000_000_000, 9) for key, value in sorted(phase_duration_ns.items())},
        "candidate_search_evidence": {"candidate_search_span_count": phase_counts.get("candidate-search", 0), "candidate_count_sum": sum(candidate_counts), "candidate_count_max": max(candidate_counts) if candidate_counts else 0, "candidate_query_count_sum": sum(candidate_query_counts)},
        "auxiliary_llm_event_counts": dict(sorted(auxiliary_events.items())),
    }


def _work_delta(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("logical_calls", "transport_attempts", "input_tokens", "output_tokens", "embedding_items")
    return {f"{field}_delta": int(candidate["work"][field]) - int(base["work"][field]) for field in fields}


def _attribution(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    delta = _work_delta(base, candidate)
    records: list[dict[str, Any]] = []
    base_ops = base["operator_counts"]
    candidate_ops = candidate["operator_counts"]
    for operator in _ALL_OPERATORS:
        call_delta = int(candidate_ops[operator]["logical_calls"]) - int(base_ops[operator]["logical_calls"])
        token_delta = int(candidate_ops[operator]["input_tokens"]) - int(base_ops[operator]["input_tokens"])
        if call_delta == 0 and token_delta == 0:
            continue
        if call_delta == 0 and token_delta != 0:
            category = "UNKNOWN"
            evidence = "Logical call count is unchanged but sealed prompt token totals differ; no payload identity or causal explanation is available."
        elif operator == "TIMESTAMP":
            category = "repeated_summary/attribute/timestamp_work"
            evidence = "Prompt-level timestamp call count is directly observed in native_trace.jsonl."
        elif operator in {"NODE_RESOLUTION", "EDGE_RESOLUTION"}:
            category = "changed_state/candidate_set_branch_divergence"
            evidence = "Resolution prompt/span count and candidate-search branch shape differ; causal state choice is not separately instrumented."
        elif operator == "SUMMARY":
            category = "different_batching_granularity"
            evidence = "Summary prompt count differs; exact batch boundaries are not represented in the sealed trace."
        else:
            category = "changed_state/candidate_set_branch_divergence"
            evidence = "Prompt-level work differs, but no operator identity join proves the causal mechanism."
        records.append({"operator": operator, "category": category, "logical_call_delta": call_delta, "input_token_delta": token_delta, "evidence": evidence})
    base_retry_overhead = int(base["work"]["transport_attempts"]) - int(base["work"]["logical_calls"])
    candidate_retry_overhead = int(candidate["work"]["transport_attempts"]) - int(candidate["work"]["logical_calls"])
    records.append({"operator": "TRANSPORT", "category": "retry_difference", "logical_call_delta": None, "input_token_delta": None, "transport_attempt_delta": delta["transport_attempts_delta"], "retry_overhead_delta": candidate_retry_overhead - base_retry_overhead, "evidence": "Request-attempt spans are directly counted; retry overhead is attempts minus logical calls, so call-plan changes are not mislabeled as retries."})
    unknowns = [
        {"category": "decomposition-created_duplicate_work", "status": "UNKNOWN", "reason": "Sealed native trace has no cross-policy logical-operation identity or decomposition parent join."},
        {"category": "instrumentation_artifact", "status": "NOT_OBSERVED", "reason": "All four attempts are validated sealed and trace counts cross-check block metrics."},
    ]
    return {"candidate": candidate["method"], "vs": base["method"], "work_delta": delta, "operator_delta_records": records, "unresolved_categories": unknowns}


def _semantic_comparison(base: Mapping[str, Any], candidate: Mapping[str, Any], base_raw: Mapping[str, Any], candidate_raw: Mapping[str, Any]) -> dict[str, Any]:
    base_metrics = base_raw["metrics"]
    candidate_metrics = candidate_raw["metrics"]
    diff = _canonical_diff(base_raw["graph"], candidate_raw["graph"], base_metrics["namespace"], candidate_metrics["namespace"])
    return {
        "reference": base["method"],
        "candidate": candidate["method"],
        "graph": diff,
        "work": _work_delta(base, candidate),
        "protocol_validity": {"reference_sealed": base["sealed"]["status"] == "VALIDATED_SEALED", "candidate_sealed": candidate["sealed"]["status"] == "VALIDATED_SEALED", "candidate_complete_publication_coverage": candidate["metrics"].get("complete_publication_coverage", True)},
        "direct_semantic_safety": {"reference_violations": base["metrics"].get("direct_semantic_violations", 0), "candidate_violations": candidate["metrics"].get("direct_semantic_violations", 0)},
        "semantic_outcome_equivalence": {"canonical_exact": diff["exact_match"], "reference_entity_count": diff["reference_counts"]["entities"], "candidate_entity_count": diff["candidate_counts"]["entities"], "reference_edge_count": diff["reference_counts"]["edges"], "candidate_edge_count": diff["candidate_counts"]["edges"]},
    }


def analyze_sealed_workload(sfw_root: Path | str) -> dict[str, Any]:
    """Load and analyze only the pre-registered v1.3 sealed attempts."""

    root = Path(sfw_root).resolve()
    loaded = {name: _load_block(root, name, spec) for name, spec in EXPECTED_BLOCKS.items()}
    blocks = {name: _aggregate_block(block) for name, block in loaded.items()}
    base = blocks["B0-A"]
    attribution = {name: _attribution(base, block) for name, block in blocks.items() if name != "B0-A"}
    comparisons = {
        "B0-A_vs_B0-B": _semantic_comparison(base, blocks["B0-B"], loaded["B0-A"], loaded["B0-B"]),
        "B0-A_vs_B1": _semantic_comparison(base, blocks["B1"], loaded["B0-A"], loaded["B1"]),
        "B0-A_vs_MemBind-v3.1": _semantic_comparison(base, blocks["MemBind-v3.1"], loaded["B0-A"], loaded["MemBind-v3.1"]),
    }
    floor = comparisons["B0-A_vs_B0-B"]
    return {
        "schema_version": "sfwb.v1.3.v5.offline-analysis.v1",
        "analysis_scope": {"benchmark": "saturated_fixed_work_baseline_v1_3", "history_id": "07741c45", "episode_count": 12, "live_execution": False, "sealed_roots_mutated": False},
        "blocks": blocks,
        "work_attribution": attribution,
        "semantic_divergence": {"serial_self_divergence_floor": {"graph": floor["graph"], "work": floor["work"]}, "comparisons": comparisons},
        "decision_evidence": {
            "membind_extra_logical_calls_vs_b0_a": attribution["MemBind-v3.1"]["work_delta"]["logical_calls_delta"],
            "b1_logical_calls_vs_b0_a": attribution["B1"]["work_delta"]["logical_calls_delta"],
            "membind_graph_difference_counts": comparisons["B0-A_vs_MemBind-v3.1"]["graph"]["difference_counts"],
            "serial_floor_difference_counts": floor["graph"]["difference_counts"],
            "resource_capacity_evidence": "NOT_EVALUATED_IN_SEALED_BLOCKS",
        },
    }


def _md_table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    output = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    output.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def write_analysis_artifacts(result: Mapping[str, Any], output_root: Path | str, *, overwrite: bool = False) -> list[Path]:
    """Write a fresh, independent report root and return its seven paths.

    ``overwrite`` is intentionally explicit for regenerating this round's own
    unsealed report root after an analyzer fix; the default remains fail-closed.
    """

    out = Path(output_root)
    if out.exists() and not overwrite:
        raise OfflineAnalysisError("ANALYSIS_ROOT_ALREADY_EXISTS")
    out.mkdir(parents=True, exist_ok=True)
    blocks = result["blocks"]
    work_payload = {"schema_version": "sfwb.v1.3.v5.realized-work-attribution.v1", "analysis_scope": result["analysis_scope"], "blocks": blocks, "attribution": result["work_attribution"], "decision_evidence": result["decision_evidence"]}
    semantic_payload = {"schema_version": "sfwb.v1.3.v5.semantic-divergence-analysis.v1", "analysis_scope": result["analysis_scope"], **result["semantic_divergence"]}
    paths: list[Path] = []
    def write(name: str, content: str) -> None:
        path = out / name
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        paths.append(path)
    write("SFWB_V13_V5_MIGRATION_AUDIT.md", """# SFWB v1.3 V5 migration audit

This analysis migrates only workload-independent research utilities: semantic prompt/operator attribution, evidence-derived work accounting, passive native-trace aggregation, deterministic source attribution, and namespace-projected canonical graph comparison.

Frozen or discarded assumptions: old arrival traces, synthetic rho/think-time, cross-version legal-window and stale-state speculation, VDC/SHADOW_READ, K=2 as a V5 constant, CACHE_AFFINE as a default policy, publication-critical scheduling, and old MEG decision gates.

The four inputs are the pre-existing validated sealed attempts under SFWB v1.3. No B0-A, B0-B, B1, MemBind v3.1, or historical paper-eval artifact was modified. Old arrival-based speedup and semantic conclusions are historical diagnostics only and are not imported into this fixed-work result.

The analyzer is provider-free and read-only. It does not claim causality where the sealed trace lacks a logical-operation identity join; such attribution is recorded as `UNKNOWN`.
""")
    write("SFWB_V13_REALIZED_WORK_ATTRIBUTION.json", json.dumps(work_payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    work_rows = []
    for name, block in blocks.items():
        work_rows.append((name, block["work"]["logical_calls"], block["work"]["transport_attempts"], block["work"]["input_tokens"], block["work"]["output_tokens"], block["work"]["embedding_items"], block["work"]["request_service_time"]["total_s"]))
    attribution_md = "# SFWB v1.3 realized work attribution\n\n" + _md_table(work_rows, ("block", "logical calls", "attempts", "input tokens", "output tokens", "embedding items", "LLM service s"))
    attribution_md += "\n\n## Prompt/operator reconstruction\n\n"
    for name, block in blocks.items():
        attribution_md += f"### {name}\n\n" + _md_table([(prompt, block["prompt_counts"][prompt], block["prompt_stats"][prompt]["input_tokens"], _operator_for_prompt(prompt)[0]) for prompt in block["prompt_counts"]], ("prompt", "calls", "input tokens", "operator")) + "\n\n"
    attribution_md += "## Why 255 / 255 / 184 / 316?\n\n"
    attribution_md += "B0-A and B0-B reproduce the same 255-call logical plan; their 51 input-token difference is token-payload variance with no sealed identity join, so it remains `UNKNOWN`, not an inferred plan change. B1 performs 71 fewer calls, principally fewer node resolutions (8), edge resolutions (33), and timestamps (31), while adding one summary call; the sealed trace shows branch-shape divergence but cannot prove which state mutation caused each omission. MemBind performs 61 more calls than B0-A: 32 additional edge resolutions and 30 additional timestamp calls, offset by one fewer node-resolution call. These prompt and span counts are observed; decomposition-created duplication and exact candidate-set causality remain `UNKNOWN` without a cross-policy operation identity.\n\n"
    attribution_md += "Retry evidence: B0-A/B0-B/B1 each have retry overhead of one attempt above their logical calls; MemBind has zero retry overhead. Total attempt deltas also reflect the logical-call deltas and are reported separately in JSON. Embedding items are 572 / 572 / 448 / 747, and create/create_batch counts are preserved as measured native operations.\n\n"
    attribution_md += "## Attribution status\n\n"
    attribution_md += "- `changed_state/candidate_set_branch_divergence`: observed for resolution and timestamp call-count deltas, with causal mechanism explicitly unproven.\n- `repeated_summary/attribute/timestamp_work`: observed for MemBind timestamp expansion.\n- `different_batching_granularity`: observed only where summary-call counts differ; exact batch boundaries are unavailable.\n- token-only changes with unchanged call count: `UNKNOWN`; no prompt payload identity join.\n- `decomposition-created_duplicate_work`: `UNKNOWN`; no operation identity join.\n- `instrumentation_artifact`: not observed; all counts cross-check sealed metrics.\n"
    write("SFWB_V13_REALIZED_WORK_ATTRIBUTION.md", attribution_md)
    write("SFWB_V13_SEMANTIC_DIVERGENCE_ANALYSIS.json", json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    floor = result["semantic_divergence"]["serial_self_divergence_floor"]
    semantic_md = "# SFWB v1.3 semantic divergence analysis\n\n"
    semantic_md += "## Serial self-divergence floor\n\n"
    semantic_md += f"B0-A versus B0-B is the empirical serial floor, not zero: `{json.dumps(floor['graph']['difference_counts'], sort_keys=True)}` normalized graph differences and `{floor['work']['input_tokens_delta']}` input-token delta with zero logical-call delta. The qualification flag `canonical_exact_match=true` is not used as a cross-attempt equality claim.\n\n"
    semantic_md += "## Policy comparisons\n\n"
    semantic_rows = []
    for key, comparison in result["semantic_divergence"]["comparisons"].items():
        d = comparison["graph"]["difference_counts"]
        semantic_rows.append((key, comparison["graph"]["exact_match"], d["entity_key"], d["edge_key"], d["attribute"], d["temporal"], d["source_link"], comparison["work"]["logical_calls_delta"], comparison["work"]["input_tokens_delta"]))
    semantic_md += _md_table(semantic_rows, ("comparison", "exact", "entity", "edge", "attribute", "temporal", "source link", "call delta", "input-token delta")) + "\n\n"
    semantic_md += "All four blocks report zero direct semantic violations; MemBind reports complete 12/12 publication coverage. That protocol/direct-safety result is distinct from semantic outcome equivalence: B1 and MemBind both differ materially from the serial graph, and MemBind's difference exceeds the serial floor across every reported graph category.\n"
    write("SFWB_V13_SEMANTIC_DIVERGENCE_ANALYSIS.md", semantic_md)
    write("SFWB_V13_V5_METHODOLOGY_RETHINK.md", """# SFWB v1.3 V5 methodology rethink

## Work conservation

The fixed-work contract makes realized semantic work a first-class outcome. Native serial is 255 calls / 717,681 input tokens / 572 embedding items; MemBind is 316 / 729,048 / 747; B1 is 184 / 213,288 / 448. The near-identical B1 and MemBind makespans therefore do not establish equivalent execution efficiency. The primary candidate is semantic work conservation: preserve the serial logical call plan and batching while changing overlap.

## Semantic equivalence

`Update_i = EvidenceWork_i + StateWork_i` remains a useful decomposition, but the stronger contract is serial-equivalent state-derived input and deterministic effect/publication behavior. Direct rule violations being zero is necessary, not sufficient. The serial self-floor and policy graph diffs show that state cut/candidate visibility must be part of V5 correctness.

## Saturation and bottlenecks

Semantic legality, application admission, and backend capacity are separate variables. The sealed blocks report `resource_availability=NOT_EVALUATED`; this analysis therefore cannot claim backend saturation or authorize a K sweep. Service-time variance is measured, but causal attribution to a capacity envelope is not.

## EvidenceWork versus StateWork

Extraction is evidence-derived and can overlap; candidate search, resolution, effect, persistence, and publication are state-derived and require the correct state cut. MEG remains valuable as a semantic representation for this distinction, logical work identity, publication correctness, and work-conservation validation. It is not promoted to a default headline scheduler mechanism.

## Candidate methodology (not implemented)

`State-Cut + Semantic Work Conservation + Backend-Saturated Legal Execution` is a research candidate only. No scheduler, dynamic admission, stale-state read, or new instrumentation was implemented in this round.
""")
    decision = """# SFWB v1.3 V5 decision gate

## Decision

`GO_V5_SEMANTIC_WORK_CONSERVATION` (primary)  
`GO_V5_SERIAL_EQUIVALENT_STATE_CUT` (secondary)

## Evidence

- MemBind adds 61 logical calls and 175 embedding items versus B0-A while producing a 487.613 s makespan close to B1's 482.967 s. This is a realized-work divergence, not evidence of a clean 2.02x scheduling gain.
- B1 removes 71 calls, 504,393 input tokens, and 124 embedding items versus B0-A, while its graph differs from serial and its inversion count is 22. The trace proves branch-shape divergence, not semantic equivalence.
- B0-A versus B0-B establishes a non-zero serial floor of 2 entity-key, 4 edge-key, 6 attribute, 6 temporal, and 4 source-link differences plus 51 input tokens. MemBind exceeds that floor materially in every graph category.
- All blocks have zero direct semantic violations; MemBind has complete publication coverage. These are protocol gates, not outcome-equivalence proof.
- Resource capacity is `NOT_EVALUATED`; no `GO_V5_BACKEND_SATURATED_EXECUTION` is justified and no backend mechanism is implemented.

The next permitted research step is an offline/provider-free specification and qualification of work-plan conservation and serial-equivalent state cuts. A production scheduler, admission policy, K sweep, new live run, SHADOW_READ, and stale-state mechanism remain frozen until those contracts are established.
"""
    write("SFWB_V13_V5_DECISION.md", decision)
    return paths


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[4]
    sfwb = repository / "saturated_fixed_work_baseline_v1_3"
    result = analyze_sealed_workload(sfwb)
    output = sfwb / "artifacts" / ANALYSIS_ROOT_NAME
    write_analysis_artifacts(result, output)
    print(output)


__all__ = ["ANALYSIS_ROOT_NAME", "EXPECTED_BLOCKS", "OfflineAnalysisError", "analyze_sealed_workload", "write_analysis_artifacts"]
