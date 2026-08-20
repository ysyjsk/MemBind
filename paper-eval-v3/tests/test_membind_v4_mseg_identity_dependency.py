from __future__ import annotations

import pytest

from paper_eval.membind_v4.mseg.dependency import (
    DependencyEdge,
    DependencyKnowledgeState,
    DependencyType,
    MemorySemanticExecutionGraph,
    OperatorInstance,
)
from paper_eval.membind_v4.mseg.operator_identity import OperatorIdentity


def _identity(
    role: str,
    *,
    source_id: int = 0,
    ordinal: int = 0,
    parent_bind_id: str = "bind-0",
    parent_operator_id: str | None = None,
) -> OperatorIdentity:
    return OperatorIdentity.create(
        history_id="07741c45",
        source_id=source_id,
        operator_role=role,
        operator_ordinal=ordinal,
        parent_bind_id=parent_bind_id,
        parent_operator_id=parent_operator_id,
    )


def _operator(
    role: str,
    *,
    source_id: int = 0,
    ordinal: int = 0,
    arrival_ns: int = 0,
    certified_ns: int | None = 0,
    start_ns: int | None = None,
    duration_ns: int = 1,
    publication: bool = False,
) -> OperatorInstance:
    return OperatorInstance(
        identity=_identity(role, source_id=source_id, ordinal=ordinal),
        arrival_ns=arrival_ns,
        dependency_certified_ns=certified_ns,
        observed_start_ns=start_ns,
        service_duration_ns=duration_ns,
        is_publication=publication,
    )


def test_operator_identity_is_stable_and_attributes_role_source_and_parent() -> None:
    parent = _identity("EntityExtract")
    first = _identity(
        "NodeResolve",
        source_id=7,
        ordinal=2,
        parent_bind_id="bind-7",
        parent_operator_id=parent.operator_id,
    )
    repeated = _identity(
        "NodeResolve",
        source_id=7,
        ordinal=2,
        parent_bind_id="bind-7",
        parent_operator_id=parent.operator_id,
    )

    assert first == repeated
    assert first.operator_id == repeated.operator_id
    assert first.operator_role == "NodeResolve"
    assert first.source_id == 7
    assert first.parent_bind_id == "bind-7"
    assert first.parent_operator_id == parent.operator_id
    assert _identity("EdgeResolve", source_id=7, ordinal=2) != first
    assert _identity("NodeResolve", source_id=8, ordinal=2) != first


@pytest.mark.parametrize(
    "dependency_type",
    [
        DependencyType.DATA_DEP,
        DependencyType.VERSION_DEP,
        DependencyType.EFFECT_CONFLICT_DEP,
        DependencyType.PUBLICATION_DEP,
    ],
)
def test_graph_preserves_each_dependency_type(dependency_type: DependencyType) -> None:
    graph = MemorySemanticExecutionGraph()
    producer = _operator("Producer")
    consumer = _operator("Consumer", ordinal=1, certified_ns=4)
    graph.add_operator(producer)
    graph.add_operator(consumer)
    graph.add_dependency(
        DependencyEdge(
            predecessor_id=producer.operator_id,
            successor_id=consumer.operator_id,
            dependency_type=dependency_type,
            discovered_ns=3,
        )
    )

    assert graph.edge_counts() == {dependency_type.value: 1}
    assert graph.predecessors(consumer.operator_id) == (producer.operator_id,)


def test_dependency_state_fails_closed_until_complete_evidence_exists() -> None:
    graph = MemorySemanticExecutionGraph()
    unresolved = _operator("NodeResolve", certified_ns=None)
    graph.add_operator(unresolved)

    assert (
        graph.knowledge_state(unresolved.operator_id, at_ns=100, completed_ids=set())
        is DependencyKnowledgeState.UNRESOLVED
    )


def test_known_unfinished_dependency_is_blocked_then_ready() -> None:
    graph = MemorySemanticExecutionGraph()
    producer = _operator("EntityExtract", duration_ns=5)
    consumer = _operator("NodeResolve", ordinal=1, certified_ns=2)
    graph.add_operator(producer)
    graph.add_operator(consumer)
    graph.add_dependency(
        DependencyEdge(
            predecessor_id=producer.operator_id,
            successor_id=consumer.operator_id,
            dependency_type=DependencyType.DATA_DEP,
            discovered_ns=1,
        )
    )

    assert (
        graph.knowledge_state(consumer.operator_id, at_ns=2, completed_ids=set())
        is DependencyKnowledgeState.CERTIFIED_BLOCKED
    )
    assert (
        graph.knowledge_state(
            consumer.operator_id,
            at_ns=5,
            completed_ids={producer.operator_id},
        )
        is DependencyKnowledgeState.CERTIFIED_READY
    )


def test_earliest_certified_start_respects_arrival_data_version_and_conflict() -> None:
    graph = MemorySemanticExecutionGraph()
    data = _operator("EntityExtract", start_ns=1, duration_ns=4)
    version = _operator("PublishPrevious", ordinal=1, start_ns=2, duration_ns=5)
    conflict = _operator("PriorEffect", ordinal=2, start_ns=3, duration_ns=8)
    target = _operator(
        "NodeResolve",
        ordinal=3,
        arrival_ns=4,
        certified_ns=9,
        start_ns=15,
        duration_ns=2,
    )
    for operator in (data, version, conflict, target):
        graph.add_operator(operator)
    for predecessor, dependency_type in (
        (data, DependencyType.DATA_DEP),
        (version, DependencyType.VERSION_DEP),
        (conflict, DependencyType.EFFECT_CONFLICT_DEP),
    ):
        graph.add_dependency(
            DependencyEdge(
                predecessor_id=predecessor.operator_id,
                successor_id=target.operator_id,
                dependency_type=dependency_type,
                discovered_ns=8,
            )
        )

    assert graph.earliest_certified_start_ns(target.operator_id) == 11
    assert graph.certified_advance_window_ns(target.operator_id) == 4


def test_earliest_start_is_unavailable_for_unresolved_operator() -> None:
    graph = MemorySemanticExecutionGraph()
    operator = _operator("EdgeResolve", certified_ns=None, start_ns=10)
    graph.add_operator(operator)

    assert graph.earliest_certified_start_ns(operator.operator_id) is None
    assert graph.certified_advance_window_ns(operator.operator_id) is None

