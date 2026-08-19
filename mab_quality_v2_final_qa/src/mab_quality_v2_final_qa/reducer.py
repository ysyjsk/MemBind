"""Deterministic, invalid-aware and context-clustered final QA analysis."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import FAILURE_TAXONOMY, canonical_sha256


class ReductionError(ValueError):
    """Rows cannot be safely reduced under the frozen inventory."""


_RETRIEVAL_METRICS = (
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "recall_at_10",
    "mrr",
    "ndcg_at_10",
)


def _number(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ReductionError("metric value is not finite")
    return float(value)


def _verified_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(rows, (str, bytes)):
        raise ReductionError("rows must be a sequence of mappings")
    result: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str]] = set()
    for value in rows:
        if not isinstance(value, Mapping):
            raise ReductionError("QA row is not an object")
        row = dict(value)
        expected = row.get("payload_sha256")
        actual = canonical_sha256(
            {key: child for key, child in row.items() if key != "payload_sha256"}
        )
        if expected != actual:
            raise ReductionError("QA row hash mismatch")
        key = (
            str(row.get("method")),
            str(row.get("context_id")),
            str(row.get("qa_pair_id")),
        )
        if key in keys:
            raise ReductionError(f"duplicate final QA key: {key}")
        keys.add(key)
        if row.get("status") not in {"COMPLETE", "INVALID"}:
            raise ReductionError("QA row status is invalid")
        if (
            not isinstance(row.get("qa_identity_sha256"), str)
            or len(row["qa_identity_sha256"]) != 64
        ):
            raise ReductionError("QA row question identity is invalid")
        if row.get("status") == "COMPLETE" and row.get("judge_valid") is not True:
            raise ReductionError("complete row must have a valid judge")
        if row.get("judge_valid") is True and not isinstance(row.get("correct"), bool):
            raise ReductionError("valid judge row lacks boolean correctness")
        if (
            row.get("judge_valid") is not True
            and row.get("failure_class") not in FAILURE_TAXONOMY
        ):
            raise ReductionError("invalid QA row failure class is unknown")
        result.append(row)
    return result


def _mean(rows: Sequence[Mapping[str, Any]], metric: str) -> float | None:
    values = []
    for row in rows:
        metrics = row.get("retrieval_metrics", {})
        if isinstance(metrics, Mapping) and metric in metrics:
            values.append(_number(metrics[metric]))
    return sum(values) / len(values) if values else None


def _pooled_accuracy(rows: Sequence[Mapping[str, Any]]) -> float | None:
    valid = [row for row in rows if row.get("judge_valid") is True]
    return (
        sum(bool(row.get("correct")) for row in valid) / len(valid) if valid else None
    )


def _bootstrap_context_accuracy(
    rows: Sequence[Mapping[str, Any]], *, seed: int = 20260819, samples: int = 2000
) -> dict[str, Any]:
    by_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_context[str(row["context_id"])].append(dict(row))
    contexts = sorted(by_context)
    if not contexts:
        return {"cluster_count": 0, "samples": samples, "seed": seed, "ci95": None}
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(max(1, samples)):
        selected = [contexts[rng.randrange(len(contexts))] for _ in contexts]
        sampled_rows = [row for context in selected for row in by_context[context]]
        value = _pooled_accuracy(sampled_rows)
        if value is not None:
            estimates.append(value)
    estimates.sort()
    if not estimates:
        interval = None
    else:
        low = estimates[max(0, int(0.025 * (len(estimates) - 1)))]
        high = estimates[min(len(estimates) - 1, int(0.975 * (len(estimates) - 1)))]
        interval = [low, high]
    return {
        "cluster_count": len(contexts),
        "samples": max(1, samples),
        "seed": seed,
        "ci95": interval,
    }


def _question_types(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("question_type", "unknown"))].append(row)
    result: dict[str, dict[str, Any]] = {}
    for name in sorted(grouped):
        selected = grouped[name]
        valid = [row for row in selected if row.get("judge_valid") is True]
        result[name] = {
            "qa_count": len(selected),
            "valid_judge_count": len(valid),
            "invalid_judge_count": len(selected) - len(valid),
            "qa_accuracy": _pooled_accuracy(selected),
            "retrieval": {
                metric: _mean(selected, metric) for metric in _RETRIEVAL_METRICS
            },
        }
    return result


def reduce_method_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    method: str | None = None,
    bootstrap_seed: int = 20260819,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    values = _verified_rows(rows)
    if method is not None:
        values = [row for row in values if row.get("method") == method]
    if not values:
        raise ReductionError("no rows selected for method reduction")
    methods = {str(row.get("method")) for row in values}
    if len(methods) != 1:
        raise ReductionError("method reduction contains mixed methods")
    invalid = [row for row in values if row.get("judge_valid") is not True]
    failures = Counter(str(row.get("failure_class")) for row in invalid)
    by_context: dict[str, dict[str, Any]] = {}
    for context_id in sorted({str(row["context_id"]) for row in values}):
        selected = [row for row in values if str(row["context_id"]) == context_id]
        by_context[context_id] = {
            "qa_count": len(selected),
            "valid_judge_count": sum(
                row.get("judge_valid") is True for row in selected
            ),
            "qa_accuracy": _pooled_accuracy(selected),
        }
    summary = {
        "schema_version": "mab-quality-v2-final-qa.method-summary.v1",
        "method": method or str(values[0].get("method")),
        "qa_count": len(values),
        "context_count": len(by_context),
        "valid_judge_count": len(values) - len(invalid),
        "invalid_judge_count": len(invalid),
        "qa_accuracy": _pooled_accuracy(values),
        "qa_accuracy_cluster_bootstrap": _bootstrap_context_accuracy(
            values, seed=bootstrap_seed, samples=bootstrap_samples
        ),
        "retrieval": {metric: _mean(values, metric) for metric in _RETRIEVAL_METRICS},
        "failure_decomposition": dict(sorted(failures.items())),
        "question_type_breakdown": _question_types(values),
        "by_context": by_context,
        "invalid_rows_excluded_from_accuracy": True,
        "fact_gold_labels_available": False,
        "edge_metrics_scope": "PROVENANCE_PROXY_NOT_GOLD_FACT_RECALL",
    }
    summary["payload_sha256"] = canonical_sha256(summary)
    return summary


def _bootstrap_paired_delta(
    u0_map: Mapping[tuple[str, str], Mapping[str, Any]],
    mb_map: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    seed: int,
    samples: int,
) -> dict[str, Any]:
    contexts = sorted({key[0] for key in u0_map})
    if not contexts:
        return {"cluster_count": 0, "samples": samples, "seed": seed, "ci95": None}
    by_context = {
        context: sorted(key for key in u0_map if key[0] == context)
        for context in contexts
    }
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(max(1, samples)):
        selected = [contexts[rng.randrange(len(contexts))] for _ in contexts]
        left: list[Mapping[str, Any]] = []
        right: list[Mapping[str, Any]] = []
        for context in selected:
            for key in by_context[context]:
                left.append(u0_map[key])
                right.append(mb_map[key])
        left_accuracy = _pooled_accuracy(left)
        right_accuracy = _pooled_accuracy(right)
        if left_accuracy is not None and right_accuracy is not None:
            estimates.append(right_accuracy - left_accuracy)
    estimates.sort()
    interval = None
    if estimates:
        interval = [
            estimates[max(0, int(0.025 * (len(estimates) - 1)))],
            estimates[min(len(estimates) - 1, int(0.975 * (len(estimates) - 1)))],
        ]
    return {
        "cluster_count": len(contexts),
        "samples": max(1, samples),
        "seed": seed,
        "ci95": interval,
    }


def reduce_paired_rows(
    u0_rows: Sequence[Mapping[str, Any]],
    membind_rows: Sequence[Mapping[str, Any]],
    *,
    bootstrap_seed: int = 20260819,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    u0 = _verified_rows(u0_rows)
    mb = _verified_rows(membind_rows)
    u0_methods = {str(row.get("method")) for row in u0}
    mb_methods = {str(row.get("method")) for row in mb}
    if len(u0_methods) != 1 or len(mb_methods) != 1:
        raise ReductionError("paired inputs contain mixed methods")
    if u0_methods == mb_methods:
        raise ReductionError("paired inputs must use distinct method identities")
    u0_keys = {(str(row["context_id"]), str(row["qa_pair_id"])) for row in u0}
    mb_keys = {(str(row["context_id"]), str(row["qa_pair_id"])) for row in mb}
    if u0_keys != mb_keys:
        raise ReductionError("U0 and MemBind QA inventories differ")
    u0_map = {(str(row["context_id"]), str(row["qa_pair_id"])): row for row in u0}
    mb_map = {(str(row["context_id"]), str(row["qa_pair_id"])): row for row in mb}
    table = Counter()
    paired_valid: list[dict[str, Any]] = []
    paired_rows: list[dict[str, Any]] = []
    for key in sorted(u0_keys):
        left, right = u0_map[key], mb_map[key]
        for identity_field in (
            "qa_identity_sha256",
            "question_id",
            "question_type",
            "context_sha256",
        ):
            if left.get(identity_field) != right.get(identity_field):
                raise ReductionError(f"paired QA identity differs: {identity_field}")
        left_valid = left.get("judge_valid") is True
        right_valid = right.get("judge_valid") is True
        if not left_valid:
            table["invalid_u0"] += 1
        if not right_valid:
            table["invalid_membind"] += 1
        if left_valid and right_valid:
            if left.get("correct") and right.get("correct"):
                category = "both_correct"
            elif left.get("correct") and not right.get("correct"):
                category = "u0_only_correct"
            elif not left.get("correct") and right.get("correct"):
                category = "membind_only_correct"
            else:
                category = "both_wrong"
            table[category] += 1
            paired_valid.append(
                {
                    "context_id": key[0],
                    "qa_pair_id": key[1],
                    "u0_correct": bool(left.get("correct")),
                    "membind_correct": bool(right.get("correct")),
                }
            )
        paired_rows.append(
            {
                "context_id": key[0],
                "qa_pair_id": key[1],
                "u0_valid": left_valid,
                "membind_valid": right_valid,
                "u0_correct": left.get("correct") if left_valid else None,
                "membind_correct": right.get("correct") if right_valid else None,
            }
        )
    left_summary = reduce_method_rows(
        u0,
        method=str(u0[0]["method"]),
        bootstrap_seed=bootstrap_seed,
        bootstrap_samples=bootstrap_samples,
    )
    right_summary = reduce_method_rows(
        mb,
        method=str(mb[0]["method"]),
        bootstrap_seed=bootstrap_seed,
        bootstrap_samples=bootstrap_samples,
    )
    delta = None
    if (
        left_summary["qa_accuracy"] is not None
        and right_summary["qa_accuracy"] is not None
    ):
        delta = right_summary["qa_accuracy"] - left_summary["qa_accuracy"]
    result = {
        "schema_version": "mab-quality-v2-final-qa.paired-summary.v1",
        "qa_count": len(u0),
        "u0_method": left_summary["method"],
        "membind_method": right_summary["method"],
        "u0": left_summary,
        "membind": right_summary,
        "delta_accuracy_membind_minus_u0": delta,
        "delta_accuracy_cluster_bootstrap": _bootstrap_paired_delta(
            u0_map,
            mb_map,
            seed=bootstrap_seed,
            samples=bootstrap_samples,
        ),
        "paired_disagreements": dict(sorted(table.items())),
        "paired_valid_count": len(paired_valid),
        "paired_rows": paired_rows,
        "invalid_rows_not_reduced_to_wrong": True,
        "claim_scope": "PAIRED_MULTI_QA_DIAGNOSTIC; NOT AUTOMATIC EQUIVALENCE",
    }
    result["payload_sha256"] = canonical_sha256(result)
    return result


__all__ = ["ReductionError", "reduce_method_rows", "reduce_paired_rows"]
