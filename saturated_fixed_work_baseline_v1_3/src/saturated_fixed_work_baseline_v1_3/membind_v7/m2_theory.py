"""Conditional M2 staged-apply and crash-state invariants (T7/T8).

This module is a proof/reference validator only.  It intentionally has no
apply, repair, persistence, or Graphiti implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ApplyPlan:
    source_sequence: int
    base_frontier: int
    preconditions: Mapping[str, int | str]
    effects: tuple[str, ...]
    idempotency_key: str
    hidden_reads: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Frontier:
    durable: int


class CrashState(str, Enum):
    ABSENT = "ABSENT"
    PREPARED = "PREPARED"
    CERTIFIED = "CERTIFIED"
    APPLYING = "APPLYING"
    COMMITTED = "COMMITTED"


def validate_apply_plan(plan: ApplyPlan, frontier: Frontier) -> None:
    if plan.hidden_reads:
        raise ValueError("hidden read in closed apply plan")
    if plan.source_sequence != plan.base_frontier + 1:
        raise ValueError("plan source must extend base frontier by one")
    if frontier.durable != plan.base_frontier:
        raise ValueError("frontier does not match plan base frontier")
    if plan.preconditions.get("frontier") != frontier.durable:
        raise ValueError("frontier precondition failed")
    if not plan.effects or not plan.idempotency_key:
        raise ValueError("closed plan requires effects and idempotency key")


def validate_recovery(before: CrashState, after: CrashState, *, receipt: bool, frontier_advanced: bool) -> None:
    """Check the T8 atomic visibility invariant for one crash point."""

    if frontier_advanced and not receipt:
        raise ValueError("frontier advanced without durable receipt")
    if after == CrashState.COMMITTED and not receipt:
        raise ValueError("committed state lacks receipt")
    if before == CrashState.APPLYING and after not in {CrashState.APPLYING, CrashState.COMMITTED, CrashState.ABSENT}:
        raise ValueError("invalid recovery transition")


__all__ = ["ApplyPlan", "CrashState", "Frontier", "validate_apply_plan", "validate_recovery"]
