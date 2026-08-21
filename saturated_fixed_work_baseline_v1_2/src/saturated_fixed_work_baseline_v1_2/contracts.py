"""Frozen protocol identities and fail-closed value contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from enum import Enum


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """A protocol identity or schema contract was violated."""


class Availability(str, Enum):
    MEASURED = "MEASURED"
    DERIVED = "DERIVED"
    NOT_EXPOSED_BY_PINNED_STACK = "NOT_EXPOSED_BY_PINNED_STACK"
    NOT_EVALUATED = "NOT_EVALUATED"
    INVALID = "INVALID"
    AMBIGUOUS_PROCESS_GLOBAL = "AMBIGUOUS_PROCESS_GLOBAL"


@dataclass(frozen=True, slots=True)
class MetricValue:
    availability: Availability
    value: float | int | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.availability, Availability):
            raise ContractError("METRIC_AVAILABILITY_INVALID")
        available = self.availability in {Availability.MEASURED, Availability.DERIVED}
        if available and (isinstance(self.value, bool) or not isinstance(self.value, (int, float))):
            raise ContractError("METRIC_VALUE_REQUIRED")
        if not available and self.value is not None:
            raise ContractError("METRIC_VALUE_FORBIDDEN")
        if not available and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ContractError("METRIC_REASON_REQUIRED")

    @classmethod
    def unavailable(cls, availability: Availability, reason: str) -> "MetricValue":
        return cls(availability=availability, value=None, reason=reason)


@dataclass(frozen=True, slots=True)
class EpisodeInput:
    history_id: str
    session_id: str
    source_sequence: int
    source_hash: str
    reference_time: str
    body: str
    namespace: str

    @property
    def name(self) -> str:
        return f"{self.history_id}::episode::{self.source_sequence:04d}"

    def __post_init__(self) -> None:
        for value, code in (
            (self.history_id, "EPISODE_HISTORY_ID_INVALID"),
            (self.session_id, "EPISODE_SESSION_ID_INVALID"),
            (self.reference_time, "EPISODE_REFERENCE_TIME_INVALID"),
            (self.body, "EPISODE_BODY_INVALID"),
            (self.namespace, "EPISODE_NAMESPACE_INVALID"),
        ):
            if not isinstance(value, str) or not value:
                raise ContractError(code)
        if isinstance(self.source_sequence, bool) or not isinstance(self.source_sequence, int) or self.source_sequence < 0:
            raise ContractError("EPISODE_SOURCE_SEQUENCE_INVALID")
        if _SHA256.fullmatch(self.source_hash) is None:
            raise ContractError("EPISODE_SOURCE_HASH_INVALID")


@dataclass(frozen=True, slots=True)
class ResumeIdentity:
    project_sha256: str
    data_sha256: str
    provider_sha256: str
    resource_sha256: str
    config_sha256: str
    cache_sha256: str
    namespace: str

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "namespace":
                if not isinstance(value, str) or not value:
                    raise ContractError("RESUME_NAMESPACE_INVALID")
            elif not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ContractError(f"RESUME_{field.name.upper()}_INVALID")


def validate_resume_identity(expected: ResumeIdentity, observed: ResumeIdentity) -> None:
    if not isinstance(expected, ResumeIdentity) or not isinstance(observed, ResumeIdentity):
        raise ContractError("RESUME_IDENTITY_INVALID")
    for field in fields(expected):
        if getattr(expected, field.name) != getattr(observed, field.name):
            raise ContractError(f"RESUME_{field.name.upper()}_MISMATCH")


__all__ = [
    "Availability",
    "ContractError",
    "EpisodeInput",
    "MetricValue",
    "ResumeIdentity",
    "validate_resume_identity",
]
