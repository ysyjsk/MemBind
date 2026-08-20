"""RED contracts for c01_ca semantic, conflict, resource, and value gates."""

from __future__ import annotations

import pytest

from paper_eval.membind_v4.admission import (
    SemanticAdmissionFacts,
    SpeculationValueEstimate,
    decide_conflict_aware_speculation,
)
from paper_eval.membind_v4.conflict_classifier import ConflictClass
from paper_eval.membind_v4.speculative_adapter import V4ResidualSlotSignal


def _semantic(**changes: object) -> SemanticAdmissionFacts:
    values: dict[str, object] = {
        "future_arrived": True,
        "prepared_ready": True,
        "speculation_distance": 1,
        "node_resolve_materializable": True,
        "execution_mode": "LLM",
    }
    values.update(changes)
    return SemanticAdmissionFacts(**values)  # type: ignore[arg-type]


def _snapshot(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "configured_limit": 2,
        "active_count": 1,
        "active_frontier_count": 1,
        "active_compile_count": 0,
        "waiting_frontier_count": 0,
        "waiting_compile_count": 0,
        "frontier_bind_region_count": 1,
        "frontier_transport_phase": "FRONTIER_LLM_PERMIT_ACTIVE",
    }
    values.update(changes)
    return values


def _value(*, benefit: float = 25.0, cost: float = 3.0) -> SpeculationValueEstimate:
    return SpeculationValueEstimate(
        expected_node_resolve_service_ms=benefit,
        estimated_frontier_interference_ms=cost,
    )


def _decide(
    conflict_class: ConflictClass,
    *,
    snapshot: dict[str, object] | None = None,
    semantic: SemanticAdmissionFacts | None = None,
    value: SpeculationValueEstimate | None = None,
    active_speculation_count: int = 0,
):
    return decide_conflict_aware_speculation(
        semantic=_semantic() if semantic is None else semantic,
        conflict_class=conflict_class,
        resource_snapshot=_snapshot() if snapshot is None else snapshot,
        active_speculation_count=active_speculation_count,
        value=_value() if value is None else value,
    )


@pytest.mark.parametrize(
    ("conflict_class", "reason"),
    [
        (ConflictClass.HIGH_CONFLICT, "HIGH_CONFLICT"),
        (ConflictClass.UNKNOWN, "UNKNOWN_CONFLICT"),
    ],
)
def test_high_and_unknown_conflict_never_launch(
    conflict_class: ConflictClass, reason: str
) -> None:
    decision = _decide(conflict_class)

    assert decision.admit is False
    assert decision.reason == reason
    assert decision.expected_benefit_ms == 0.0


def test_low_conflict_without_residual_slot_does_not_launch() -> None:
    decision = _decide(
        ConflictClass.LOW_CONFLICT,
        snapshot=_snapshot(active_count=2, active_frontier_count=1),
    )

    assert decision.admit is False
    assert decision.reason == "NO_RESIDUAL_SLOT"


def test_low_conflict_with_frontier_waiter_does_not_launch() -> None:
    decision = _decide(
        ConflictClass.LOW_CONFLICT,
        snapshot=_snapshot(waiting_frontier_count=1),
    )

    assert decision.admit is False
    assert decision.reason == "FRONTIER_WAITER"


def test_low_conflict_with_one_active_frontier_and_k2_residual_slot_launches() -> None:
    decision = _decide(ConflictClass.LOW_CONFLICT)

    assert decision.admit is True
    assert decision.reason == "ADMIT"
    assert decision.expected_benefit_ms == 25.0
    assert decision.expected_cost_ms == 3.0


def test_compile_waiter_does_not_itself_block_low_conflict_speculation() -> None:
    snapshot = _snapshot(waiting_compile_count=7)

    decision = _decide(ConflictClass.LOW_CONFLICT, snapshot=snapshot)
    signal = V4ResidualSlotSignal()
    signal.observe(snapshot)

    assert decision.admit is True
    assert decision.reason == "ADMIT"
    assert signal.ready is True


def test_active_speculation_and_unprofitable_work_fail_closed() -> None:
    assert _decide(
        ConflictClass.LOW_CONFLICT, active_speculation_count=1
    ).reason == "ACTIVE_SPECULATION"
    assert _decide(
        ConflictClass.LOW_CONFLICT, value=_value(benefit=3.0, cost=3.0)
    ).reason == "NOT_PROFITABLE"


@pytest.mark.parametrize(
    "semantic",
    [
        _semantic(future_arrived=False),
        _semantic(prepared_ready=False),
        _semantic(speculation_distance=2),
        _semantic(node_resolve_materializable=False),
    ],
)
def test_semantic_requirements_fail_closed(semantic: SemanticAdmissionFacts) -> None:
    assert _decide(ConflictClass.LOW_CONFLICT, semantic=semantic).reason == "SEMANTIC_NOT_READY"


def test_non_llm_execution_is_not_speculated() -> None:
    assert _decide(
        ConflictClass.LOW_CONFLICT,
        semantic=_semantic(execution_mode="NO_LLM"),
    ).reason == "EXECUTION_NOT_LLM"

