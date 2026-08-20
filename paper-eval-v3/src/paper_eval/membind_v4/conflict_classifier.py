"""Deterministic conflict classification for selective v4 speculation.

Classification estimates profitability only. It neither proves semantic
independence nor authorizes reuse; exact predecessor-state validation remains
the sole correctness gate.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from enum import Enum

from paper_eval.membind_v4.conflict_signature import ConflictSignature


class ConflictClass(str, Enum):
    LOW_CONFLICT = "LOW_CONFLICT"
    HIGH_CONFLICT = "HIGH_CONFLICT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ConflictClassification:
    conflict_class: ConflictClass
    reason: str
    overlapping_entity_names: tuple[str, ...] = ()
    overlapping_existing_candidate_ids: tuple[str, ...] = ()


class RecentConflictTelemetry:
    """Bounded telemetry derived only from completed, causally prior events."""

    def __init__(self, *, window_size: int = 8, hot_threshold: int = 2) -> None:
        if (
            isinstance(window_size, bool)
            or not isinstance(window_size, int)
            or window_size <= 0
        ):
            raise ValueError("window_size_invalid")
        if (
            isinstance(hot_threshold, bool)
            or not isinstance(hot_threshold, int)
            or hot_threshold <= 0
            or hot_threshold > window_size
        ):
            raise ValueError("hot_threshold_invalid")
        self._hot_threshold = hot_threshold
        self._events: deque[
            tuple[tuple[tuple[str, str], ...], tuple[str, ...], str | None]
        ] = deque(maxlen=window_size)

    def record_publication(
        self,
        signature: ConflictSignature,
        *,
        published_entity_ids: tuple[str, ...] = (),
        validation_outcome: str | None = None,
    ) -> None:
        if not isinstance(signature, ConflictSignature) or not signature.complete:
            raise ValueError("published_signature_invalid")
        if any(not isinstance(value, str) or not value for value in published_entity_ids):
            raise ValueError("published_entity_ids_invalid")
        if validation_outcome not in {None, "HIT", "MISS", "NO_SPECULATION"}:
            raise ValueError("validation_outcome_invalid")
        self._events.append(
            (
                tuple(sorted(set(signature.entity_keys))),
                tuple(sorted(set(published_entity_ids))),
                validation_outcome,
            )
        )

    @property
    def hot_entity_keys(self) -> frozenset[tuple[str, str]]:
        counts: Counter[tuple[str, str]] = Counter()
        for keys, _ids, _outcome in self._events:
            counts.update(keys)
        return frozenset(
            key for key, count in counts.items() if count >= self._hot_threshold
        )

    @property
    def hot_entity_ids(self) -> frozenset[str]:
        counts: Counter[str] = Counter()
        for _keys, entity_ids, _outcome in self._events:
            counts.update(entity_ids)
        return frozenset(
            entity_id
            for entity_id, count in counts.items()
            if count >= self._hot_threshold
        )

    @property
    def outcome_counts(self) -> dict[str, int]:
        counts = Counter(
            outcome
            for _keys, _ids, outcome in self._events
            if outcome is not None
        )
        return dict(counts)


def classify_conflict(
    frontier: ConflictSignature,
    candidate: ConflictSignature,
    *,
    telemetry: RecentConflictTelemetry | None = None,
) -> ConflictClassification:
    """Classify with stable direct, existing-ID, and bounded-hot signals."""

    if not isinstance(frontier, ConflictSignature) or not isinstance(
        candidate, ConflictSignature
    ):
        raise ValueError("conflict_signature_invalid")
    if not frontier.complete or not candidate.complete:
        return ConflictClassification(ConflictClass.UNKNOWN, "INCOMPLETE_SIGNAL")
    if frontier.namespace is None or candidate.namespace is None:
        return ConflictClassification(ConflictClass.UNKNOWN, "NAMESPACE_UNKNOWN")
    if frontier.namespace != candidate.namespace:
        return ConflictClassification(
            ConflictClass.LOW_CONFLICT, "NAMESPACE_ISOLATED"
        )

    direct = tuple(
        sorted(set(frontier.canonical_names) & set(candidate.canonical_names))
    )
    if direct:
        return ConflictClassification(
            ConflictClass.HIGH_CONFLICT,
            "DIRECT_ENTITY_OVERLAP",
            overlapping_entity_names=direct,
        )

    if (
        frontier.existing_candidate_ids is not None
        and candidate.existing_candidate_ids is not None
    ):
        existing = tuple(
            sorted(
                set(frontier.existing_candidate_ids)
                & set(candidate.existing_candidate_ids)
            )
        )
        if existing:
            return ConflictClassification(
                ConflictClass.HIGH_CONFLICT,
                "EXISTING_CANDIDATE_ID_OVERLAP",
                overlapping_existing_candidate_ids=existing,
            )

    if telemetry is not None:
        if not isinstance(telemetry, RecentConflictTelemetry):
            raise ValueError("conflict_telemetry_invalid")
        hot_names = tuple(
            sorted(
                name
                for namespace, name in candidate.entity_keys
                if (namespace, name) in telemetry.hot_entity_keys
            )
        )
        candidate_ids = set(candidate.existing_candidate_ids or ())
        hot_ids = tuple(sorted(candidate_ids & telemetry.hot_entity_ids))
        if hot_names or hot_ids:
            return ConflictClassification(
                ConflictClass.HIGH_CONFLICT,
                "RECENT_HOT_ENTITY",
                overlapping_entity_names=hot_names,
                overlapping_existing_candidate_ids=hot_ids,
            )

    return ConflictClassification(ConflictClass.LOW_CONFLICT, "KNOWN_DISJOINT")


__all__ = [
    "ConflictClass",
    "ConflictClassification",
    "RecentConflictTelemetry",
    "classify_conflict",
]
