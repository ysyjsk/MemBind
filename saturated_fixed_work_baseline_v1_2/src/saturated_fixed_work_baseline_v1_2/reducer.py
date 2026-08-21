"""Deterministic reducers for the two development main tables."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from .canonical_diff import canonical_diff
from .dataset import EXPECTED_EPISODE_COUNTS, EXPECTED_SOURCE_TOKENS
from .schedules import Method


RESULT_SCOPE = "development / protocol-qualified / one run per method-history"


class ReductionError(ValueError):
    """Raw sealed rows do not meet a pre-registered reduction contract."""


def _number(row: Mapping[str, Any], field: str) -> float:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ReductionError(f"{field.upper()}_INVALID")
    return float(value)


def attach_paired_canonical_diffs(
    rows: Sequence[Mapping[str, Any]],
    *,
    repository_root: Path,
    expected_histories: Sequence[str],
) -> dict[str, Any]:
    histories = tuple(expected_histories)
    methods = (
        Method.B0_NATIVE_SERIAL.value,
        Method.B1_NAIVE_WHOLE_UPDATE_ASYNC.value,
    )
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("valid") is not True:
            raise ReductionError("PAIRED_CANONICAL_ROW_INVALID")
        key = (str(row.get("method")), str(row.get("history_id")))
        if key in indexed:
            raise ReductionError("PAIRED_CANONICAL_IDENTITY_DUPLICATE")
        indexed[key] = row
    expected = {(method, history) for method in methods for history in histories}
    if set(indexed) != expected:
        raise ReductionError("PAIRED_CANONICAL_COVERAGE_INVALID")
    derived = [copy.deepcopy(dict(row)) for row in rows]
    derived_by_key = {
        (str(row["method"]), str(row["history_id"])): row for row in derived
    }
    diffs: list[dict[str, Any]] = []
    for history in histories:
        b0 = indexed[(methods[0], history)]
        b1 = indexed[(methods[1], history)]
        b0_namespace = b0.get("namespace")
        b1_namespace = b1.get("namespace")
        if (
            not isinstance(b0_namespace, str)
            or not b0_namespace
            or not isinstance(b1_namespace, str)
            or not b1_namespace
            or b0_namespace == b1_namespace
        ):
            raise ReductionError("PAIRED_CANONICAL_NAMESPACE_INVALID")

        def load_graph(row: Mapping[str, Any]) -> dict[str, Any]:
            attempt_root = row.get("attempt_root")
            if not isinstance(attempt_root, str) or not attempt_root:
                raise ReductionError("PAIRED_CANONICAL_ATTEMPT_ROOT_INVALID")
            try:
                value = json.loads(
                    (Path(attempt_root) / "canonical_graph.json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise ReductionError("PAIRED_CANONICAL_GRAPH_UNREADABLE") from None
            if not isinstance(value, dict):
                raise ReductionError("PAIRED_CANONICAL_GRAPH_INVALID")
            return value

        diff = canonical_diff(
            load_graph(b0),
            load_graph(b1),
            repository_root=repository_root,
            reference_namespace=b0_namespace,
            candidate_namespace=b1_namespace,
        )
        diff = {**diff, "history_id": history, "reference_method": methods[0], "candidate_method": methods[1]}
        diffs.append(diff)
        b0_derived = derived_by_key[(methods[0], history)]
        b1_derived = derived_by_key[(methods[1], history)]
        b0_derived.update(
            {
                "canonical_exact_match": True,
                "canonical_paired_reference_hash": diff["reference_hash"],
                "canonical_paired_candidate_hash": diff["reference_hash"],
                "canonical_difference_counts": {
                    name: 0 for name in diff["difference_counts"]
                },
            }
        )
        b1_derived.update(
            {
                "canonical_exact_match": diff["exact_match"],
                "canonical_paired_reference_hash": diff["reference_hash"],
                "canonical_paired_candidate_hash": diff["candidate_hash"],
                "canonical_difference_counts": dict(diff["difference_counts"]),
            }
        )
    return {
        "schema_version": "membind.saturated-fixed-work.paired-canonical-reduction.v1",
        "rows": derived,
        "diffs": diffs,
    }


def reduce_construction_main_table(
    rows: Sequence[Mapping[str, Any]], *, expected_histories: Sequence[str]
) -> list[dict[str, Any]]:
    histories = tuple(expected_histories)
    if not histories or len(set(histories)) != len(histories):
        raise ReductionError("EXPECTED_HISTORIES_INVALID")
    methods = (Method.B0_NATIVE_SERIAL.value, Method.B1_NAIVE_WHOLE_UPDATE_ASYNC.value)
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReductionError("BLOCK_ROW_INVALID")
        key = (str(row.get("method")), str(row.get("history_id")))
        if key[0] not in methods or key[1] not in histories or key in indexed:
            raise ReductionError("BLOCK_IDENTITY_INVALID")
        if row.get("valid") is not True:
            raise ReductionError("INVALID_BLOCK_IN_MAIN_TABLE")
        indexed[key] = row
    expected = {(method, history) for method in methods for history in histories}
    if set(indexed) != expected:
        raise ReductionError("BLOCK_COVERAGE_INVALID")
    if histories == tuple(EXPECTED_EPISODE_COUNTS):
        for (method, history), row in indexed.items():
            del method
            if row.get("source_tokens") != EXPECTED_SOURCE_TOKENS[history]:
                raise ReductionError("FORMAL_SOURCE_TOKENS_MISMATCH")
            if row.get("episode_count") != EXPECTED_EPISODE_COUNTS[history]:
                raise ReductionError("FORMAL_EPISODE_COUNT_MISMATCH")

    totals: dict[str, dict[str, Any]] = {}
    for method in methods:
        selected = [indexed[(method, history)] for history in histories]
        makespan = sum(_number(row, "build_makespan_s") for row in selected)
        source_tokens = sum(_number(row, "source_tokens") for row in selected)
        llm_tokens = sum(_number(row, "llm_input_tokens") for row in selected)
        if makespan <= 0:
            code = "B0_MAKESPAN_DENOMINATOR_INVALID" if method == methods[0] else "B1_MAKESPAN_DENOMINATOR_INVALID"
            raise ReductionError(code)
        totals[method] = {
            "selected": selected,
            "makespan": makespan,
            "source_tokens": source_tokens,
            "llm_tokens": llm_tokens,
        }
    b0 = totals[methods[0]]
    if b0["llm_tokens"] <= 0:
        raise ReductionError("B0_LLM_TOKEN_DENOMINATOR_INVALID")

    result: list[dict[str, Any]] = []
    for method in methods:
        total = totals[method]
        selected = total["selected"]
        result.append(
            {
                "method": method,
                "valid_histories": len(histories),
                "episodes": int(sum(_number(row, "episode_count") for row in selected)),
                "total_build_makespan_s": total["makespan"],
                "speedup_vs_b0": b0["makespan"] / total["makespan"],
                "source_tokens_per_s": total["source_tokens"] / total["makespan"],
                "llm_input_token_ratio_vs_b0": total["llm_tokens"] / b0["llm_tokens"],
                "direct_semantic_violations": int(
                    sum(_number(row, "direct_semantic_violations") for row in selected)
                ),
                "canonical_exact_match_histories": (
                    f"{sum(row.get('canonical_exact_match') is True for row in selected)}/{len(histories)}"
                ),
                "result_scope": RESULT_SCOPE,
            }
        )
    return result


def reduce_quality_main_table(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    methods = (Method.B0_NATIVE_SERIAL.value, Method.B1_NAIVE_WHOLE_UPDATE_ASYNC.value)
    selected_by_method: dict[str, list[Mapping[str, Any]]] = {method: [] for method in methods}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("method") not in selected_by_method:
            raise ReductionError("QA_ROW_INVALID")
        selected_by_method[str(row["method"])].append(row)
    question_sets = [
        {str(row.get("qa_pair_id")) for row in selected_by_method[method]}
        for method in methods
    ]
    if any(len(selected_by_method[method]) != 16 for method in methods) or question_sets[0] != question_sets[1]:
        raise ReductionError("QA_COVERAGE_INVALID")
    result: list[dict[str, Any]] = []
    metric_fields = (
        "recall_at_1",
        "recall_at_5",
        "recall_at_10",
        "mrr",
        "ndcg_at_10",
    )
    for method in methods:
        selected = selected_by_method[method]
        row = {
            "method": method,
            "qa_n": 16,
            **{
                metric: sum(_number(item, metric) for item in selected) / 16
                for metric in metric_fields
            },
            "accuracy_invalid_wrong": sum(
                item.get("correct") is True and item.get("invalid") is not True
                for item in selected
            )
            / 16,
            "invalid": sum(item.get("invalid") is True for item in selected),
            "result_scope": RESULT_SCOPE,
        }
        valid = [item for item in selected if item.get("invalid") is not True]
        row["valid_only_accuracy"] = (
            sum(item.get("correct") is True for item in valid) / len(valid)
            if valid
            else None
        )
        result.append(row)
    return result


__all__ = [
    "RESULT_SCOPE",
    "ReductionError",
    "attach_paired_canonical_diffs",
    "reduce_construction_main_table",
    "reduce_quality_main_table",
]
