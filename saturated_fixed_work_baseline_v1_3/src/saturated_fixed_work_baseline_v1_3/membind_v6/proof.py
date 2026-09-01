"""Machine-checkable V6 runtime invariants used before a success seal."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class V6ProofError(ValueError):
    pass


def validate_frontier_events(events: Sequence[Mapping[str, Any]], *, source_count: int) -> dict[str, Any]:
    durable = [row for row in events if row.get("event") == "PUBLICATION_DURABLE"]
    observed = [row.get("source_sequence") for row in durable]
    expected = list(range(int(source_count)))
    if observed != expected:
        raise V6ProofError(f"durable frontier is not strictly ordered: {observed}")
    return {"schema_version": "membind.v6.frontier-proof.v1", "status": "PASS", "durable_frontier": int(source_count) - 1, "publication_count": len(durable)}


def validate_provider_events(events: Sequence[Mapping[str, Any]], *, capacity: int) -> dict[str, Any]:
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        raise V6ProofError("provider capacity is invalid")
    admits = [row for row in events if row.get("event") == "ADMISSION_ADMIT"]
    for row in admits:
        outstanding = row.get("outstanding")
        future = row.get("future_outstanding")
        if not isinstance(outstanding, int) or outstanding < 0 or outstanding > capacity:
            raise V6ProofError("provider outstanding exceeds capacity")
        if not isinstance(future, int) or future < 0 or future > max(0, capacity - 1):
            raise V6ProofError("future outstanding violates reserved capacity")
    return {
        "schema_version": "membind.v6.provider-proof.v1",
        "status": "PASS",
        "capacity": capacity,
        "admission_count": len(admits),
        "max_outstanding": max((int(row["outstanding"]) for row in admits), default=0),
        "max_future_outstanding": max((int(row["future_outstanding"]) for row in admits), default=0),
    }


def validate_replay_accounting(summary: Mapping[str, Any]) -> dict[str, Any]:
    captured = summary.get("logical_captured")
    consumed = summary.get("logical_consumed")
    discarded = summary.get("logical_discarded", 0)
    duplicates = summary.get("duplicates")
    unconsumed = summary.get("unconsumed")
    fresh_fallback = summary.get("fresh_fallback", 0)
    mismatch_fallback = summary.get("mismatch_fallback", 0)
    missing_fallback = summary.get("missing_fallback", 0)
    values = (
        captured,
        consumed,
        discarded,
        duplicates,
        unconsumed,
        fresh_fallback,
        mismatch_fallback,
        missing_fallback,
    )
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in values
    ):
        raise V6ProofError("replay accounting fields are invalid")
    if (
        captured != consumed + discarded + unconsumed
        or fresh_fallback != mismatch_fallback + missing_fallback
        or duplicates != 0
        or unconsumed != 0
    ):
        raise V6ProofError("replay accounting is incomplete")
    return {
        "schema_version": "membind.v6.replay-proof.v2",
        "status": "PASS",
        "logical_captured": captured,
        "logical_consumed": consumed,
        "logical_discarded": discarded,
        "fresh_fallback": fresh_fallback,
        "mismatch_fallback": mismatch_fallback,
        "missing_fallback": missing_fallback,
    }


def validate_request_comparisons(comparisons: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in comparisons:
        if row.get("match") is True and row.get("changed_fields"):
            raise V6ProofError("false accept in request comparison")
    return {
        "schema_version": "membind.v6.request-proof.v1",
        "status": "PASS",
        "comparison_count": len(comparisons),
        "match_count": sum(row.get("match") is True for row in comparisons),
        "miss_count": sum(row.get("match") is not True for row in comparisons),
    }


__all__ = [
    "V6ProofError",
    "validate_frontier_events",
    "validate_provider_events",
    "validate_replay_accounting",
    "validate_request_comparisons",
]
