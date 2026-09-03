from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.bounded_edge_tasks import (
    EdgeTaskOverflow,
    EdgeTaskProtocolError,
    build_edge_task_plan,
    validate_edge_task_result,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.evidence import (
    validate_finite_edge_task_ledger,
)


def _edge(source: str, target: str, fact: str = "supports") -> dict[str, object]:
    return {
        "source_entity_name": source,
        "target_entity_name": target,
        "relation_type": "SUPPORTS",
        "fact": fact,
        "valid_at": None,
        "invalid_at": None,
        "episode_indices": [0],
    }


def test_pair_task_plan_is_finite_deterministic_and_batched() -> None:
    plan = build_edge_task_plan(["B", "A", "C", "A"], max_pairs_per_task=2)
    assert plan.pair_count == 3
    assert plan.declared_task_count == 2
    assert plan.maximum_provider_calls == 2
    assert [task.pair_ids for task in plan.tasks] == [
        (("A", "B"), ("A", "C")),
        (("B", "C"),),
    ]
    assert plan.digest == build_edge_task_plan(
        ["A", "B", "C"], max_pairs_per_task=2
    ).digest


def test_pair_task_requires_ack_for_every_pair_and_allows_empty_pair() -> None:
    task = build_edge_task_plan(["A", "B"], max_pairs_per_task=2).tasks[0]
    result = {
        "status": "complete",
        "pairs_completed": ["A||B"],
        "edges": [],
    }
    assert validate_edge_task_result(result, task) == []


def test_pair_task_rejects_missing_or_unknown_pair() -> None:
    task = build_edge_task_plan(["A", "B", "C"], max_pairs_per_task=2).tasks[0]
    with pytest.raises(EdgeTaskProtocolError, match="pair coverage"):
        validate_edge_task_result(
            {"status": "complete", "pairs_completed": ["A||B"], "edges": []},
            task,
        )
    with pytest.raises(EdgeTaskProtocolError, match="unknown pair"):
        validate_edge_task_result(
            {
                "status": "complete",
                "pairs_completed": ["A||B", "A||Z"],
                "edges": [],
            },
            task,
        )


def test_pair_task_rejects_terminal_only_and_cross_pair_edges() -> None:
    task = build_edge_task_plan(["A", "B"], max_pairs_per_task=2).tasks[0]
    with pytest.raises(EdgeTaskProtocolError, match="terminal-only"):
        validate_edge_task_result(
            {"status": "no_additional_edge", "edge": None}, task
        )
    with pytest.raises(EdgeTaskProtocolError, match="endpoint"):
        validate_edge_task_result(
            {
                "status": "complete",
                "pairs_completed": ["A||B"],
                "edges": [_edge("A", "Z")],
            },
            task,
        )


def test_pair_task_rejects_relation_cap_instead_of_silent_truncation() -> None:
    task = build_edge_task_plan(["A", "B"], max_pairs_per_task=2).tasks[0]
    # The cap is inclusive; overflow is the first relation beyond it.
    edges = [_edge("A", "B", f"fact-{i}") for i in range(task.max_relations_per_pair + 1)]
    with pytest.raises(EdgeTaskOverflow, match="relation cap"):
        validate_edge_task_result(
            {"status": "complete", "pairs_completed": ["A||B"], "edges": edges},
            task,
        )


def test_provider_that_repeats_same_edge_fails_in_one_bounded_task() -> None:
    task = build_edge_task_plan(["A", "B", "C"], max_pairs_per_task=2).tasks[0]
    repeated = _edge("A", "B", "same")
    with pytest.raises(EdgeTaskProtocolError, match="coverage"):
        validate_edge_task_result(
            {
                "status": "complete",
                "pairs_completed": ["A||B", "A||B"],
                "edges": [repeated],
            },
            task,
        )


def test_different_unsupported_edge_fails_closed() -> None:
    task = build_edge_task_plan(["A", "B", "C"], max_pairs_per_task=2).tasks[0]
    with pytest.raises(EdgeTaskProtocolError, match="unknown pair|endpoint"):
        validate_edge_task_result(
            {
                "status": "complete",
                "pairs_completed": ["A||B", "A||C"],
                "edges": [_edge("B", "C")],
            },
            task,
        )


def test_large_entity_domain_is_rejected_before_provider_calls() -> None:
    with pytest.raises(EdgeTaskOverflow, match="finite source guard"):
        build_edge_task_plan([f"E{i:02d}" for i in range(46)])


def test_zero_edge_task_is_complete_only_with_explicit_pair_acknowledgements() -> None:
    plan = build_edge_task_plan(["A", "B", "C"], max_pairs_per_task=2)
    assert validate_edge_task_result(
        {
            "status": "complete",
            "pairs_completed": ["A||B", "A||C"],
            "edges": [],
        },
        plan.tasks[0],
    ) == []
    with pytest.raises(EdgeTaskProtocolError, match="terminal-only"):
        validate_edge_task_result(
            {"status": "no_additional_edge", "edge": None}, plan.tasks[0]
        )


def test_incomplete_task_ledger_cannot_be_sealed() -> None:
    with pytest.raises(ValueError, match="ledger is incomplete"):
        validate_finite_edge_task_ledger(
            [
                {
                    "schema_version": "membind.v6.1.edge-task-plan.v1",
                    "status": "sealed",
                    "declared_task_count": 2,
                    "completed_task_count": 1,
                }
            ]
        )
    assert validate_finite_edge_task_ledger(
        [
            {
                "schema_version": "membind.v6.1.edge-task-plan.v1",
                "status": "complete",
                "declared_task_count": 2,
                "completed_task_count": 2,
            }
        ]
    )["completed_task_count"] == 2
