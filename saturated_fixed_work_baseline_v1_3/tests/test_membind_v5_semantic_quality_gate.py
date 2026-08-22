from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.semantic_quality_gate import (
    STOP_OBSERVABILITY,
    STOP_UNSAFE_UPDATE,
    SemanticQualityGateError,
    evaluate_parallel_quality_gate,
)


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
METHODS = ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")


def _construction(*, canonical_b1: bool | None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for history in HISTORIES:
        rows.extend(
            [
                {
                    "method": METHODS[0],
                    "history_id": history,
                    "valid": True,
                    "episode_count": 4,
                    "published_episodes": 4,
                    "direct_semantic_violations": 0,
                    "canonical_exact_match": True,
                },
                {
                    "method": METHODS[1],
                    "history_id": history,
                    "valid": True,
                    "episode_count": 4,
                    "published_episodes": 4,
                    "direct_semantic_violations": 0,
                    "canonical_exact_match": canonical_b1,
                },
            ]
        )
    return rows


def _qa() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for history in HISTORIES:
        for method in METHODS:
            for question in range(4):
                rows.append(
                    {
                        "method": method,
                        "history_id": history,
                        "qa_pair_id": f"{history}-{method}-{question}",
                        "correct": method == METHODS[1] and question == 0,
                        "invalid": False,
                        "graph_hash_before": method,
                        "graph_hash_after": method,
                    }
                )
    return rows


def test_unsafe_parallel_graph_is_not_rescued_by_higher_qa_accuracy() -> None:
    result = evaluate_parallel_quality_gate(_construction(canonical_b1=False), _qa())
    assert result["decision"] == STOP_UNSAFE_UPDATE
    assert result["b1_quality_eligible"] is False
    assert result["histories"][0]["b1"]["qa"]["accuracy"] == 0.25
    assert result["histories"][0]["paired_accuracy_delta_b1_minus_b0"] == 0.25


def test_missing_canonical_evidence_fails_closed() -> None:
    result = evaluate_parallel_quality_gate(_construction(canonical_b1=None), _qa())
    assert result["decision"] == STOP_OBSERVABILITY
    assert result["b1_quality_eligible"] is False


def test_gate_rejects_partial_qa_inventory() -> None:
    with pytest.raises(SemanticQualityGateError, match="QA_COVERAGE"):
        evaluate_parallel_quality_gate(_construction(canonical_b1=True), _qa()[:-1])
