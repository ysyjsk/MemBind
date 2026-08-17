"""TDD for the fresh, cross-method-aligned development benchmark plan."""

from __future__ import annotations

import pytest

from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    ALIGNED_METHODS,
    AlignedPlanError,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)


def _sources() -> dict[str, list[str]]:
    return {
        history_id: [f"{offset + index + 1:064x}" for index in range(3)]
        for offset, history_id in enumerate(ALIGNED_DEVELOPMENT_HISTORIES, start=100)
    }


def test_aligned_plan_binds_one_trace_manifest_and_k2_for_each_fresh_method_row() -> None:
    plan = build_aligned_development_plan(
        aligned_run_id="aligned-dev-test-001",
        history_source_sha256s=_sources(),
        interarrival_ns=0,
        shared_execution_envelope_sha256="a" * 64,
    )

    assert verify_aligned_development_plan(plan) == plan
    assert plan["methods"] == list(ALIGNED_METHODS)
    assert plan["global_llm_admission_k"] == 2
    assert len(plan["blocks"]) == 12
    assert {row["arrival_trace_sha256"] for row in plan["blocks"]} == {
        plan["arrival_trace_sha256"]
    }
    assert {row["source_manifest_sha256"] for row in plan["blocks"]} == {
        plan["source_manifest_sha256"]
    }
    assert {row["shared_execution_envelope_sha256"] for row in plan["blocks"]} == {
        "a" * 64
    }


def test_aligned_plan_counterbalances_method_position_over_the_four_histories() -> None:
    plan = build_aligned_development_plan(
        aligned_run_id="aligned-dev-test-002",
        history_source_sha256s=_sources(),
        interarrival_ns=1_000,
        shared_execution_envelope_sha256="a" * 64,
    )

    positions = {method: [] for method in ALIGNED_METHODS}
    for block in plan["blocks"]:
        positions[block["method"]].append(block["method_position"])
    assert all(sorted(values) in ([0, 0, 1, 2], [0, 1, 1, 2], [0, 1, 2, 2]) for values in positions.values())
    assert all(plan["arrival_traces"][history_id]["arrival_offsets_ns"] == [0, 1_000, 2_000] for history_id in ALIGNED_DEVELOPMENT_HISTORIES)


@pytest.mark.parametrize(
    "mutate, expected",
        [
            (lambda value: value["blocks"].pop(), "block inventory"),
            (lambda value: value["blocks"][0].update(global_llm_admission_k=3), "global LLM admission"),
            (lambda value: value["arrival_traces"]["07741c45"].update(arrival_offsets_ns=[0, 4, 3]), "arrival trace"),
            # This remains structurally valid and monotonic, but is not the
            # trace deterministically derived from the frozen interarrival.
            (lambda value: value["arrival_traces"]["07741c45"].update(arrival_offsets_ns=[0, 0, 1]), "arrival trace"),
            (lambda value: value["history_source_sha256s"].update({"07741c45": ["bad"]}), "source manifest"),
        ],
)
def test_plan_verifier_recomputes_inventory_and_rejects_fairness_or_trace_drift(mutate, expected) -> None:
    plan = build_aligned_development_plan(
        aligned_run_id="aligned-dev-test-003",
        history_source_sha256s=_sources(),
        interarrival_ns=0,
        shared_execution_envelope_sha256="a" * 64,
    )
    mutate(plan)

    with pytest.raises(AlignedPlanError, match=expected):
        verify_aligned_development_plan(plan)
