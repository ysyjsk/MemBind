"""Small immutable contracts shared by every MemBind backend.

The core stores no Graphiti, model, database, or benchmark objects.  A
prepared result is reusable only when its complete request identity matches
the authoritative request identity at publication time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


def _json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    """All fields that can change the result of one Native request."""

    logical_id: str
    method: str
    episode_sha256: str
    previous_state_sha256: str
    model_identity: str
    graphiti_identity: str
    schema_sha256: str
    config_sha256: str
    extra: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in (
            "logical_id", "method", "episode_sha256", "previous_state_sha256",
            "model_identity", "graphiti_identity", "schema_sha256", "config_sha256",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in self.extra):
            raise ValueError("extra identity fields must be string pairs")
        if len({k for k, _ in self.extra}) != len(self.extra):
            raise ValueError("extra identity keys must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_id": self.logical_id,
            "method": self.method,
            "episode_sha256": self.episode_sha256,
            "previous_state_sha256": self.previous_state_sha256,
            "model_identity": self.model_identity,
            "graphiti_identity": self.graphiti_identity,
            "schema_sha256": self.schema_sha256,
            "config_sha256": self.config_sha256,
            "extra": dict(self.extra),
        }

    @property
    def digest(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class PreparedWork:
    """A durable result of Native preparation, never an authoritative write."""

    identity: RequestIdentity
    payload: Any
    producer: str
    created_ns: int
    payload_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RequestIdentity):
            raise TypeError("identity must be RequestIdentity")
        if not isinstance(self.producer, str) or not self.producer:
            raise ValueError("producer must be non-empty")
        if isinstance(self.created_ns, bool) or not isinstance(self.created_ns, int) or self.created_ns < 0:
            raise ValueError("created_ns must be a non-negative integer")
        computed = canonical_sha256(self.payload)
        if self.payload_sha256 and self.payload_sha256 != computed:
            raise ValueError("payload_sha256 mismatch")
        object.__setattr__(self, "payload_sha256", computed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "identity_digest": self.identity.digest,
            "payload": self.payload,
            "payload_sha256": self.payload_sha256,
            "producer": self.producer,
            "created_ns": self.created_ns,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool) or not isinstance(self.reason, str) or not self.reason:
            raise ValueError("invalid validation result")


def validate_prepared_work(work: PreparedWork | None, expected: RequestIdentity) -> ValidationResult:
    """Validate identity and payload integrity before allowing reuse."""

    if not isinstance(expected, RequestIdentity):
        raise TypeError("expected must be RequestIdentity")
    if work is None:
        return ValidationResult(False, "MISSING_PREPARED_WORK")
    if not isinstance(work, PreparedWork):
        return ValidationResult(False, "INVALID_PREPARED_WORK_TYPE")
    if work.identity != expected:
        return ValidationResult(False, "REQUEST_IDENTITY_MISMATCH")
    if canonical_sha256(work.payload) != work.payload_sha256:
        return ValidationResult(False, "PAYLOAD_DIGEST_MISMATCH")
    return ValidationResult(True, "VALID")


class PreparedWorkStore:
    """In-memory store with one consumable entry per logical request."""

    def __init__(self) -> None:
        self._items: dict[str, PreparedWork] = {}

    def put(self, work: PreparedWork) -> None:
        if not isinstance(work, PreparedWork):
            raise TypeError("store accepts PreparedWork only")
        key = work.identity.logical_id
        if key in self._items:
            raise ValueError(f"prepared work already exists: {key}")
        self._items[key] = work

    def get(self, logical_id: str) -> PreparedWork | None:
        return self._items.get(logical_id)

    def pop(self, logical_id: str) -> PreparedWork | None:
        return self._items.pop(logical_id, None)

    def __len__(self) -> int:
        return len(self._items)

