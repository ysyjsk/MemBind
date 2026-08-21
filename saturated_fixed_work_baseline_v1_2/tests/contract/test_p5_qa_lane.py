from __future__ import annotations

import copy
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.dataset import (
    EXPECTED_EPISODE_COUNTS,
    load_and_validate_qa_inventory,
)
from saturated_fixed_work_baseline_v1_2.qa_lane import (
    QALaneError,
    NamespaceSeal,
    build_gold_blind_projection,
    cluster_bootstrap_accuracy,
    paired_qa_summary,
    run_history_qa,
    validate_l4_namespace_inventory,
)
from saturated_fixed_work_baseline_v1_2.reducer import reduce_quality_main_table
from saturated_fixed_work_baseline_v1_2.schedules import Method


def _seals() -> tuple[NamespaceSeal, ...]:
    result = []
    ordinal = 0
    for history in EXPECTED_EPISODE_COUNTS:
        for method in Method:
            ordinal += 1
            result.append(
                NamespaceSeal(
                    method=method.value,
                    history_id=history,
                    namespace=f"v1_2/{method.value}/{history}/formal",
                    canonical_hash=f"{ordinal:064x}",
                    construction_call_ordinal=ordinal,
                )
            )
    return tuple(result)


def test_l4_accepts_only_the_exact_eight_l3_namespaces() -> None:
    seals = _seals()
    validated = validate_l4_namespace_inventory(
        seals, expected_histories=tuple(EXPECTED_EPISODE_COUNTS), construction_calls=8
    )
    assert len(validated) == 8
    with pytest.raises(QALaneError, match="L4_NAMESPACE_COVERAGE_INVALID"):
        validate_l4_namespace_inventory(
            seals[:-1],
            expected_histories=tuple(EXPECTED_EPISODE_COUNTS),
            construction_calls=8,
        )
    with pytest.raises(QALaneError, match="QA_EXTRA_CONSTRUCTION_CALLS"):
        validate_l4_namespace_inventory(
            seals,
            expected_histories=tuple(EXPECTED_EPISODE_COUNTS),
            construction_calls=9,
        )


def test_gold_blind_projection_contains_no_private_fields(repository_root: Any) -> None:
    inventory = load_and_validate_qa_inventory(repository_root)
    row = inventory["questions"][0]
    public = build_gold_blind_projection(row)
    assert set(public) == {
        "question_id",
        "qa_pair_id",
        "history_id",
        "question_type",
        "question_date",
        "question",
    }
    assert "reference_answer" not in public
    assert "gold_session_ids" not in public
    assert "gold_evidence_quotes" not in public


def test_one_sealed_build_serves_four_qa_without_graph_writes(repository_root: Any) -> None:
    inventory = load_and_validate_qa_inventory(repository_root)
    history = next(iter(EXPECTED_EPISODE_COUNTS))
    questions = [row for row in inventory["questions"] if row["history_id"] == history]
    graph = {"nodes": [1], "edges": [2]}
    writes = 0
    retrieval_payloads: list[dict[str, Any]] = []

    def snapshot() -> dict[str, Any]:
        return copy.deepcopy(graph)

    def retrieve(public: dict[str, Any]) -> dict[str, Any]:
        retrieval_payloads.append(public)
        private = questions[len(retrieval_payloads) - 1]
        return {
            "retrieved_session_ids": list(private["gold_session_ids"]),
            "context": ["public context"],
        }

    def reader(public: dict[str, Any], retrieval: dict[str, Any]) -> str:
        assert "reference_answer" not in public
        assert retrieval["context"] == ["public context"]
        return "candidate"

    def judge(candidate: str, reference_answer: str) -> bool:
        assert candidate == "candidate"
        return bool(reference_answer)

    rows = run_history_qa(
        seal=_seals()[0],
        questions=questions,
        snapshot_graph=snapshot,
        graph_write_attempt_count=lambda: writes,
        retrieve=retrieve,
        reader=reader,
        judge=judge,
    )
    assert len(rows) == 4
    assert all(row["correct"] is True and row["invalid"] is False for row in rows)
    assert all(row["graph_hash_before"] == row["graph_hash_after"] for row in rows)
    assert all(row["graph_write_attempts"] == 0 for row in rows)
    assert all("reference_answer" not in payload for payload in retrieval_payloads)


def test_qa_graph_mutation_or_write_attempt_fails_closed(repository_root: Any) -> None:
    inventory = load_and_validate_qa_inventory(repository_root)
    history = next(iter(EXPECTED_EPISODE_COUNTS))
    questions = [row for row in inventory["questions"] if row["history_id"] == history]
    graph = {"nodes": [1]}
    writes = 0

    def retrieve(_public: dict[str, Any]) -> dict[str, Any]:
        nonlocal writes
        writes += 1
        graph["nodes"].append(2)
        return {"retrieved_session_ids": [], "context": []}

    with pytest.raises(QALaneError, match="QA_GRAPH_WRITE_OR_MUTATION"):
        run_history_qa(
            seal=_seals()[0],
            questions=questions,
            snapshot_graph=lambda: copy.deepcopy(graph),
            graph_write_attempt_count=lambda: writes,
            retrieve=retrieve,
            reader=lambda _public, _retrieval: "answer",
            judge=lambda _answer, _gold: False,
        )


def _qa_rows() -> list[dict[str, Any]]:
    rows = []
    for method in Method:
        for history_index, history in enumerate(EXPECTED_EPISODE_COUNTS):
            for question_index in range(4):
                invalid = method is Method.B1_NAIVE_WHOLE_UPDATE_ASYNC and history_index == 3 and question_index == 3
                correct = not invalid and question_index < (3 if method is Method.B0_NATIVE_SERIAL else 2)
                rows.append(
                    {
                        "method": method.value,
                        "history_id": history,
                        "question_id": f"{history}-q{question_index}",
                        "qa_pair_id": f"{history}-q{question_index}",
                        "recall_at_1": float(question_index == 0),
                        "recall_at_5": 1.0,
                        "recall_at_10": 1.0,
                        "mrr": 1.0 / (question_index + 1),
                        "ndcg_at_10": 0.5,
                        "correct": correct,
                        "invalid": invalid,
                        "failure_layer": "reader" if invalid else None,
                    }
                )
    return rows


def test_quality_reducer_counts_invalid_as_wrong_and_pairs_rows() -> None:
    rows = _qa_rows()
    table = reduce_quality_main_table(rows)
    b0, b1 = table
    assert b0["qa_n"] == 16
    assert b0["accuracy_invalid_wrong"] == 12 / 16
    assert b0["invalid"] == 0
    assert b1["qa_n"] == 16
    assert b1["accuracy_invalid_wrong"] == 8 / 16
    assert b1["invalid"] == 1
    paired = paired_qa_summary(rows)
    assert sum(paired[key] for key in ("both_correct", "b0_only_correct", "b1_only_correct", "both_wrong")) == 16
    assert paired["invalid_by_layer"] == {"reader": 1}


def test_four_cluster_bootstrap_resamples_histories_not_qa_rows() -> None:
    result = cluster_bootstrap_accuracy(_qa_rows(), seed=7, resamples=100)
    assert result["n_clusters"] == 4
    assert result["resamples"] == 100
    assert 0 <= result["interval_low"] <= result["point_estimate"] <= result["interval_high"] <= 1
