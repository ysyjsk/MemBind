"""Ordered durable frontier state machine."""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class FrontierViolation(RuntimeError):
    pass


@dataclass(slots=True)
class FrontierRuntime:
    source_count: int
    clock: Callable[[], int] = time.monotonic_ns
    event_sink: Callable[[dict[str, Any]], None] | None = None
    durable_frontier: int = -1
    failed_sequence: int | None = None
    prepared: dict[int, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def _event(self, event: str, sequence: int | None = None, **extra: Any) -> None:
        row = {"event": event, "monotonic_ns": int(self.clock()), "source_sequence": sequence, **extra}
        self.events.append(row)
        if self.event_sink is not None:
            self.event_sink(dict(row))

    async def mark_prepared(self, sequence: int, transcript: Any) -> None:
        self._check_sequence(sequence)
        if self.failed_sequence is not None:
            raise FrontierViolation("frontier failed")
        if sequence in self.prepared:
            raise FrontierViolation("duplicate preparation")
        self.prepared[sequence] = transcript
        self._event("PREPARED", sequence)

    async def publish(self, sequence: int, native_publish: Callable[[Any], Awaitable[Any]]) -> Any:
        self._check_sequence(sequence)
        if self.failed_sequence is not None:
            raise FrontierViolation("frontier failed")
        expected = self.durable_frontier + 1
        if sequence != expected:
            raise FrontierViolation(f"predecessor frontier requires {expected}, got {sequence}")
        if sequence not in self.prepared:
            raise FrontierViolation("missing preparation")
        value = self.prepared[sequence]
        self._event("NATIVE_ENTER", sequence)
        try:
            result = native_publish(value)
            if inspect.isawaitable(result):
                result = await result
        except BaseException as exc:
            self.failed_sequence = sequence
            self._event("FAILURE", sequence, error_type=f"{type(exc).__module__}.{type(exc).__qualname__}")
            raise
        self.durable_frontier = sequence
        self.prepared.pop(sequence, None)
        self._event("PUBLICATION_DURABLE", sequence)
        return result

    def _check_sequence(self, sequence: int) -> None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0 or sequence >= self.source_count:
            raise FrontierViolation("sequence out of range")

    def evidence(self) -> dict[str, Any]:
        return {
            "durable_frontier": self.durable_frontier,
            "failed_sequence": self.failed_sequence,
            "prepared_sequences": sorted(self.prepared),
            "events": list(self.events),
        }
