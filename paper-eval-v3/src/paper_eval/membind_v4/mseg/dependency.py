"""Dependency and readiness contracts for a Memory Semantic Execution Graph."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from enum import Enum

from .operator_identity import OperatorIdentity


class MSEGDependencyError(ValueError):
    """The graph or its progressive evidence is internally inconsistent."""


def _fail(code: str) -> MSEGDependencyError:
    return MSEGDependencyError(code)


def _timestamp(value: object, code: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


class DependencyType(str, Enum):
    DATA_DEP = "DATA_DEP"
    VERSION_DEP = "VERSION_DEP"
    EFFECT_CONFLICT_DEP = "EFFECT_CONFLICT_DEP"
    PUBLICATION_DEP = "PUBLICATION_DEP"


class DependencyKnowledgeState(str, Enum):
    CERTIFIED_READY = "CERTIFIED_READY"
    CERTIFIED_BLOCKED = "CERTIFIED_BLOCKED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class OperatorInstance:
    identity: OperatorIdentity
    arrival_ns: int
    dependency_certified_ns: int | None
    observed_start_ns: int | None
    service_duration_ns: int
    is_publication: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.identity, OperatorIdentity):
            raise _fail("operator_identity_invalid")
        arrival = _timestamp(self.arrival_ns, "arrival_ns_invalid")
        certified = _timestamp(
            self.dependency_certified_ns,
            "dependency_certified_ns_invalid",
            optional=True,
        )
        start = _timestamp(
            self.observed_start_ns,
            "observed_start_ns_invalid",
            optional=True,
        )
        duration = _timestamp(self.service_duration_ns, "service_duration_ns_invalid")
        if certified is not None and certified < arrival:
            raise _fail("dependency_certified_before_arrival")
        if start is not None and start < arrival:
            raise _fail("observed_start_before_arrival")
        if start is not None and certified is not None and start < certified:
            raise _fail("observed_start_before_certification")
        if not isinstance(self.is_publication, bool):
            raise _fail("publication_flag_invalid")
        assert duration is not None

    @property
    def operator_id(self) -> str:
        return self.identity.operator_id

    @property
    def observed_end_ns(self) -> int | None:
        if self.observed_start_ns is None:
            return None
        return self.observed_start_ns + self.service_duration_ns


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    predecessor_id: str
    successor_id: str
    dependency_type: DependencyType
    discovered_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor_id, str) or not self.predecessor_id:
            raise _fail("dependency_predecessor_invalid")
        if not isinstance(self.successor_id, str) or not self.successor_id:
            raise _fail("dependency_successor_invalid")
        if self.predecessor_id == self.successor_id:
            raise _fail("dependency_self_edge")
        if not isinstance(self.dependency_type, DependencyType):
            raise _fail("dependency_type_invalid")
        _timestamp(self.discovered_ns, "dependency_discovered_ns_invalid")


class MemorySemanticExecutionGraph:
    """A DAG whose readiness is gated by explicit progressive evidence."""

    def __init__(self) -> None:
        self._operators: dict[str, OperatorInstance] = {}
        self._edges: list[DependencyEdge] = []

    def add_operator(self, operator: OperatorInstance) -> None:
        if not isinstance(operator, OperatorInstance):
            raise _fail("operator_invalid")
        if operator.operator_id in self._operators:
            raise _fail("operator_duplicate")
        self._operators[operator.operator_id] = operator

    def add_dependency(self, edge: DependencyEdge) -> None:
        if not isinstance(edge, DependencyEdge):
            raise _fail("dependency_invalid")
        if edge.predecessor_id not in self._operators:
            raise _fail("dependency_predecessor_missing")
        successor = self._operators.get(edge.successor_id)
        if successor is None:
            raise _fail("dependency_successor_missing")
        if edge in self._edges:
            raise _fail("dependency_duplicate")
        certified = successor.dependency_certified_ns
        if certified is not None and edge.discovered_ns > certified:
            raise _fail("dependency_discovered_after_certification")
        self._edges.append(edge)
        try:
            self.topological_order()
        except MSEGDependencyError:
            self._edges.pop()
            raise

    @property
    def operators(self) -> tuple[OperatorInstance, ...]:
        return tuple(self._operators.values())

    @property
    def dependencies(self) -> tuple[DependencyEdge, ...]:
        return tuple(self._edges)

    def operator(self, operator_id: str) -> OperatorInstance:
        try:
            return self._operators[operator_id]
        except KeyError:
            raise _fail("operator_missing") from None

    def incoming_dependencies(self, operator_id: str) -> tuple[DependencyEdge, ...]:
        self.operator(operator_id)
        return tuple(edge for edge in self._edges if edge.successor_id == operator_id)

    def outgoing_dependencies(self, operator_id: str) -> tuple[DependencyEdge, ...]:
        self.operator(operator_id)
        return tuple(edge for edge in self._edges if edge.predecessor_id == operator_id)

    def predecessors(self, operator_id: str) -> tuple[str, ...]:
        return tuple(edge.predecessor_id for edge in self.incoming_dependencies(operator_id))

    def successors(self, operator_id: str) -> tuple[str, ...]:
        return tuple(edge.successor_id for edge in self.outgoing_dependencies(operator_id))

    def edge_counts(self) -> dict[str, int]:
        counts = Counter(edge.dependency_type.value for edge in self._edges)
        return dict(sorted(counts.items()))

    def topological_order(self) -> tuple[str, ...]:
        indegree = {operator_id: 0 for operator_id in self._operators}
        successors: dict[str, list[str]] = {operator_id: [] for operator_id in self._operators}
        for edge in self._edges:
            indegree[edge.successor_id] += 1
            successors[edge.predecessor_id].append(edge.successor_id)
        ready = deque(
            operator_id for operator_id in self._operators if indegree[operator_id] == 0
        )
        order: list[str] = []
        while ready:
            operator_id = ready.popleft()
            order.append(operator_id)
            for successor_id in successors[operator_id]:
                indegree[successor_id] -= 1
                if indegree[successor_id] == 0:
                    ready.append(successor_id)
        if len(order) != len(self._operators):
            raise _fail("dependency_cycle")
        return tuple(order)

    def knowledge_state(
        self,
        operator_id: str,
        *,
        at_ns: int,
        completed_ids: set[str],
    ) -> DependencyKnowledgeState:
        operator = self.operator(operator_id)
        now = _timestamp(at_ns, "knowledge_timestamp_invalid")
        if not isinstance(completed_ids, set):
            raise _fail("completed_ids_invalid")
        assert now is not None
        known_dependencies = [
            edge for edge in self.incoming_dependencies(operator_id) if edge.discovered_ns <= now
        ]
        if any(edge.predecessor_id not in completed_ids for edge in known_dependencies):
            return DependencyKnowledgeState.CERTIFIED_BLOCKED
        certified = operator.dependency_certified_ns
        if certified is None or now < certified:
            return DependencyKnowledgeState.UNRESOLVED
        return DependencyKnowledgeState.CERTIFIED_READY

    def earliest_certified_start_ns(self, operator_id: str) -> int | None:
        operator = self.operator(operator_id)
        certified = operator.dependency_certified_ns
        if certified is None:
            return None
        boundaries = [operator.arrival_ns, certified]
        for predecessor_id in self.predecessors(operator_id):
            predecessor_end = self.operator(predecessor_id).observed_end_ns
            if predecessor_end is None:
                raise _fail("predecessor_observed_end_missing")
            boundaries.append(predecessor_end)
        return max(boundaries)

    def certified_advance_window_ns(self, operator_id: str) -> int | None:
        operator = self.operator(operator_id)
        earliest = self.earliest_certified_start_ns(operator_id)
        if earliest is None or operator.observed_start_ns is None:
            return None
        if operator.observed_start_ns < earliest:
            raise _fail("observed_start_illegal")
        return operator.observed_start_ns - earliest
