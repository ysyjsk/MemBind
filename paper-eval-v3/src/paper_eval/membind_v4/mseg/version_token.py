"""Backend-neutral logical memory version evidence.

The MEG compiler must never turn a wall-clock timestamp or source sequence
into a state version.  A :class:`MemoryVersionToken` is a committed logical
position with an explicit namespace, backend epoch, transaction evidence, and
content hash.  The module is pure and has no database or clock dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum


class VersionTokenError(ValueError):
    """A logical memory version token is malformed or unsafe."""


def _fail(code: str) -> VersionTokenError:
    return VersionTokenError(code)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _hash(value: object, code: str) -> str:
    selected = _text(value, code).lower()
    if _HEX64.fullmatch(selected) is None:
        raise _fail(code)
    return selected


def _counter(value: object, code: str = "logical_counter_required") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(code)
    if value < 0:
        raise _fail("logical_counter_nonnegative")
    return value


@dataclass(frozen=True, slots=True)
class MemoryVersionToken:
    """An exact logical state position, independent of wall-clock time."""

    namespace: str
    backend_id: str
    epoch: str
    counter: int
    transaction_id: str
    evidence_hash: str
    predecessor: "MemoryVersionToken | None" = None

    def __post_init__(self) -> None:
        _text(self.namespace, "version_namespace_invalid")
        _text(self.backend_id, "version_backend_invalid")
        _text(self.epoch, "version_epoch_invalid")
        _counter(self.counter)
        _text(self.transaction_id, "version_transaction_required")
        _hash(self.evidence_hash, "version_evidence_hash_invalid")
        if self.predecessor is not None:
            if not isinstance(self.predecessor, MemoryVersionToken):
                raise _fail("version_predecessor_invalid")
            if (
                self.predecessor.namespace != self.namespace
                or self.predecessor.backend_id != self.backend_id
                or self.predecessor.epoch != self.epoch
            ):
                raise _fail("version_predecessor_domain_mismatch")
            if self.counter <= self.predecessor.counter:
                raise _fail("version_counter_not_after_predecessor")

    @property
    def canonical(self) -> str:
        payload = {
            "backend_id": self.backend_id,
            "counter": self.counter,
            "epoch": self.epoch,
            "evidence_hash": self.evidence_hash,
            "namespace": self.namespace,
            "predecessor": None
            if self.predecessor is None
            else self.predecessor.canonical,
            "transaction_id": self.transaction_id,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        # The counter is intentionally visible for diagnostics, while the
        # digest binds all fields and prevents timestamp-only substitution.
        return f"mv1:{self.namespace}:{self.epoch}:{self.counter}:{digest}"

    @property
    def is_logical(self) -> bool:
        return True

    def is_after(self, predecessor: "MemoryVersionToken | None") -> bool:
        if predecessor is None:
            return self.predecessor is None
        return (
            self.namespace == predecessor.namespace
            and self.backend_id == predecessor.backend_id
            and self.epoch == predecessor.epoch
            and self.counter > predecessor.counter
        )

    @classmethod
    def from_external(cls, value: object) -> "MemoryVersionToken":
        """Reject untrusted external values, especially wall-clock strings.

        A backend adapter must construct a token from a commit/version API and
        include transaction plus evidence hash.  Accepting an ISO timestamp,
        integer, or arbitrary opaque string here would silently turn temporal
        ordering into state ordering.
        """

        if isinstance(value, (int, float)) or isinstance(value, str):
            raise _fail("wall_clock_only_version_forbidden")
        raise _fail("external_version_evidence_required")


class VersionTokenValidation(str, Enum):
    CERTIFIED = "CERTIFIED"
    OPAQUE = "OPAQUE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class VersionValidationResult:
    status: VersionTokenValidation
    codes: tuple[str, ...] = ()


def validate_version_token(
    token: MemoryVersionToken,
    *,
    predecessor: MemoryVersionToken | None = None,
) -> VersionValidationResult:
    """Validate token provenance without inferring missing backend facts."""

    if not isinstance(token, MemoryVersionToken):
        return VersionValidationResult(
            VersionTokenValidation.INVALID, ("version_token_invalid",)
        )
    if predecessor is not None:
        if not isinstance(predecessor, MemoryVersionToken):
            return VersionValidationResult(
                VersionTokenValidation.INVALID, ("version_predecessor_invalid",)
            )
        if not token.is_after(predecessor):
            return VersionValidationResult(
                VersionTokenValidation.INVALID,
                ("version_not_after_predecessor",),
            )
        if token.predecessor != predecessor:
            return VersionValidationResult(
                VersionTokenValidation.OPAQUE,
                ("predecessor_binding_not_observed",),
            )
    return VersionValidationResult(VersionTokenValidation.CERTIFIED)


class VersionTokenFactory:
    """Small deterministic issuer used by offline fixtures and adapters.

    The factory models a backend commit counter.  It does not use a clock and
    does not contact a backend; a production adapter would replace the commit
    counter with an atomically returned backend token.
    """

    def __init__(self, *, backend_id: str, epoch: str) -> None:
        self.backend_id = _text(backend_id, "version_backend_invalid")
        self.epoch = _text(epoch, "version_epoch_invalid")
        self._counters: dict[str, int] = defaultdict(int)

    def commit(
        self,
        *,
        namespace: str,
        transaction_id: str,
        evidence_hash: str,
        predecessor: MemoryVersionToken | None = None,
    ) -> MemoryVersionToken:
        namespace = _text(namespace, "version_namespace_invalid")
        if predecessor is not None and predecessor.namespace != namespace:
            raise _fail("version_predecessor_domain_mismatch")
        next_counter = self._counters[namespace] + 1
        if predecessor is not None:
            next_counter = max(next_counter, predecessor.counter + 1)
        token = MemoryVersionToken(
            namespace=namespace,
            backend_id=self.backend_id,
            epoch=self.epoch,
            counter=next_counter,
            transaction_id=_text(transaction_id, "version_transaction_required"),
            evidence_hash=_hash(evidence_hash, "version_evidence_hash_invalid"),
            predecessor=predecessor,
        )
        self._counters[namespace] = next_counter
        return token


__all__ = [
    "MemoryVersionToken",
    "VersionTokenError",
    "VersionTokenFactory",
    "VersionTokenValidation",
    "VersionValidationResult",
    "validate_version_token",
]
