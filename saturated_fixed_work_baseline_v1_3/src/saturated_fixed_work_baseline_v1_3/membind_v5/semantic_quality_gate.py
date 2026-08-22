"""Offline semantic-safety gate for paired B0/B1 quality results.

QA accuracy answers whether a frozen reader/judge chain produced an acceptable
answer for a small question inventory.  It does not establish that an async
construction produced the serial-equivalent memory state.  This module keeps
those claims separate and makes the latter a hard eligibility condition for a
parallel quality comparison.

The functions are deliberately provider-free.  They consume sealed/reduced
rows only and never infer safety from a high QA score.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


FORMAL_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
B0_METHOD = "B0_NATIVE_SERIAL"
B1_METHOD = "B1_NAIVE_WHOLE_UPDATE_ASYNC"
METHODS = (B0_METHOD, B1_METHOD)

PASS_SEMANTIC_SAFETY = "SERIAL_EQUIVALENT_SAFE"
UNSAFE_UPDATE = "UNSAFE_UPDATE_NON_EQUIVALENT"
OBSERVABILITY_INSUFFICIENT = "OBSERVABILITY_INSUFFICIENT"
STOP_UNSAFE_UPDATE = "STOP_PARALLEL_UNSAFE_UPDATE"
STOP_OBSERVABILITY = "STOP_PARALLEL_SEMANTIC_OBSERVABILITY_INSUFFICIENT"
QUALITY_NOT_ELIGIBLE = "UNSAFE_UPDATE_NOT_QUALITY_ELIGIBLE"


class SemanticQualityGateError(ValueError):
    """Rows do not satisfy the paired semantic-quality gate contract."""


def _bool_or_none(value: object) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise SemanticQualityGateError("SEMANTIC_GATE_BOOLEAN_INVALID")
    return value


def _qa_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(rows) != 4:
        raise SemanticQualityGateError("SEMANTIC_GATE_QA_COVERAGE_INVALID")
    pair_ids = [str(row.get("qa_pair_id") or row.get("question_id") or "") for row in rows]
    if any(not value for value in pair_ids) or len(set(pair_ids)) != 4:
        raise SemanticQualityGateError("SEMANTIC_GATE_QA_IDENTITY_INVALID")
    invalid = sum(row.get("invalid") is True for row in rows)
    correct = sum(row.get("correct") is True and row.get("invalid") is not True for row in rows)
    return {
        "qa_n": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows),
        "invalid": invalid,
        "all_graphs_unchanged_during_qa": all(
            row.get("graph_hash_before") == row.get("graph_hash_after") for row in rows
        ),
    }


def _semantic_status(row: Mapping[str, Any], *, method: str) -> dict[str, Any]:
    if row.get("valid") is not True:
        return {"status": "CONSTRUCTION_INVALID", "quality_eligible": False}
    episode_count = row.get("episode_count")
    published = row.get("published_episodes")
    publication_complete = (
        isinstance(episode_count, int)
        and not isinstance(episode_count, bool)
        and isinstance(published, int)
        and not isinstance(published, bool)
        and episode_count == published
    )
    direct = row.get("direct_semantic_violations")
    if isinstance(direct, bool) or not isinstance(direct, int) or direct < 0:
        raise SemanticQualityGateError("SEMANTIC_GATE_DIRECT_VIOLATION_INVALID")
    canonical = _bool_or_none(row.get("canonical_exact_match"))
    if not publication_complete:
        status = "PUBLICATION_INCOMPLETE"
    elif direct != 0:
        status = "DIRECT_SEMANTIC_VIOLATION"
    elif method == B0_METHOD:
        # B0 is the reference execution.  It still must carry an explicit
        # canonical marker so a missing paired comparison cannot pass silently.
        status = PASS_SEMANTIC_SAFETY if canonical is True else OBSERVABILITY_INSUFFICIENT
    elif canonical is False:
        status = UNSAFE_UPDATE
    elif canonical is True:
        status = PASS_SEMANTIC_SAFETY
    else:
        status = OBSERVABILITY_INSUFFICIENT
    return {
        "status": status,
        "quality_eligible": status == PASS_SEMANTIC_SAFETY,
        "publication_complete": publication_complete,
        "direct_semantic_violations": direct,
        "canonical_exact_match": canonical,
    }


def evaluate_parallel_quality_gate(
    construction_rows: Iterable[Mapping[str, Any]],
    qa_rows: Iterable[Mapping[str, Any]],
    *,
    expected_histories: Sequence[str] = FORMAL_HISTORIES,
) -> dict[str, Any]:
    """Evaluate semantic safety before interpreting B1 QA as a quality result.

    A high B1 QA score never overrides ``UNSAFE_UPDATE`` or missing semantic
    evidence.  The raw QA accuracy is retained for diagnosis, while
    ``quality_eligible`` controls whether it may be used in a quality claim.
    """

    histories = tuple(str(value) for value in expected_histories)
    if histories != FORMAL_HISTORIES:
        raise SemanticQualityGateError("SEMANTIC_GATE_HISTORY_SET_INVALID")
    construction = [dict(row) for row in construction_rows]
    expected = {(method, history) for method in METHODS for history in histories}
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in construction:
        key = (str(row.get("method")), str(row.get("history_id")))
        if key in indexed or key not in expected:
            raise SemanticQualityGateError("SEMANTIC_GATE_CONSTRUCTION_COVERAGE_INVALID")
        indexed[key] = row
    if set(indexed) != expected or len(construction) != len(expected):
        raise SemanticQualityGateError("SEMANTIC_GATE_CONSTRUCTION_COVERAGE_INVALID")

    qa_grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in qa_rows:
        key = (str(row.get("method")), str(row.get("history_id")))
        if key not in expected:
            raise SemanticQualityGateError("SEMANTIC_GATE_QA_IDENTITY_INVALID")
        qa_grouped[key].append(row)
    if set(qa_grouped) != expected:
        raise SemanticQualityGateError("SEMANTIC_GATE_QA_COVERAGE_INVALID")

    per_history: list[dict[str, Any]] = []
    statuses: list[str] = []
    for history in histories:
        b0 = _semantic_status(indexed[(B0_METHOD, history)], method=B0_METHOD)
        b1 = _semantic_status(indexed[(B1_METHOD, history)], method=B1_METHOD)
        b0_qa = _qa_summary(qa_grouped[(B0_METHOD, history)])
        b1_qa = _qa_summary(qa_grouped[(B1_METHOD, history)])
        statuses.append(str(b1["status"]))
        per_history.append({
            "history": history,
            "b0": {**b0, "qa": b0_qa},
            "b1": {**b1, "qa": b1_qa},
            "paired_accuracy_delta_b1_minus_b0": b1_qa["accuracy"] - b0_qa["accuracy"],
        })

    if UNSAFE_UPDATE in statuses or "DIRECT_SEMANTIC_VIOLATION" in statuses:
        decision = STOP_UNSAFE_UPDATE
    elif any(status != PASS_SEMANTIC_SAFETY for status in statuses):
        decision = STOP_OBSERVABILITY
    else:
        decision = PASS_SEMANTIC_SAFETY
    return {
        "schema_version": "sfwb.v1.3.parallel-semantic-quality-gate.v1",
        "provider_free": True,
        "live_execution": False,
        "decision": decision,
        "quality_interpretation": (
            "B1 QA is diagnostic only; unsafe or unproven state cannot be reported as an acceptable quality result."
            if decision != PASS_SEMANTIC_SAFETY
            else "B1 QA may be compared because serial-equivalent semantic safety is explicitly evidenced."
        ),
        "histories": per_history,
        "b1_quality_eligible": decision == PASS_SEMANTIC_SAFETY,
        "b1_quality_status": (
            "ELIGIBLE" if decision == PASS_SEMANTIC_SAFETY else QUALITY_NOT_ELIGIBLE
        ),
    }


__all__ = [
    "B0_METHOD",
    "B1_METHOD",
    "FORMAL_HISTORIES",
    "METHODS",
    "OBSERVABILITY_INSUFFICIENT",
    "PASS_SEMANTIC_SAFETY",
    "QUALITY_NOT_ELIGIBLE",
    "STOP_OBSERVABILITY",
    "STOP_UNSAFE_UPDATE",
    "UNSAFE_UPDATE",
    "SemanticQualityGateError",
    "evaluate_parallel_quality_gate",
]
