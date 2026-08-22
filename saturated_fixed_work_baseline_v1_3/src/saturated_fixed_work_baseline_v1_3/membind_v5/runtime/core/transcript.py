"""Immutable logical oracle transcript capture and strict single-consume ledger."""

from __future__ import annotations

import copy
import contextvars
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .request_identity import RequestIdentity
from .trace import SourceTraceRecorder


class TranscriptError(RuntimeError):
    pass


class DuplicateCapture(TranscriptError):
    pass


class BindingMismatch(TranscriptError):
    """Fail-closed replay error with a sanitized, machine-readable reason."""

    def __init__(self, message: str, *, reason: str = "unknown", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = str(reason)
        self.details = dict(details or {})


def _digest_value(value: Any) -> str:
    """Return a stable short digest without retaining prompts or responses."""

    encoded = repr(value).encode("utf-8", errors="backslashreplace")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _identity_diff(expected: RequestIdentity, actual: RequestIdentity) -> dict[str, Any]:
    fields = (
        "source_sequence",
        "callsite",
        "ordinal",
        "messages",
        "response_model",
        "max_tokens",
        "model_size",
        "group_id",
        "prompt_name",
        "flags",
        "client_identity",
        "transport_identity",
        "cache_salt",
        "previous_context_digest",
    )
    changed: list[str] = []
    for field in fields:
        if getattr(expected, field) != getattr(actual, field):
            changed.append(field)
    return {
        "expected_digest_prefix": expected.digest[:16],
        "candidate_digest_prefix": actual.digest[:16],
        "source_sequence": expected.source_sequence,
        "callsite": expected.callsite,
        "ordinal": expected.ordinal,
        "changed_fields": changed,
        "expected_messages_digest": _digest_value(expected.messages),
        "candidate_messages_digest": _digest_value(actual.messages),
    }


@dataclass(frozen=True, slots=True)
class Transcript:
    identity: RequestIdentity
    response: Any
    transport_attempts: int
    consumed: bool = False

    def copy_response(self) -> Any:
        return copy.deepcopy(self.response)


class TranscriptStore:
    def __init__(self) -> None:
        self._items: dict[str, Transcript] = {}
        self._captured = 0
        self._consumed = 0
        self._duplicates = 0

    def capture(self, identity: RequestIdentity, response: Any, *, transport_attempts: int = 1) -> Transcript:
        if identity.digest in self._items:
            self._duplicates += 1
            raise DuplicateCapture(f"duplicate logical request: {identity.digest}")
        if isinstance(transport_attempts, bool) or transport_attempts <= 0:
            raise ValueError("transport_attempts must be positive")
        item = Transcript(identity, copy.deepcopy(response), int(transport_attempts))
        self._items[identity.digest] = item
        self._captured += 1
        return item

    def has(self, identity: RequestIdentity) -> bool:
        return identity.digest in self._items

    def consume(self, identity: RequestIdentity) -> Any:
        item = self._items.get(identity.digest)
        if item is None:
            same_key = [
                candidate.identity
                for candidate in self._items.values()
                if candidate.identity.source_sequence == identity.source_sequence
                and candidate.identity.callsite == identity.callsite
                and candidate.identity.ordinal == identity.ordinal
            ]
            same_source = [
                candidate.identity
                for candidate in self._items.values()
                if candidate.identity.source_sequence == identity.source_sequence
            ]
            if same_key:
                details = _identity_diff(identity, same_key[0])
                raise BindingMismatch(
                    f"identity mismatch: {identity.digest}",
                    reason="identity_mismatch",
                    details=details,
                )
            raise BindingMismatch(
                f"missing transcript: {identity.digest}",
                reason="missing",
                details={
                    "source_sequence": identity.source_sequence,
                    "callsite": identity.callsite,
                    "ordinal": identity.ordinal,
                    "same_source_count": len(same_source),
                    "requested_digest_prefix": identity.digest[:16],
                },
            )
        if item.consumed:
            self._duplicates += 1
            raise BindingMismatch(
                f"duplicate transcript consume: {identity.digest}",
                reason="duplicate_consume",
                details={"requested_digest_prefix": identity.digest[:16]},
            )
        self._items[identity.digest] = Transcript(item.identity, item.response, item.transport_attempts, True)
        self._consumed += 1
        return item.copy_response()

    def unconsumed(self) -> tuple[RequestIdentity, ...]:
        return tuple(item.identity for item in self._items.values() if not item.consumed)

    def unconsumed_for_source(self, source_sequence: int) -> tuple[RequestIdentity, ...]:
        return tuple(
            item.identity
            for item in self._items.values()
            if not item.consumed and item.identity.source_sequence == int(source_sequence)
        )

    def summary(self) -> dict[str, int]:
        return {
            "logical_captured": self._captured,
            "logical_consumed": self._consumed,
            "unconsumed": len(self.unconsumed()),
            "duplicates": self._duplicates,
        }

    def export_public_summary(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "keys": sorted(self._items),
            "transport_attempts": {key: item.transport_attempts for key, item in sorted(self._items.items())},
        }


class CaptureSession:
    """Capture final logical responses while preserving caller and source scope."""

    def __init__(self, store: TranscriptStore, *, source_sequence: int, recorder: SourceTraceRecorder | None = None) -> None:
        self.store = store
        self.source_sequence = int(source_sequence)
        self.recorder = recorder
        self._ordinals: dict[str, int] = {}

    @contextmanager
    def logical_call(self, callsite: str):
        if self.recorder is not None:
            with self.recorder.span("PREPARE", callsite):
                yield
        else:
            yield

    def next_ordinal(self, callsite: str) -> int:
        ordinal = self._ordinals.get(callsite, 0)
        self._ordinals[callsite] = ordinal + 1
        return ordinal

    async def capture_call(self, identity: RequestIdentity, delegate: Any, *, transport_attempts: int = 1) -> Any:
        if identity.source_sequence != self.source_sequence:
            raise BindingMismatch("capture source sequence mismatch")
        response = await delegate()
        self.store.capture(identity, response, transport_attempts=transport_attempts)
        return copy.deepcopy(response)
