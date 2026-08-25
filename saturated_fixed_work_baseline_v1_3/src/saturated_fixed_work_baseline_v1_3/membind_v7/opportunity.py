"""Semantic DAG counterfactual and same-resource opportunity metrics (R3)."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DagNode:
    node_id: str
    predecessors: tuple[str, ...]
    cost: float

    def __post_init__(self) -> None:
        if self.cost < 0:
            raise ValueError("DAG cost must be non-negative")
        object.__setattr__(self, "predecessors", tuple(self.predecessors))


@dataclass(frozen=True, slots=True)
class LongestPath:
    cost: float
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Counterfactual:
    baseline: LongestPath
    candidate: LongestPath
    saved_cost: float
    removed: frozenset[str]

    @property
    def path(self) -> tuple[str, ...]:
        return self.candidate.path


def longest_path(nodes: Iterable[DagNode]) -> LongestPath:
    by_id = {node.node_id: node for node in nodes}
    state: dict[str, int] = {}
    memo: dict[str, LongestPath] = {}

    def visit(node_id: str) -> LongestPath:
        mark = state.get(node_id, 0)
        if mark == 1:
            raise ValueError("semantic DAG contains a cycle")
        if mark == 2:
            return memo[node_id]
        node = by_id[node_id]
        state[node_id] = 1
        if not node.predecessors:
            result = LongestPath(node.cost, (node.node_id,))
        else:
            choices = [visit(parent) for parent in node.predecessors]
            best = max(choices, key=lambda value: (value.cost, value.path))
            result = LongestPath(best.cost + node.cost, best.path + (node.node_id,))
        state[node_id] = 2
        memo[node_id] = result
        return result

    if not by_id:
        return LongestPath(0.0, ())
    return max((visit(node_id) for node_id in by_id), key=lambda value: (value.cost, value.path))


def counterfactual(nodes: Iterable[DagNode], *, removed: set[str] | frozenset[str]) -> Counterfactual:
    original = tuple(nodes)
    removed_set = frozenset(removed)
    unknown = removed_set - {node.node_id for node in original}
    if unknown:
        raise ValueError(f"counterfactual references unknown nodes: {sorted(unknown)}")
    candidate = tuple(
        DagNode(node.node_id, node.predecessors, 0.0 if node.node_id in removed_set else node.cost)
        for node in original
    )
    baseline_path = longest_path(original)
    candidate_path = longest_path(candidate)
    return Counterfactual(baseline_path, candidate_path, baseline_path.cost - candidate_path.cost, removed_set)


def costed_counterfactual(
    nodes: Iterable[DagNode],
    *,
    removed: set[str] | frozenset[str],
    added: Iterable[DagNode],
    gates: dict[str, tuple[str, ...]] | None = None,
) -> Counterfactual:
    """Recompute a zeroed-work graph with explicit certificate/repair nodes.

    ``gates`` maps an existing candidate node to added cost nodes that must
    complete before it.  Original dependency edges are retained, so a cost is
    represented exactly once in the costed DAG instead of being subtracted a
    second time after longest-path recomputation.
    """

    original = tuple(nodes)
    additions = tuple(added)
    original_ids = {node.node_id for node in original}
    added_ids = {node.node_id for node in additions}
    if len(original_ids) != len(original) or len(added_ids) != len(additions):
        raise ValueError("semantic DAG node IDs must be unique")
    if original_ids & added_ids:
        raise ValueError("costed DAG additions collide with baseline nodes")
    removed_set = frozenset(removed)
    if not removed_set <= original_ids:
        raise ValueError("costed counterfactual removes an unknown node")
    selected_gates = dict(gates or {})
    if not set(selected_gates) <= original_ids:
        raise ValueError("costed counterfactual gates an unknown node")
    if any(not set(parents) <= added_ids for parents in selected_gates.values()):
        raise ValueError("costed counterfactual gate references an unknown cost node")
    known = original_ids | added_ids
    if any(not set(node.predecessors) <= known for node in additions):
        raise ValueError("costed counterfactual cost node has an unknown predecessor")
    candidate = tuple(
        DagNode(
            node.node_id,
            tuple(dict.fromkeys((*node.predecessors, *selected_gates.get(node.node_id, ())))),
            0.0 if node.node_id in removed_set else node.cost,
        )
        for node in original
    ) + additions
    baseline_path = longest_path(original)
    candidate_path = longest_path(candidate)
    return Counterfactual(
        baseline_path,
        candidate_path,
        baseline_path.cost - candidate_path.cost,
        removed_set,
    )


def work_ratio(*, direct: float, affected: float) -> float | None:
    if direct < 0 or affected < 0:
        raise ValueError("work must be non-negative")
    if direct == 0:
        return None if affected == 0 else inf
    return affected / direct


def cascade_share(*, affected: float, direct: float, total: float) -> float | None:
    if min(affected, direct, total) < 0:
        raise ValueError("work must be non-negative")
    if total == 0:
        return None
    return max(0.0, affected - direct) / total


__all__ = [
    "Counterfactual",
    "DagNode",
    "LongestPath",
    "cascade_share",
    "costed_counterfactual",
    "counterfactual",
    "longest_path",
    "work_ratio",
]
