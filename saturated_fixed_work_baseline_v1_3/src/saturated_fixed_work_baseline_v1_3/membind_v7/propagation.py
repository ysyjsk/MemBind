"""Finite semantic change propagation and reconvergence reference algorithm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .semantics import alpha_equivalent


# ``None`` is a valid operator result.  A separate identity sentinel is
# therefore required to represent a repair that has not been executed.
MISSING_REPAIR = object()


@dataclass(frozen=True, slots=True)
class PropagationNode:
    node_id: str
    output: Any
    dirty: bool = False
    repaired_output: Any = MISSING_REPAIR
    unknown: bool = False


@dataclass(frozen=True, slots=True)
class PropagationResult:
    repaired: frozenset[str]
    affected: frozenset[str]
    unaffected: frozenset[str]
    iterations: int
    unknown: frozenset[str] = frozenset()


def propagate(
    nodes: Mapping[str, PropagationNode],
    edges: tuple[tuple[str, str], ...],
    *,
    max_repairs: int | None = None,
) -> PropagationResult:
    """Run the guarded fixed point; equal repaired output reconverges locally."""

    if max_repairs == 0:
        raise ValueError("termination bound is exhausted")
    successors: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    for source, target in edges:
        if source not in nodes or target not in nodes:
            raise ValueError("propagation edge references unknown node")
        successors[source].append(target)
        indegree[target] += 1
    topo = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while topo:
        current = topo.pop()
        visited += 1
        for target in successors[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                topo.append(target)
    if visited != len(nodes):
        raise ValueError("propagation termination requires an acyclic semantic graph")
    dirty = {node_id for node_id, node in nodes.items() if node.dirty or node.unknown}
    queue = list(dirty)
    repaired: set[str] = set()
    unknown: set[str] = set()
    affected: set[str] = set(dirty)
    iterations = 0
    while queue:
        current = queue.pop(0)
        iterations += 1
        if max_repairs is not None and iterations > max_repairs:
            raise ValueError("propagation did not terminate within bound")
        node = nodes[current]
        if node.unknown:
            unknown.add(current)
            for successor in successors[current]:
                if successor not in affected:
                    affected.add(successor)
                    queue.append(successor)
            continue
        if node.dirty and node.repaired_output is not MISSING_REPAIR:
            repaired.add(current)
            if alpha_equivalent(node.output, node.repaired_output):
                continue
        else:
            # A dirty node without an actual repair result, or a clean
            # successor reached after a changed predecessor, is unknown.
            unknown.add(current)
        for successor in successors[current]:
            if successor not in affected:
                affected.add(successor)
                queue.append(successor)
    unaffected = set(nodes) - affected
    return PropagationResult(frozenset(repaired), frozenset(affected), frozenset(unaffected), iterations, frozenset(unknown))


__all__ = ["MISSING_REPAIR", "PropagationNode", "PropagationResult", "propagate"]
