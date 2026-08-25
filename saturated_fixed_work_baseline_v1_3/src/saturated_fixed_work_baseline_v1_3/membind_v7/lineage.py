"""Typed six-kind dependency closure and stable-name alignment (T4/T5)."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from enum import Enum
from typing import Any, Iterable


class DependencyKind(str, Enum):
    DATA = "data"
    CONTROL = "control"
    EXISTENCE = "existence"
    ORDERED = "ordered-collection"
    ENVIRONMENT = "environment/oracle"
    EFFECT = "effect/publication"


class AlignmentStatus(str, Enum):
    UNIQUE = "UNIQUE"
    OLD_ONLY = "OLD_ONLY"
    NEW_ONLY = "NEW_ONLY"
    AMBIGUOUS = "AMBIGUOUS"


class DependencyGraph:
    def __init__(self) -> None:
        self._edges: dict[str, list[tuple[str, DependencyKind]]] = defaultdict(list)

    def add(self, source: str, target: str, kind: DependencyKind) -> None:
        if not source or not target:
            raise ValueError("dependency endpoints are required")
        edge = (target, DependencyKind(kind))
        if edge not in self._edges[source]:
            self._edges[source].append(edge)

    def successors(self, source: str) -> tuple[tuple[str, DependencyKind], ...]:
        return tuple(self._edges.get(source, ()))

    def reaches(self, source: str, target: str) -> bool:
        seen = {source}
        todo = deque([source])
        while todo:
            current = todo.popleft()
            for successor, _ in self._edges.get(current, ()):
                if successor == target:
                    return True
                if successor not in seen:
                    seen.add(successor)
                    todo.append(successor)
        return source == target

    def upstream(self, target: str) -> tuple[tuple[str, DependencyKind], ...]:
        found: list[tuple[str, DependencyKind]] = []
        for source, successors in self._edges.items():
            for candidate, kind in successors:
                if candidate == target or self.reaches(candidate, target):
                    found.append((source, kind))
        return tuple(sorted(found, key=lambda item: (item[0], item[1].value)))

    def fingerprint(self, target: str) -> str:
        payload = [(source, kind.value) for source, kind in self.upstream(target)]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def dependency_fingerprint(graph: DependencyGraph, target: str) -> str:
    return graph.fingerprint(target)


class AlignmentResult:
    def __init__(self, status: AlignmentStatus, *, key: tuple[Any, ...] | None = None, reason: str = "") -> None:
        self.status = status
        self.key = key
        self.reason = reason


def align_names(*, old: Iterable[tuple[Any, ...]], new: Iterable[tuple[Any, ...]]) -> AlignmentResult:
    old_list = list(old)
    new_list = list(new)
    old_counts: dict[tuple[Any, ...], int] = defaultdict(int)
    new_counts: dict[tuple[Any, ...], int] = defaultdict(int)
    for key in old_list:
        old_counts[tuple(key)] += 1
    for key in new_list:
        new_counts[tuple(key)] += 1
    ambiguous = [key for key, count in {**old_counts, **new_counts}.items() if old_counts[key] > 1 or new_counts[key] > 1]
    if ambiguous:
        return AlignmentResult(AlignmentStatus.AMBIGUOUS, key=tuple(ambiguous[0]), reason="stable name is not unique")
    if len(old_counts) == 1 and not new_counts:
        return AlignmentResult(AlignmentStatus.OLD_ONLY, key=next(iter(old_counts)))
    if len(new_counts) == 1 and not old_counts:
        return AlignmentResult(AlignmentStatus.NEW_ONLY, key=next(iter(new_counts)))
    if set(old_counts) == set(new_counts):
        return AlignmentResult(AlignmentStatus.UNIQUE, key=next(iter(old_counts), None))
    return AlignmentResult(AlignmentStatus.AMBIGUOUS, reason="alignment requires a non-unique or structural mapping")


__all__ = [
    "AlignmentResult",
    "AlignmentStatus",
    "DependencyGraph",
    "DependencyKind",
    "dependency_fingerprint",
    "align_names",
]
