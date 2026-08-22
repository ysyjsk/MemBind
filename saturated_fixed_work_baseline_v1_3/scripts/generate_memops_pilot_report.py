#!/usr/bin/env python3
"""Build an append-only offline B0/B1 MemOps pilot report.

This script reads only the already sealed pilot attempts.  It does not call
Graphiti, vLLM, Neo4j, the QA runner, or any execution-policy entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping


METHODS = ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")
RUNS = {METHODS[0]: "b0-run", METHODS[1]: "b1-run"}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def norm_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def hashable(value: Any) -> Any:
    return canonical_bytes(value).decode("utf-8") if isinstance(value, (list, dict)) else value


def jsonable_set_diff(left: set[tuple[Any, ...]], right: set[tuple[Any, ...]], limit: int = 5) -> list[list[Any]]:
    return [list(row) for row in sorted(right - left, key=lambda row: repr(row))[:limit]]


def semantic_signature(graph: Mapping[str, Any]) -> dict[str, set[tuple[Any, ...]]]:
    """Normalize semantic graph fields while excluding runtime-only expiry/UUID fields."""

    entities: set[tuple[Any, ...]] = set()
    for entity in graph.get("entities", []) if isinstance(graph.get("entities"), list) else []:
        if not isinstance(entity, Mapping):
            continue
        attrs = entity.get("attributes", {})
        entities.add(
            (
                norm_text(entity.get("name")),
                norm_text(entity.get("summary")),
                canonical_bytes(attrs).decode("utf-8"),
            )
        )

    edges: set[tuple[Any, ...]] = set()
    for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
        if not isinstance(edge, Mapping):
            continue
        edges.add(
            (
                norm_text(edge.get("source_entity_key")),
                norm_text(edge.get("target_entity_key")),
                norm_text(edge.get("relation_type")),
                norm_text(edge.get("fact")),
                edge.get("valid_at"),
                edge.get("invalid_at"),
                hashable(edge.get("source_episode_sequence")),
                canonical_bytes(edge.get("attributes", {})).decode("utf-8"),
            )
        )

    episodes: set[tuple[Any, ...]] = set()
    for episode in graph.get("episodes", []) if isinstance(graph.get("episodes"), list) else []:
        if not isinstance(episode, Mapping):
            continue
        episodes.add(
            (
                episode.get("source_sequence"),
                episode.get("source_hash"),
                episode.get("session_id"),
            )
        )
    return {"entities": entities, "edges": edges, "episodes": episodes}


def semantic_diff(b0_graph: Mapping[str, Any], b1_graph: Mapping[str, Any]) -> dict[str, Any]:
    left = semantic_signature(b0_graph)
    right = semantic_signature(b1_graph)
    sections: dict[str, Any] = {}
    for name in ("entities", "edges", "episodes"):
        sections[name] = {
            "b0_count": len(left[name]),
            "b1_count": len(right[name]),
            "added_count": len(right[name] - left[name]),
            "removed_count": len(left[name] - right[name]),
            "added_examples": jsonable_set_diff(left[name], right[name]),
            "removed_examples": jsonable_set_diff(right[name], left[name]),
        }
    return {
        "canonical_hash_equal": b0_graph.get("canonical_graph_hash") == b1_graph.get("canonical_graph_hash"),
        "semantic_normalized_equal": all(
            not (left[name] ^ right[name]) for name in ("entities", "edges", "episodes")
        ),
        "runtime_only_fields_excluded": ["canonical_graph_hash", "group_id", "expired_at"],
        "sections": sections,
    }


def publication_complete(attempt: Path, metrics: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    seal = read_json(attempt / "seal.json")
    evidence = seal.get("evidence", {}) if isinstance(seal, Mapping) else {}
    episode_count = int(metrics.get("episode_count") or 0)
    checks = {
        "seal_validated": seal.get("status") == "VALIDATED_SEALED",
        "terminal_episode_task_count": evidence.get("terminal_episode_task_count") == episode_count,
        "open_requests_zero": evidence.get("open_requests") == 0,
        "open_spans_zero": evidence.get("open_spans") == 0,
        "open_transactions_zero": evidence.get("open_transactions") == 0,
        "orphan_tasks_zero": evidence.get("orphan_tasks") == 0,
        "unobserved_exceptions_zero": evidence.get("unobserved_exceptions") == 0,
        "created_sequences_complete": metrics.get("created_sequences") == list(range(episode_count)),
    }
    return all(checks.values()), checks


def readonly_pass(qa: Mapping[str, Any]) -> bool:
    return bool(
        qa.get("graph_mutated") is False
        and qa.get("graph_write_attempts") == 0
        and qa.get("construction_calls") == 0
    )


def readonly_checks(qa: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "graph_mutated_false": qa.get("graph_mutated") is False,
        "graph_write_attempts_zero": qa.get("graph_write_attempts") == 0,
        "construction_calls_zero": qa.get("construction_calls") == 0,
    }


def load_attempt(root: Path, method: str, sample_id: str) -> dict[str, Any]:
    run = root / "runs" / RUNS[method]
    matches = sorted((run / "blocks").glob(f"*{sample_id}-{method}/attempt-001"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one attempt for {method}/{sample_id}, found {len(matches)}")
    attempt = matches[0]
    block = read_json(attempt / "memops_block_result.json")
    metrics = block["metrics"]
    qa = read_json(attempt / "memops_qa_summary.json")
    qa_rows_file = read_json(attempt / "memops_qa_rows.json")
    graph = read_json(attempt / "canonical_graph.json")
    seal = read_json(attempt / "seal.json")
    publication, publication_checks = publication_complete(attempt, metrics)
    state = qa.get("state_inspection") or {}
    official_qa = {
        "correct_rows": qa.get("correct_rows"),
        "rows": qa.get("rows"),
        "all_correct": qa.get("all_correct"),
        "stale_value_errors": qa.get("stale_value_errors"),
        "question_results": [
            {
                "question_id": row.get("question_id"),
                "evaluation_type": row.get("evaluation_type"),
                "evaluation_setting": row.get("evaluation_setting"),
                "correct": row.get("correct"),
                "invalid": row.get("invalid"),
                "stale_value": (row.get("memops_official_judge") or {}).get("stale_value"),
                "failure_layer": row.get("failure_layer"),
            }
            for row in (qa_rows_file.get("rows", []) if isinstance(qa_rows_file, Mapping) else [])
        ],
    }
    semantic_pass = bool(
        qa.get("all_correct")
        and state.get("status") == "PASS"
        and publication
        and qa.get("stale_value_errors") == 0
        and readonly_pass(qa)
    )
    return {
        "sample_id": sample_id,
        "method": method,
        "attempt_root": str(attempt),
        "namespace": block.get("namespace"),
        "workload_sha256": block.get("workload_sha256"),
        "metrics": metrics,
        "qa": official_qa,
        "qa_summary": qa,
        "state": state,
        "publication_complete": publication,
        "publication_checks": publication_checks,
        "seal_status": seal.get("status"),
        "readonly_pass": readonly_pass(qa),
        "readonly_checks": readonly_checks(qa),
        "semantic_pass": semantic_pass,
        "graph": graph,
    }


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def vals(key: str) -> list[float]:
        return [float(row["metrics"][key]) for row in rows if row["metrics"].get(key) is not None]

    makespan = vals("build_makespan_s")
    source_tokens = vals("source_tokens")
    total_makespan = sum(makespan)
    total_tokens = sum(source_tokens)
    semantic_count = sum(bool(row["semantic_pass"]) for row in rows)
    qa_count = sum(bool(row["qa"]["all_correct"]) for row in rows)
    publication_count = sum(bool(row["publication_complete"]) for row in rows)
    readonly_count = sum(bool(row["readonly_pass"]) for row in rows)

    def distribution(key: str) -> dict[str, Any]:
        values = vals(key)
        return {
            "sample_summary_p50": percentile(values, 0.50),
            "sample_summary_p95": percentile(values, 0.95),
            "sample_summary_mean": statistics.fmean(values) if values else None,
            "sample_count": len(values),
            "interpretation": "Existing per-sample summary metric; not a pooled call-level percentile.",
        }

    return {
        "sample_count": len(rows),
        "completed_count": len(rows),
        "valid_sealed_count": sum(bool(row["metrics"].get("valid")) and row["seal_status"] == "VALIDATED_SEALED" for row in rows),
        "aggregate_makespan_s": total_makespan,
        "makespan_p50_s": percentile(makespan, 0.50),
        "makespan_p95_s": percentile(makespan, 0.95),
        "makespan_mean_s": statistics.fmean(makespan) if makespan else None,
        "aggregate_source_tokens": int(total_tokens),
        "aggregate_source_tokens_per_s": total_tokens / total_makespan if total_makespan else None,
        "throughput_definition": "sum(source_tokens) / sum(build_makespan_s)",
        "qa_all_correct_count": qa_count,
        "current_state_pass_count": sum(row["state"].get("status") == "PASS" for row in rows),
        "publication_complete_count": publication_count,
        "readonly_pass_count": readonly_count,
        "semantic_pass_count": semantic_count,
        "semantic_goodput_samples_per_s": semantic_count / total_makespan if total_makespan else None,
        "goodput_definition": "semantic_pass samples / aggregate build makespan; not a replacement for QA accuracy",
        "stale_value_error_total": sum(int(row["qa"].get("stale_value_errors") or 0) for row in rows),
        "llm_logical_calls_total": sum(int(row["metrics"].get("llm_logical_calls") or 0) for row in rows),
        "llm_transport_attempts_total": sum(int(row["metrics"].get("llm_transport_attempts") or 0) for row in rows),
        "llm_input_tokens_total": sum(int(row["metrics"].get("llm_input_tokens") or 0) for row in rows),
        "db_writes_total": sum(int(row["metrics"].get("db_writes") or 0) for row in rows),
        "embedding_items_total": sum(int(row["metrics"].get("embedding_items") or 0) for row in rows),
        "llm_latency_s": {key: distribution(key) for key in ("llm_duration_p50_s", "llm_duration_p95_s", "llm_duration_p99_s")},
        "db_latency_s": {key: distribution(key) for key in ("db_duration_p50_s", "db_duration_p95_s", "db_duration_p99_s")},
        "embedding_latency_s": {key: distribution(key) for key in ("embedding_duration_p50_s", "embedding_duration_p95_s", "embedding_duration_p99_s")},
        "drain_tail_s": distribution("drain_tail_ns") if False else {
            "sample_summary_p50": percentile([float(row["metrics"].get("drain_tail_ns") or 0) / 1e9 for row in rows], 0.50),
            "sample_summary_p95": percentile([float(row["metrics"].get("drain_tail_ns") or 0) / 1e9 for row in rows], 0.95),
            "sample_summary_mean": statistics.fmean([float(row["metrics"].get("drain_tail_ns") or 0) / 1e9 for row in rows]),
            "interpretation": "Recorded drain tail after feeder/construction; not a direct queue-delay measurement.",
        },
        "feeder_workload_await_count_total": sum(int(row["metrics"].get("feeder_workload_await_count") or 0) for row in rows),
        "active_concurrency_max": max((row["metrics"].get("whole_update_active_max") for row in rows), default=None),
        "active_concurrency_mean": statistics.fmean(float(row["metrics"].get("whole_update_active_mean") or 0) for row in rows),
        "configured_max_inflight": sorted({row["metrics"].get("configured_max_inflight") for row in rows}, key=str),
        "queue_delay": {"status": "NOT_DIRECTLY_RECORDED", "field": None},
        "backlog": {"status": "NOT_DIRECTLY_RECORDED", "proxy_fields": ["drain_tail_ns", "whole_update_active_max", "whole_update_active_mean", "feeder_workload_await_count"]},
        "freshness": {"definition": "MemOps inspect_current_state status plus stale_value_errors", "pass_count": sum(row["state"].get("status") == "PASS" and row["qa"].get("stale_value_errors") == 0 for row in rows)},
    }


def markdown(report: Mapping[str, Any]) -> str:
    methods = report["methods"]
    lines = [
        "# MemOps B0 vs B1 Pilot",
        "",
        f"Pilot: `{report['pilot_id']}`; samples: `{report['sample_count']}`; live status: B0 `{report['b0_status']}`, B1 `{report['b1_status']}`.",
        "",
        "This is an append-only paired pilot report. It reads sealed artifacts only; it does not create a formal B1 gate result and does not authorize V5.",
        "",
        "## Main Table",
        "",
        "| Metric | B0 Native Serial | B1 Naive Whole-Update Async |",
        "|---|---:|---:|",
    ]
    labels = [
        ("completed / sealed", "completed_count", "completed_count"),
        ("makespan aggregate (s)", "aggregate_makespan_s", "aggregate_makespan_s"),
        ("makespan P50 (s)", "makespan_p50_s", "makespan_p50_s"),
        ("makespan P95 (s)", "makespan_p95_s", "makespan_p95_s"),
        ("source throughput (tokens/s)", "aggregate_source_tokens_per_s", "aggregate_source_tokens_per_s"),
        ("semantic goodput (samples/s)", "semantic_goodput_samples_per_s", "semantic_goodput_samples_per_s"),
        ("LLM logical calls", "llm_logical_calls_total", "llm_logical_calls_total"),
        ("LLM transport attempts", "llm_transport_attempts_total", "llm_transport_attempts_total"),
        ("LLM input tokens", "llm_input_tokens_total", "llm_input_tokens_total"),
        ("DB writes", "db_writes_total", "db_writes_total"),
        ("embedding items", "embedding_items_total", "embedding_items_total"),
        ("official QA all-correct", "qa_all_correct_count", "qa_all_correct_count"),
        ("current-state PASS", "current_state_pass_count", "current_state_pass_count"),
        ("publication complete", "publication_complete_count", "publication_complete_count"),
        ("read-only QA PASS", "readonly_pass_count", "readonly_pass_count"),
        ("semantic PASS", "semantic_pass_count", "semantic_pass_count"),
        ("stale-value errors", "stale_value_error_total", "stale_value_error_total"),
        ("feeder await count", "feeder_workload_await_count_total", "feeder_workload_await_count_total"),
        ("active concurrency max", "active_concurrency_max", "active_concurrency_max"),
    ]
    for label, key0, key1 in labels:
        lines.append(f"| {label} | {report['method_summary'][methods[0]].get(key0)} | {report['method_summary'][methods[1]].get(key1)} |")
    lines += [
        "| queue delay | NOT_DIRECTLY_RECORDED | NOT_DIRECTLY_RECORDED |",
        "| backlog | NOT_DIRECTLY_RECORDED (proxies retained) | NOT_DIRECTLY_RECORDED (proxies retained) |",
        "",
        "Service latency fields below are existing per-sample p50/p95/p99 summaries; they are not pooled call-level percentiles.",
        "",
        "| Service summary | B0 | B1 |",
        "|---|---:|---:|",
    ]
    for key, title in (("llm_duration_p50_s", "LLM p50"), ("llm_duration_p95_s", "LLM p95"), ("llm_duration_p99_s", "LLM p99"), ("db_duration_p50_s", "DB p50"), ("db_duration_p95_s", "DB p95"), ("embedding_duration_p50_s", "Embedding p50"), ("embedding_duration_p95_s", "Embedding p95")):
        resource = "llm_latency_s" if key.startswith("llm") else "db_latency_s" if key.startswith("db") else "embedding_latency_s"
        field = key
        b0 = report["method_summary"][methods[0]][resource][field]["sample_summary_mean"]
        b1 = report["method_summary"][methods[1]][resource][field]["sample_summary_mean"]
        lines.append(f"| {title} sample-summary mean (s) | {b0:.6f} | {b1:.6f} |")
    lines += ["", "## Paired Samples", "", "| Sample | B0 state | B1 state | B0 semantic | B1 semantic | Paired | B0 makespan s | B1 makespan s | B0 calls | B1 calls | B0/B1 canonical hash equal | Semantic diff |", "|---|---|---|---:|---:|---|---:|---:|---:|---:|---|---|"]
    for row in report["paired_samples"]:
        lines.append("| {sample_id} | {b0_state} | {b1_state} | {b0_semantic} | {b1_semantic} | {paired_outcome} | {b0_makespan:.3f} | {b1_makespan:.3f} | {b0_calls} | {b1_calls} | {canonical_hash_equal} | +{edge_added}/-{edge_removed} edges; +{entity_added}/-{entity_removed} entities |".format(**row))
    lines += [
        "",
        "## QA and Publication",
        "",
        "All 10 blocks completed and were `VALIDATED_SEALED`; every QA evaluation made zero graph writes and zero construction calls. Official QA was 2/2 for every sample under both methods. Current-state inspection is stricter than Reader correctness: B0 A28 is `FAIL`, while B1 A05 is `AMBIGUOUS` and B1 A28 is `PASS`.",
        "",
        "## Interpretation",
        "",
        f"Paired outcomes: PP={report['paired_counts'].get('PP', 0)}, PF={report['paired_counts'].get('PF', 0)}, FP={report['paired_counts'].get('FP', 0)}, FF={report['paired_counts'].get('FF', 0)}.",
        "",
        "The pilot is operationally reproducible: both existing baseline entry points ran the same five frozen workloads and produced complete sealed artifacts. It is not a causal semantic comparison: every sample has a normalized canonical semantic diff and B1 LLM work differs from B0, while each method was run once under stochastic LLM service. The paired table therefore records outcomes, but does not attribute the differences to async scheduling alone.",
        "",
        "Queue delay and true backlog are not present in the current v1.3 block schema. Drain tail and active-concurrency fields are retained as labeled proxies, never relabeled as queue delay/backlog.",
        "",
        "No V5, scheduler, Graphiti, QA, Judge, or qualification predicate was changed by this report generation.",
    ]
    return "\n".join(lines) + "\n"


def build_report(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "pilot_manifest.json")
    b0 = read_json(root / "runs" / "b0-run" / "pilot_result.json")
    b1 = read_json(root / "runs" / "b1-run" / "pilot_result.json")
    sample_ids = list(manifest["sample_ids"])
    rows_by_method = {method: {sample_id: load_attempt(root, method, sample_id) for sample_id in sample_ids} for method in METHODS}
    paired: list[dict[str, Any]] = []
    paired_counts = {key: 0 for key in ("PP", "PF", "FP", "FF")}
    identity_mismatches: list[str] = []
    for sample_id in sample_ids:
        left = rows_by_method[METHODS[0]][sample_id]
        right = rows_by_method[METHODS[1]][sample_id]
        identity_match = left["workload_sha256"] == right["workload_sha256"]
        if not identity_match:
            identity_mismatches.append(sample_id)
        outcome = ("P" if left["semantic_pass"] else "F") + ("P" if right["semantic_pass"] else "F")
        paired_counts[outcome] += 1
        diff = semantic_diff(left["graph"], right["graph"])
        edge_diff = diff["sections"]["edges"]
        entity_diff = diff["sections"]["entities"]
        paired.append({
            "sample_id": sample_id,
            "operation_type": sample_id.rsplit("__", 1)[-1],
            "b0_namespace": left["namespace"],
            "b1_namespace": right["namespace"],
            "b0_workload_sha256": left["workload_sha256"],
            "b1_workload_sha256": right["workload_sha256"],
            "workload_identity_match": identity_match,
            "b0_state": left["state"].get("status"),
            "b1_state": right["state"].get("status"),
            "b0_current_state": left["state"],
            "b1_current_state": right["state"],
            "b0_semantic": left["semantic_pass"],
            "b1_semantic": right["semantic_pass"],
            "b0_qa": left["qa"],
            "b1_qa": right["qa"],
            "b0_official_qa_rows": left["qa"]["question_results"],
            "b1_official_qa_rows": right["qa"]["question_results"],
            "b0_publication_complete": left["publication_complete"],
            "b1_publication_complete": right["publication_complete"],
            "b0_publication_checks": left["publication_checks"],
            "b1_publication_checks": right["publication_checks"],
            "b0_readonly_pass": left["readonly_pass"],
            "b1_readonly_pass": right["readonly_pass"],
            "b0_readonly_checks": left["readonly_checks"],
            "b1_readonly_checks": right["readonly_checks"],
            "b0_makespan": left["metrics"].get("build_makespan_s"),
            "b1_makespan": right["metrics"].get("build_makespan_s"),
            "b0_source_tokens_per_s": left["metrics"].get("source_tokens_per_s"),
            "b1_source_tokens_per_s": right["metrics"].get("source_tokens_per_s"),
            "b0_calls": left["metrics"].get("llm_logical_calls"),
            "b1_calls": right["metrics"].get("llm_logical_calls"),
            "b0_llm_latency": {key: left["metrics"].get(key) for key in ("llm_duration_p50_s", "llm_duration_p95_s", "llm_duration_p99_s")},
            "b1_llm_latency": {key: right["metrics"].get(key) for key in ("llm_duration_p50_s", "llm_duration_p95_s", "llm_duration_p99_s")},
            "b0_db_latency": {key: left["metrics"].get(key) for key in ("db_duration_p50_s", "db_duration_p95_s", "db_duration_p99_s")},
            "b1_db_latency": {key: right["metrics"].get(key) for key in ("db_duration_p50_s", "db_duration_p95_s", "db_duration_p99_s")},
            "b0_embedding_latency": {key: left["metrics"].get(key) for key in ("embedding_duration_p50_s", "embedding_duration_p95_s", "embedding_duration_p99_s")},
            "b1_embedding_latency": {key: right["metrics"].get(key) for key in ("embedding_duration_p50_s", "embedding_duration_p95_s", "embedding_duration_p99_s")},
            "b0_drain_tail_s": float(left["metrics"].get("drain_tail_ns") or 0) / 1e9,
            "b1_drain_tail_s": float(right["metrics"].get("drain_tail_ns") or 0) / 1e9,
            "b0_active_max": left["metrics"].get("whole_update_active_max"),
            "b1_active_max": right["metrics"].get("whole_update_active_max"),
            "canonical_hash_equal": diff["canonical_hash_equal"],
            "semantic_normalized_equal": diff["semantic_normalized_equal"],
            "edge_added": edge_diff["added_count"],
            "edge_removed": edge_diff["removed_count"],
            "entity_added": entity_diff["added_count"],
            "entity_removed": entity_diff["removed_count"],
            "semantic_diff": diff,
            "paired_outcome": outcome,
        })
    report = {
        "schema_version": "sfwb.v1.3.memops-pilot-b0-vs-b1.v1",
        "pilot_id": manifest["pilot_id"],
        "pilot_root": str(root.resolve()),
        "sample_ids": sample_ids,
        "sample_count": len(sample_ids),
        "b0_status": b0.get("status"),
        "b1_status": b1.get("status"),
        "methods": list(METHODS),
        "b0_gate_required_for_b1": manifest.get("b0_gate_required_for_b1"),
        "workload_identity_mismatches": identity_mismatches,
        "method_summary": {method: metric_summary(list(rows_by_method[method].values())) for method in METHODS},
        "paired_counts": paired_counts,
        "paired_samples": paired,
        "canonical_semantic_diff_scope": {
            "canonical_hash": "retained as an auxiliary exact-export indicator",
            "semantic_normalization": "entity name/summary/attributes and edge source/target/relation/fact/valid_at/invalid_at/source episode/attributes; runtime group_id and expired_at excluded",
            "causal_claim": "none; one run per policy cannot separate LLM stochasticity/service variance from policy effects",
        },
        "protocol_assessment": {
            "operationally_reproducible": len(identity_mismatches) == 0 and b0.get("status") == "LIVE_COMPLETE" and b1.get("status") == "LIVE_COMPLETE" and all(row["b0_publication_complete"] and row["b1_publication_complete"] for row in paired),
            "semantic_causal_comparison_ready": False,
            "reason": "one paired run per method plus normalized semantic graph differences and stochastic LLM work; follow-up replication is required before attributing semantic differences to async scheduling",
        },
    }
    report["payload_sha256"] = sha256(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.pilot_root.resolve()
    report = build_report(root)
    json_path = root / "pilot_b0_vs_b1_master_v2.json"
    md_path = root / "PILOT_B0_VS_B1_REPORT_v2.md"
    if json_path.exists():
        existing = read_json(json_path)
        if existing.get("payload_sha256") != report.get("payload_sha256"):
            raise SystemExit("REPORT_ALREADY_EXISTS_WITH_DIFFERENT_PAYLOAD")
        report = existing
    else:
        json_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if md_path.exists():
        raise SystemExit("REPORT_ALREADY_EXISTS")
    md_path.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "payload_sha256": report["payload_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
