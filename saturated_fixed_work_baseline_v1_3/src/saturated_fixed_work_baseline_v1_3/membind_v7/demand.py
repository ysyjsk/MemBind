"""Adaptive demand validity and provider replay contract separation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ReplayStatus(str, Enum):
    ALLOWED = "ALLOWED"
    DISALLOWED = "DISALLOWED"
    UNKNOWN = "UNKNOWN"


REQUIRED_REPLAY_FIELDS = frozenset(
    {"request_digest", "model_epoch", "schema_epoch", "tool_epoch", "config_epoch", "policy_epoch"}
)


@dataclass(frozen=True, slots=True)
class ReplayAdmissibility:
    status: ReplayStatus
    authority: str | None
    request_fields: frozenset[str]
    artifact_complete: bool
    hidden_state_fields: frozenset[str] = frozenset()
    external_side_effects: bool = False

    @property
    def can_replay(self) -> bool:
        return (
            self.status == ReplayStatus.ALLOWED
            and bool(self.authority)
            and self.artifact_complete
            and REQUIRED_REPLAY_FIELDS <= self.request_fields
            and not self.hidden_state_fields
            and not self.external_side_effects
        )

    def require_replay(self) -> None:
        if not self.can_replay:
            raise ValueError("provider replay contract is not admissible")


@dataclass(frozen=True, slots=True)
class DemandNode:
    name: str
    dependencies: Mapping[str, Any]
    request_digest: str | None = None
    response_contract: ReplayAdmissibility | None = None


@dataclass(frozen=True, slots=True)
class DemandValidity:
    status: str
    reasons: tuple[str, ...] = ()
    request_stable: bool = False
    response_replayable: bool = False


def check_demand_validity(demand: DemandNode) -> DemandValidity:
    required = ("existence", "binding", "predecessor", "builder", "request")
    reasons = tuple(name for name in required if demand.dependencies.get(name) != "same")
    request_stable = not reasons or "request" not in reasons
    response_replayable = bool(demand.response_contract and demand.response_contract.can_replay)
    return DemandValidity(
        status="STABLE" if not reasons else "INVALID",
        reasons=reasons,
        request_stable=request_stable,
        response_replayable=response_replayable,
    )


__all__ = ["DemandNode", "DemandValidity", "ReplayAdmissibility", "ReplayStatus", "check_demand_validity"]
