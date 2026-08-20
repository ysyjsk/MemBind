"""Finite-resource scheduling oracle over certified MSEG operators."""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .dependency import MemorySemanticExecutionGraph, OperatorInstance


class OracleError(ValueError):
    """The oracle input violates its legality or resource contract."""


def _fail(code: str) -> OracleError:
    return OracleError(code)


@dataclass(frozen=True, slots=True)
class ScheduleRecord:
    operator_id: str
    source_id: int
    operator_role: str
    start_ns: int
    end_ns: int


@dataclass(frozen=True, slots=True)
class OracleSchedule:
    resource_limit: int
    records: tuple[ScheduleRecord, ...]
    makespan_ns: int
    peak_running: int


def _release_ns(operator: OperatorInstance) -> int:
    certified = operator.dependency_certified_ns
    if certified is None:
        raise _fail("unresolved_operator")
    return max(operator.arrival_ns, certified)


def schedule_finite_resource(
    graph: MemorySemanticExecutionGraph,
    *,
    resource_limit: int,
) -> OracleSchedule:
    """Schedule only certified work without changing durations or the finite K."""

    if (
        isinstance(resource_limit, bool)
        or not isinstance(resource_limit, int)
        or resource_limit <= 0
    ):
        raise _fail("resource_limit_invalid")
    topological_order = graph.topological_order()
    operators = {operator.operator_id: operator for operator in graph.operators}
    releases = {operator_id: _release_ns(operators[operator_id]) for operator_id in operators}
    if not operators:
        return OracleSchedule(
            resource_limit=resource_limit,
            records=(),
            makespan_ns=0,
            peak_running=0,
        )

    pending = set(operators)
    completed: set[str] = set()
    running: list[tuple[int, str, int]] = []
    records: list[ScheduleRecord] = []
    peak_running = 0
    current_ns = min(releases.values())
    order_index = {operator_id: index for index, operator_id in enumerate(topological_order)}

    while pending or running:
        while running and running[0][0] <= current_ns:
            end_ns, operator_id, start_ns = heapq.heappop(running)
            operator = operators[operator_id]
            if end_ns - start_ns != operator.service_duration_ns:
                raise _fail("service_duration_modified")
            completed.add(operator_id)

        available = [
            operator_id
            for operator_id in pending
            if releases[operator_id] <= current_ns
            and all(
                predecessor_id in completed
                for predecessor_id in graph.predecessors(operator_id)
            )
        ]
        available.sort(
            key=lambda operator_id: (
                operators[operator_id].identity.source_id,
                order_index[operator_id],
                operator_id,
            )
        )
        free_slots = resource_limit - len(running)
        for operator_id in available[:free_slots]:
            operator = operators[operator_id]
            end_ns = current_ns + operator.service_duration_ns
            pending.remove(operator_id)
            heapq.heappush(running, (end_ns, operator_id, current_ns))
            records.append(
                ScheduleRecord(
                    operator_id=operator_id,
                    source_id=operator.identity.source_id,
                    operator_role=operator.identity.operator_role,
                    start_ns=current_ns,
                    end_ns=end_ns,
                )
            )
        peak_running = max(peak_running, len(running))

        if not pending and not running:
            break
        if running and running[0][0] == current_ns:
            continue
        next_times = [end_ns for end_ns, _operator_id, _start_ns in running]
        next_times.extend(
            release
            for operator_id, release in releases.items()
            if operator_id in pending
            and release > current_ns
            and all(
                predecessor_id in completed
                for predecessor_id in graph.predecessors(operator_id)
            )
        )
        if not next_times:
            raise _fail("oracle_progress_impossible")
        current_ns = min(next_times)

    by_id = {record.operator_id: record for record in records}
    for edge in graph.dependencies:
        if by_id[edge.predecessor_id].end_ns > by_id[edge.successor_id].start_ns:
            raise _fail("dependency_schedule_violation")
    for record in records:
        if record.start_ns < releases[record.operator_id]:
            raise _fail("future_evidence_used")
    first_arrival = min(operator.arrival_ns for operator in operators.values())
    last_completion = max(record.end_ns for record in records)
    return OracleSchedule(
        resource_limit=resource_limit,
        records=tuple(records),
        makespan_ns=last_completion - first_arrival,
        peak_running=peak_running,
    )
