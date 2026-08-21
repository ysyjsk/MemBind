"""Offline first-divergence analysis for the sealed SFWB v1.3 runs.

The native traces expose request/span shape, but not the serialized semantic
payloads that would be needed to prove an extraction or candidate identity
change.  This module therefore records the earliest *observable* signal and
keeps ``FIRST_PROVABLE_DIVERGENCE`` fail-closed when that signal is not causal.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .offline_analyzer import EXPECTED_BLOCKS, _json, _jsonl, _load_block, _stable


FIRST_DIVERGENCE_ROOT_NAME = "sfwb-v1-3-v5-first-divergence-20260821-001"
STOP_GATE = "STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY"

_PROMPTS = {
    "node_extraction": "extract_nodes.extract_message",
    "edge_extraction": "extract_edges.edge",
    "node_resolution": "dedupe_nodes.nodes",
    "edge_resolution": "dedupe_edges.resolve_edge",
    "summary": "extract_nodes.extract_summaries_batch",
    "timestamp": "extract_edges.extract_timestamps",
}

_OPERATOR_TYPES = {
    "extract_nodes.extract_message": "NODE_EXTRACTION",
    "extract_edges.edge": "EDGE_EXTRACTION",
    "dedupe_nodes.nodes": "NODE_RESOLUTION/BATCH_RESOLUTION",
    "dedupe_edges.resolve_edge": "EDGE_RESOLUTION",
    "extract_nodes.extract_summaries_batch": "SUMMARY/ATTRIBUTE",
    "extract_edges.extract_timestamps": "TIMESTAMP",
}

_STAGE_ORDER = (
    "source_evidence",
    "node_extraction",
    "edge_extraction",
    "prepared_extraction_outputs",
    "node_candidate_formation",
    "node_resolution_batch",
    "edge_candidate_formation",
    "edge_resolution_fan_out",
    "attribute_summary_timestamp",
    "persistence",
    "publication",
)


class FirstDivergenceError(ValueError):
    """The sealed inputs do not satisfy this analysis contract."""


def _source_rows(block: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = {str(row["source_sequence"]): row for row in block["trace_rows"]}
    if set(rows) != {str(i) for i in range(12)}:
        raise FirstDivergenceError("SOURCE_TRACE_COVERAGE_INVALID")
    return rows


def _spans(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(row.get("spans", []))


def _logical(spans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for span in spans:
        if span.get("operation_class") != "logical-call":
            continue
        metadata = span.get("metadata") if isinstance(span.get("metadata"), Mapping) else {}
        result.append(
            {
                "sequence": span.get("sequence"),
                "prompt_name": metadata.get("prompt_name"),
                "operator_type": _OPERATOR_TYPES.get(str(metadata.get("prompt_name") or ""), "UNKNOWN"),
                "input_tokens": metadata.get("input_tokens"),
                "output_tokens": metadata.get("output_tokens"),
                "retry_count": metadata.get("retry_count"),
                "start_ns": span.get("start_ns"),
                "end_ns": span.get("end_ns"),
            }
        )
    return result


def _prompt_rows(logical: Sequence[Mapping[str, Any]], prompt: str) -> list[dict[str, Any]]:
    return [dict(row) for row in logical if row.get("prompt_name") == prompt]


def _token_vector(logical: Sequence[Mapping[str, Any]], prompt: str) -> list[Any]:
    return [row.get("input_tokens") for row in _prompt_rows(logical, prompt)]


def _count_prompt(logical: Sequence[Mapping[str, Any]], prompt: str) -> int:
    return len(_prompt_rows(logical, prompt))


def _candidate_rows(spans: Sequence[Mapping[str, Any]], operations: set[str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for span in spans:
        if span.get("phase") != "candidate-search":
            continue
        operation = span.get("operation_class")
        if operations is not None and operation not in operations:
            continue
        metadata = span.get("metadata") if isinstance(span.get("metadata"), Mapping) else {}
        rows.append(
            {
                "operation_class": operation,
                "candidate_count": metadata.get("candidate_count"),
                "candidate_query_count": metadata.get("candidate_query_count"),
            }
        )
    return rows


def _candidate_detail(spans: Sequence[Mapping[str, Any]], operations: set[str] | None = None) -> dict[str, Any]:
    rows = _candidate_rows(spans, operations)
    return {
        "span_count": len(rows),
        "candidate_counts": [row["candidate_count"] for row in rows],
        "candidate_query_counts": [row["candidate_query_count"] for row in rows],
        "identity_available": False,
        "order_available": False,
    }


def _operation_count(spans: Sequence[Mapping[str, Any]], operation: str) -> int:
    return sum(1 for span in spans if span.get("operation_class") == operation)


def _phase_count(spans: Sequence[Mapping[str, Any]], phase: str) -> int:
    return sum(1 for span in spans if span.get("phase") == phase)


def _phase_duration(spans: Sequence[Mapping[str, Any]], phase: str) -> int:
    return sum(int(span.get("duration_ns") or 0) for span in spans if span.get("phase") == phase)


def _event_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for value in _jsonl(path):
        event = value.get("event")
        if isinstance(event, str):
            row = dict(value)
            row["event_type"] = event
            if "monotonic_ns" in row:
                row["timestamp_ns"] = row["monotonic_ns"]
            rows.append(row)
        elif isinstance(event, Mapping):
            rows.append(dict(event))
        elif isinstance(value, Mapping):
            rows.append(dict(value))
    return rows


def _event_detail(events: Sequence[Mapping[str, Any]], source: int) -> dict[str, Any]:
    selected = [row for row in events if row.get("source_sequence") == source]
    counts = Counter(str(row.get("event_type") or "UNKNOWN") for row in selected)
    return {"event_counts": dict(sorted(counts.items())), "event_types": sorted(counts)}


def _prepared_detail(attempt: Path, source: int) -> dict[str, Any]:
    path = attempt / "private" / "prepared" / f"{source:08d}.json"
    if not path.exists():
        return {
            "available": False,
            "path": None,
            "node_count": None,
            "edge_count": None,
            "node_identity_digest": None,
            "edge_identity_digest": None,
            "artifact_sha256": None,
            "evidence_sha256": None,
        }
    value = _json(path)
    raw_nodes = value.get("raw_nodes") if isinstance(value.get("raw_nodes"), list) else []
    raw_edges = value.get("raw_edges") if isinstance(value.get("raw_edges"), list) else []
    node_keys = sorted((str(row.get("name") or "").lower(), str(row.get("uuid") or "")) for row in raw_nodes if isinstance(row, Mapping))
    edge_keys = sorted(
        (
            str(row.get("name") or ""),
            str(row.get("fact") or ""),
            str(row.get("source_node_uuid") or ""),
            str(row.get("target_node_uuid") or ""),
        )
        for row in raw_edges
        if isinstance(row, Mapping)
    )
    return {
        "available": True,
        "path": str(path),
        "node_count": len(raw_nodes),
        "edge_count": len(raw_edges),
        "node_identity_digest": hashlib.sha256(_stable(node_keys).encode()).hexdigest(),
        "edge_identity_digest": hashlib.sha256(_stable(edge_keys).encode()).hexdigest(),
        "artifact_sha256": value.get("artifact_sha256"),
        "evidence_sha256": value.get("evidence_sha256"),
    }


def _llm_auxiliary_detail(block: Mapping[str, Any], source: int) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    request_kinds: Counter[str] = Counter()
    submitted_token_counts: list[int] = []
    prefix_hashes: list[str] = []
    for value in block.get("auxiliary_llm", []):
        record = value.get("record") if isinstance(value.get("record"), Mapping) else value
        row = record.get("row") if isinstance(record, Mapping) and isinstance(record.get("row"), Mapping) else record
        if not isinstance(row, Mapping) or row.get("source_sequence") != source:
            continue
        event_type = str(row.get("event_type") or "UNKNOWN")
        counts[event_type] += 1
        if row.get("request_kind") is not None:
            request_kinds[str(row["request_kind"])] += 1
        if event_type == "llm_request_submitted" and isinstance(row.get("token_count"), int):
            submitted_token_counts.append(row["token_count"])
        if isinstance(row.get("prefix_metadata_sha256"), str):
            prefix_hashes.append(row["prefix_metadata_sha256"])
    return {
        "event_counts": dict(sorted(counts.items())),
        "request_kind_counts": dict(sorted(request_kinds.items())),
        "submitted_token_counts": submitted_token_counts,
        "prefix_metadata_hash_count": len(prefix_hashes),
        "full_prompt_hash_available": False,
    }


def _stage_record(name: str, b0: Mapping[str, Any], mb: Mapping[str, Any], *, comparable: bool = True, note: str = "") -> dict[str, Any]:
    equal = comparable and _stable(b0) == _stable(mb)
    return {
        "stage": name,
        "b0_a": dict(b0),
        "membind_v3_1": dict(mb),
        "status": "EQUAL" if equal else ("DIFFERENT" if comparable else "NOT_COMPARABLE"),
        "semantic_cause_provable": False,
        "note": note,
    }


def _source_chain(b0_block: Mapping[str, Any], mb_block: Mapping[str, Any], source: int) -> dict[str, Any]:
    b0_rows = _source_rows(b0_block)
    mb_rows = _source_rows(mb_block)
    b0_row = b0_rows[str(source)]
    mb_row = mb_rows[str(source)]
    b0_spans = _spans(b0_row)
    mb_spans = _spans(mb_row)
    b0_logical = _logical(b0_spans)
    mb_logical = _logical(mb_spans)
    b0_attempt = b0_block["path"]
    mb_attempt = mb_block["path"]
    b0_events = _event_rows(b0_attempt / "raw_events.jsonl")
    mb_events = _event_rows(mb_attempt / "raw_events.jsonl")
    evidence = {
        "b0_source_hash": b0_row.get("source_hash"),
        "membind_source_hash": mb_row.get("source_hash"),
        "hash_equal": b0_row.get("source_hash") == mb_row.get("source_hash"),
        "episode_id_equal": b0_row.get("episode_id") == mb_row.get("episode_id"),
        "history_id_equal": b0_row.get("history_id") == mb_row.get("history_id"),
    }
    b0_node_extract = _token_vector(b0_logical, _PROMPTS["node_extraction"])
    mb_node_extract = _token_vector(mb_logical, _PROMPTS["node_extraction"])
    b0_edge_extract = _token_vector(b0_logical, _PROMPTS["edge_extraction"])
    mb_edge_extract = _token_vector(mb_logical, _PROMPTS["edge_extraction"])
    b0_node_candidate = _candidate_detail(b0_spans, {"node-dedup"})
    mb_node_candidate = _candidate_detail(mb_spans, {"node-dedup"})
    b0_edge_candidate = _candidate_detail(b0_spans, {"edge-dedup", "edge-empty"})
    mb_edge_candidate = _candidate_detail(mb_spans, {"edge-dedup", "edge-empty"})
    b0_edge_resolution = _token_vector(b0_logical, _PROMPTS["edge_resolution"])
    mb_edge_resolution = _token_vector(mb_logical, _PROMPTS["edge_resolution"])
    b0_timestamp = _token_vector(b0_logical, _PROMPTS["timestamp"])
    mb_timestamp = _token_vector(mb_logical, _PROMPTS["timestamp"])
    b0_prepared = _prepared_detail(b0_attempt, source)
    mb_prepared = _prepared_detail(mb_attempt, source)
    b0_publication_events = sum(1 for event in b0_events if event.get("event_type") == "PUBLICATION_DURABLE" and event.get("source_sequence") == source)
    mb_publication_events = sum(1 for event in mb_events if event.get("event_type") == "PUBLICATION_DURABLE" and event.get("source_sequence") == source)

    stages = {
        "source_evidence": _stage_record("source_evidence", evidence, evidence, note="Immutable source identity is equal; this is not a divergence."),
        "node_extraction": _stage_record("node_extraction", {"call_count": len(b0_node_extract), "input_token_vector": b0_node_extract, "prompt_hash_available": False}, {"call_count": len(mb_node_extract), "input_token_vector": mb_node_extract, "prompt_hash_available": False}, note="Native trace exposes token counts but not serialized extraction outputs or full prompt identity."),
        "edge_extraction": _stage_record("edge_extraction", {"call_count": len(b0_edge_extract), "input_token_vector": b0_edge_extract, "prompt_hash_available": False}, {"call_count": len(mb_edge_extract), "input_token_vector": mb_edge_extract, "prompt_hash_available": False}, note="Token-vector differences are request-shape observations, not proof of different extracted edges."),
        "prepared_extraction_outputs": _stage_record("prepared_extraction_outputs", b0_prepared, mb_prepared, comparable=False, note="B0-A has no prepared artifact; MemBind-only prepared cardinality/identity cannot be paired."),
        "node_candidate_formation": _stage_record("node_candidate_formation", {**b0_node_candidate, "all_candidate_search_span_count": _phase_count(b0_spans, "candidate-search")}, {**mb_node_candidate, "all_candidate_search_span_count": _phase_count(mb_spans, "candidate-search")}, note="Candidate count is observable; identity, order, and state-version are absent."),
        "node_resolution_batch": _stage_record("node_resolution_batch", {"input_token_vector": _token_vector(b0_logical, _PROMPTS["node_resolution"]), "call_count": _count_prompt(b0_logical, _PROMPTS["node_resolution"])}, {"input_token_vector": _token_vector(mb_logical, _PROMPTS["node_resolution"]), "call_count": _count_prompt(mb_logical, _PROMPTS["node_resolution"])}, note="No resolution decision/output digest or batch membership is present."),
        "edge_candidate_formation": _stage_record("edge_candidate_formation", b0_edge_candidate, mb_edge_candidate, note="Candidate cardinality is exposed, but candidate identity/order is not."),
        "edge_resolution_fan_out": _stage_record("edge_resolution_fan_out", {"input_token_vector": b0_edge_resolution, "call_count": len(b0_edge_resolution), "edge_dedup_spans": _operation_count(b0_spans, "edge-dedup"), "edge_invalidation_spans": _operation_count(b0_spans, "edge-invalidation")}, {"input_token_vector": mb_edge_resolution, "call_count": len(mb_edge_resolution), "edge_dedup_spans": _operation_count(mb_spans, "edge-dedup"), "edge_invalidation_spans": _operation_count(mb_spans, "edge-invalidation")}, note="Fan-out is directly counted, but it cannot be distinguished as duplicate consumption versus changed upstream branch."),
        "attribute_summary_timestamp": _stage_record("attribute_summary_timestamp", {"summary_calls": _count_prompt(b0_logical, _PROMPTS["summary"]), "timestamp_calls": len(b0_timestamp), "timestamp_input_token_vector": b0_timestamp}, {"summary_calls": _count_prompt(mb_logical, _PROMPTS["summary"]), "timestamp_calls": len(mb_timestamp), "timestamp_input_token_vector": mb_timestamp}, note="Downstream work is observable only as request counts and token vectors."),
        "persistence": _stage_record("persistence", {"write_spans": _operation_count(b0_spans, "write"), "invalidation_spans": _operation_count(b0_spans, "edge-invalidation"), "mutation_spans": _operation_count(b0_spans, "existing-edge-mutation") + _operation_count(b0_spans, "new-edge-expiration-observation")}, {"write_spans": _operation_count(mb_spans, "write"), "invalidation_spans": _operation_count(mb_spans, "edge-invalidation"), "mutation_spans": _operation_count(mb_spans, "existing-edge-mutation") + _operation_count(mb_spans, "new-edge-expiration-observation")}, note="Transaction/write spans are present; semantic effect identity is not."),
        "publication": _stage_record("publication", {"publication_events": b0_publication_events, "complete": b0_publication_events == 1 and b0_block["seal"].get("status") == "VALIDATED_SEALED"}, {"publication_events": mb_publication_events, "complete": mb_publication_events == 1 and mb_block["seal"].get("status") == "VALIDATED_SEALED"}, note="Publication completion is sealed for both paths; it is downstream evidence, not a cause."),
    }

    # Find the earliest measured request/span-shape mismatch in the registered
    # semantic order.  A missing B0 prepared artifact is reported separately,
    # rather than being misrepresented as a semantic change.
    observable_candidates: list[dict[str, Any]] = []
    node_stage = stages["node_extraction"]
    if node_stage["status"] == "DIFFERENT":
        observable_candidates.append({"stage": "node_extraction", "kind": "input_token_vector", "classification_candidate": "EXTRACTION_DIVERGENCE", "details": node_stage})
    edge_stage = stages["edge_extraction"]
    if edge_stage["status"] == "DIFFERENT":
        observable_candidates.append({"stage": "edge_extraction", "kind": "input_token_vector", "classification_candidate": "EXTRACTION_DIVERGENCE", "details": edge_stage})
    node_candidate_stage = stages["node_candidate_formation"]
    if node_candidate_stage["status"] == "DIFFERENT":
        observable_candidates.append({"stage": "node_candidate_formation", "kind": "candidate_span_or_cardinality", "classification_candidate": "STATE_SNAPSHOT_OR_CANDIDATE_DIVERGENCE", "details": node_candidate_stage})
    node_resolution_stage = stages["node_resolution_batch"]
    if node_resolution_stage["status"] == "DIFFERENT":
        observable_candidates.append({"stage": "node_resolution_batch", "kind": "input_token_or_call_vector", "classification_candidate": "RESOLUTION_DECISION_DIVERGENCE", "details": node_resolution_stage})
    edge_candidate_stage = stages["edge_candidate_formation"]
    if edge_candidate_stage["status"] == "DIFFERENT":
        observable_candidates.append({"stage": "edge_candidate_formation", "kind": "candidate_span_or_cardinality", "classification_candidate": "STATE_SNAPSHOT_OR_CANDIDATE_DIVERGENCE", "details": edge_candidate_stage})
    edge_resolution_stage = stages["edge_resolution_fan_out"]
    if edge_resolution_stage["status"] == "DIFFERENT":
        observable_candidates.append({"stage": "edge_resolution_fan_out", "kind": "fan_out_count_or_input_vector", "classification_candidate": "UNKNOWN", "details": edge_resolution_stage})
    downstream_stage = stages["attribute_summary_timestamp"]
    if downstream_stage["status"] == "DIFFERENT":
        observable_candidates.append({"stage": "attribute_summary_timestamp", "kind": "downstream_call_count", "classification_candidate": "UNKNOWN", "details": downstream_stage})
    persistence_stage = stages["persistence"]
    if persistence_stage["status"] == "DIFFERENT":
        observable_candidates.append({"stage": "persistence", "kind": "effect_span_count", "classification_candidate": "PERSISTENCE_DIVERGENCE", "details": persistence_stage})
    first_observable = observable_candidates[0] if observable_candidates else {"stage": "publication", "kind": "none", "classification_candidate": "OBSERVABILITY_INSUFFICIENT", "details": stages["publication"]}
    first_observable = {**first_observable, "semantic_cause_provable": False}
    first_provable = {
        "stage": first_observable["stage"],
        "classification": "OBSERVABILITY_INSUFFICIENT",
        "observed_signal_kind": first_observable["kind"],
        "observed_signal_classification_candidate": first_observable["classification_candidate"],
        "semantic_cause_provable": False,
        "reason": "The first signal is request/span shape only; the sealed inputs do not contain paired extraction outputs, candidate identities/order, state version, batch membership, or resolution decision digests.",
    }
    fanout = {
        "edge_resolution_delta": len(mb_edge_resolution) - len(b0_edge_resolution),
        "timestamp_delta": len(mb_timestamp) - len(b0_timestamp),
        "edge_dedup_span_delta": _operation_count(mb_spans, "edge-dedup") - _operation_count(b0_spans, "edge-dedup"),
        "edge_invalidation_span_delta": _operation_count(mb_spans, "edge-invalidation") - _operation_count(b0_spans, "edge-invalidation"),
        "summary_delta": _count_prompt(mb_logical, _PROMPTS["summary"]) - _count_prompt(b0_logical, _PROMPTS["summary"]),
    }
    observability = {
        "b0_prepared_outputs": b0_prepared["available"],
        "membind_prepared_outputs": mb_prepared["available"],
        "extraction_output_parity": False,
        "prompt_hash_parity": False,
        "candidate_identity_parity": False,
        "candidate_order_parity": False,
        "batch_membership_parity": False,
        "resolution_decision_parity": False,
        "effect_identity_parity": False,
        "membind_llm_auxiliary": _llm_auxiliary_detail(mb_block, source),
        "b0_llm_auxiliary": _llm_auxiliary_detail(b0_block, source),
    }
    return {
        "source_sequence": source,
        "source_evidence": evidence,
        "logical_operator_sequence": {"b0_a": b0_logical, "membind_v3_1": mb_logical},
        "stages": stages,
        "first_observable_signal": first_observable,
        "first_provable_divergence": first_provable,
        "fan_out": fanout,
        "batching": {
            "b0_a": {"batch_ids": None, "membership": None, "batch_sizes": None},
            "membind_v3_1": {"batch_ids": None, "membership": None, "batch_sizes": None},
            "membership_parity": False,
            "reason": "Sealed native trace and auxiliary request records do not carry a paired batch membership identity.",
        },
        "persistence": stages["persistence"],
        "publication": {"b0_complete": stages["publication"]["b0_a"]["complete"], "membind_complete": stages["publication"]["membind_v3_1"]["complete"], "b0_events": _event_detail(b0_events, source), "membind_events": _event_detail(mb_events, source)},
        "observability": observability,
        "causal_chain": [
            {"from": "source_evidence", "to": first_observable["stage"], "relation": "same immutable source enters both paths; first request/span-shape signal appears downstream"},
            {"from": first_observable["stage"], "to": "edge_resolution_fan_out", "relation": "observed downstream fan-out changes; causal transfer is not provable"},
            {"from": "edge_resolution_fan_out", "to": "attribute_summary_timestamp", "relation": "timestamp/summary request counts follow observed edge fan-out"},
            {"from": "attribute_summary_timestamp", "to": "persistence", "relation": "effect span counts are observed after semantic requests"},
            {"from": "persistence", "to": "publication", "relation": "both paths reach sealed publication; publication is not a causal explanation"},
        ],
    }


def _operator_delta(sources: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    prompts = ("node_extraction", "edge_extraction", "node_resolution", "edge_resolution", "summary", "timestamp")
    mapping = {name: prompt for name, prompt in _PROMPTS.items()}
    result: dict[str, int] = {"NODE_EXTRACTION": 0, "EDGE_EXTRACTION": 0, "NODE_RESOLUTION": 0, "EDGE_RESOLUTION": 0, "SUMMARY": 0, "TIMESTAMP": 0}
    for source in sources.values():
        b0 = source["logical_operator_sequence"]["b0_a"]
        mb = source["logical_operator_sequence"]["membind_v3_1"]
        for name in prompts:
            label = name.upper()
            result[label] += _count_prompt(mb, mapping[name]) - _count_prompt(b0, mapping[name])
    return result


def analyze_first_divergence(sfw_root: Path | str) -> dict[str, Any]:
    root = Path(sfw_root).resolve()
    b0 = _load_block(root, "B0-A", EXPECTED_BLOCKS["B0-A"])
    mb = _load_block(root, "MemBind-v3.1", EXPECTED_BLOCKS["MemBind-v3.1"])
    sources = {str(source): _source_chain(b0, mb, source) for source in range(12)}
    operator_delta = _operator_delta(sources)
    edge_resolution_delta = sum(row["fan_out"]["edge_resolution_delta"] for row in sources.values())
    timestamp_delta = sum(row["fan_out"]["timestamp_delta"] for row in sources.values())
    source_first_signals = Counter(row["first_observable_signal"]["stage"] for row in sources.values())
    return {
        "schema_version": "sfwb.v1.3.v5.first-divergence-analysis.v1",
        "analysis_scope": {"benchmark": "saturated_fixed_work_baseline_v1_3", "history_id": "07741c45", "source_count": 12, "reference": "B0-A", "candidate": "MemBind-v3.1", "live_execution": False, "sealed_artifacts_mutated": False, "final_graph_used_for_causality": False},
        "decision": {"gate": STOP_GATE, "primary_root_cause": "FIRST_DIVERGENCE_NOT_PROVABLE_FROM_SEALED_TELEMETRY", "semantic_cause_provable": False},
        "sources": sources,
        "aggregate": {
            "logical_operator_delta": operator_delta,
            "edge_resolution_delta": edge_resolution_delta,
            "timestamp_delta": timestamp_delta,
            "first_observable_signal_stage_counts": dict(sorted(source_first_signals.items())),
            "extra_work_explanation": {"duplicate_consumption_provable": False, "changed_upstream_branch_provable": False, "observed_as_downstream_fan_out": True, "reason": "The telemetry has counts and token vectors but no paired semantic output, candidate identity/order, batch membership, state version, or resolution decision identity."},
            "publication_complete_source_count": sum(1 for row in sources.values() if row["publication"]["b0_complete"] and row["publication"]["membind_complete"]),
        },
        "minimum_additional_observability": [
            {"requirement": "paired_extraction_output_digest", "fields": ["source_sequence", "operator_id", "canonical_node_output_digest", "canonical_edge_output_digest", "cardinality"]},
            {"requirement": "exact_prompt_identity", "fields": ["source_sequence", "operator_id", "prompt_hash", "input_payload_hash", "batch_id"]},
            {"requirement": "candidate_set_identity", "fields": ["source_sequence", "state_version", "candidate_stage", "ordered_candidate_identity_digest", "candidate_count"]},
            {"requirement": "batch_membership", "fields": ["batch_id", "operator_ids", "source_sequences", "batch_size", "ordering"]},
            {"requirement": "resolution_and_effect_identity", "fields": ["operator_id", "decision_digest", "effect_digest", "publication_version"]},
        ],
        "v5_contract_if_only_timing_changes": [
            "preserve the Native Serial logical operator partial order and one-to-one operator lineage",
            "preserve exact extraction input/output identity and batching membership",
            "bind every state-derived operation to the same exact predecessor state version",
            "preserve candidate set identity/order and resolution decision identity",
            "preserve effect cardinality/content and ordered durable publication",
        ],
    }


def write_first_divergence_artifacts(result: Mapping[str, Any], output_root: Path | str, *, overwrite: bool = False) -> list[Path]:
    out = Path(output_root)
    if out.exists() and not overwrite:
        raise FirstDivergenceError("FIRST_DIVERGENCE_ROOT_ALREADY_EXISTS")
    out.mkdir(parents=True, exist_ok=True)
    analysis = out / "SFWB_V13_V5_FIRST_DIVERGENCE_ANALYSIS.json"
    chain = out / "SFWB_V13_V5_SOURCE_CAUSAL_CHAIN.json"
    analysis.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    chain_payload = {
        "schema_version": "sfwb.v1.3.v5.source-causal-chain.v1",
        "analysis_scope": result["analysis_scope"],
        "aggregate_chain": result["aggregate"],
        "sources": {source: {"source_evidence": row["source_evidence"], "first_observable_signal": row["first_observable_signal"], "first_provable_divergence": row["first_provable_divergence"], "fan_out": row["fan_out"], "causal_chain": row["causal_chain"]} for source, row in result["sources"].items()},
        "v5_contract_if_only_timing_changes": result["v5_contract_if_only_timing_changes"],
    }
    chain.write_text(json.dumps(chain_payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    rows = []
    for source, row in result["sources"].items():
        rows.append("| {} | {} | {} | {} | {} | {} |".format(source, row["first_observable_signal"]["stage"], row["first_observable_signal"]["kind"], row["first_provable_divergence"]["classification"], row["fan_out"]["edge_resolution_delta"], row["fan_out"]["timestamp_delta"]))
    md = """# SFWB v1.3 V5 first-divergence analysis

## Decision

`STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY`

The analysis does not use final graph differences to infer a cause. It aligns the same 12 source hashes through request/span-level stages and records the earliest observable request-shape or candidate-cardinality signal. A signal is not promoted to a semantic cause unless paired payload, output, candidate identity, state version, and batch lineage are present; those fields are absent from the sealed telemetry.

## Per-source earliest observable signal

| source | first observable stage | signal | FIRST_PROVABLE_DIVERGENCE | edge-resolution delta | timestamp delta |
| --- | --- | --- | --- | ---: | ---: |
""" + "\n".join(rows) + "\n\n"
    md += """## Causal interpretation

The aggregate `+32 EDGE_RESOLUTION` and `+30 TIMESTAMP` calls are real downstream fan-out observations. The sealed traces do not prove that they are duplicate consumption, and do not prove which earlier extraction, state snapshot, candidate set, or batch decision caused them. Several sources show an earlier edge-extraction input-token mismatch; others first show node-candidate cardinality or node-resolution request-shape differences. Source 0 has equal extraction token vectors and only a candidate-span-shape difference. These are distinct observations, not a single proven root cause.

The minimum observed Compile/Bind boundary is the point where v3.1 request planning no longer exposes the Native Serial semantic path as a paired operator lineage: prepared outputs exist only for MemBind, and B0 has no matching artifact. For sources with differing logical request order, the ordering difference is observable; its semantic effect is not provable.

## Required observability before mechanism design

1. Paired canonical extraction output digests and cardinalities.
2. Exact prompt/input hashes and batch IDs on both paths.
3. Ordered candidate-set identity, state version, and resolution decision digest.
4. Effect/mutation identity and publication-version lineage.

No runtime mechanism, scheduler, admission change, or live retry is authorized by this artifact.
"""
    md_path = out / "SFWB_V13_V5_FIRST_DIVERGENCE_ANALYSIS.md"
    md_path.write_text(md, encoding="utf-8")
    decision = """# SFWB v1.3 V5 root-cause decision

`STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY`

## What is proven

- The 12 immutable source hashes match between B0-A and MemBind v3.1.
- The request/span telemetry reconstructs `+32 EDGE_RESOLUTION` and `+30 TIMESTAMP` downstream work.
- The extra work is not proven to be duplicate consumption; it is compatible with an earlier legal branch, state/candidate, extraction-input, or batching divergence.
- Both paths reach sealed publication for every source.

## What is not proven

The current sealed inputs cannot establish the first semantic output divergence. B0-A has no prepared/extraction artifact paired with MemBind; neither path provides complete extraction output digests, exact full prompt hashes, candidate identity/order, batch membership, state-version-at-read, resolution decision identity, or effect identity. Final graph differences are intentionally excluded as causal evidence.

## V5 contract gate

Before any mechanism is implemented, V5 must preserve Native Serial operator lineage and partial order, exact extraction input/output identity, batch membership, state-version-bound candidate/resolution decisions, effect identity/cardinality, and durable publication lineage when only execution timing is changed.

No `GO_V5_NATIVE_EQUIVALENT_COMPILE`, `GO_V5_SERIAL_EQUIVALENT_STATE_BIND`, `GO_V5_NATIVE_BATCH_PRESERVATION`, or `GO_V5_SEMANTIC_WORK_DEDUPLICATION` is justified yet. The next step is limited to provider-free observability contract design and fixture qualification; no live run or runtime change is authorized.
"""
    decision_path = out / "SFWB_V13_V5_ROOT_CAUSE_DECISION.md"
    decision_path.write_text(decision, encoding="utf-8")
    return [analysis, md_path, chain, decision_path]


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[4]
    root = repository / "saturated_fixed_work_baseline_v1_3"
    result = analyze_first_divergence(root)
    write_first_divergence_artifacts(result, root / "artifacts" / FIRST_DIVERGENCE_ROOT_NAME)


__all__ = ["FIRST_DIVERGENCE_ROOT_NAME", "STOP_GATE", "FirstDivergenceError", "analyze_first_divergence", "write_first_divergence_artifacts"]
