"""Finite, auditable edge-task planning for the shared extraction substrate.

The planner defines the semantic candidate domain before any provider call.  A
response acknowledges every pair in a task and contains zero or more relations
for those pairs.  It never uses a model-generated terminal assertion as proof
of exhaustion.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


MAX_PAIRS_PER_TASK = 2
# A single relation per pair keeps the worst-case finite task schema below
# Graphiti's pinned 16,384 completion-token wire budget.  A second relation is
# an explicit overflow/failure, never silent truncation.
MAX_RELATIONS_PER_PAIR = 1
MAX_TASKS_PER_SOURCE = 512
FACT_MAX_LENGTH = 1900


class EdgeTaskProtocolError(ValueError):
    """A task response does not prove completion of its declared domain."""


class EdgeTaskOverflow(EdgeTaskProtocolError):
    """The bounded relation list may have truncated a pair's relations."""


def _normal_name(value: Any) -> str:
    return " ".join(str(value).split())


def _pair_id(pair: tuple[str, str]) -> str:
    return f"{pair[0]}||{pair[1]}"


@dataclass(frozen=True, slots=True)
class EdgeTask:
    task_id: str
    pair_ids: tuple[tuple[str, str], ...]
    evidence_hash: str
    max_relations_per_pair: int = MAX_RELATIONS_PER_PAIR

    @property
    def entity_names(self) -> tuple[str, ...]:
        return tuple(sorted({name for pair in self.pair_ids for name in pair}))


@dataclass(frozen=True, slots=True)
class EdgeTaskPlan:
    tasks: tuple[EdgeTask, ...]
    pair_count: int
    declared_task_count: int
    maximum_provider_calls: int
    digest: str
    max_pairs_per_task: int


def build_edge_task_plan(
    entity_names: Sequence[str],
    *,
    evidence: str = "",
    max_pairs_per_task: int = MAX_PAIRS_PER_TASK,
    max_relations_per_pair: int = MAX_RELATIONS_PER_PAIR,
    max_tasks: int = MAX_TASKS_PER_SOURCE,
) -> EdgeTaskPlan:
    """Build a deterministic complete unordered-pair task plan.

    The quadratic logical domain is explicit.  The finite source task guard is
    a correctness guard, not a sampling policy: callers must fail closed when
    it is exceeded rather than dropping pairs.
    """

    if max_pairs_per_task < 1 or max_relations_per_pair < 1 or max_tasks < 1:
        raise ValueError("edge task bounds must be positive")
    names = tuple(sorted({_normal_name(value) for value in entity_names if _normal_name(value)}))
    pairs = tuple(
        (left, right)
        for index, left in enumerate(names)
        for right in names[index + 1 :]
    )
    task_count = (len(pairs) + max_pairs_per_task - 1) // max_pairs_per_task
    if task_count > max_tasks:
        raise EdgeTaskOverflow(
            f"declared edge task count {task_count} exceeds finite source guard {max_tasks}"
        )
    evidence_hash = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    tasks = tuple(
        EdgeTask(
            task_id=f"edge-task-{index:06d}",
            pair_ids=tuple(pairs[start : start + max_pairs_per_task]),
            evidence_hash=evidence_hash,
            max_relations_per_pair=max_relations_per_pair,
        )
        for index, start in enumerate(range(0, len(pairs), max_pairs_per_task))
    )
    digest = hashlib.sha256(
        json.dumps(
            {
                "evidence_hash": evidence_hash,
                "max_pairs_per_task": max_pairs_per_task,
                "max_relations_per_pair": max_relations_per_pair,
                "tasks": [
                    {"task_id": task.task_id, "pair_ids": task.pair_ids}
                    for task in tasks
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return EdgeTaskPlan(
        tasks=tasks,
        pair_count=len(pairs),
        declared_task_count=len(tasks),
        maximum_provider_calls=len(tasks),
        digest=digest,
        max_pairs_per_task=max_pairs_per_task,
    )


def validate_edge_task_result(
    result: Mapping[str, Any],
    task: EdgeTask,
    *,
    fact_max_length: int = FACT_MAX_LENGTH,
) -> list[dict[str, Any]]:
    """Validate one finite task and return canonical Graphiti edge payloads."""

    if not isinstance(result, Mapping):
        raise EdgeTaskProtocolError("edge task response is not an object")
    if result.get("status") == "no_additional_edge":
        raise EdgeTaskProtocolError("terminal-only response is not a task completion")
    if result.get("status") != "complete":
        raise EdgeTaskProtocolError("edge task status is not complete")
    completed = result.get("pairs_completed")
    if not isinstance(completed, list):
        raise EdgeTaskProtocolError("edge task pair coverage is missing")
    expected_ids = {_pair_id(pair) for pair in task.pair_ids}
    observed_ids = {str(value) for value in completed}
    unknown = observed_ids - expected_ids
    if unknown:
        raise EdgeTaskProtocolError(f"unknown pair acknowledgement: {sorted(unknown)}")
    if observed_ids != expected_ids or len(completed) != len(expected_ids):
        raise EdgeTaskProtocolError("edge task pair coverage is incomplete or duplicated")
    raw_edges = result.get("edges")
    if not isinstance(raw_edges, list):
        raise EdgeTaskProtocolError("edge task edges list is missing")
    allowed_pairs = {frozenset(pair) for pair in task.pair_ids}
    counts: dict[frozenset[str], int] = {}
    seen_edges: set[tuple[str, str, str, str]] = set()
    validated: list[dict[str, Any]] = []
    for raw in raw_edges:
        if not isinstance(raw, Mapping):
            raise EdgeTaskProtocolError("edge task contains a non-object edge")
        edge = dict(raw)
        source = _normal_name(edge.get("source_entity_name"))
        target = _normal_name(edge.get("target_entity_name"))
        if not source or not target or source == target:
            raise EdgeTaskProtocolError("edge endpoint is invalid")
        pair = frozenset((source, target))
        if pair not in allowed_pairs:
            raise EdgeTaskProtocolError("edge endpoint is outside the declared task pair")
        relation_type = _normal_name(edge.get("relation_type"))
        fact = str(edge.get("fact", ""))
        if (
            not relation_type
            or not fact
            or len(relation_type) > 128
            or len(fact) > fact_max_length
            or any(ord(c) > 127 for c in relation_type + fact)
        ):
            raise EdgeTaskProtocolError("edge relation or fact violates bounded fields")
        edge_identity = (source, target, relation_type, fact)
        if edge_identity in seen_edges:
            raise EdgeTaskProtocolError("edge task repeats the same edge")
        seen_edges.add(edge_identity)
        counts[pair] = counts.get(pair, 0) + 1
        # The configured cap is inclusive: exactly one relation is valid when
        # max_relations_per_pair=1; reject only the next relation.
        if counts[pair] > task.max_relations_per_pair:
            raise EdgeTaskOverflow(
                f"edge relation cap reached for pair {source}||{target}"
            )
        edge["source_entity_name"] = source
        edge["target_entity_name"] = target
        edge["relation_type"] = relation_type
        edge["fact"] = fact
        validated.append(edge)
    return validated


__all__ = [
    "MAX_PAIRS_PER_TASK",
    "MAX_RELATIONS_PER_PAIR",
    "MAX_TASKS_PER_SOURCE",
    "EdgeTask",
    "EdgeTaskOverflow",
    "EdgeTaskPlan",
    "EdgeTaskProtocolError",
    "build_edge_task_plan",
    "validate_edge_task_result",
]
