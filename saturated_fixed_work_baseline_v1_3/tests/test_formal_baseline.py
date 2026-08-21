from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.formal_baseline import (
    FORMAL_HISTORIES,
    FORMAL_METHODS,
    build_formal_matrix,
    build_lifecycle_evidence,
    group_formal_matrix_by_history,
    reduce_baseline_outputs,
    validate_formal_matrix,
)


def test_formal_matrix_is_exactly_eight_method_history_pairs() -> None:
    matrix = build_formal_matrix("sfwb-v1-3-formal-baseline-test")
    assert len(matrix) == 8
    assert {(row.history_id, row.method) for row in matrix} == {
        (history, method) for history in FORMAL_HISTORIES for method in FORMAL_METHODS
    }
    assert len({row.namespace for row in matrix}) == 8
    assert len({row.cache_salt for row in matrix}) == 8


def test_formal_execution_groups_b0_and_b1_before_next_history() -> None:
    matrix = build_formal_matrix("sfwb-v1-3-formal-baseline-test")

    groups = group_formal_matrix_by_history(matrix)

    assert tuple(history for history, _ in groups) == FORMAL_HISTORIES
    assert all(
        tuple(row.method for row in rows) == FORMAL_METHODS
        for _, rows in groups
    )
    assert tuple(row.history_id for _, rows in groups for row in rows) == tuple(
        history for history in FORMAL_HISTORIES for _ in FORMAL_METHODS
    )


def test_formal_matrix_rejects_extra_method_or_duplicate_namespace() -> None:
    matrix = build_formal_matrix("sfwb-v1-3-formal-baseline-test")
    with pytest.raises(ValueError, match="FORMAL_MATRIX_COVERAGE_INVALID"):
        validate_formal_matrix(matrix[:-1])
    duplicate = list(matrix)
    duplicate[1] = duplicate[0]
    with pytest.raises(ValueError, match="FORMAL_MATRIX_NAMESPACE_INVALID"):
        validate_formal_matrix(duplicate)


def test_lifecycle_evidence_requires_formal_start_and_durable_complete() -> None:
    lifecycle = build_lifecycle_evidence(
        formal_start_ns=100,
        durable_complete_ns=250,
        validation_complete_ns=400,
        namespace="ns-1",
    )
    assert lifecycle["events"][-3:] == [
        {"event": "CONSTRUCTION_COMPLETE", "monotonic_ns": 250},
        {"event": "DURABLE_COMPLETE", "monotonic_ns": 250},
        {"event": "VALIDATION_COMPLETE", "monotonic_ns": 400},
    ]
    assert lifecycle["build_makespan_ns"] == 150


def test_lifecycle_evidence_rejects_validation_before_durable() -> None:
    with pytest.raises(ValueError, match="DURABLE_COMPLETION_REQUIRED"):
        build_lifecycle_evidence(
            formal_start_ns=100,
            durable_complete_ns=250,
            validation_complete_ns=200,
            namespace="ns-1",
        )


def test_reducer_emits_only_b0_b1_and_required_main_columns(tmp_path: Path) -> None:
    rows = []
    for index, (history, method) in enumerate(
        (pair for history in FORMAL_HISTORIES for pair in [(history, FORMAL_METHODS[0]), (history, FORMAL_METHODS[1])])
    ):
        rows.append(
            {
                "block_id": f"b-{index}",
                "history_id": history,
                "method": method,
                "valid": True,
                "episode_count": {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}[history],
                "source_tokens": 100,
                "build_makespan_s": 2.0,
                "source_tokens_per_s": 50.0,
                "llm_logical_calls": 3,
                "llm_transport_attempts": 3,
                "llm_input_tokens": 10,
                "embedding_items": 2,
                "embedding_calls": 1,
                "db_writes": 4,
                "whole_update_active_max": 1,
                "inversion_count": 0,
                "direct_semantic_violations": 0,
                "instrumentation_error_spans": 0,
                "published_episodes": {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}[history],
                "attempt_root": str(tmp_path / f"b-{index}"),
            }
        )
    result = reduce_baseline_outputs(rows)
    assert len(result["main_table"]) == 8
    assert set(result["main_table"][0]) >= {
        "policy",
        "history",
        "makespan",
        "throughput",
        "llm_calls",
        "tokens",
        "quality",
    }
    assert all(row["policy"] in FORMAL_METHODS for row in result["main_table"])
    json.dumps(result)
