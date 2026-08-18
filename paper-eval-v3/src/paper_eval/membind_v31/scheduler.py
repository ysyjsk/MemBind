"""Arrival-safe, bounded, source-ordered state for the MemBind v3.1 runtime.

This module deliberately owns no transport or persistence integration.  It is
the small deterministic state machine that a live coordinator must drive.
Public observations contain identities and states only; source payloads and
prepared artifacts are returned exclusively at their legal execution boundary.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class MemBindV31SchedulerError(ValueError):
    """An arrival, compile-window, or publication-frontier rule was violated."""


def _fail(code: str) -> MemBindV31SchedulerError:
    return MemBindV31SchedulerError(code)


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise _fail(code)
    return value


def _sequence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("source_sequence_invalid")
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _positive_int(value: object, code: str) -> int:
    result = _nonnegative_int(value, code)
    if result == 0:
        raise _fail(code)
    return result


def _error_class(error: BaseException) -> str:
    if not isinstance(error, BaseException):
        raise _fail("error_invalid")
    return f"{type(error).__module__}.{type(error).__qualname__}"


@dataclass(frozen=True, slots=True)
class SourceEnvelope:
    """One immutable source input; payload access is mediated by ``ArrivalGate``."""

    stream_id: str
    source_sequence: int
    arrival_time: float
    payload: Any

    def __post_init__(self) -> None:
        _identity(self.stream_id, "stream_id_invalid")
        _sequence(self.source_sequence)
        if (
            isinstance(self.arrival_time, bool)
            or not isinstance(self.arrival_time, (int, float))
            or not math.isfinite(float(self.arrival_time))
        ):
            raise _fail("arrival_time_invalid")


class ArrivalGate:
    """Release immutable source inputs only after their wall-clock arrival."""

    def __init__(self, sources: Iterable[SourceEnvelope]) -> None:
        if isinstance(sources, (str, bytes)):
            raise _fail("sources_invalid")
        by_key: dict[tuple[str, int], SourceEnvelope] = {}
        try:
            for source in sources:
                if not isinstance(source, SourceEnvelope):
                    raise _fail("source_invalid")
                key = (source.stream_id, source.source_sequence)
                if key in by_key:
                    raise _fail("source_duplicate")
                by_key[key] = source
        except TypeError:
            raise _fail("sources_invalid") from None
        self._sources = by_key
        self._released: set[tuple[str, int]] = set()
        self._events: list[dict[str, object]] = []

    @staticmethod
    def _now(now: object) -> float:
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(float(now)):
            raise _fail("now_invalid")
        return float(now)

    def _release(self, key: tuple[str, int]) -> SourceEnvelope:
        source = self._sources[key]
        if key not in self._released:
            self._released.add(key)
            self._events.append(
                {
                    "event_sequence": len(self._events),
                    "event_type": "source_arrived",
                    "stream_id": source.stream_id,
                    "source_sequence": source.source_sequence,
                    "arrival_time": float(source.arrival_time),
                }
            )
        return source

    def release_eligible(self, *, now: float) -> tuple[SourceEnvelope, ...]:
        """Release newly eligible sources in a stable arrival/stream/sequence order."""

        timestamp = self._now(now)
        eligible = [
            (key, source)
            for key, source in self._sources.items()
            if key not in self._released and float(source.arrival_time) <= timestamp
        ]
        eligible.sort(
            key=lambda item: (
                float(item[1].arrival_time),
                item[1].stream_id,
                item[1].source_sequence,
            )
        )
        return tuple(self._release(key) for key, _source in eligible)

    def claim(self, *, stream_id: str, source_sequence: int, now: float) -> SourceEnvelope:
        """Return a source payload only if its declared arrival has occurred."""

        key = (
            _identity(stream_id, "stream_id_invalid"),
            _sequence(source_sequence),
        )
        source = self._sources.get(key)
        if source is None:
            raise _fail("source_unknown")
        if float(source.arrival_time) > self._now(now):
            raise _fail("source_not_arrived")
        return self._release(key)

    def observation(self, *, now: float) -> dict[str, int]:
        """Return aggregate content-safe state without source payloads."""

        timestamp = self._now(now)
        eligible = sum(
            key not in self._released and float(source.arrival_time) <= timestamp
            for key, source in self._sources.items()
        )
        return {
            "eligible_count": eligible,
            "pending_count": len(self._sources) - len(self._released),
            "released_count": len(self._released),
        }

    @property
    def public_events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(event) for event in self._events)


@dataclass(slots=True)
class _ROBSlot:
    state: str = "ARRIVED"
    artifact: Any = None


class PreparedROB:
    """Bounded per-stream Prepared ROB with one global state-bound worker."""

    def __init__(self, *, compile_workers: int, lookahead: int) -> None:
        self._compile_workers = _positive_int(compile_workers, "compile_workers_invalid")
        self._lookahead = _nonnegative_int(lookahead, "lookahead_invalid")
        self._slots: dict[tuple[str, int], _ROBSlot] = {}
        self._frontiers: dict[str, int] = {}
        self._active_compiles: set[tuple[str, int]] = set()
        self._active_bind: tuple[str, int] | None = None
        self._terminal_streams: set[str] = set()
        self._observed_max_active_compiles = 0
        self._events: list[dict[str, object]] = []

    def _key(self, stream_id: str, source_sequence: int) -> tuple[str, int]:
        return (
            _identity(stream_id, "stream_id_invalid"),
            _sequence(source_sequence),
        )

    def _emit(
        self,
        event_type: str,
        key: tuple[str, int],
        *,
        error_class: str | None = None,
    ) -> None:
        event: dict[str, object] = {
            "event_sequence": len(self._events),
            "event_type": event_type,
            "stream_id": key[0],
            "source_sequence": key[1],
        }
        if error_class is not None:
            event["error_class"] = error_class
        self._events.append(event)

    def _assert_stream_live(self, stream_id: str) -> None:
        if stream_id in self._terminal_streams:
            raise _fail("stream_failed")

    def _terminate_stream(self, stream_id: str, *, except_key: tuple[str, int] | None = None) -> None:
        """Release outstanding same-stream workers in stable order after a terminal error."""

        active_compile_keys = sorted(
            key
            for key in self._active_compiles
            if key[0] == stream_id and key != except_key
        )
        for key in active_compile_keys:
            slot = self._slots[key]
            slot.state = "CANCELLED"
            self._active_compiles.remove(key)
            self._emit("compile_cancelled_after_stream_failure", key)
        if self._active_bind is not None and self._active_bind[0] == stream_id and self._active_bind != except_key:
            bind_key = self._active_bind
            self._slots[bind_key].state = "CANCELLED"
            self._active_bind = None
            self._emit("bind_cancelled_after_stream_failure", bind_key)
        self._terminal_streams.add(stream_id)

    def _slot(self, key: tuple[str, int]) -> _ROBSlot:
        slot = self._slots.get(key)
        if slot is None:
            raise _fail("source_not_arrived")
        return slot

    def record_arrival(self, stream_id: str, source_sequence: int) -> None:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        if key in self._slots:
            raise _fail("source_duplicate")
        self._slots[key] = _ROBSlot()
        self._frontiers.setdefault(key[0], 0)
        self._emit("arrival", key)

    def frontier(self, stream_id: str) -> int:
        selected = _identity(stream_id, "stream_id_invalid")
        if selected not in self._frontiers:
            raise _fail("stream_unknown")
        return self._frontiers[selected]

    def start_compile(self, stream_id: str, source_sequence: int) -> None:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        slot = self._slot(key)
        if slot.state != "ARRIVED":
            raise _fail("compile_state_invalid")
        if key[1] > self.frontier(key[0]) + self._lookahead:
            raise _fail("outside_lookahead")
        if len(self._active_compiles) >= self._compile_workers:
            raise _fail("compile_worker_limit")
        slot.state = "COMPILING"
        self._active_compiles.add(key)
        self._observed_max_active_compiles = max(
            self._observed_max_active_compiles, len(self._active_compiles)
        )
        self._emit("compile_start", key)

    def complete_compile(self, stream_id: str, source_sequence: int, *, artifact: Any) -> None:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        slot = self._slot(key)
        if slot.state != "COMPILING" or key not in self._active_compiles:
            raise _fail("compile_state_invalid")
        slot.artifact = artifact
        slot.state = "PREPARED"
        self._active_compiles.remove(key)
        self._emit("compile_complete", key)

    def fail_compile(self, stream_id: str, source_sequence: int, error: BaseException) -> None:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        slot = self._slot(key)
        if slot.state != "COMPILING" or key not in self._active_compiles:
            raise _fail("compile_state_invalid")
        slot.state = "FAILED"
        self._active_compiles.remove(key)
        self._terminate_stream(key[0], except_key=key)
        self._emit("compile_failed", key, error_class=_error_class(error))

    def cancel_compile(self, stream_id: str, source_sequence: int) -> None:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        slot = self._slot(key)
        if slot.state not in {"ARRIVED", "COMPILING"}:
            raise _fail("compile_state_invalid")
        self._active_compiles.discard(key)
        slot.state = "CANCELLED"
        self._terminate_stream(key[0], except_key=key)
        self._emit("compile_cancelled", key)

    def start_bind(self, stream_id: str, source_sequence: int) -> Any:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        if key[1] != self.frontier(key[0]):
            raise _fail("bind_not_at_frontier")
        slot = self._slot(key)
        if slot.state != "PREPARED":
            raise _fail("bind_state_invalid")
        if self._active_bind is not None:
            raise _fail("bind_worker_busy")
        self._active_bind = key
        slot.state = "BINDING"
        self._emit("bind_start", key)
        return slot.artifact

    def publish(self, stream_id: str, source_sequence: int) -> None:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        if key[1] != self.frontier(key[0]):
            raise _fail("publication_not_at_frontier")
        slot = self._slot(key)
        if slot.state != "BINDING" or self._active_bind != key:
            raise _fail("publication_state_invalid")
        slot.state = "PUBLISHED"
        slot.artifact = None
        self._active_bind = None
        self._frontiers[key[0]] += 1
        self._emit("publication", key)

    def fail_bind(self, stream_id: str, source_sequence: int, error: BaseException) -> None:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        slot = self._slot(key)
        if slot.state != "BINDING" or self._active_bind != key:
            raise _fail("bind_state_invalid")
        slot.state = "FAILED"
        self._active_bind = None
        self._terminate_stream(key[0], except_key=key)
        self._emit("bind_failed", key, error_class=_error_class(error))

    def cancel_bind(self, stream_id: str, source_sequence: int) -> None:
        key = self._key(stream_id, source_sequence)
        self._assert_stream_live(key[0])
        slot = self._slot(key)
        if slot.state != "BINDING" or self._active_bind != key:
            raise _fail("bind_state_invalid")
        slot.state = "CANCELLED"
        self._active_bind = None
        self._terminate_stream(key[0], except_key=key)
        self._emit("bind_cancelled", key)

    def observation(self) -> dict[str, object]:
        """Return a deterministic content-safe runtime snapshot."""

        state_counts: dict[str, int] = {}
        for slot in self._slots.values():
            state_counts[slot.state] = state_counts.get(slot.state, 0) + 1
        return {
            "active_bind": None
            if self._active_bind is None
            else {
                "stream_id": self._active_bind[0],
                "source_sequence": self._active_bind[1],
            },
            "active_compile_count": len(self._active_compiles),
            "compile_workers": self._compile_workers,
            "frontiers": dict(sorted(self._frontiers.items())),
            "lookahead": self._lookahead,
            "observed_max_active_compiles": self._observed_max_active_compiles,
            "prepared_count": state_counts.get("PREPARED", 0),
            "state_counts": dict(sorted(state_counts.items())),
            "terminal_streams": sorted(self._terminal_streams),
        }

    @property
    def public_events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(event) for event in self._events)


__all__ = [
    "ArrivalGate",
    "MemBindV31SchedulerError",
    "PreparedROB",
    "SourceEnvelope",
]
