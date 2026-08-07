"""Statistical summaries and GO/NO-GO decision logic."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Iterable


def percentile(values: Iterable[float], q: float) -> float:
    xs = sorted(float(v) for v in values)
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def geometric_mean(values: Iterable[float]) -> float:
    xs = [float(v) for v in values if float(v) > 0]
    if not xs:
        return float("nan")
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def paired_speedups(rows: list[dict[str, Any]], metric: str, baseline: str = "M0", candidate: str = "M2") -> list[dict[str, Any]]:
    by_q: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_q[str(row["question_id"])][str(row["method"])].append(float(row[metric]))
    out = []
    for qid, methods in by_q.items():
        if baseline in methods and candidate in methods:
            b = sum(methods[baseline]) / len(methods[baseline])
            c = sum(methods[candidate]) / len(methods[candidate])
            out.append({"question_id": qid, "baseline": b, "candidate": c, "speedup": b / c if c > 0 else float("inf")})
    return out


def bootstrap_ci_speedup(
    paired: list[dict[str, Any]],
    samples: int = 10_000,
    seed: int = 20260806,
    alpha: float = 0.05,
) -> dict[str, float]:
    if not paired:
        return {"lower": float("nan"), "upper": float("nan")}
    rng = random.Random(seed)
    vals = []
    n = len(paired)
    for _ in range(samples):
        draw = [paired[rng.randrange(n)]["speedup"] for _ in range(n)]
        vals.append(geometric_mean(draw))
    vals.sort()
    return {
        "lower": vals[int((alpha / 2) * (len(vals) - 1))],
        "upper": vals[int((1 - alpha / 2) * (len(vals) - 1))],
    }


def summarize_episode_metrics(episode_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in episode_rows:
        grouped[(str(row["question_id"]), str(row["method"]), int(row.get("repeat", 0)))].append(row)
    out = []
    for (qid, method, repeat), rows in grouped.items():
        latencies = [float(r["arrival_to_publish_ms"]) for r in rows]
        arrivals = [float(r["arrival_time_ms"]) for r in rows]
        publishes = [float(r["publish_time_ms"]) for r in rows]
        out.append(
            {
                "question_id": qid,
                "method": method,
                "repeat": repeat,
                "p50_arrival_to_publish_ms": percentile(latencies, 0.50),
                "p95_arrival_to_publish_ms": percentile(latencies, 0.95),
                "p99_arrival_to_publish_ms": percentile(latencies, 0.99),
                "makespan_ms": max(publishes) - min(arrivals) if arrivals and publishes else float("nan"),
                "drain_ms": max(publishes) - max(arrivals) if arrivals and publishes else float("nan"),
                "episode_count": len(rows),
            }
        )
    return out


def decide_go_no_go(summary: dict[str, Any]) -> str:
    if summary.get("pending_run_count", 0) > 0:
        return "INCONCLUSIVE"
    if summary.get("hard_inconclusive_reasons"):
        return "INCONCLUSIVE"
    if summary.get("completed_live_instance_count", 8) < 7:
        return "INCONCLUSIVE"
    if summary.get("structured_output_parse_success_rate", 1.0) < 0.995:
        return "INCONCLUSIVE"
    failed_by_method = summary.get("failed_run_rates_by_method", {}) or {}
    if any(float(rate) > 0.05 for rate in failed_by_method.values()):
        return "INCONCLUSIVE"
    if summary.get("failed_run_rate", 0.0) > 0.05:
        return "INCONCLUSIVE"
    correctness_expected = summary.get("m2_correctness_expected_run_count")
    if correctness_expected is not None and int(correctness_expected) < 8:
        return "INCONCLUSIVE"
    if correctness_expected is not None and int(correctness_expected) >= 8:
        if summary.get("m2_correctness_exactly_once") is not True:
            return "NO-GO"
        if int(summary.get("m2_correctness_unexpected_prompt_run_count", 0)) > 0:
            return "NO-GO"
    if summary.get("m2_canonical_graph_parity_count", 0) < 8:
        return "NO-GO"
    if summary.get("m2_m0_makespan_geomean_speedup", 0.0) < 1.2:
        return "NO-GO"
    go = (
        summary.get("m2_m0_makespan_geomean_speedup", 0.0) >= 1.5
        and summary.get("m2_m0_p95_latency_reduction", 0.0) >= 0.30
        and summary.get("m2_m0_p95_speedup_ci_lower", 0.0) > 1.0
        and summary.get("m2_canonical_graph_parity_count", 0) == 8
        and summary.get("m2_recall10_drop_pp", 100.0) <= 1.0
        and summary.get("m2_llm_token_growth", 100.0) <= 0.05
        and not summary.get("m2_source_order_violation", False)
        and summary.get("m1_divergence_count", 0) >= 1
    )
    return "GO" if go else "NO-GO"
