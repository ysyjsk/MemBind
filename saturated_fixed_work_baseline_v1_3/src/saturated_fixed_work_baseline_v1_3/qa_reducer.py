"""Official QA inventory reducer with invalid/null and context-cluster rules."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


class QAReductionError(ValueError):
    """QA rows cannot support the frozen quality guard."""


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise QAReductionError("QA metric is not finite")
    return float(value)


def _cluster_uncertainty(by_context: Mapping[str, list[dict[str, Any]]], *, seed: int, samples: int) -> dict[str, Any]:
    contexts = sorted(by_context)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(max(1, samples)):
        selected = [contexts[rng.randrange(len(contexts))] for _ in contexts]
        correct = valid = 0
        for context in selected:
            for row in by_context[context]:
                if row.get("judge_valid") is True:
                    valid += 1
                    correct += bool(row.get("correct"))
        if valid:
            estimates.append(correct / valid)
    estimates.sort()
    return {
        "cluster_count": len(contexts),
        "samples": max(1, samples),
        "seed": seed,
        "ci95": [estimates[int(0.025 * (len(estimates) - 1))], estimates[int(0.975 * (len(estimates) - 1))]] if estimates else None,
    }


def reduce_qa_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    method: str | None = None,
    expected_context_count: int = 5,
    expected_qa_per_context: int = 60,
    bootstrap_seed: int = 20260824,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise QAReductionError("QA inventory is empty")
    selected = [dict(row) for row in rows if method is None or row.get("method") == method]
    if not selected:
        raise QAReductionError("QA inventory is empty after method filter")
    methods = {str(row.get("method")) for row in selected}
    if len(methods) != 1:
        raise QAReductionError("QA reducer requires one method or explicit filtering")
    keys: set[tuple[str, str, int, str]] = set()
    by_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_group: Counter[tuple[str, int]] = Counter()
    for row in selected:
        if row.get("scope") != "FULL":
            raise QAReductionError("SMOKE QA cannot enter formal reducer")
        context = row.get("context_id")
        pair = row.get("qa_pair_id")
        repeat = row.get("repeat", 0)
        if not isinstance(context, str) or not isinstance(pair, str) or isinstance(repeat, bool) or not isinstance(repeat, int):
            raise QAReductionError("QA identity is invalid")
        key = (str(row.get("method")), context, repeat, pair)
        if key in keys:
            raise QAReductionError("duplicate QA row")
        keys.add(key)
        by_context[context].append(row)
        by_group[(context, repeat)] += 1
        if row.get("status") not in {"COMPLETE", "INVALID"}:
            raise QAReductionError("QA status is invalid")
        if row.get("judge_valid") is True and not isinstance(row.get("correct"), bool):
            raise QAReductionError("valid QA row lacks boolean correctness")
        if row.get("judge_valid") is not True and row.get("correct") is not None:
            raise QAReductionError("invalid QA correctness must be null")
    if len(by_context) != expected_context_count:
        raise QAReductionError("QA context inventory is incomplete")
    if any(count != expected_qa_per_context for count in by_group.values()):
        raise QAReductionError("QA inventory count is invalid")
    context_summary: dict[str, dict[str, Any]] = {}
    all_valid = [row for row in selected if row.get("judge_valid") is True]
    for context, context_rows in sorted(by_context.items()):
        valid = [row for row in context_rows if row.get("judge_valid") is True]
        partial = [row for row in context_rows if row.get("gold_mapping_status") == "PARTIAL_GOLD_MAPPING"]
        evidence: dict[str, float | None] = {}
        for name in ("evidence_recall", "evidence_mrr", "evidence_ndcg"):
            values = [_finite(row.get(name)) for row in context_rows if row.get(name) is not None]
            evidence[name] = None if partial else (sum(value for value in values if value is not None) / len(values) if values else None)
        context_summary[context] = {
            "qa_count": len(context_rows),
            "valid_judge_count": len(valid),
            "invalid_judge_count": len(context_rows) - len(valid),
            "accuracy": (sum(bool(row.get("correct")) for row in valid) / len(valid)) if valid else None,
            "partial_gold_evidence_rows": len(partial),
            **evidence,
        }
    type_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        type_rows[str(row.get("question_type", "unknown"))].append(row)
    type_summary = {
        name: {
            "qa_count": len(values),
            "valid_judge_count": sum(row.get("judge_valid") is True for row in values),
            "accuracy": (sum(bool(row.get("correct")) for row in values if row.get("judge_valid") is True) / sum(row.get("judge_valid") is True for row in values)) if any(row.get("judge_valid") is True for row in values) else None,
        }
        for name, values in sorted(type_rows.items())
    }
    pooled = (sum(bool(row.get("correct")) for row in all_valid) / len(all_valid)) if all_valid else None
    macro_values = [value["accuracy"] for value in context_summary.values() if value["accuracy"] is not None]
    return {
        "schema_version": "membind.v1.3.qa-reduction.v1",
        "method": next(iter(methods)),
        "qa_count": len(selected),
        "context_count": len(by_context),
        "valid_judge_count": len(all_valid),
        "invalid_judge_count": len(selected) - len(all_valid),
        "overall_accuracy": pooled,
        "equal_context_macro_accuracy": sum(macro_values) / len(macro_values) if macro_values else None,
        "question_type_breakdown": type_summary,
        "by_context": context_summary,
        "uncertainty": _cluster_uncertainty(by_context, seed=bootstrap_seed, samples=bootstrap_samples),
        "invalid_score_is_null": True,
        "partial_gold_evidence_is_null": True,
    }


__all__ = ["QAReductionError", "reduce_qa_rows"]
