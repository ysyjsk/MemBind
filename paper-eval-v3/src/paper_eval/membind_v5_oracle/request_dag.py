from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from .model import (
    DependencyKind,
    DAGEdge,
    RequestRecord,
    TraceBundle,
)


PUBLICATION_PREFIX = "publication:"


@dataclass(frozen=True, slots=True)
class _SyntheticNode:
    node_id: str
    source_sequence: int
    service_duration_ns: int
    completion_ns: int


class RequestDAG:
    """Evidence-backed request DAG plus synthetic publication sinks."""

    def __init__(
        self,
        bundle: TraceBundle,
        edges: Iterable[DAGEdge] = (),
        *,
        unknown_dependencies: Iterable[str] = (),
    ) -> None:
        self.bundle = bundle
        self._requests = bundle.request_by_id
        self._edges: list[DAGEdge] = []
        self._nodes: set[str] = set(self._requests)
        self._synthetic: dict[str, _SyntheticNode] = {}
        for publication in bundle.publications:
            sink_id = self.publication_sink_id(publication.source_sequence)
            tail = self._publication_tail(publication.source_sequence)
            self._synthetic[sink_id] = _SyntheticNode(
                node_id=sink_id,
                source_sequence=publication.source_sequence,
                service_duration_ns=tail,
                completion_ns=publication.publication_ns,
            )
            self._nodes.add(sink_id)
        self.unknown_dependencies = tuple(sorted(set(unknown_dependencies)))
        for edge in edges:
            self._add_edge(edge)

    @classmethod
    def from_edges(
        cls,
        bundle: TraceBundle,
        *,
        extra_edges: Iterable[tuple[str, str, DependencyKind]] = (),
    ) -> "RequestDAG":
        dag = cls(bundle)
        for predecessor, successor, kind in extra_edges:
            dag._add_edge(
                DAGEdge(
                    predecessor=predecessor,
                    successor=successor,
                    kind=kind,
                    evidence="TEST_OR_EXPLICIT",
                )
            )
        dag._ensure_acyclic()
        return dag

    @staticmethod
    def publication_sink_id(source_sequence: int) -> str:
        return f"{PUBLICATION_PREFIX}{source_sequence}"

    def _publication_tail(self, source_sequence: int) -> int:
        publication = self.bundle.publication_by_source.get(source_sequence)
        if publication is None:
            return 0
        requests = [
            request
            for request in self.bundle.requests
            if request.source_sequence == source_sequence
            and request.request_kind == "FRONTIER"
        ]
        if not requests:
            requests = [
                request
                for request in self.bundle.requests
                if request.source_sequence == source_sequence
            ]
        latest_terminal = max((request.terminal_ns for request in requests), default=publication.arrival_ns)
        tail = publication.publication_ns - latest_terminal
        if tail < 0:
            raise ValueError("publication_tail_negative")
        return tail

    @property
    def nodes(self) -> frozenset[str]:
        return frozenset(self._nodes)

    @property
    def requests(self) -> tuple[RequestRecord, ...]:
        return self.bundle.requests

    @property
    def edges(self) -> tuple[DAGEdge, ...]:
        return tuple(self._edges)

    @property
    def has_cycle(self) -> bool:
        try:
            self._ensure_acyclic()
        except ValueError:
            return True
        return False

    @property
    def oracle_evaluable(self) -> bool:
        return not self.unknown_dependencies and not self.has_cycle

    @property
    def topological_order(self) -> tuple[str, ...]:
        indegree = {node: 0 for node in self._nodes}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in self._edges:
            indegree[edge.successor] += 1
            outgoing[edge.predecessor].append(edge.successor)
        ready = deque(node for node in self._nodes if indegree[node] == 0)
        result: list[str] = []
        while ready:
            node = ready.popleft()
            result.append(node)
            for successor in outgoing[node]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
        if len(result) != len(self._nodes):
            raise ValueError("request_dag_cycle")
        return tuple(result)

    def edge(self, predecessor: str, successor: str) -> DAGEdge:
        for edge in self._edges:
            if edge.predecessor == predecessor and edge.successor == successor:
                return edge
        raise KeyError((predecessor, successor))

    def predecessors(self, node_id: str) -> tuple[str, ...]:
        return tuple(edge.predecessor for edge in self._edges if edge.successor == node_id)

    def successors(self, node_id: str) -> tuple[str, ...]:
        return tuple(edge.successor for edge in self._edges if edge.predecessor == node_id)

    def service_duration_ns(self, node_id: str) -> int:
        request = self._requests.get(node_id)
        if request is not None:
            return request.service_duration_ns
        try:
            return self._synthetic[node_id].service_duration_ns
        except KeyError:
            raise KeyError(node_id) from None

    def source_sequence(self, node_id: str) -> int:
        request = self._requests.get(node_id)
        if request is not None:
            return request.source_sequence
        return self._synthetic[node_id].source_sequence

    def criticality_ns(self, node_id: str) -> int:
        """Longest remaining service path to publication, including fixed tail."""
        order = self.topological_order
        remaining: dict[str, int] = {}
        for current in reversed(order):
            remaining[current] = self.service_duration_ns(current) + max(
                (remaining[successor] for successor in self.successors(current)),
                default=0,
            )
        return remaining[node_id]

    def downstream_nodes(self, node_id: str) -> frozenset[str]:
        """Return the transitive successor set without inventing dependencies."""
        if node_id not in self._nodes:
            raise KeyError(node_id)
        selected: set[str] = set()
        stack = list(self.successors(node_id))
        while stack:
            current = stack.pop()
            if current in selected:
                continue
            selected.add(current)
            stack.extend(self.successors(current))
        return frozenset(selected)

    def downstream_publication_distance_ns(self, node_id: str) -> int:
        return self.criticality_ns(node_id)

    def downstream_blocked_work_count(self, node_id: str) -> int:
        return sum(
            successor in self._requests
            for successor in self.downstream_nodes(node_id)
        )

    def publication_unlock_count(self, node_id: str) -> int:
        return sum(
            successor.startswith(PUBLICATION_PREFIX)
            for successor in self.downstream_nodes(node_id)
        )

    def critical_path_membership(self, node_id: str) -> bool:
        """Whether a node lies on a longest request-to-publication path."""
        if node_id not in self._nodes:
            raise KeyError(node_id)
        source = self.source_sequence(node_id)
        source_requests = [
            request.request_id
            for request in self.bundle.requests
            if request.source_sequence == source
        ]
        sink_id = self.publication_sink_id(source)
        if not source_requests:
            return node_id == sink_id
        order = self.topological_order
        prefix: dict[str, int] = {}
        for current in order:
            if self.source_sequence(current) != source:
                continue
            predecessors = [
                predecessor
                for predecessor in self.predecessors(current)
                if self.source_sequence(predecessor) == source
            ]
            prefix[current] = self.service_duration_ns(current) + max(
                (prefix[predecessor] for predecessor in predecessors),
                default=0,
            )
        max_total = max(
            (prefix[request_id] + self.criticality_ns(request_id) - self.service_duration_ns(request_id)
             for request_id in source_requests if request_id in prefix),
            default=self.criticality_ns(sink_id),
        )
        if node_id == sink_id:
            return self.criticality_ns(node_id) == max_total
        return node_id in prefix and prefix[node_id] + self.criticality_ns(node_id) - self.service_duration_ns(node_id) == max_total

    def source_publication_critical_path_ns(self, source_sequence: int) -> int:
        request_ids = [
            request.request_id
            for request in self.bundle.requests
            if request.source_sequence == source_sequence
        ]
        sink_id = self.publication_sink_id(source_sequence)
        if not request_ids:
            return self.criticality_ns(sink_id)
        return max(
            (
                self.criticality_ns(request_id)
                for request_id in request_ids
                if not self.predecessors(request_id)
            ),
            default=self.criticality_ns(sink_id),
        )

    def publication_tail_ns(self, source_sequence: int) -> int:
        return self._synthetic[self.publication_sink_id(source_sequence)].service_duration_ns

    def _add_edge(self, edge: DAGEdge) -> None:
        if edge.predecessor not in self._nodes:
            raise ValueError("dependency_predecessor_missing")
        if edge.successor not in self._nodes:
            raise ValueError("dependency_successor_missing")
        if edge.predecessor == edge.successor:
            raise ValueError("dependency_self_edge")
        if edge in self._edges:
            return
        self._edges.append(edge)
        try:
            self._ensure_acyclic()
        except ValueError:
            self._edges.pop()
            raise

    def _ensure_acyclic(self) -> None:
        self.topological_order


def _edge(
    predecessor: RequestRecord,
    successor: RequestRecord,
    kind: DependencyKind,
    evidence: str,
) -> DAGEdge:
    return DAGEdge(
        predecessor=predecessor.request_id,
        successor=successor.request_id,
        kind=kind,
        evidence=evidence,
    )


def build_request_dag(bundle: TraceBundle) -> RequestDAG:
    """Build only dependencies supported by the installed Graphiti call graph."""

    edges: list[DAGEdge] = []
    unknown: set[str] = set()
    by_source: dict[int, list[RequestRecord]] = defaultdict(list)
    for request in bundle.requests:
        by_source[request.source_sequence].append(request)

    known_roles = {
        "graphiti.extract_nodes",
        "graphiti.extract_edges",
        "graphiti.resolve_extracted_nodes",
        "graphiti.resolve_extracted_edges",
        "graphiti.extract_attributes_from_nodes",
    }
    for request in bundle.requests:
        if request.operator_role not in known_roles:
            unknown.add(
                f"UNKNOWN_DEPENDENCY:{request.operator_id}:operator_role_unrecognized"
            )

    # resolve_extracted_edges fans out one coroutine per extracted edge, but a
    # single coroutine may issue dedupe, attribute, and timestamp requests in
    # sequence. Q0 records only their shared source-level operator_id. Without
    # prompt_name or a per-edge child identity, the exact intra-operator
    # predecessor pairs cannot be recovered from request timing alone.
    edge_requests_by_operator: dict[str, list[RequestRecord]] = defaultdict(list)
    for request in bundle.requests:
        if request.operator_role == "graphiti.resolve_extracted_edges":
            edge_requests_by_operator[request.operator_id].append(request)
    for operator_id, requests in edge_requests_by_operator.items():
        if len(requests) > 1:
            unknown.add(
                f"UNKNOWN_DEPENDENCY:{operator_id}:per_edge_child_identity_missing"
            )

    for source, source_requests in sorted(by_source.items()):
        ordered = sorted(source_requests, key=lambda item: (item.submitted_ns, item.request_id))
        compile_requests = [r for r in ordered if r.request_kind == "COMPILE"]
        frontier_requests = [r for r in ordered if r.request_kind == "FRONTIER"]
        by_role: dict[str, list[RequestRecord]] = defaultdict(list)
        for request in ordered:
            by_role[request.operator_role].append(request)

        # Graphiti calls extraction in this order within a compile phase.
        if by_role["graphiti.extract_nodes"] and by_role["graphiti.extract_edges"]:
            edges.append(
                _edge(
                    by_role["graphiti.extract_nodes"][-1],
                    by_role["graphiti.extract_edges"][0],
                    DependencyKind.DATA,
                    "graphiti_core.graphiti._extract_and_resolve_edges",
                )
            )

        # resolve_extracted_edges is launched with semaphore_gather; no chain is
        # inferred between its individual requests.
        if by_role["graphiti.resolve_extracted_nodes"] and by_role[
            "graphiti.resolve_extracted_edges"
        ]:
            for edge_request in by_role["graphiti.resolve_extracted_edges"]:
                edges.append(
                    _edge(
                        by_role["graphiti.resolve_extracted_nodes"][-1],
                        edge_request,
                        DependencyKind.DATA,
                        "graphiti_core.graphiti._extract_and_resolve_edges",
                    )
                )
        if by_role["graphiti.resolve_extracted_edges"] and by_role[
            "graphiti.extract_attributes_from_nodes"
        ]:
            for resolve_request in by_role["graphiti.resolve_extracted_edges"]:
                edges.append(
                    _edge(
                        resolve_request,
                        by_role["graphiti.extract_attributes_from_nodes"][0],
                        DependencyKind.CONTROL,
                        "graphiti_core.graphiti.add_episode",
                    )
                )
        elif by_role["graphiti.resolve_extracted_nodes"] and by_role[
            "graphiti.extract_attributes_from_nodes"
        ]:
            edges.append(
                _edge(
                    by_role["graphiti.resolve_extracted_nodes"][-1],
                    by_role["graphiti.extract_attributes_from_nodes"][0],
                    DependencyKind.CONTROL,
                    "graphiti_core.graphiti.add_episode (empty edge phase)",
                )
            )

        if compile_requests and frontier_requests:
            for frontier_request in frontier_requests:
                edges.append(
                    _edge(
                        compile_requests[-1],
                        frontier_request,
                        DependencyKind.CONTROL,
                        "membind_v31.graphiti_adapter prepared-before-bind",
                    )
                )

        # A source cannot publish before its final frontier request. If an
        # operator is unknown, this edge still records publication dependency,
        # while the unknown marker disables the oracle gate.
        sink_id = RequestDAG.publication_sink_id(source)
        publication_predecessors = frontier_requests or compile_requests
        for publication_predecessor in publication_predecessors:
            edges.append(
                DAGEdge(
                    predecessor=publication_predecessor.request_id,
                    successor=sink_id,
                    kind=DependencyKind.PUBLICATION,
                    evidence="Graphiti.add_episode awaits all invoked LLM work before publication",
                )
            )

    dag = RequestDAG(bundle, edges, unknown_dependencies=unknown)
    dag._ensure_acyclic()
    return dag
