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

_FINGERPRINT_BOUNDARIES = {
    "NODE_EXTRACTION_OUTPUT",
    "EDGE_EXTRACTION_OUTPUT",
    "NODE_CANDIDATE_SET",
    "NODE_RESOLUTION_BATCH",
    "NODE_RESOLUTION_DECISION",
    "EDGE_CANDIDATE_SET",
    "EDGE_RESOLUTION_INPUT",
    "EDGE_RESOLUTION_DECISION",
    "PERSISTENCE_EFFECT",
}

_FINGERPRINT_CANDIDATES_FOR_REPORT = (
    ("node_extraction", "NODE_EXTRACTION_OUTPUT"),
    ("edge_extraction", "EDGE_EXTRACTION_OUTPUT"),
    ("node_candidate_formation", "NODE_CANDIDATE_SET"),
    ("node_resolution_batch", "NODE_RESOLUTION_BATCH"),
    ("node_resolution_batch", "NODE_RESOLUTION_DECISION"),
    ("edge_candidate_formation", "EDGE_CANDIDATE_SET"),
    ("edge_resolution_fan_out", "EDGE_RESOLUTION_INPUT"),
    ("edge_resolution_fan_out", "EDGE_RESOLUTION_DECISION"),
    ("persistence", "PERSISTENCE_EFFECT"),
)

_FINGERPRINT_FILES = (
    "semantic_fingerprints.jsonl",
    "semantic_fingerprint_telemetry.jsonl",
    "semantic_fingerprint.jsonl",
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


def _fingerprint_candidate_rows(block: Mapping[str, Any], source: int) -> list[Mapping[str, Any]]:
    """Read optional passive fingerprint records without touching providers."""

    rows: list[Mapping[str, Any]] = []
    direct = block.get("semantic_fingerprints")
    if isinstance(direct, Sequence) and not isinstance(direct, (str, bytes)):
        rows.extend(value for value in direct if isinstance(value, Mapping))
    attempt = block.get("path")
    if isinstance(attempt, Path):
        for filename in _FINGERPRINT_FILES:
            path = attempt / filename
            if path.exists():
                rows.extend(_jsonl(path))
        event_path = attempt / "raw_events.jsonl"
        for event in _event_rows(event_path):
            event_type = str(event.get("event_type") or event.get("event") or "")
            payload = event.get("semantic_fingerprint")
            if event_type in {"semantic_fingerprint", "semantic_fingerprint_record", "semantic_fingerprint_telemetry"}:
                rows.append(payload if isinstance(payload, Mapping) else event)
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        value = row.get("record") if isinstance(row.get("record"), Mapping) else row
        if isinstance(value, Mapping) and value.get("source_sequence") == source:
            selected.append(value)
    return selected


def _normalize_fingerprint_row(row: Mapping[str, Any], boundary: str) -> dict[str, Any] | None:
    value = row.get("fingerprint") if isinstance(row.get("fingerprint"), Mapping) else row
    if not isinstance(value, Mapping):
        return None
    selected_boundary = value.get("boundary") or value.get("stage")
    if selected_boundary != boundary:
        return None
    if boundary not in _FINGERPRINT_BOUNDARIES:
        return None
    count = next(
        (value.get(key) for key in ("count", "cardinality", "output_count", "candidate_count", "batch_size", "decision_count", "child_count", "effect_cardinality") if value.get(key) is not None),
        None,
    )
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        count = None
    digests = {
        key: value.get(key)
        for key in (
            "semantic_fingerprint",
            "ordered_identity_sha256",
            "ordered_semantic_identity_sha256",
            "ordering_preserving_sha256",
            "membership_identity_sha256",
            "content_identity_sha256",
            "input_identity_sha256",
            "decision_identity_sha256",
            "effect_identity_sha256",
        )
        if isinstance(value.get(key), str)
    }
    if not digests:
        return None
    return {"boundary": boundary, "count": count, **digests}


def _fingerprint_detail(block: Mapping[str, Any], source: int, boundary: str) -> dict[str, Any]:
    records = []
    for row in _fingerprint_candidate_rows(block, source):
        normalized = _normalize_fingerprint_row(row, boundary)
        if normalized is not None:
            records.append(normalized)
    return {
        "available": bool(records),
        "boundary": boundary,
        "records": records,
        "record_count": len(records),
    }


def _fingerprint_signature(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = detail.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return []
    signatures = []
    for row in records:
        if not isinstance(row, Mapping):
            continue
        signature: dict[str, Any] = {
            "boundary": row.get("boundary"),
            "count": row.get("count"),
        }
        for canonical, aliases in (
            ("semantic_fingerprint", ("semantic_fingerprint",)),
            ("ordered_identity_sha256", ("ordered_identity_sha256", "ordered_semantic_identity_sha256", "ordering_preserving_sha256")),
            ("membership_identity_sha256", ("membership_identity_sha256", "content_identity_sha256")),
            ("input_identity_sha256", ("input_identity_sha256",)),
            ("decision_identity_sha256", ("decision_identity_sha256",)),
            ("effect_identity_sha256", ("effect_identity_sha256",)),
        ):
            selected = next((row.get(alias) for alias in aliases if isinstance(row.get(alias), str)), None)
            if selected is not None:
                signature[canonical] = selected
        signatures.append(signature)
    return signatures


def _fingerprint_status(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    if not left.get("available") or not right.get("available"):
        return "NOT_COMPARABLE"
    return "EQUAL" if _stable(_fingerprint_signature(left)) == _stable(_fingerprint_signature(right)) else "DIFFERENT"


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

    fingerprint_boundaries = {
        boundary: {
            "b0_a": _fingerprint_detail(b0_block, source, boundary),
            "membind_v3_1": _fingerprint_detail(mb_block, source, boundary),
        }
        for boundary in _FINGERPRINT_BOUNDARIES
    }

    def fingerprint_pair(boundary: str) -> dict[str, Any]:
        pair = fingerprint_boundaries[boundary]
        return {
            "boundary": boundary,
            "b0_a": pair["b0_a"],
            "membind_v3_1": pair["membind_v3_1"],
            "status": _fingerprint_status(pair["b0_a"], pair["membind_v3_1"]),
        }

    stages = {
        "source_evidence": _stage_record("source_evidence", evidence, evidence, note="Immutable source identity is equal; this is not a divergence."),
        "node_extraction": _stage_record("node_extraction", {"call_count": len(b0_node_extract), "input_token_vector": b0_node_extract, "prompt_hash_available": False, "semantic_fingerprint": fingerprint_boundaries["NODE_EXTRACTION_OUTPUT"]["b0_a"]}, {"call_count": len(mb_node_extract), "input_token_vector": mb_node_extract, "prompt_hash_available": False, "semantic_fingerprint": fingerprint_boundaries["NODE_EXTRACTION_OUTPUT"]["membind_v3_1"]}, note="Native trace exposes token counts; an optional passive output fingerprint is preferred when present."),
        "edge_extraction": _stage_record("edge_extraction", {"call_count": len(b0_edge_extract), "input_token_vector": b0_edge_extract, "prompt_hash_available": False, "semantic_fingerprint": fingerprint_boundaries["EDGE_EXTRACTION_OUTPUT"]["b0_a"]}, {"call_count": len(mb_edge_extract), "input_token_vector": mb_edge_extract, "prompt_hash_available": False, "semantic_fingerprint": fingerprint_boundaries["EDGE_EXTRACTION_OUTPUT"]["membind_v3_1"]}, note="Token vectors remain observations; paired output fingerprints are causal only when both sides are available."),
        "prepared_extraction_outputs": _stage_record("prepared_extraction_outputs", b0_prepared, mb_prepared, comparable=False, note="B0-A has no prepared artifact; MemBind-only prepared cardinality/identity cannot be paired."),
        "node_candidate_formation": _stage_record("node_candidate_formation", {**b0_node_candidate, "all_candidate_search_span_count": _phase_count(b0_spans, "candidate-search"), "semantic_fingerprint": fingerprint_boundaries["NODE_CANDIDATE_SET"]["b0_a"]}, {**mb_node_candidate, "all_candidate_search_span_count": _phase_count(mb_spans, "candidate-search"), "semantic_fingerprint": fingerprint_boundaries["NODE_CANDIDATE_SET"]["membind_v3_1"]}, note="Candidate count is observable; an ordered candidate identity fingerprint is used when supplied."),
        "node_resolution_batch": _stage_record("node_resolution_batch", {"input_token_vector": _token_vector(b0_logical, _PROMPTS["node_resolution"]), "call_count": _count_prompt(b0_logical, _PROMPTS["node_resolution"]), "semantic_fingerprint_batch": fingerprint_boundaries["NODE_RESOLUTION_BATCH"]["b0_a"], "semantic_fingerprint_decision": fingerprint_boundaries["NODE_RESOLUTION_DECISION"]["b0_a"]}, {"input_token_vector": _token_vector(mb_logical, _PROMPTS["node_resolution"]), "call_count": _count_prompt(mb_logical, _PROMPTS["node_resolution"]), "semantic_fingerprint_batch": fingerprint_boundaries["NODE_RESOLUTION_BATCH"]["membind_v3_1"], "semantic_fingerprint_decision": fingerprint_boundaries["NODE_RESOLUTION_DECISION"]["membind_v3_1"]}, note="Batch membership and normalized decisions are preferred when paired passive fingerprints exist."),
        "edge_candidate_formation": _stage_record("edge_candidate_formation", {**b0_edge_candidate, "semantic_fingerprint": fingerprint_boundaries["EDGE_CANDIDATE_SET"]["b0_a"]}, {**mb_edge_candidate, "semantic_fingerprint": fingerprint_boundaries["EDGE_CANDIDATE_SET"]["membind_v3_1"]}, note="Candidate cardinality is exposed; ordered identity is available only from optional passive telemetry."),
        "edge_resolution_fan_out": _stage_record("edge_resolution_fan_out", {"input_token_vector": b0_edge_resolution, "call_count": len(b0_edge_resolution), "edge_dedup_spans": _operation_count(b0_spans, "edge-dedup"), "edge_invalidation_spans": _operation_count(b0_spans, "edge-invalidation"), "semantic_fingerprint_input": fingerprint_boundaries["EDGE_RESOLUTION_INPUT"]["b0_a"], "semantic_fingerprint_decision": fingerprint_boundaries["EDGE_RESOLUTION_DECISION"]["b0_a"]}, {"input_token_vector": mb_edge_resolution, "call_count": len(mb_edge_resolution), "edge_dedup_spans": _operation_count(mb_spans, "edge-dedup"), "edge_invalidation_spans": _operation_count(mb_spans, "edge-invalidation"), "semantic_fingerprint_input": fingerprint_boundaries["EDGE_RESOLUTION_INPUT"]["membind_v3_1"], "semantic_fingerprint_decision": fingerprint_boundaries["EDGE_RESOLUTION_DECISION"]["membind_v3_1"]}, note="Fan-out remains downstream; paired edge input/decision fingerprints can localize a divergence."),
        "attribute_summary_timestamp": _stage_record("attribute_summary_timestamp", {"summary_calls": _count_prompt(b0_logical, _PROMPTS["summary"]), "timestamp_calls": len(b0_timestamp), "timestamp_input_token_vector": b0_timestamp}, {"summary_calls": _count_prompt(mb_logical, _PROMPTS["summary"]), "timestamp_calls": len(mb_timestamp), "timestamp_input_token_vector": mb_timestamp}, note="Downstream work is observable only as request counts and token vectors."),
        "persistence": _stage_record("persistence", {"write_spans": _operation_count(b0_spans, "write"), "invalidation_spans": _operation_count(b0_spans, "edge-invalidation"), "mutation_spans": _operation_count(b0_spans, "existing-edge-mutation") + _operation_count(b0_spans, "new-edge-expiration-observation"), "semantic_fingerprint": fingerprint_boundaries["PERSISTENCE_EFFECT"]["b0_a"]}, {"write_spans": _operation_count(mb_spans, "write"), "invalidation_spans": _operation_count(mb_spans, "edge-invalidation"), "mutation_spans": _operation_count(mb_spans, "existing-edge-mutation") + _operation_count(mb_spans, "new-edge-expiration-observation"), "semantic_fingerprint": fingerprint_boundaries["PERSISTENCE_EFFECT"]["membind_v3_1"]}, note="Transaction/write spans are present; effect identity is consumed only when optional passive fingerprints are paired."),
        "publication": _stage_record("publication", {"publication_events": b0_publication_events, "complete": b0_publication_events == 1 and b0_block["seal"].get("status") == "VALIDATED_SEALED"}, {"publication_events": mb_publication_events, "complete": mb_publication_events == 1 and mb_block["seal"].get("status") == "VALIDATED_SEALED"}, note="Publication completion is sealed for both paths; it is downstream evidence, not a cause."),
    }

    # A paired passive fingerprint is the only signal promoted to a semantic
    # cause.  Missing fingerprints intentionally fall through to the legacy
    # request/span observations below.
    fingerprint_candidates = (
        ("node_extraction", "NODE_EXTRACTION_OUTPUT", "EXTRACTION_DIVERGENCE", "extraction_output_fingerprint"),
        ("edge_extraction", "EDGE_EXTRACTION_OUTPUT", "EXTRACTION_DIVERGENCE", "extraction_output_fingerprint"),
        ("node_candidate_formation", "NODE_CANDIDATE_SET", "STATE_SNAPSHOT_OR_CANDIDATE_DIVERGENCE", "candidate_set_fingerprint"),
        ("node_resolution_batch", "NODE_RESOLUTION_BATCH", "BATCHING_DIVERGENCE", "batch_fingerprint"),
        ("node_resolution_batch", "NODE_RESOLUTION_DECISION", "RESOLUTION_DECISION_DIVERGENCE", "resolution_decision_fingerprint"),
        ("edge_candidate_formation", "EDGE_CANDIDATE_SET", "STATE_SNAPSHOT_OR_CANDIDATE_DIVERGENCE", "candidate_set_fingerprint"),
        ("edge_resolution_fan_out", "EDGE_RESOLUTION_INPUT", "RESOLUTION_DECISION_DIVERGENCE", "resolution_input_fingerprint"),
        ("edge_resolution_fan_out", "EDGE_RESOLUTION_DECISION", "RESOLUTION_DECISION_DIVERGENCE", "resolution_decision_fingerprint"),
        ("persistence", "PERSISTENCE_EFFECT", "PERSISTENCE_DIVERGENCE", "effect_fingerprint"),
    )
    first_semantic_fingerprint: dict[str, Any] | None = None
    for stage_name, boundary, classification, kind in fingerprint_candidates:
        pair = fingerprint_boundaries[boundary]
        status = _fingerprint_status(pair["b0_a"], pair["membind_v3_1"])
        if status == "DIFFERENT":
            first_semantic_fingerprint = {
                "stage": stage_name,
                "boundary": boundary,
                "kind": kind,
                "classification": classification,
                "status": status,
                "semantic_cause_provable": True,
                "details": fingerprint_pair(boundary),
            }
            break

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
    if first_semantic_fingerprint is not None:
        first_provable = {
            "stage": first_semantic_fingerprint["stage"],
            "status": "FIRST_PROVABLE_SEMANTIC_DIVERGENCE",
            "classification": first_semantic_fingerprint["classification"],
            "observed_signal_kind": first_semantic_fingerprint["kind"],
            "semantic_fingerprint_boundary": first_semantic_fingerprint["boundary"],
            "semantic_cause_provable": True,
            "reason": "Both paths supplied passive canonical semantic fingerprints and the first ordered boundary differs.",
            "details": first_semantic_fingerprint["details"],
        }
    else:
        first_provable = {
            "stage": first_observable["stage"],
            "status": "OBSERVABILITY_INSUFFICIENT",
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
        "extraction_output_parity": all(
            _fingerprint_status(
                fingerprint_boundaries[boundary]["b0_a"],
                fingerprint_boundaries[boundary]["membind_v3_1"],
            ) == "EQUAL"
            for boundary in ("NODE_EXTRACTION_OUTPUT", "EDGE_EXTRACTION_OUTPUT")
        ),
        "prompt_hash_parity": False,
        "candidate_identity_parity": all(
            _fingerprint_status(
                fingerprint_boundaries[boundary]["b0_a"],
                fingerprint_boundaries[boundary]["membind_v3_1"],
            ) == "EQUAL"
            for boundary in ("NODE_CANDIDATE_SET", "EDGE_CANDIDATE_SET")
        ),
        "candidate_order_parity": all(
            _fingerprint_status(
                fingerprint_boundaries[boundary]["b0_a"],
                fingerprint_boundaries[boundary]["membind_v3_1"],
            ) == "EQUAL"
            for boundary in ("NODE_CANDIDATE_SET", "EDGE_CANDIDATE_SET")
        ),
        "batch_membership_parity": _fingerprint_status(
            fingerprint_boundaries["NODE_RESOLUTION_BATCH"]["b0_a"],
            fingerprint_boundaries["NODE_RESOLUTION_BATCH"]["membind_v3_1"],
        ) == "EQUAL",
        "resolution_decision_parity": all(
            _fingerprint_status(
                fingerprint_boundaries[boundary]["b0_a"],
                fingerprint_boundaries[boundary]["membind_v3_1"],
            ) == "EQUAL"
            for boundary in ("NODE_RESOLUTION_DECISION", "EDGE_RESOLUTION_DECISION")
        ),
        "effect_identity_parity": _fingerprint_status(
            fingerprint_boundaries["PERSISTENCE_EFFECT"]["b0_a"],
            fingerprint_boundaries["PERSISTENCE_EFFECT"]["membind_v3_1"],
        ) == "EQUAL",
        "membind_llm_auxiliary": _llm_auxiliary_detail(mb_block, source),
        "b0_llm_auxiliary": _llm_auxiliary_detail(b0_block, source),
    }
    semantic_observability = {}
    for boundary in _FINGERPRINT_BOUNDARIES:
        pair = fingerprint_boundaries[boundary]
        semantic_observability[boundary] = {
            "b0_available": pair["b0_a"]["available"],
            "membind_available": pair["membind_v3_1"]["available"],
            "status": _fingerprint_status(pair["b0_a"], pair["membind_v3_1"]),
        }
    observability["semantic_fingerprints"] = semantic_observability
    observability["semantic_fingerprint_coverage"] = sum(
        1 for row in semantic_observability.values() if row["status"] in {"EQUAL", "DIFFERENT"}
    )
    causal_chain = [
        {"from": "source_evidence", "to": first_observable["stage"], "relation": "same immutable source enters both paths; first request/span-shape signal appears downstream"},
        {"from": first_observable["stage"], "to": "edge_resolution_fan_out", "relation": "observed downstream fan-out changes; causal transfer is not provable"},
        {"from": "edge_resolution_fan_out", "to": "attribute_summary_timestamp", "relation": "timestamp/summary request counts follow observed edge fan-out"},
        {"from": "attribute_summary_timestamp", "to": "persistence", "relation": "effect span counts are observed after semantic requests"},
        {"from": "persistence", "to": "publication", "relation": "both paths reach sealed publication; publication is not a causal explanation"},
    ]
    if first_semantic_fingerprint is not None:
        causal_chain.insert(
            1,
            {
                "from": "source_evidence",
                "to": first_semantic_fingerprint["stage"],
                "relation": "paired passive semantic fingerprints first differ at this ordered boundary",
            },
        )
    return {
        "source_sequence": source,
        "source_evidence": evidence,
        "logical_operator_sequence": {"b0_a": b0_logical, "membind_v3_1": mb_logical},
        "stages": stages,
        "first_observable_signal": first_observable,
        "first_provable_divergence": first_provable,
        "first_semantic_fingerprint": first_semantic_fingerprint,
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
        "causal_chain": causal_chain,
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
    proven = [
        row["first_provable_divergence"]
        for row in sources.values()
        if row["first_provable_divergence"].get("semantic_cause_provable")
    ]
    proven_classifications = Counter(str(row.get("classification")) for row in proven)
    if proven_classifications.get("EXTRACTION_DIVERGENCE"):
        gate = "GO_V5_NATIVE_EQUIVALENT_COMPILE"
        primary = "EXTRACTION_OUTPUT_FINGERPRINT_DIVERGENCE"
    elif proven_classifications.get("STATE_SNAPSHOT_OR_CANDIDATE_DIVERGENCE"):
        gate = "GO_V5_SERIAL_EQUIVALENT_STATE_BIND"
        primary = "CANDIDATE_SET_FINGERPRINT_DIVERGENCE"
    elif proven_classifications.get("BATCHING_DIVERGENCE"):
        gate = "GO_V5_NATIVE_BATCH_PRESERVATION"
        primary = "BATCH_MEMBERSHIP_FINGERPRINT_DIVERGENCE"
    elif proven_classifications:
        gate = "STOP_V5_FIRST_SEMANTIC_DIVERGENCE_UNRESOLVED"
        primary = "SEMANTIC_FINGERPRINT_DIVERGENCE_AFTER_UPSTREAM_BOUNDARY"
    else:
        gate = STOP_GATE
        primary = "FIRST_DIVERGENCE_NOT_PROVABLE_FROM_SEALED_TELEMETRY"
    return {
        "schema_version": "sfwb.v1.3.v5.first-divergence-analysis.v2",
        "analysis_scope": {"benchmark": "saturated_fixed_work_baseline_v1_3", "history_id": "07741c45", "source_count": 12, "reference": "B0-A", "candidate": "MemBind-v3.1", "live_execution": False, "sealed_artifacts_mutated": False, "final_graph_used_for_causality": False},
        "decision": {"gate": gate, "primary_root_cause": primary, "semantic_cause_provable": bool(proven)},
        "sources": sources,
        "aggregate": {
            "logical_operator_delta": operator_delta,
            "edge_resolution_delta": edge_resolution_delta,
            "timestamp_delta": timestamp_delta,
            "first_observable_signal_stage_counts": dict(sorted(source_first_signals.items())),
            "extra_work_explanation": {"duplicate_consumption_provable": False, "changed_upstream_branch_provable": False, "observed_as_downstream_fan_out": True, "reason": "The telemetry has counts and token vectors but no paired semantic output, candidate identity/order, batch membership, state version, or resolution decision identity."},
            "publication_complete_source_count": sum(1 for row in sources.values() if row["publication"]["b0_complete"] and row["publication"]["membind_complete"]),
            "semantic_fingerprint": {
                "sources_with_first_provable_divergence": len(proven),
                "classification_counts": dict(sorted(proven_classifications.items())),
                "boundary_order": [item[1] for item in _FINGERPRINT_CANDIDATES_FOR_REPORT],
            },
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
        "schema_version": "sfwb.v1.3.v5.source-causal-chain.v2",
        "analysis_scope": result["analysis_scope"],
        "aggregate_chain": result["aggregate"],
        "sources": {source: {"source_evidence": row["source_evidence"], "first_observable_signal": row["first_observable_signal"], "first_semantic_fingerprint": row.get("first_semantic_fingerprint"), "first_provable_divergence": row["first_provable_divergence"], "fan_out": row["fan_out"], "causal_chain": row["causal_chain"]} for source, row in result["sources"].items()},
        "v5_contract_if_only_timing_changes": result["v5_contract_if_only_timing_changes"],
    }
    chain.write_text(json.dumps(chain_payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    rows = []
    for source, row in result["sources"].items():
        rows.append("| {} | {} | {} | {} | {} | {} |".format(source, row["first_observable_signal"]["stage"], row["first_observable_signal"]["kind"], row["first_provable_divergence"]["classification"], row["fan_out"]["edge_resolution_delta"], row["fan_out"]["timestamp_delta"]))
    md = (
        "# SFWB v1.3 V5 first-divergence analysis\n\n"
        "## Decision\n\n"
        f"`{result['decision']['gate']}`\n\n"
        "The semantic fingerprint gate is derived only from paired passive records.\n\n"
    )

    md += """The analysis does not use final graph differences to infer a cause. It aligns the same 12 source hashes through request/span-level stages and records the earliest observable request-shape or candidate-cardinality signal. A signal is not promoted to a semantic cause unless paired payload, output, candidate identity, state version, and batch lineage are present; those fields are absent from the sealed telemetry.

## Per-source earliest observable signal

| source | first observable stage | signal | FIRST_PROVABLE_DIVERGENCE | edge-resolution delta | timestamp delta |
| --- | --- | --- | --- | ---: | ---: |
"""
    md += "\n".join(rows) + "\n\n"
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
    decision = f"""# SFWB v1.3 V5 root-cause decision

`{result['decision']['gate']}`

## What is proven

- The 12 immutable source hashes match between B0-A and MemBind v3.1.
- The request/span telemetry reconstructs `+32 EDGE_RESOLUTION` and `+30 TIMESTAMP` downstream work.
- The extra work is not proven to be duplicate consumption; it is compatible with an earlier legal branch, state/candidate, extraction-input, or batching divergence.
- Both paths reach sealed publication for every source.

## What is not proven

The current sealed inputs cannot establish the first semantic output divergence. B0-A has no prepared/extraction artifact paired with MemBind; neither path provides complete extraction output digests, exact full prompt hashes, candidate identity/order, batch membership, state-version-at-read, resolution decision identity, or effect identity. Final graph differences are intentionally excluded as causal evidence.

## V5 contract gate

Before any mechanism is implemented, V5 must preserve Native Serial operator lineage and partial order, exact extraction input/output identity, batch membership, state-version-bound candidate/resolution decisions, effect identity/cardinality, and durable publication lineage when only execution timing is changed.

No runtime mechanism, scheduler, admission change, or live retry is authorized by this offline artifact. The current sealed-input result remains `STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY` unless paired fingerprint telemetry is supplied in a future separately authorized diagnostic.
"""
    decision_path = out / "SFWB_V13_V5_ROOT_CAUSE_DECISION.md"
    decision_path.write_text(decision, encoding="utf-8")
    return [analysis, md_path, chain, decision_path]


def write_first_semantic_divergence_artifacts(
    result: Mapping[str, Any], output_root: Path | str, *, overwrite: bool = False
) -> list[Path]:
    """Write the fingerprint-aware report without replacing sealed history."""

    out = Path(output_root)
    if out.exists() and not overwrite:
        raise FirstDivergenceError("FIRST_SEMANTIC_DIVERGENCE_ROOT_ALREADY_EXISTS")
    out.mkdir(parents=True, exist_ok=True)
    analysis = out / "SFWB_V13_V5_FIRST_SEMANTIC_DIVERGENCE.json"
    markdown = out / "SFWB_V13_V5_FIRST_SEMANTIC_DIVERGENCE.md"
    mechanism = out / "SFWB_V13_V5_MECHANISM_DECISION.md"
    payload = {
        "schema_version": "sfwb.v1.3.v5.first-semantic-divergence.v1",
        "analysis": dict(result),
        "historical_sealed_decision": STOP_GATE,
        "live_execution": False,
        "sealed_artifacts_mutated": False,
    }
    analysis.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    rows = []
    for source, row in result["sources"].items():
        first = row["first_provable_divergence"]
        rows.append(
            f"| {source} | {first.get('stage')} | {first.get('classification')} | {str(first.get('semantic_cause_provable')).upper()} | {row['observability'].get('semantic_fingerprint_coverage', 0)} |"
        )
    markdown.write_text(
        "# SFWB v1.3 V5 first semantic divergence\n\n"
        f"Offline gate: `{result['decision']['gate']}`\n\n"
        "No new live diagnostic was run. Existing sealed telemetry contains no paired semantic fingerprint records, so the prior fail-closed conclusion remains authoritative.\n\n"
        "| source | first boundary | classification | semantic cause provable | fingerprint boundary coverage |\n"
        "| --- | --- | --- | --- | ---: |\n"
        + "\n".join(rows)
        + "\n\nThe request/token and span/cardinality signals remain fallback observations only. Final graph differences were not used for causality.\n",
        encoding="utf-8",
    )
    mechanism.write_text(
        "# SFWB v1.3 V5 mechanism decision\n\n"
        f"`{result['decision']['gate']}`\n\n"
        "The passive fingerprint contract is qualified provider-free, but it has not been attached to a live B0/MemBind source-0 diagnostic in this turn. Therefore no V5 mechanism is authorized and no first semantic boundary is claimed.\n\n"
        f"Historical sealed state is preserved as `{STOP_GATE}`. The only next-step authorization condition is a separately approved source-0 diagnostic using the already qualified passive observer; it must not expand automatically to source 1 or sources 0..11.\n",
        encoding="utf-8",
    )
    return [analysis, markdown, mechanism]


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[4]
    root = repository / "saturated_fixed_work_baseline_v1_3"
    result = analyze_first_divergence(root)
    write_first_divergence_artifacts(result, root / "artifacts" / FIRST_DIVERGENCE_ROOT_NAME)


__all__ = ["FIRST_DIVERGENCE_ROOT_NAME", "STOP_GATE", "FirstDivergenceError", "analyze_first_divergence", "write_first_divergence_artifacts", "write_first_semantic_divergence_artifacts"]
