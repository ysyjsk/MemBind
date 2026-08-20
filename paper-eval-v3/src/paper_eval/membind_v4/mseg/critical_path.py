"""Publication-relative critical-path analysis for a certified MSEG DAG."""

from __future__ import annotations

from dataclasses import dataclass

from .dependency import MemorySemanticExecutionGraph


class MSEGCriticalPathError(ValueError):
    """Critical-path evidence is incomplete or internally inconsistent."""


def _fail(code: str) -> MSEGCriticalPathError:
    return MSEGCriticalPathError(code)


@dataclass(frozen=True, slots=True)
class CriticalPathEntry:
    operator_id: str
    earliest_start_ns: int
    earliest_finish_ns: int
    latest_start_ns: int
    publication_slack_ns: int
    critical_path_remaining_ns: int
    critical_path_contribution_ns: int
    on_publication_critical_path: bool


@dataclass(frozen=True, slots=True)
class PublicationCriticalPath:
    publication_operator_id: str
    critical_path_length_ns: int
    publication_finish_ns: int
    entries: dict[str, CriticalPathEntry]


def _ancestors(graph: MemorySemanticExecutionGraph, operator_id: str) -> set[str]:
    selected = {operator_id}
    stack = [operator_id]
    while stack:
        current = stack.pop()
        for predecessor_id in graph.predecessors(current):
            if predecessor_id not in selected:
                selected.add(predecessor_id)
                stack.append(predecessor_id)
    return selected


def analyze_publication_critical_path(
    graph: MemorySemanticExecutionGraph,
    publication_operator_id: str,
) -> PublicationCriticalPath:
    publication = graph.operator(publication_operator_id)
    if not publication.is_publication:
        raise _fail("publication_operator_required")
    selected = _ancestors(graph, publication_operator_id)
    order = [operator_id for operator_id in graph.topological_order() if operator_id in selected]
    earliest_start: dict[str, int] = {}
    earliest_finish: dict[str, int] = {}
    for operator_id in order:
        operator = graph.operator(operator_id)
        if operator.dependency_certified_ns is None:
            raise _fail("unresolved_operator")
        predecessor_finishes = [
            earliest_finish[predecessor_id]
            for predecessor_id in graph.predecessors(operator_id)
            if predecessor_id in selected
        ]
        start = max(
            operator.arrival_ns,
            operator.dependency_certified_ns,
            *predecessor_finishes,
        )
        earliest_start[operator_id] = start
        earliest_finish[operator_id] = start + operator.service_duration_ns

    publication_finish = earliest_finish[publication_operator_id]
    latest_start: dict[str, int] = {}
    for operator_id in reversed(order):
        operator = graph.operator(operator_id)
        if operator_id == publication_operator_id:
            latest_finish = publication_finish
        else:
            successor_starts = [
                latest_start[successor_id]
                for successor_id in graph.successors(operator_id)
                if successor_id in selected
            ]
            if not successor_starts:
                raise _fail("publication_path_disconnected")
            latest_finish = min(successor_starts)
        latest_start[operator_id] = latest_finish - operator.service_duration_ns

    remaining: dict[str, int] = {}
    for operator_id in reversed(order):
        operator = graph.operator(operator_id)
        successor_remaining = [
            remaining[successor_id]
            for successor_id in graph.successors(operator_id)
            if successor_id in selected
        ]
        remaining[operator_id] = operator.service_duration_ns + max(
            successor_remaining,
            default=0,
        )

    entries: dict[str, CriticalPathEntry] = {}
    for operator_id in order:
        operator = graph.operator(operator_id)
        slack = latest_start[operator_id] - earliest_start[operator_id]
        if slack < 0:
            raise _fail("negative_publication_slack")
        critical = slack == 0
        entries[operator_id] = CriticalPathEntry(
            operator_id=operator_id,
            earliest_start_ns=earliest_start[operator_id],
            earliest_finish_ns=earliest_finish[operator_id],
            latest_start_ns=latest_start[operator_id],
            publication_slack_ns=slack,
            critical_path_remaining_ns=remaining[operator_id],
            critical_path_contribution_ns=operator.service_duration_ns if critical else 0,
            on_publication_critical_path=critical,
        )
    origin = min(
        max(
            graph.operator(operator_id).arrival_ns,
            graph.operator(operator_id).dependency_certified_ns or 0,
        )
        for operator_id in selected
    )
    return PublicationCriticalPath(
        publication_operator_id=publication_operator_id,
        critical_path_length_ns=publication_finish - origin,
        publication_finish_ns=publication_finish,
        entries=entries,
    )
