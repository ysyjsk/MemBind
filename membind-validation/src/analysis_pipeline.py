"""Formal artifact analysis, figures, and protocol decision report."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from canonicalize_graph import compare_canonical_graphs
from retrieval_eval import retrieval_metrics
from statistics import (
    bootstrap_ci_speedup,
    decide_go_no_go,
    geometric_mean,
    paired_speedups,
    summarize_episode_metrics,
)


def analyze_artifacts(
    artifacts: str | Path,
    *,
    bootstrap_samples: int = 10_000,
) -> dict[str, Any]:
    artifacts = Path(artifacts)
    final = artifacts / "final"
    final.mkdir(parents=True, exist_ok=True)
    plan = _read_jsonl(final / "run_plan.jsonl")
    specs = {str(item["run_id"]): item for item in plan}
    statuses = {
        run_id: _read_json(artifacts / "runs" / f"{run_id}.json")
        if (artifacts / "runs" / f"{run_id}.json").exists()
        else {**spec, "status": "pending"}
        for run_id, spec in specs.items()
    }
    _write_manifest(final / "run_manifest.parquet", plan, statuses)

    run_health = {
        run_id: _inspect_run_artifacts(artifacts, spec, statuses[run_id])
        for run_id, spec in specs.items()
    }
    performance_qids = _complete_performance_question_ids(plan, run_health)
    episode_rows = []
    for spec in plan:
        if (
            spec.get("lane") != "performance"
            or str(spec["question_id"]) not in performance_qids
        ):
            continue
        for row in run_health[str(spec["run_id"])]["trace_rows"]:
            episode_rows.append({**row, "lane": "performance"})
    valid_episode_rows = [
        row
        for row in episode_rows
        if row.get("error") is None and row.get("arrival_to_publish_ms") is not None
    ]
    instance_rows = summarize_episode_metrics(valid_episode_rows)
    _safe_frame(episode_rows).to_parquet(final / "episode_metrics.parquet", index=False)
    _safe_frame(instance_rows).to_parquet(final / "instance_metrics.parquet", index=False)

    graph_rows = _graph_parity_rows(plan, run_health, performance_qids)
    retrieval_rows = _retrieval_rows(plan, run_health, performance_qids)
    _write_csv(final / "graph_parity.csv", graph_rows)
    _write_csv(final / "retrieval_metrics.csv", retrieval_rows)

    pending = sum(status.get("status") not in {"success", "failed"} for status in statuses.values())
    # Partial traces are useful diagnostics, but must never be presented as a
    # formal paired performance result while any planned run is pending.
    performance_comparisons = _performance_comparisons(
        instance_rows if not pending else [],
        bootstrap_samples=bootstrap_samples,
    )
    p95_m2_m0 = performance_comparisons["p95_arrival_to_publish_ms"][
        "M2_vs_M0"
    ]
    makespan_m2_m0 = performance_comparisons["makespan_ms"]["M2_vs_M0"]
    drain_m2_m0 = performance_comparisons["drain_ms"]["M2_vs_M0"]

    correctness_m2 = [
        row
        for row in graph_rows
        if row["lane"] == "correctness" and row["candidate_method"] == "M2"
    ]
    m1_graph_comparisons = [
        row for row in graph_rows if row["candidate_method"] == "M1"
    ]
    correctness_m2_specs = [
        item
        for item in plan
        if item.get("lane") == "correctness" and item.get("method") == "M2"
    ]
    correctness_exactly_once_count = sum(
        _trace_exactly_once(
            statuses[str(item["run_id"])], run_health[str(item["run_id"])]
        )
        for item in correctness_m2_specs
    )
    correctness_source_order_violation_count = sum(
        _trace_order_violation(
            statuses[str(item["run_id"])], run_health[str(item["run_id"])]
        )
        for item in correctness_m2_specs
    )
    correctness_unexpected_prompt_run_ids = sorted(
        str(item["run_id"])
        for item in correctness_m2_specs
        if statuses[str(item["run_id"])]
        .get("llm_metrics", {})
        .get("unexpected_prompt", False)
    )
    m1_divergent_qids = {
        str(row["question_id"])
        for row in graph_rows
        if row["candidate_method"] == "M1" and not row["canonical_graph_parity"]
    }
    m1_graph_divergent_qids = set(m1_divergent_qids)
    m1_divergent_qids.update(
        str(row["question_id"])
        for row in retrieval_rows
        if row["candidate_method"] == "M1"
        and row.get("evidence_recall_at_10", 0.0) < row.get("m0_evidence_recall_at_10", 0.0)
    )
    m1_order_violation_qids = _method_order_violation_qids(
        "M1",
        plan,
        statuses,
        run_health,
        eligible_performance_qids=performance_qids,
    )
    m1_divergent_qids.update(m1_order_violation_qids)

    m0_recall = [
        float(row["m0_evidence_recall_at_10"])
        for row in retrieval_rows
        if row["lane"] == "performance" and row["candidate_method"] == "M2"
    ]
    m2_recall = [
        float(row["evidence_recall_at_10"])
        for row in retrieval_rows
        if row["lane"] == "performance" and row["candidate_method"] == "M2"
    ]
    m0_tokens, m2_tokens = _performance_tokens(
        plan, statuses, eligible_performance_qids=performance_qids
    )
    completed_live_instances = len(performance_qids)
    failed = sum(status.get("status") == "failed" for status in statuses.values())
    failed_counts_by_method, failed_rates_by_method = _failed_run_rates_by_method(
        plan, statuses
    )
    structured_requests = 0
    structured_response_failures = 0
    for status in statuses.values():
        metrics = status.get("llm_metrics", {})
        if "structured_request_count" in metrics:
            structured_requests += int(metrics.get("structured_request_count", 0))
            structured_response_failures += int(
                metrics.get("structured_response_failures", 0)
            )
        else:
            # Compatibility for retained attempts written before logical-request
            # counters were introduced. Formal runs use the counters above.
            structured_requests += int(metrics.get("llm_call_count", 0))
            structured_response_failures += int(
                metrics.get("structured_parse_failures", 0)
            )
    m2_order_violation = bool(
        _method_order_violation_qids("M2", plan, statuses, run_health)
    )
    hard_inconclusive_reasons = _hard_inconclusive_reasons(statuses)
    retrieval_parity = _retrieval_parity_summary(retrieval_rows)
    incomplete_successful_run_ids = sorted(
        run_id
        for run_id, health in run_health.items()
        if statuses[run_id].get("status") == "success" and not health["complete"]
    )

    summary = {
        "planned_run_count": len(plan),
        "successful_run_count": sum(status.get("status") == "success" for status in statuses.values()),
        "failed_run_count": failed,
        "pending_run_count": pending,
        "formal_execution_complete": pending == 0,
        "completed_live_instance_count": completed_live_instances,
        "performance_analysis_instance_count": completed_live_instances,
        "performance_analysis_question_ids": sorted(performance_qids),
        "failed_run_rate": failed / len(plan) if plan else 1.0,
        "failed_run_counts_by_method": failed_counts_by_method,
        "failed_run_rates_by_method": failed_rates_by_method,
        "hard_inconclusive_reasons": hard_inconclusive_reasons,
        "incomplete_successful_run_count": len(incomplete_successful_run_ids),
        "incomplete_successful_run_ids": incomplete_successful_run_ids,
        "structured_output_request_count": structured_requests,
        "structured_output_failure_count": structured_response_failures,
        "structured_output_parse_success_rate": (
            (structured_requests - structured_response_failures) / structured_requests
            if structured_requests
            else 0.0
        ),
        "bootstrap_resampling_unit": "question_id",
        "bootstrap_samples": int(bootstrap_samples),
        "bootstrap_seed": 20260806,
        "bootstrap_confidence_level": 0.95,
        "performance_comparisons": performance_comparisons,
        "m2_m0_p95_geomean_speedup": p95_m2_m0["geometric_mean_speedup"],
        "m2_m0_p95_median_speedup": p95_m2_m0["median_speedup"],
        "m2_m0_p95_latency_reduction": p95_m2_m0["reduction"],
        "m2_m0_p95_speedup_ci": p95_m2_m0["bootstrap_ci"],
        "m2_m0_p95_speedup_ci_lower": p95_m2_m0["bootstrap_ci"]["lower"],
        "m2_m0_makespan_geomean_speedup": makespan_m2_m0[
            "geometric_mean_speedup"
        ],
        "m2_m0_makespan_median_speedup": makespan_m2_m0["median_speedup"],
        "m2_m0_makespan_speedup_ci": makespan_m2_m0["bootstrap_ci"],
        "m2_m0_drain_geomean_speedup": drain_m2_m0[
            "geometric_mean_speedup"
        ],
        "m2_m0_drain_median_speedup": drain_m2_m0["median_speedup"],
        "m2_m0_drain_speedup_ci": drain_m2_m0["bootstrap_ci"],
        "m2_canonical_graph_parity_count": sum(
            bool(row["canonical_graph_parity"]) for row in correctness_m2
        ),
        "m2_correctness_instance_count": len(correctness_m2_specs),
        "m2_correctness_exactly_once_count": correctness_exactly_once_count,
        "m2_correctness_expected_run_count": len(correctness_m2_specs),
        "m2_correctness_exactly_once": bool(correctness_m2_specs)
        and correctness_exactly_once_count == len(correctness_m2_specs),
        "m2_correctness_source_order_violation_count": correctness_source_order_violation_count,
        "m2_correctness_unexpected_prompt_run_count": len(
            correctness_unexpected_prompt_run_ids
        ),
        "m2_correctness_unexpected_prompt_run_ids": correctness_unexpected_prompt_run_ids,
        "retrieval_parity": retrieval_parity,
        "m1_divergence_count": len(m1_divergent_qids),
        "m1_canonical_graph_parity_count": sum(
            bool(row["canonical_graph_parity"]) for row in m1_graph_comparisons
        ),
        "m1_graph_comparison_count": len(m1_graph_comparisons),
        "m1_canonical_graph_comparison_count": len(m1_graph_comparisons),
        "m1_canonical_graph_divergence_question_count": len(
            m1_graph_divergent_qids
        ),
        "m1_source_order_violation_count": len(m1_order_violation_qids),
        "m2_recall10_drop_pp": (
            (_mean(m0_recall) - _mean(m2_recall)) * 100.0
            if m0_recall and m2_recall
            else float("nan")
        ),
        "m2_llm_token_growth": (m2_tokens / m0_tokens - 1.0) if m0_tokens else float("nan"),
        "m0_performance_llm_tokens": m0_tokens,
        "m2_performance_llm_tokens": m2_tokens,
        "m2_source_order_violation": m2_order_violation,
    }
    summary["decision"] = decide_go_no_go(summary)
    _write_json(final / "statistical_summary.json", summary)
    _write_figures(final, instance_rows, graph_rows)
    _write_report(final / "VALIDATION_REPORT.md", summary)
    return summary


def _performance_comparisons(
    instance_rows: list[dict[str, Any]],
    *,
    bootstrap_samples: int,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Summarize every protocol comparison with question-level paired values."""

    comparisons = {
        "M2_vs_M0": ("M0", "M2"),
        "M1_vs_M0": ("M0", "M1"),
        "M2_vs_M1": ("M1", "M2"),
    }
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for metric in (
        "p95_arrival_to_publish_ms",
        "makespan_ms",
        "drain_ms",
    ):
        result[metric] = {}
        for label, (baseline, candidate) in comparisons.items():
            pairs = paired_speedups(
                instance_rows,
                metric,
                baseline=baseline,
                candidate=candidate,
            )
            speedup = geometric_mean(item["speedup"] for item in pairs)
            result[metric][label] = {
                "baseline_method": baseline,
                "candidate_method": candidate,
                "pair_count": len(pairs),
                "geometric_mean_speedup": speedup,
                "median_speedup": _median(
                    [float(item["speedup"]) for item in pairs]
                ),
                "bootstrap_ci": bootstrap_ci_speedup(
                    pairs, samples=bootstrap_samples
                ),
                "reduction": (
                    1.0 - (1.0 / speedup)
                    if speedup and math.isfinite(speedup)
                    else float("nan")
                ),
            }
    return result


def _retrieval_parity_summary(
    retrieval_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    groups = {
        "correctness_M2_vs_M0": ("correctness", "M2"),
        "performance_M2_vs_M0": ("performance", "M2"),
        "performance_M1_vs_M0": ("performance", "M1"),
    }
    result: dict[str, dict[str, Any]] = {}
    for label, (lane, method) in groups.items():
        rows = [
            row
            for row in retrieval_rows
            if row.get("lane") == lane and row.get("candidate_method") == method
        ]
        overlaps = [float(row["episode_set_overlap_with_m0"]) for row in rows]
        rank_overlaps = [float(row["rank_biased_overlap_with_m0"]) for row in rows]
        result[label] = {
            "comparison_count": len(rows),
            "instance_count": len({str(row["question_id"]) for row in rows}),
            "mean_m0_evidence_recall_at_5": _mean(
                [float(row["m0_evidence_recall_at_5"]) for row in rows]
            ),
            "mean_m0_evidence_recall_at_10": _mean(
                [float(row["m0_evidence_recall_at_10"]) for row in rows]
            ),
            "mean_candidate_evidence_recall_at_5": _mean(
                [float(row["evidence_recall_at_5"]) for row in rows]
            ),
            "mean_candidate_evidence_recall_at_10": _mean(
                [float(row["evidence_recall_at_10"]) for row in rows]
            ),
            "mean_episode_set_overlap_with_m0": _mean(overlaps),
            "minimum_episode_set_overlap_with_m0": min(overlaps)
            if overlaps
            else float("nan"),
            "mean_rank_biased_overlap_with_m0": _mean(rank_overlaps),
            "minimum_rank_biased_overlap_with_m0": min(rank_overlaps)
            if rank_overlaps
            else float("nan"),
        }
    return result


def _graph_parity_rows(
    plan: list[dict[str, Any]],
    run_health: dict[str, dict[str, Any]],
    eligible_performance_qids: set[str],
) -> list[dict[str, Any]]:
    captures = {
        str(item["question_id"]): item
        for item in plan
        if item.get("lane") == "correctness" and item.get("mode") == "capture"
    }
    performance_m0 = {
        (str(item["question_id"]), int(item.get("repeat", 0))): item
        for item in plan
        if item.get("lane") == "performance" and item.get("method") == "M0"
    }
    rows = []
    for candidate in plan:
        lane = candidate.get("lane")
        method = candidate.get("method")
        if lane == "correctness" and method == "M2":
            reference = captures.get(str(candidate["question_id"]))
        elif lane == "performance" and method in {"M1", "M2"}:
            if str(candidate["question_id"]) not in eligible_performance_qids:
                continue
            reference = performance_m0.get(
                (str(candidate["question_id"]), int(candidate.get("repeat", 0)))
            )
        else:
            continue
        if reference is None:
            continue
        reference_health = run_health[str(reference["run_id"])]
        candidate_health = run_health[str(candidate["run_id"])]
        if not reference_health["complete"] or not candidate_health["complete"]:
            continue
        comparison = compare_canonical_graphs(
            reference_health["graph"], candidate_health["graph"]
        )
        rows.append(
            {
                "lane": lane,
                "question_id": str(candidate["question_id"]),
                "repeat": int(candidate.get("repeat", 0)),
                "reference_run_id": reference["run_id"],
                "candidate_run_id": candidate["run_id"],
                "candidate_method": method,
                **comparison,
            }
        )
    return rows


def _retrieval_rows(
    plan: list[dict[str, Any]],
    run_health: dict[str, dict[str, Any]],
    eligible_performance_qids: set[str],
) -> list[dict[str, Any]]:
    graph_pairs = _graph_pair_specs(plan)
    rows = []
    for reference, candidate in graph_pairs:
        if (
            candidate.get("lane") == "performance"
            and str(candidate["question_id"]) not in eligible_performance_qids
        ):
            continue
        reference_health = run_health[str(reference["run_id"])]
        candidate_health = run_health[str(candidate["run_id"])]
        if not reference_health["complete"] or not candidate_health["complete"]:
            continue
        ref = reference_health["retrieval"]
        cand = candidate_health["retrieval"]
        reference_metrics = retrieval_metrics(
            ref.get("retrieved_episode_ids", []),
            ref.get("gold_episode_ids", []),
        )
        metrics = retrieval_metrics(
            cand.get("retrieved_episode_ids", []),
            cand.get("gold_episode_ids", []),
            reference_episode_ids=ref.get("retrieved_episode_ids", []),
        )
        rows.append(
            {
                "lane": candidate["lane"],
                "question_id": str(candidate["question_id"]),
                "repeat": int(candidate.get("repeat", 0)),
                "reference_run_id": reference["run_id"],
                "candidate_run_id": candidate["run_id"],
                "candidate_method": candidate["method"],
                "m0_evidence_recall_at_5": reference_metrics[
                    "evidence_recall_at_5"
                ],
                "m0_evidence_recall_at_10": reference_metrics[
                    "evidence_recall_at_10"
                ],
                **metrics,
            }
        )
    return rows


def _graph_pair_specs(plan: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    captures = {
        str(item["question_id"]): item
        for item in plan
        if item.get("lane") == "correctness" and item.get("mode") == "capture"
    }
    perf_m0 = {
        (str(item["question_id"]), int(item.get("repeat", 0))): item
        for item in plan
        if item.get("lane") == "performance" and item.get("method") == "M0"
    }
    pairs = []
    for candidate in plan:
        if candidate.get("lane") == "correctness" and candidate.get("method") == "M2":
            reference = captures.get(str(candidate["question_id"]))
        elif candidate.get("lane") == "performance" and candidate.get("method") in {"M1", "M2"}:
            reference = perf_m0.get(
                (str(candidate["question_id"]), int(candidate.get("repeat", 0)))
            )
        else:
            reference = None
        if reference is not None:
            pairs.append((reference, candidate))
    return pairs


def _inspect_run_artifacts(
    artifacts: Path,
    spec: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    """Load a run's artifacts once and establish whether it is analysis-grade."""

    run_id = str(spec["run_id"])
    reasons: list[str] = []
    if status.get("status") != "success":
        reasons.append(f"status_{status.get('status', 'missing')}")

    expected_count: int | None = None
    try:
        expected_count = int(status["episode_count"])
        if expected_count < 1:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        reasons.append("invalid_episode_count")
        expected_count = None

    trace_rows: list[dict[str, Any]] = []
    trace_path = artifacts / "traces" / f"{run_id}.jsonl"
    if not trace_path.exists():
        reasons.append("missing_trace")
    else:
        try:
            trace_rows = _read_jsonl(trace_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            reasons.append("invalid_trace")
        else:
            if not all(isinstance(row, dict) for row in trace_rows):
                reasons.append("invalid_trace")
                trace_rows = []
            if expected_count is None or len(trace_rows) != expected_count:
                reasons.append("trace_episode_count_mismatch")
            expected_sequences = (
                list(range(expected_count)) if expected_count is not None else None
            )
            try:
                sequences = [int(row["source_sequence"]) for row in trace_rows]
            except (KeyError, TypeError, ValueError):
                sequences = []
                reasons.append("invalid_trace_sequence")
            if expected_sequences is not None and sorted(sequences) != expected_sequences:
                reasons.append("trace_not_exactly_once")
            for row in trace_rows:
                try:
                    repeat_matches = int(row.get("repeat", -1)) == int(
                        spec.get("repeat", 0)
                    )
                except (TypeError, ValueError):
                    repeat_matches = False
                if (
                    str(row.get("run_id")) != run_id
                    or str(row.get("question_id")) != str(spec["question_id"])
                    or str(row.get("method")) != str(spec["method"])
                    or not repeat_matches
                ):
                    reasons.append("trace_identity_mismatch")
                    break
            if any(row.get("error") is not None for row in trace_rows):
                reasons.append("trace_contains_error")
            if any(
                row.get("publish_time") is None
                or row.get("publish_time_ms") is None
                or row.get("arrival_time_ms") is None
                or row.get("arrival_to_publish_ms") is None
                or not all(
                    _is_finite_number(row.get(field))
                    for field in (
                        "publish_time",
                        "publish_time_ms",
                        "arrival_time_ms",
                        "arrival_to_publish_ms",
                    )
                )
                for row in trace_rows
            ):
                reasons.append("trace_missing_timing")

    graph: dict[str, Any] | None = None
    graph_path = artifacts / "graphs" / f"{run_id}.canonical.json"
    if not graph_path.exists():
        reasons.append("missing_graph")
    else:
        try:
            candidate_graph = _read_json(graph_path)
        except (OSError, json.JSONDecodeError):
            reasons.append("invalid_graph")
        else:
            if not isinstance(candidate_graph, dict) or not all(
                isinstance(candidate_graph.get(key), list)
                for key in ("entities", "edges", "episodes")
            ):
                reasons.append("invalid_graph")
            else:
                graph = candidate_graph
                episodes = graph["episodes"]
                try:
                    graph_sequences = sorted(
                        int(row["source_sequence"]) for row in episodes
                    )
                except (KeyError, TypeError, ValueError):
                    graph_sequences = []
                if (
                    expected_count is None
                    or graph_sequences != list(range(expected_count))
                ):
                    reasons.append("graph_episode_count_mismatch")

    retrieval: dict[str, Any] | None = None
    retrieval_path = artifacts / "retrieval" / f"{run_id}.json"
    if not retrieval_path.exists():
        reasons.append("missing_retrieval")
    else:
        try:
            candidate_retrieval = _read_json(retrieval_path)
        except (OSError, json.JSONDecodeError):
            reasons.append("invalid_retrieval")
        else:
            if (
                not isinstance(candidate_retrieval, dict)
                or str(candidate_retrieval.get("question_id"))
                != str(spec["question_id"])
                or not isinstance(candidate_retrieval.get("gold_episode_ids"), list)
                or not isinstance(candidate_retrieval.get("retrieved_episode_ids"), list)
                or not isinstance(candidate_retrieval.get("metrics"), dict)
            ):
                reasons.append("invalid_retrieval")
            else:
                retrieval = candidate_retrieval

    return {
        "complete": not reasons,
        "reasons": sorted(set(reasons)),
        "expected_episode_count": expected_count,
        "trace_rows": trace_rows,
        "graph": graph,
        "retrieval": retrieval,
    }


def _complete_performance_question_ids(
    plan: list[dict[str, Any]],
    run_health: dict[str, dict[str, Any]],
) -> set[str]:
    required = {
        (method, repeat)
        for method in ("M0", "M1", "M2")
        for repeat in (0, 1)
    }
    by_question: dict[str, dict[tuple[str, int], list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in plan:
        if item.get("lane") != "performance":
            continue
        by_question[str(item["question_id"])][
            (str(item["method"]), int(item.get("repeat", 0)))
        ].append(str(item["run_id"]))

    eligible: set[str] = set()
    for qid, runs_by_key in by_question.items():
        if set(runs_by_key) != required:
            continue
        run_ids = [run_ids[0] for run_ids in runs_by_key.values() if len(run_ids) == 1]
        if len(run_ids) != len(required):
            continue
        if all(run_health[run_id]["complete"] for run_id in run_ids):
            eligible.add(qid)
    return eligible


def _trace_exactly_once(
    status: dict[str, Any], health: dict[str, Any]
) -> bool:
    if status.get("status") != "success":
        return False
    expected_count = health.get("expected_episode_count")
    rows = health.get("trace_rows") or []
    if expected_count is None or len(rows) != expected_count:
        return False
    try:
        sequences = [int(row["source_sequence"]) for row in rows]
    except (KeyError, TypeError, ValueError):
        return False
    return (
        sorted(sequences) == list(range(expected_count))
        and len(set(sequences)) == expected_count
        and all(
            row.get("error") is None
            and row.get("publish_time") is not None
            and _is_finite_number(row.get("publish_time"))
            for row in rows
        )
    )


def _trace_order_violation(
    status: dict[str, Any], health: dict[str, Any]
) -> bool:
    if status.get("status") not in {"success", "failed"}:
        return False
    expected_count = health.get("expected_episode_count")
    rows = health.get("trace_rows") or []
    if expected_count is None or len(rows) != expected_count:
        return True
    try:
        sequences = [int(row["source_sequence"]) for row in rows]
    except (KeyError, TypeError, ValueError):
        return True
    expected = list(range(expected_count))
    if sorted(sequences) != expected:
        return True
    if any(row.get("error") is not None or row.get("publish_time") is None for row in rows):
        return True
    try:
        published_sequences = [
            int(row["source_sequence"])
            for row in sorted(rows, key=lambda row: int(row["publish_time"]))
        ]
    except (KeyError, TypeError, ValueError):
        return True
    return published_sequences != expected


def _method_order_violation_qids(
    method: str,
    plan: list[dict[str, Any]],
    statuses: dict[str, dict[str, Any]],
    run_health: dict[str, dict[str, Any]],
    *,
    eligible_performance_qids: set[str] | None = None,
) -> set[str]:
    qids: set[str] = set()
    for item in plan:
        if item.get("method") != method:
            continue
        qid = str(item["question_id"])
        if (
            eligible_performance_qids is not None
            and item.get("lane") == "performance"
            and qid not in eligible_performance_qids
        ):
            continue
        run_id = str(item["run_id"])
        if _trace_order_violation(statuses[run_id], run_health[run_id]):
            qids.add(qid)
    return qids


def _performance_tokens(
    plan: list[dict[str, Any]],
    statuses: dict[str, dict[str, Any]],
    *,
    eligible_performance_qids: set[str],
) -> tuple[int, int]:
    totals = {"M0": 0, "M2": 0}
    for item in plan:
        if item.get("lane") != "performance" or item.get("method") not in totals:
            continue
        if str(item["question_id"]) not in eligible_performance_qids:
            continue
        status = statuses.get(str(item["run_id"]), {})
        if status.get("status") == "success":
            totals[str(item["method"])] += int(
                status.get("llm_metrics", {}).get("llm_total_tokens", 0)
            )
    return totals["M0"], totals["M2"]


def _failed_run_rates_by_method(
    plan: list[dict[str, Any]], statuses: dict[str, dict[str, Any]]
) -> tuple[dict[str, int], dict[str, float]]:
    planned: dict[str, int] = defaultdict(int)
    failed: dict[str, int] = defaultdict(int)
    for item in plan:
        method = str(item.get("method"))
        planned[method] += 1
        if statuses.get(str(item["run_id"]), {}).get("status") == "failed":
            failed[method] += 1
    counts = {method: int(failed.get(method, 0)) for method in sorted(planned)}
    rates = {
        method: failed.get(method, 0) / planned[method]
        for method in sorted(planned)
        if planned[method]
    }
    return counts, rates


def _hard_inconclusive_reasons(statuses: dict[str, dict[str, Any]]) -> list[str]:
    """Return protocol-level failures that are inconclusive regardless of rate."""

    reasons: list[str] = []
    for run_id, status in statuses.items():
        text = " ".join(
            str(value)
            for value in (
                status.get("error", ""),
                status.get("cleanup_errors", ""),
            )
        ).lower()
        if (
            status.get("database_isolation_failure") is True
            or status.get("post_run_node_count") not in (None, 0)
            or bool(status.get("cleanup_errors"))
            or "database isolation" in text
            or "neo4j isolation" in text
        ):
            reasons.append(f"database_isolation_failure:{run_id}")
        if (
            status.get("response_cache_conflict") is True
            or status.get("cache_conflict") is True
            or (
                "cache" in text
                and any(token in text for token in ("conflict", "overwrite", "different"))
            )
        ):
            reasons.append(f"response_cache_conflict:{run_id}")
        if (
            status.get("gpu_oom") is True
            or "out of memory" in text
            or "cuda oom" in text
            or re.search(r"\boom\b", text) is not None
        ):
            reasons.append(f"gpu_oom:{run_id}")
        if any(
            status.get(flag) is True
            for flag in ("model_changed", "prompt_changed", "graphiti_commit_changed")
        ):
            reasons.append(f"configuration_drift:{run_id}")
    return sorted(set(reasons))


def _write_figures(final: Path, instance_rows: list[dict[str, Any]], graph_rows: list[dict[str, Any]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    frame = pd.DataFrame(instance_rows)
    for metric, filename, ylabel in (
        ("p95_arrival_to_publish_ms", "figure_p95_latency.pdf", "P95 arrival-to-publish (ms)"),
        ("makespan_ms", "figure_makespan.pdf", "Makespan (ms)"),
    ):
        fig, axis = plt.subplots(figsize=(6.4, 4.0))
        if not frame.empty and metric in frame:
            means = frame.groupby("method")[metric].mean().reindex(["M0", "M1", "M2"])
            means.dropna().plot(kind="bar", ax=axis, color=["#4C78A8", "#F58518", "#54A24B"])
        axis.set_ylabel(ylabel)
        axis.set_xlabel("Method")
        axis.tick_params(axis="x", rotation=0)
        fig.tight_layout()
        fig.savefig(final / filename)
        plt.close(fig)

    fig, axis = plt.subplots(figsize=(6.4, 4.0))
    parity = pd.DataFrame(graph_rows)
    if not parity.empty:
        counts = parity.groupby("candidate_method")["canonical_graph_parity"].sum()
        totals = parity.groupby("candidate_method")["canonical_graph_parity"].count()
        (counts / totals).reindex(["M1", "M2"]).dropna().plot(
            kind="bar", ax=axis, color=["#F58518", "#54A24B"]
        )
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("Canonical parity rate")
    axis.set_xlabel("Method")
    axis.tick_params(axis="x", rotation=0)
    fig.tight_layout()
    fig.savefig(final / "figure_parity.pdf")
    plt.close(fig)


def _write_report(
    path: Path,
    summary: dict[str, Any],
) -> None:
    comparison_labels = {
        "M2_vs_M0": "M2 vs M0",
        "M1_vs_M0": "M1 vs M0",
        "M2_vs_M1": "M2 vs M1",
    }
    metric_labels = {
        "p95_arrival_to_publish_ms": "P95 arrival-to-publish",
        "makespan_ms": "makespan",
        "drain_ms": "drain",
    }
    comparison_lines = [
        "| 指标 | 比较 | 配对 instance | geometric mean speedup | median speedup | 95% cluster bootstrap CI |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for metric, comparisons in summary["performance_comparisons"].items():
        for comparison, values in comparisons.items():
            ci = values["bootstrap_ci"]
            comparison_lines.append(
                f"| {metric_labels[metric]} | {comparison_labels[comparison]} | "
                f"{values['pair_count']} | {values['geometric_mean_speedup']} | "
                f"{values['median_speedup']} | [{ci['lower']}, {ci['upper']}] |"
            )
    comparison_table = "\n".join(comparison_lines)
    retrieval = summary["retrieval_parity"]["correctness_M2_vs_M0"]
    necessity_statement = (
        "M1 至少在一个 evaluation instance 上出现语义、retrieval 或 source-order 偏差，"
        "因此本实验提供了 Late Binding 必要性证据。"
        if summary["m1_divergence_count"] >= 1
        else "M1 未出现语义、retrieval 或 source-order 偏差，Late Binding 的必要性未被证明。"
    )
    failed_rates = summary["failed_run_rates_by_method"]
    max_failed_rate = max((float(value) for value in failed_rates.values()), default=0.0)
    criteria = [
        (
            "Makespan speedup >= 1.5x",
            summary["m2_m0_makespan_geomean_speedup"],
            summary["m2_m0_makespan_geomean_speedup"] >= 1.5,
        ),
        (
            "P95 reduction >= 30%",
            summary["m2_m0_p95_latency_reduction"],
            summary["m2_m0_p95_latency_reduction"] >= 0.30,
        ),
        (
            "P95 CI lower > 1.0",
            summary["m2_m0_p95_speedup_ci_lower"],
            summary["m2_m0_p95_speedup_ci_lower"] > 1.0,
        ),
        (
            "M2 canonical parity = 8/8",
            f"{summary['m2_canonical_graph_parity_count']}/{summary['m2_correctness_instance_count']}",
            summary["m2_canonical_graph_parity_count"] == 8
            and summary["m2_correctness_instance_count"] == 8,
        ),
        (
            "Recall@10 drop <= 1 pp",
            summary["m2_recall10_drop_pp"],
            summary["m2_recall10_drop_pp"] <= 1.0,
        ),
        (
            "LLM token growth <= 5%",
            summary["m2_llm_token_growth"],
            summary["m2_llm_token_growth"] <= 0.05,
        ),
        (
            "M2 exactly-once and source ordered",
            f"exactly_once={summary['m2_correctness_exactly_once']}, order_violation={summary['m2_source_order_violation']}",
            summary["m2_correctness_exactly_once"]
            and not summary["m2_source_order_violation"],
        ),
        (
            "M1 divergence >= 1 instance",
            summary["m1_divergence_count"],
            summary["m1_divergence_count"] >= 1,
        ),
        (
            "Structured-output success >= 99.5%",
            summary["structured_output_parse_success_rate"],
            summary["structured_output_parse_success_rate"] >= 0.995,
        ),
        (
            "Each method failed-run rate <= 5%",
            max_failed_rate,
            max_failed_rate <= 0.05,
        ),
    ]
    criterion_lines = [
        "| 冻结判据 | 实际值 | 满足 |",
        "|---|---:|---:|",
        *[
            f"| {label} | {value} | {'是' if passed else '否'} |"
            for label, value, passed in criteria
        ],
    ]
    criterion_table = "\n".join(criterion_lines)
    report = f"""# MemBind Basic Validation Report

## 1. 是否保持原生语义？

M2 correctness canonical parity 为 {summary['m2_canonical_graph_parity_count']}/{summary['m2_correctness_instance_count']}。correctness lane 的 unexpected prompt run 数为 {summary['m2_correctness_unexpected_prompt_run_count']}；episode exactly-once 为 {summary['m2_correctness_exactly_once_count']}/{summary['m2_correctness_expected_run_count']}（全量通过：{summary['m2_correctness_exactly_once']}）；correctness source-order violation run 数为 {summary['m2_correctness_source_order_violation_count']}，所有 lane 的 source-order violation 为 {summary['m2_source_order_violation']}。

M2 vs M0 correctness retrieval parity 覆盖 {retrieval['instance_count']} 个 instance：Evidence Recall@5 的 M0/M2 均值分别为 {retrieval['mean_m0_evidence_recall_at_5']}/{retrieval['mean_candidate_evidence_recall_at_5']}，Evidence Recall@10 的 M0/M2 均值分别为 {retrieval['mean_m0_evidence_recall_at_10']}/{retrieval['mean_candidate_evidence_recall_at_10']}，episode-set overlap 均值为 {retrieval['mean_episode_set_overlap_with_m0']}，rank-biased overlap 均值为 {retrieval['mean_rank_biased_overlap_with_m0']}。逐 run 数值见 `retrieval_metrics.csv`。

## 2. 是否明显加速？

进入正式性能分析的完整 instance 数为 {summary['performance_analysis_instance_count']}。M2 vs M0 的 P95 reduction 为 {summary['m2_m0_p95_latency_reduction']}。P95、makespan 和 drain 的三组冻结比较如下；CI 以 question_id 为 cluster、seed=20260806：

{comparison_table}

## 3. 为什么不能直接并发完整 update？

M1 canonical parity 为 {summary['m1_canonical_graph_parity_count']}/{summary['m1_graph_comparison_count']}；M1 canonical divergence question 数为 {summary['m1_canonical_graph_divergence_question_count']}；M1 divergence instance 数为 {summary['m1_divergence_count']}；M1 source-order violation instance 数为 {summary['m1_source_order_violation_count']}。性能对照见上一节的 M1 vs M0 与 M2 vs M1。{necessity_statement}

## 4. 是否值得继续？

按冻结的 Go / Inconclusive / No-Go 判据，结论为 **{summary['decision']}**。正式成功 run 为 {summary['successful_run_count']}/{summary['planned_run_count']}，全局失败率为 {summary['failed_run_rate']}，各方法失败率为 {json.dumps(summary['failed_run_rates_by_method'], ensure_ascii=False, sort_keys=True)}。硬性 INCONCLUSIVE 原因为 {json.dumps(summary['hard_inconclusive_reasons'], ensure_ascii=False)}。

{criterion_table}
"""
    path.write_text(report, encoding="utf-8")


def _write_manifest(path: Path, plan: list[dict[str, Any]], statuses: dict[str, dict[str, Any]]) -> None:
    rows = []
    for item in plan:
        row = {**item, **statuses[str(item["run_id"])]}
        rows.append(row)
    _safe_frame(rows).to_parquet(path, index=False)


def _safe_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    normalized = []
    for row in rows:
        normalized.append(
            {
                key: json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            }
        )
    return pd.DataFrame(normalized)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("status\nno comparable successful runs\n", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _median(values: list[float]) -> float:
    if not values:
        return float("nan")
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0
