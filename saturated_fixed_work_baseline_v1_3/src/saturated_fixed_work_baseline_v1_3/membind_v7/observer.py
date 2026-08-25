"""Schema-frozen, observer-only records for R0-R3.

The disabled path calls the native function directly and does not inspect or
mutate its return value.  Completion timestamps are deliberately excluded from
semantic edges.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping


class ObservationError(ValueError):
    pass


REQUIRED_FIELDS = frozenset(
    {
        "snapshot",
        "read_operator",
        "observable_projection",
        "query",
        "filter",
        "limit",
        "ranking",
        "tie_contract",
        "index_epoch",
        "witness",
        "delta",
        "completeness_status",
        "previous_episode_selector",
        "previous_episode_window",
        "previous_episode_order",
        "previous_episode_projection",
        "previous_episode_digest",
        "dependency_edges",
        "demand_existence",
        "demand_binding",
        "demand_predecessors",
        "request_identity",
        "continuation_observable_k",
        "logical_id_bijection",
        "native_read_epoch",
        "native_oracle_epoch",
        "native_effect_epoch",
        "replay_contract",
        "mutation_intent",
        "post_backend_diff",
        "resource_cost",
        "semantic_critical_path",
    }
)


def validate_record(record: Mapping[str, Any]) -> None:
    missing = sorted(REQUIRED_FIELDS - set(record))
    if missing:
        raise ObservationError(f"required observation field missing: {missing[0]}")
    replay_contract = record.get("replay_contract")
    if not isinstance(replay_contract, Mapping) or replay_contract.get("status") not in {"ALLOWED", "DISALLOWED", "UNKNOWN"}:
        raise ObservationError("invalid replay contract status")
    if record.get("completeness_status") not in {"COMPLETE", "UNKNOWN", "INCOMPLETE"}:
        raise ObservationError("invalid completeness status")


@dataclass(frozen=True, slots=True)
class Observation:
    operation: str
    payload_digest: str
    record: Mapping[str, Any]


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class Observer:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self._records: list[Observation] = []

    @property
    def records(self) -> tuple[Observation, ...]:
        return tuple(self._records)

    def run(self, operation: str, native: Callable[[], Any], *, record: Mapping[str, Any] | None = None) -> Any:
        result = native()
        if self.enabled and record is not None:
            validate_record(record)
            self._records.append(Observation(str(operation), _digest(result), dict(record)))
        return result


def semantic_edges(events: list[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    """Extract only declared semantic predecessors; ignore completion order."""

    edges: set[tuple[str, str]] = set()
    for event in events:
        target = event.get("id")
        for source in event.get("semantic_predecessors", ()):
            if isinstance(target, str) and isinstance(source, str):
                edges.add((source, target))
    return tuple(sorted(edges))


__all__ = ["Observation", "ObservationError", "Observer", "REQUIRED_FIELDS", "semantic_edges", "validate_record"]
