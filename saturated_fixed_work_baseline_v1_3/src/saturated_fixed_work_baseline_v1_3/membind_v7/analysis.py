"""Observer-only R2/R3 descriptive metrics and opportunity bounds."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping, Sequence

from .opportunity import work_ratio


def confusion_matrix(predictions: Sequence[str], truths: Sequence[str]) -> dict[str, int]:
    if len(predictions) != len(truths):
        raise ValueError("prediction/truth lengths differ")
    allowed_pred = {"STABLE", "INVALID", "UNKNOWN"}
    allowed_truth = {"SAME", "CHANGED"}
    matrix = {f"{prediction}/{truth}": 0 for prediction in sorted(allowed_pred) for truth in sorted(allowed_truth)}
    for prediction, truth in zip(predictions, truths):
        if prediction not in allowed_pred or truth not in allowed_truth:
            raise ValueError("invalid confusion-matrix label")
        matrix[f"{prediction}/{truth}"] += 1
    return matrix


def false_stable_rate(matrix: Mapping[str, int]) -> float | None:
    denominator = int(matrix.get("STABLE/SAME", 0)) + int(matrix.get("STABLE/CHANGED", 0))
    if denominator == 0:
        return None
    return int(matrix.get("STABLE/CHANGED", 0)) / denominator


def _union(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((float(start), float(end)) for start, end in intervals if end >= start)
    merged: list[list[float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _duration(intervals: Iterable[tuple[float, float]]) -> float:
    return sum(end - start for start, end in _union(intervals))


def _intersection(left: Iterable[tuple[float, float]], right: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for a_start, a_end in _union(left):
        for b_start, b_end in _union(right):
            start, end = max(a_start, b_start), min(a_end, b_end)
            if end > start:
                result.append((start, end))
    return result


def certifiable_stable_portion(certified: Iterable[tuple[float, float]], state_dependent: Iterable[tuple[float, float]]) -> float | None:
    state = list(state_dependent)
    denominator = _duration(state)
    if denominator == 0:
        return None
    numerator = _duration(_intersection(certified, state))
    return numerator / denominator


def semantic_change_amplification(resources: Mapping[str, tuple[float, float]]) -> dict[str, float | None]:
    return {resource: work_ratio(direct=values[0], affected=values[1]) for resource, values in resources.items()}


def mutation_locality(*, changed_objects: int, total_objects: int) -> float | None:
    if changed_objects < 0 or total_objects < 0:
        raise ValueError("object counts must be non-negative")
    return None if total_objects == 0 else changed_objects / total_objects


def reconvergence_summary(*, affected: int, repaired: int, depth: int, fanout: int) -> dict[str, float | int | None]:
    return {
        "reconvergence_rate": None if affected == 0 else repaired / affected,
        "affected_count": affected,
        "repaired_count": repaired,
        "depth": depth,
        "fanout": fanout,
    }


def offline_opportunity_margin(*, gross_saved_cp_lb: float, certificate_cost_ub: float, repair_cost_ub: float, required_headroom: float) -> dict[str, float | bool]:
    margin = gross_saved_cp_lb - certificate_cost_ub - repair_cost_ub
    return {
        "gross_saved_cp_lb": gross_saved_cp_lb,
        "certificate_cost_ub": certificate_cost_ub,
        "repair_cost_ub": repair_cost_ub,
        "required_headroom": required_headroom,
        "margin": margin,
        "opportunity_eligible": margin > required_headroom,
    }


__all__ = [
    "certifiable_stable_portion",
    "confusion_matrix",
    "false_stable_rate",
    "mutation_locality",
    "offline_opportunity_margin",
    "reconvergence_summary",
    "semantic_change_amplification",
]
