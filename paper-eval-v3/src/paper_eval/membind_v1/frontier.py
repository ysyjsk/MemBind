"""Source-ordered durable-state model and canonical node coalescing rules."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import canonical_bytes


_NEW = "NEW"
_INTENT = "INTENT_DURABLE"
_PREPARE_RUNNING = "PREPARE_RUNNING"
_PREPARED = "PREPARED_DURABLE"
_BIND_RUNNING = "BIND_RUNNING"
_COMMIT_RETURNED = "COMMIT_RETURNED"
_PUBLICATION = "PUBLICATION_DURABLE"
_POISONED = "AMBIGUOUS_COMMIT_POISONED"


class MemBindV1FrontierError(ValueError):
    """A source-order or durable-state invariant was violated."""


def _fail(code: str) -> MemBindV1FrontierError:
    return MemBindV1FrontierError(code)


def _source_sequence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("source_sequence_invalid")
    return value


def _canonical_node(node: object) -> tuple[str, dict[str, Any]]:
    if not isinstance(node, Mapping):
        raise _fail("resolved_node_invalid")
    uuid = node.get("uuid")
    if not isinstance(uuid, str) or not uuid:
        raise _fail("resolved_node_uuid_missing")
    try:
        encoded = canonical_bytes(dict(node)).decode("utf-8")
        decoded = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail("resolved_node_projection_invalid") from None
    if not isinstance(decoded, dict):
        raise _fail("resolved_node_projection_invalid")
    return encoded, decoded


def coalesce_compatible_nodes(nodes: Sequence[Mapping[str, object]]) -> tuple[dict[str, Any], ...]:
    """Coalesce same UUID/same projection duplicates and reject conflicts."""

    if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Sequence):
        raise _fail("resolved_nodes_invalid")
    selected: list[dict[str, Any]] = []
    by_uuid: dict[str, str] = {}
    for node in nodes:
        projection, decoded = _canonical_node(node)
        uuid = str(decoded["uuid"])
        prior = by_uuid.get(uuid)
        if prior is None:
            by_uuid[uuid] = projection
            selected.append(decoded)
        elif prior != projection:
            raise _fail("conflicting_duplicate_uuid")
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class FrontierEvent:
    """A pure event projection that an external durable store can persist."""

    event_sequence: int
    source_sequence: int
    state: str


class SourceOrderedFrontier:
    """Fail-closed state machine for one exact source inventory.

    This class is intentionally storage-free.  Its ordered event projections are
    the complete data a later durable-store integration must persist before the
    corresponding state is considered acknowledged by a live runtime.
    """

    def __init__(self, *, source_count: int) -> None:
        if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count <= 0:
            raise _fail("source_count_invalid")
        self._states = [_NEW for _ in range(source_count)]
        self._events: list[FrontierEvent] = []
        self._published_frontier = -1
        self._poisoned = False

    @property
    def source_count(self) -> int:
        return len(self._states)

    @property
    def published_frontier(self) -> int:
        return self._published_frontier

    @property
    def published_source_sequences(self) -> tuple[int, ...]:
        return tuple(range(self._published_frontier + 1))

    @property
    def durable_events(self) -> tuple[FrontierEvent, ...]:
        return tuple(self._events)

    @property
    def is_complete(self) -> bool:
        return self._published_frontier == self.source_count - 1 and not self._poisoned

    def state_of(self, source_sequence: int) -> str:
        sequence = self._checked_sequence(source_sequence)
        return self._states[sequence]

    def _checked_sequence(self, source_sequence: int) -> int:
        sequence = _source_sequence(source_sequence)
        if sequence >= self.source_count:
            raise _fail("source_sequence_out_of_range")
        return sequence

    def _assert_not_poisoned(self) -> None:
        if self._poisoned:
            raise _fail("attempt_poisoned")

    def _transition(self, source_sequence: int, *, expected: str, next_state: str) -> None:
        self._assert_not_poisoned()
        sequence = self._checked_sequence(source_sequence)
        if self._states[sequence] != expected:
            raise _fail("invalid_state_transition")
        self._states[sequence] = next_state
        self._events.append(
            FrontierEvent(
                event_sequence=len(self._events),
                source_sequence=sequence,
                state=next_state,
            )
        )

    def record_intent(self, source_sequence: int) -> None:
        self._transition(source_sequence, expected=_NEW, next_state=_INTENT)

    def record_prepare_started(self, source_sequence: int) -> None:
        self._transition(source_sequence, expected=_INTENT, next_state=_PREPARE_RUNNING)

    def record_prepared(self, source_sequence: int) -> None:
        self._transition(source_sequence, expected=_PREPARE_RUNNING, next_state=_PREPARED)

    def record_bind_started(self, source_sequence: int) -> None:
        self._assert_not_poisoned()
        sequence = self._checked_sequence(source_sequence)
        if sequence != self._published_frontier + 1:
            raise _fail("bind_not_at_frontier")
        self._transition(sequence, expected=_PREPARED, next_state=_BIND_RUNNING)

    def record_commit_returned(self, source_sequence: int) -> None:
        self._transition(source_sequence, expected=_BIND_RUNNING, next_state=_COMMIT_RETURNED)

    def record_publication_durable(self, source_sequence: int) -> None:
        self._assert_not_poisoned()
        sequence = self._checked_sequence(source_sequence)
        if sequence != self._published_frontier + 1:
            raise _fail("publication_not_at_frontier")
        self._transition(sequence, expected=_COMMIT_RETURNED, next_state=_PUBLICATION)
        self._published_frontier = sequence

    def poison_ambiguous_commit(self, source_sequence: int) -> None:
        sequence = self._checked_sequence(source_sequence)
        if self._states[sequence] != _COMMIT_RETURNED:
            raise _fail("ambiguous_commit_state_invalid")
        self._states[sequence] = _POISONED
        self._events.append(
            FrontierEvent(
                event_sequence=len(self._events),
                source_sequence=sequence,
                state=_POISONED,
            )
        )
        self._poisoned = True


__all__ = [
    "FrontierEvent",
    "MemBindV1FrontierError",
    "SourceOrderedFrontier",
    "coalesce_compatible_nodes",
]
