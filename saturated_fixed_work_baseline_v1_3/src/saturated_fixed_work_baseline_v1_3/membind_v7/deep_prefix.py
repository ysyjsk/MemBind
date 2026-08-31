"""Fail-closed observation contract for the nested CUT-D deep prefix.

This module deliberately does not claim a Summary C1 delta theorem.  It only
compares two complete observations and returns UNKNOWN whenever any upstream,
batching, schema, or canonical-request input differs.  A selected operator may
later use a C0 fresh oracle or a C2 repair branch around this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .dvsr_seam import canonical_digest


SUMMARY_OBSERVATION_REQUIRED = frozenset(
    {
        "read_epoch",
        "resolved_node_ids",
        "resolved_node_order",
        "existing_summaries",
        "new_edges",
        "new_edge_order",
        "previous_episode_projection",
        "entity_type_schema",
        "batch_membership",
        "batch_order",
        "canonical_request_digest",
        "hydrated_continuation_digest",
    }
)


class DeepPrefixStatus(str, Enum):
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class DeepPrefixResult:
    status: DeepPrefixStatus
    reason: str
    changed_fields: tuple[str, ...] = ()


def validate_summary_observation(observation: Mapping[str, Any]) -> tuple[str, ...]:
    """Return missing fields; callers must fail closed when non-empty."""

    return tuple(sorted(SUMMARY_OBSERVATION_REQUIRED - set(observation)))


def compare_deep_prefix_observations(
    old: Mapping[str, Any],
    fresh: Mapping[str, Any],
) -> DeepPrefixResult:
    missing_old = validate_summary_observation(old)
    missing_fresh = validate_summary_observation(fresh)
    if missing_old or missing_fresh:
        missing = tuple(sorted(set(missing_old) | set(missing_fresh)))
        return DeepPrefixResult(DeepPrefixStatus.UNKNOWN, "summary observation is incomplete", missing)
    if old["read_epoch"] != fresh["read_epoch"]:
        return DeepPrefixResult(DeepPrefixStatus.UNKNOWN, "mixed snapshot or read epoch changed", ("read_epoch",))

    # Every listed field is prompt-visible or controls Graphiti's batching and
    # continuation.  No field is silently normalized: order and partition are
    # semantically observable at the native publication seam.
    comparable = tuple(sorted(SUMMARY_OBSERVATION_REQUIRED - {"read_epoch"}))
    changed = tuple(field for field in comparable if canonical_digest(old[field]) != canonical_digest(fresh[field]))
    if changed:
        return DeepPrefixResult(DeepPrefixStatus.UNKNOWN, "deep-prefix semantic input changed", changed)
    return DeepPrefixResult(DeepPrefixStatus.STABLE, "complete deep-prefix observation is identical")


__all__ = [
    "DeepPrefixResult",
    "DeepPrefixStatus",
    "SUMMARY_OBSERVATION_REQUIRED",
    "compare_deep_prefix_observations",
    "validate_summary_observation",
]
