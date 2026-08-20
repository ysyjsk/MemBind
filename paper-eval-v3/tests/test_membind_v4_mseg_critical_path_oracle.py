from __future__ import annotations

import pytest

from paper_eval.membind_v4.mseg.critical_path import analyze_publication_critical_path
from paper_eval.membind_v4.mseg.dependency import (
    DependencyEdge,
    DependencyType,
    MemorySemanticExecutionGraph,
    OperatorInstance,
)
from paper_eval.membind_v4.mseg.operator_identity import OperatorIdentity
from paper_eval.membind_v4.mseg.oracle import OracleError, schedule_finite_resource


def _operator(
    role: str,
    ordinal: int,
    duration_ns: int,
    *,
    arrival_ns: int = 0,
    certified_ns: int | None = 0,
    source_id: int = 0,
    publication: bool = False,
) -> OperatorInstance:
    identity = OperatorIdentity.create(
        history_id="toy",
        source_id=source_id,
        operator_role=role,
        operator_ordinal=ordinal,
        parent_bind_id=f"bind-{source_id}",
    )
    return OperatorInstance(
        identity=identity,
        arrival_ns=arrival_ns,
        dependency_certified_ns=certified_ns,
        observed_start_ns=None,
        service_duration_ns=duration_ns,
        is_publication=publication,
    )


def _edge(
    left: OperatorInstance,
    right: OperatorInstance,
    dependency_type: DependencyType = DependencyType.DATA_DEP,
) -> DependencyEdge:
    return DependencyEdge(
        predecessor_id=left.operator_id,
        successor_id=right.operator_id,
        dependency_type=dependency_type,
        discovered_ns=0,
    )


def test_publication_critical_path_matches_hand_computed_toy_dag() -> None:
    graph = MemorySemanticExecutionGraph()
    long_branch = _operator("Long", 0, 3)
    short_branch = _operator("Short", 1, 1)
    publish = _operator("Publish", 2, 2, publication=True)
    for operator in (long_branch, short_branch, publish):
        graph.add_operator(operator)
    graph.add_dependency(_edge(long_branch, publish))
    graph.add_dependency(_edge(short_branch, publish))

    result = analyze_publication_critical_path(graph, publish.operator_id)

    assert result.critical_path_length_ns == 5
    assert result.entries[long_branch.operator_id].on_publication_critical_path is True
    assert result.entries[publish.operator_id].on_publication_critical_path is True
    assert result.entries[short_branch.operator_id].on_publication_critical_path is False
    assert result.entries[short_branch.operator_id].publication_slack_ns == 2


def test_finite_resource_oracle_preserves_durations_and_k() -> None:
    graph = MemorySemanticExecutionGraph()
    first = _operator("EntityExtract", 0, 5, source_id=0)
    second = _operator("EntityExtract", 1, 7, source_id=1)
    for operator in (first, second):
        graph.add_operator(operator)

    serial = schedule_finite_resource(graph, resource_limit=1)
    parallel = schedule_finite_resource(graph, resource_limit=2)

    assert serial.makespan_ns == 12
    assert parallel.makespan_ns == 7
    assert serial.peak_running == 1
    assert parallel.peak_running == 2
    assert {
        record.operator_id: record.end_ns - record.start_ns
        for record in parallel.records
    } == {
        first.operator_id: 5,
        second.operator_id: 7,
    }


def test_oracle_never_starts_before_arrival_certificate_or_dependency() -> None:
    graph = MemorySemanticExecutionGraph()
    predecessor = _operator("Lookup", 0, 4, arrival_ns=3, certified_ns=3)
    target = _operator("Resolve", 1, 2, arrival_ns=1, certified_ns=8)
    graph.add_operator(predecessor)
    graph.add_operator(target)
    graph.add_dependency(_edge(predecessor, target, DependencyType.VERSION_DEP))

    result = schedule_finite_resource(graph, resource_limit=2)
    by_id = {record.operator_id: record for record in result.records}

    assert by_id[predecessor.operator_id].start_ns == 3
    assert by_id[target.operator_id].start_ns == 8
    assert by_id[target.operator_id].start_ns >= by_id[predecessor.operator_id].end_ns


def test_oracle_rejects_unresolved_dependencies_instead_of_using_future_information() -> None:
    graph = MemorySemanticExecutionGraph()
    unresolved = _operator("NodeResolve", 0, 5, certified_ns=None)
    graph.add_operator(unresolved)

    with pytest.raises(OracleError, match="unresolved_operator"):
        schedule_finite_resource(graph, resource_limit=2)


@pytest.mark.parametrize("resource_limit", [0, -1, True, None])
def test_oracle_requires_a_finite_positive_resource_envelope(resource_limit: object) -> None:
    graph = MemorySemanticExecutionGraph()
    graph.add_operator(_operator("EntityExtract", 0, 1))

    with pytest.raises(OracleError, match="resource_limit_invalid"):
        schedule_finite_resource(graph, resource_limit=resource_limit)  # type: ignore[arg-type]

