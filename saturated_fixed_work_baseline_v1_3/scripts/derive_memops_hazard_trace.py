#!/usr/bin/env python3
"""Derive minimal state-hazard observability from existing sealed traces.

This is passive post-processing.  It does not import or call Graphiti, vLLM,
Neo4j, the QA lane, or either execution policy.  It uses timestamps and span
metadata already emitted by the native v1.3 trace and marks unavailable
semantic identities explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def phase_spans(spans: list[Mapping[str, Any]], phase: str) -> list[Mapping[str, Any]]:
    return [span for span in spans if span.get("phase") == phase]


def first_time(spans: list[Mapping[str, Any]], key: str = "start_ns") -> int | None:
    values = [int(span[key]) for span in spans if isinstance(span.get(key), int)]
    return min(values) if values else None


def last_time(spans: list[Mapping[str, Any]], key: str = "end_ns") -> int | None:
    values = [int(span[key]) for span in spans if isinstance(span.get(key), int)]
    return max(values) if values else None


def frontier_at(publications: Mapping[int, int], timestamp_ns: int) -> int:
    visible = sorted(sequence for sequence, value in publications.items() if value <= timestamp_ns)
    return visible[-1] if visible else -1


def derive_attempt(attempt: Path, audit_root: Path) -> dict[str, Any]:
    block = read_json(attempt / "memops_block_result.json")
    metrics = block.get("metrics", {})
    events = read_jsonl(attempt / "raw_events.jsonl")
    traces = read_jsonl(attempt / "native_trace.jsonl")
    audit = read_json(audit_root / "hazard_audit.json")
    cohort_id = str(block.get("sample_id") or "")
    audit_rows = {
        f"{row['sample_id']}__{row['operation_type']}": row
        for row in audit.get("selected_samples", [])
    }
    hazard = audit_rows.get(cohort_id)
    if not isinstance(hazard, Mapping):
        raise RuntimeError(f"HAZARD_AUDIT_ROW_MISSING:{cohort_id}")
    raw = read_json(Path(str(hazard["source_file"])))
    conversations = raw.get("conversations", [])
    segment_to_sequence = {
        int(row["segment_index"]): sequence
        for sequence, row in enumerate(conversations)
        if isinstance(row, Mapping) and isinstance(row.get("segment_index"), int)
    }
    transitions_by_sequence: dict[int, list[dict[str, Any]]] = {}
    for pair in hazard.get("confirmed_transition_pairs", []):
        old_seq = segment_to_sequence.get(pair.get("old_segment_index"))
        new_seq = segment_to_sequence.get(pair.get("new_segment_index"))
        if old_seq is None or new_seq is None:
            continue
        transitions_by_sequence.setdefault(new_seq, []).append(
            {"predecessor_source_sequence": old_seq, **dict(pair)}
        )
    publications = {
        int(event["source_sequence"]): int(event["monotonic_ns"])
        for event in events
        if event.get("event") == "PUBLICATION_DURABLE"
        and isinstance(event.get("source_sequence"), int)
        and isinstance(event.get("monotonic_ns"), int)
    }
    source_records: list[dict[str, Any]] = []
    execution_starts: dict[int, int] = {}
    execution_ends: dict[int, int] = {}
    for trace in traces:
        source = int(trace.get("source_sequence"))
        spans = [span for span in trace.get("spans", []) if isinstance(span, Mapping)]
        event_starts = [
            int(event["monotonic_ns"])
            for event in events
            if event.get("event") == "EXECUTION_START"
            and event.get("source_sequence") == source
            and isinstance(event.get("monotonic_ns"), int)
        ]
        event_returns = [
            int(event["monotonic_ns"])
            for event in events
            if event.get("event") == "CALLER_RETURN"
            and event.get("source_sequence") == source
            and isinstance(event.get("monotonic_ns"), int)
        ]
        add_episode = phase_spans(spans, "add-episode")
        start_ns = min(event_starts or [first_time(add_episode) or 0])
        end_ns = max(event_returns or [last_time(add_episode) or start_ns])
        execution_starts[source] = start_ns
        execution_ends[source] = end_ns
    actual_admission_order = [source for source, _ in sorted(execution_starts.items(), key=lambda item: (item[1], item[0]))]
    for trace in traces:
        source = int(trace.get("source_sequence"))
        spans = [span for span in trace.get("spans", []) if isinstance(span, Mapping)]
        previous_context = phase_spans(spans, "previous-context")
        candidate_search = phase_spans(spans, "candidate-search")
        database = phase_spans(spans, "database")
        state_read_spans = previous_context or candidate_search or database
        first_state_read_ns = first_time(state_read_spans)
        dependency_rows = transitions_by_sequence.get(source, [])
        predecessor_info: list[dict[str, Any]] = []
        for dependency in dependency_rows:
            predecessor = int(dependency["predecessor_source_sequence"])
            predecessor_durable = publications.get(predecessor)
            read_before_durable = predecessor_durable is not None and first_state_read_ns is not None and first_state_read_ns < predecessor_durable
            predecessor_info.append(
                {
                    **dependency,
                    "predecessor_publication_durable_ns": predecessor_durable,
                    "first_state_dependent_read_ns": first_state_read_ns,
                    "read_before_predecessor_durable": read_before_durable,
                    "durable_frontier_at_first_state_read": frontier_at(publications, first_state_read_ns) if first_state_read_ns is not None else None,
                }
            )
        candidate_metadata = []
        for span in candidate_search:
            metadata = span.get("metadata") if isinstance(span.get("metadata"), Mapping) else {}
            candidate_metadata.append(
                {
                    "operation_class": span.get("operation_class"),
                    "candidate_count": metadata.get("candidate_count"),
                    "candidate_query_count": metadata.get("candidate_query_count"),
                    "phase": span.get("phase"),
                }
            )
        request_shape = []
        for span in spans:
            if span.get("operation_class") not in {"logical-call", "request-attempt"} and span.get("phase") not in {"llm", "llm-transport"}:
                continue
            metadata = span.get("metadata") if isinstance(span.get("metadata"), Mapping) else {}
            request_shape.append(
                {
                    "phase": span.get("phase"),
                    "operation_class": span.get("operation_class"),
                    "prompt_name": metadata.get("prompt_name"),
                    "input_tokens": metadata.get("input_tokens"),
                    "output_tokens": metadata.get("output_tokens"),
                    "retry_count": metadata.get("retry_count"),
                    "attempt_index": metadata.get("attempt_index"),
                }
            )
        publication_ns = publications.get(source)
        source_records.append(
            {
                "source_sequence": source,
                "source_hash": trace.get("source_hash"),
                "execution_start_ns": execution_starts.get(source),
                "execution_end_ns": execution_ends.get(source),
                "publication_durable_ns": publication_ns,
                "state_dependent": bool(dependency_rows),
                "state_dependent_read": predecessor_info,
                "first_state_dependent_read_ns": first_state_read_ns if dependency_rows else None,
                "durable_frontier_at_first_state_read": (
                    frontier_at(publications, first_state_read_ns) if dependency_rows and first_state_read_ns is not None else None
                ),
                "candidate_search_cardinality": {
                    "span_count": len(candidate_metadata),
                    "candidate_count_values": [row["candidate_count"] for row in candidate_metadata if isinstance(row.get("candidate_count"), int)],
                    "candidate_query_count_values": [row["candidate_query_count"] for row in candidate_metadata if isinstance(row.get("candidate_query_count"), int)],
                    "cardinality_fingerprint": digest(candidate_metadata),
                    "candidate_identity_fingerprint": "NOT_OBSERVABLE",
                },
                "resolution_extraction_request": {
                    "request_shape_fingerprint": digest(request_shape),
                    "semantic_prompt_fingerprint": "NOT_OBSERVABLE",
                    "output_semantic_fingerprint": "NOT_OBSERVABLE",
                    "request_shape": request_shape,
                },
                "read_before_dependency_durable": any(
                    row["read_before_predecessor_durable"] for row in predecessor_info
                ),
            }
        )
    unordered = actual_admission_order != sorted(actual_admission_order)
    overlap_pairs = []
    for left in source_records:
        for right in source_records:
            if left["source_sequence"] >= right["source_sequence"]:
                continue
            if left.get("execution_start_ns") is None or right.get("execution_start_ns") is None:
                continue
            if left["execution_start_ns"] < (right.get("execution_end_ns") or right["execution_start_ns"]):
                overlap_pairs.append([left["source_sequence"], right["source_sequence"]])
    mechanism_links = {
        "unordered_admission": "ESTABLISHED" if unordered else "NOT_OBSERVED",
        "predecessor_not_durable_at_state_read": "ESTABLISHED" if any(
            dependency.get("read_before_predecessor_durable")
            for source in source_records
            for dependency in source["state_dependent_read"]
        ) else "NOT_OBSERVED",
        "stale_or_different_graph_observation": "NOT_ESTABLISHED",
        "different_semantic_request_or_candidate_set": "NOT_ESTABLISHED",
        "additional_or_divergent_work": "NOT_ESTABLISHED",
        "semantic_consequence": "NOT_ESTABLISHED",
    }
    result = {
        "schema_version": "sfwb.v1.3.memops-hazard-observability.v1",
        "sample_id": cohort_id,
        "method": block.get("method"),
        "namespace": block.get("namespace"),
        "attempt_root": str(attempt),
        "source_count": len(source_records),
        "actual_admission_order": actual_admission_order,
        "unordered_admission": unordered,
        "overlap_pair_count": len(overlap_pairs),
        "overlap_pairs": overlap_pairs,
        "publication_durable_by_source": publications,
        "source_records": sorted(source_records, key=lambda row: row["source_sequence"]),
        "work_metrics": {
            key: metrics.get(key)
            for key in (
                "build_makespan_s", "llm_logical_calls", "llm_transport_attempts", "llm_input_tokens",
                "embedding_items", "db_writes", "whole_update_active_max", "whole_update_active_mean",
            )
        },
        "mechanism_links": mechanism_links,
        "observability_limits": [
            "Graph read returns are not captured as raw candidate identities; only candidate cardinality metadata is available.",
            "Prompt/output semantic fingerprints are not present in native trace; only request-shape fingerprints are derived.",
            "A final canonical graph is downstream state and is not used to infer the first read cause.",
        ],
    }
    result["payload_sha256"] = digest(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    args = parser.parse_args()
    result = derive_attempt(args.attempt.resolve(), args.audit_root.resolve())
    path = args.attempt.resolve() / "hazard_observability.json"
    if path.exists():
        raise SystemExit("HAZARD_OBSERVABILITY_ALREADY_EXISTS")
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(path), "payload_sha256": result["payload_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
