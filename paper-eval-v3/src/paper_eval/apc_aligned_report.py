"""Offline-only reduction and rendering for APC-aligned three baselines."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


METHODS = ("U0", "A0", "P(C=2)")
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")


def _nearest(values: Sequence[int], probability: float) -> int:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("metric sample inventory empty")
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def _number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(code)
    return float(value)


def reduce_apc_aligned_results(
    *, blocks: Sequence[Mapping[str, object]], quality_report: Mapping[str, object] | None
) -> dict[str, object]:
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        raise ValueError("block inventory invalid")
    by_identity: dict[tuple[str, str], Mapping[str, object]] = {}
    for value in blocks:
        if not isinstance(value, Mapping) or value.get("status") != "PASS":
            raise ValueError("block inventory invalid")
        identity = (
            str(value.get("method", "")).removesuffix("-aligned"),
            str(value.get("history_id", "")),
        )
        if identity in by_identity:
            raise ValueError("block identity duplicate")
        by_identity[identity] = value
    expected = {(method, history) for method in METHODS for history in HISTORIES}
    if set(by_identity) != expected:
        raise ValueError("block inventory incomplete")
    quality_by_method: Mapping[str, object] = {}
    status = "CONSTRUCTION_PASS_QUALITY_PENDING"
    quality_identity: Mapping[str, object] | None = None
    if quality_report is not None:
        if not isinstance(quality_report, Mapping) or quality_report.get("status") != "PASS":
            raise ValueError("Quality Evaluation v1 report invalid")
        summary = quality_report.get("summary")
        if not isinstance(summary, Mapping) or not isinstance(summary.get("by_method"), Mapping):
            raise ValueError("Quality Evaluation v1 summary invalid")
        quality_by_method = summary["by_method"]
        if set(quality_by_method) != set(METHODS):
            raise ValueError("Quality Evaluation v1 method inventory invalid")
        quality_identity = quality_report.get("quality_identity")
        if not isinstance(quality_identity, Mapping):
            raise ValueError("Quality Evaluation v1 identity invalid")
        status = "PASS"
    table: dict[str, dict[str, object]] = {}
    diagnostics: dict[str, object] = {}
    for method in METHODS:
        selected = [by_identity[(method, history)] for history in HISTORIES]
        freshness: list[int] = []
        queue_delays: list[int] = []
        service_latencies: list[int] = []
        episode_count = 0
        makespan = 0
        max_backlog = 0
        max_waiting = 0
        direct = 0
        violation_counts: dict[str, int] = {}
        hit = query = preemptions = prompt_tokens = generation_tokens = 0.0
        telemetry_duration = 0.0
        gauge_max: dict[str, float] = {}
        gauge_weighted_mean: dict[str, float] = {}
        embedding_prompt_tokens = 0.0
        embedding_cache_hits = 0.0
        embedding_cache_queries = 0.0
        final_node_count = 0
        final_relationship_count = 0
        for block in selected:
            performance = block.get("performance")
            correctness = block.get("correctness")
            telemetry = block.get("vllm_telemetry")
            if not isinstance(performance, Mapping) or not isinstance(correctness, Mapping) or not isinstance(telemetry, Mapping):
                raise ValueError("block evidence incomplete")
            if correctness.get("checker_status") != "MEASURED":
                raise ValueError("Direct Violations not measured")
            rows = performance.get("per_source")
            if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
                raise ValueError("per-source metrics missing")
            episode_count += int(block["episode_count"])
            if len(rows) != int(block["episode_count"]):
                raise ValueError("per-source metric coverage invalid")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError("per-source metric invalid")
                freshness.append(int(row["freshness_ns"]))
                queue_delays.append(int(row["queue_delay_ns"]))
                service_latencies.append(int(row["service_latency_ns"]))
            makespan += int(performance["makespan_ns"])
            max_backlog = max(max_backlog, int(performance["max_outstanding_backlog"]))
            max_waiting = max(max_waiting, int(performance["max_waiting_queue_depth"]))
            direct += int(correctness["direct_violations_total"])
            counts = correctness.get("counts")
            if not isinstance(counts, Mapping):
                raise ValueError("Direct Violation categories missing")
            for key, value in counts.items():
                violation_counts[str(key)] = violation_counts.get(str(key), 0) + int(value)
            counters = telemetry.get("counters")
            gauges = telemetry.get("gauges")
            duration = _number(telemetry.get("measured_duration_seconds"), "telemetry duration invalid")
            if not isinstance(counters, Mapping) or not isinstance(gauges, Mapping) or duration <= 0:
                raise ValueError("telemetry invalid")
            hit += _number(counters.get("prefix_cache_hit_delta", 0), "cache hit invalid")
            query += _number(counters.get("prefix_cache_query_delta", 0), "cache query invalid")
            preemptions += _number(counters.get("preemption_delta", 0), "preemption invalid")
            prompt_tokens += _number(counters.get("prompt_token_delta", 0), "prompt tokens invalid")
            generation_tokens += _number(counters.get("generation_token_delta", 0), "generation tokens invalid")
            telemetry_duration += duration
            for name, raw in gauges.items():
                if not isinstance(raw, Mapping):
                    raise ValueError("telemetry gauge invalid")
                gauge_max[str(name)] = max(
                    gauge_max.get(str(name), 0.0),
                    _number(raw.get("max"), "telemetry gauge invalid"),
                )
                gauge_weighted_mean[str(name)] = gauge_weighted_mean.get(str(name), 0.0) + _number(
                    raw.get("mean"), "telemetry gauge invalid"
                ) * duration
            embedding_telemetry = block.get("embedding_vllm_telemetry")
            if isinstance(embedding_telemetry, Mapping):
                embedding_counters = embedding_telemetry.get("counters")
                if not isinstance(embedding_counters, Mapping):
                    raise ValueError("embedding telemetry invalid")
                embedding_prompt_tokens += _number(
                    embedding_counters.get("prompt_token_delta", 0),
                    "embedding prompt tokens invalid",
                )
                embedding_cache_hits += _number(
                    embedding_counters.get("prefix_cache_hit_delta", 0),
                    "embedding cache hits invalid",
                )
                embedding_cache_queries += _number(
                    embedding_counters.get("prefix_cache_query_delta", 0),
                    "embedding cache queries invalid",
                )
            live = block.get("live")
            if isinstance(live, Mapping) and isinstance(live.get("final_namespace"), Mapping):
                final_node_count += int(live["final_namespace"].get("node_count", 0))
                final_relationship_count += int(
                    live["final_namespace"].get("relationship_count", 0)
                )
        quality = quality_by_method.get(method, {})
        if quality and not isinstance(quality, Mapping):
            raise ValueError("quality method summary invalid")
        table[method] = {
            "qa_accuracy": quality.get("qa_accuracy") if quality else None,
            "recall_at_1": quality.get("recall_at_1_macro") if quality else None,
            "recall_at_3": quality.get("recall_at_3_macro") if quality else None,
            "recall_at_5": quality.get("recall_at_5_macro") if quality else None,
            "recall_at_10": quality.get("recall_at_10_macro") if quality else None,
            "mrr": quality.get("mrr_macro") if quality else None,
            "episode_count": episode_count,
            "direct_violations": direct,
            "p95_freshness_ns": _nearest(freshness, 0.95),
            "p99_freshness_ns": _nearest(freshness, 0.99),
            "goodput_episodes_per_second": episode_count * 1_000_000_000 / makespan,
            "makespan_ns": makespan,
            "max_backlog": max_backlog,
            "prefix_cache_hit_rate": None if query == 0 else hit / query,
        }
        diagnostics[method] = {
            "p95_queue_delay_ns": _nearest(queue_delays, 0.95),
            "p99_queue_delay_ns": _nearest(queue_delays, 0.99),
            "p95_service_latency_ns": _nearest(service_latencies, 0.95),
            "p99_service_latency_ns": _nearest(service_latencies, 0.99),
            "max_waiting_queue_depth": max_waiting,
            "direct_violation_categories": violation_counts,
            "prefix_cache_hit_tokens": hit,
            "prefix_cache_query_tokens": query,
            "preemption_count": preemptions,
            "prompt_tokens": prompt_tokens,
            "generation_tokens": generation_tokens,
            "embedding_prompt_tokens": embedding_prompt_tokens,
            "embedding_prefix_cache_hit_rate": (
                None
                if embedding_cache_queries == 0
                else embedding_cache_hits / embedding_cache_queries
            ),
            "final_graph_node_count_sum": final_node_count,
            "final_graph_relationship_count_sum": final_relationship_count,
            "prompt_throughput_tokens_per_second": prompt_tokens / telemetry_duration,
            "generation_throughput_tokens_per_second": generation_tokens / telemetry_duration,
            "vllm_gauge_mean": {
                key: value / telemetry_duration for key, value in gauge_weighted_mean.items()
            },
            "vllm_gauge_max": gauge_max,
            "stale_fact_count_macro": quality.get("stale_fact_count_macro") if quality else None,
            "conflicting_relation_group_count_macro": quality.get("conflicting_relation_group_count_macro") if quality else None,
        }
    return {
        "schema_version": "membind.paper-eval-v3.apc-aligned-baseline-report.v1",
        "status": status,
        "claim_scope": "DEVELOPMENT_DIAGNOSTIC_NOT_PAPER_SIGNIFICANCE",
        "freshness_comparability": "COMPARABLE_SHARED_RELATIVE_ARRIVAL_TRACE",
        "quality_identity": None if quality_identity is None else dict(quality_identity),
        "main_table": table,
        "diagnostics": diagnostics,
    }


def render_apc_aligned_markdown(report: Mapping[str, object]) -> str:
    table = report.get("main_table")
    if not isinstance(table, Mapping):
        raise ValueError("report main table invalid")
    lines = [
        "# APC-Aligned Three-Baseline Development Qualification",
        "",
        f"Status: `{report.get('status')}`",
        "",
        "| Method | QA | R@1 / R@3 / R@5 / R@10 | MRR | Direct Violations | P95 / P99 Freshness (s) | Goodput (eps/s) | Makespan (s) | Max Backlog | APC Hit Rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        row = table[method]
        qa = "N/A" if row["qa_accuracy"] is None else f"{float(row['qa_accuracy']):.3f}"
        recalls = (
            "N/A"
            if row["recall_at_1"] is None
            else "/".join(f"{float(row[key]):.3f}" for key in ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10"))
        )
        mrr = "N/A" if row["mrr"] is None else f"{float(row['mrr']):.3f}"
        hit = "N/A" if row["prefix_cache_hit_rate"] is None else f"{float(row['prefix_cache_hit_rate']):.3f}"
        lines.append(
            f"| {method} | {qa} | {recalls} | {mrr} | {row['direct_violations']} | "
            f"{row['p95_freshness_ns']/1e9:.3f} / {row['p99_freshness_ns']/1e9:.3f} | "
            f"{row['goodput_episodes_per_second']:.5f} | {row['makespan_ns']/1e9:.3f} | "
            f"{row['max_backlog']} | {hit} |"
        )
    lines.extend(
        [
            "",
            "`R@1` is fractional multi-gold session recall. Quality latency is excluded from all construction metrics.",
            "",
            "This four-history run is development qualification evidence, not a significance or final-paper claim.",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = ["reduce_apc_aligned_results", "render_apc_aligned_markdown"]
