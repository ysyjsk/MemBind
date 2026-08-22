"""Source-attributed PREPARE/NATIVE tracing without provider payload leakage."""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True, slots=True)
class TraceContext:
    run_id: str
    namespace: str
    episode_id: str
    source_sequence: int


@dataclass(frozen=True, slots=True)
class TraceSpan:
    phase: str
    operation: str
    source_sequence: int
    start_ns: int
    end_ns: int
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "operation": self.operation,
            "source_sequence": self.source_sequence,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "status": self.status,
        }


class SourceTraceRecorder:
    def __init__(self, *, clock: Any = time.monotonic_ns) -> None:
        self.clock = clock
        self._context: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar("membind_v5_trace_context", default=None)
        self._spans: list[TraceSpan] = []
        self._envelopes: dict[int, dict[str, Any]] = {}

    def current(self) -> TraceContext | None:
        return self._context.get()

    @contextmanager
    def episode_scope(self, namespace: str, episode_id: str, source_sequence: int, *, run_id: str = "v5") -> Iterator[None]:
        if self.current() is not None:
            raise RuntimeError("nested source trace scope")
        context = TraceContext(str(run_id), str(namespace), str(episode_id), int(source_sequence))
        token = self._context.set(context)
        try:
            yield
        finally:
            self._context.reset(token)

    @contextmanager
    def span(self, phase: str, operation: str) -> Iterator[None]:
        context = self.current()
        if context is None:
            raise RuntimeError("trace span outside episode_scope")
        start = int(self.clock())
        status = "ok"
        try:
            yield
        except BaseException:
            status = "error"
            raise
        finally:
            end = int(self.clock())
            self._spans.append(TraceSpan(str(phase), str(operation), context.source_sequence, start, end, status))

    def materialize(self, *, source_sequence: int) -> dict[str, Any]:
        if source_sequence in self._envelopes:
            raise RuntimeError("duplicate source trace envelope")
        spans = [span for span in self._spans if span.source_sequence == int(source_sequence)]
        if not spans:
            raise RuntimeError("source has no trace spans")
        phases = {span.phase for span in spans}
        if not {"PREPARE", "NATIVE"} <= phases:
            raise RuntimeError("source trace must cover PREPARE and NATIVE")
        envelope = {
            "schema_version": "membind.v5.source-trace-envelope.v1",
            "source_sequence": int(source_sequence),
            "spans": [span.to_dict() for span in spans],
        }
        self._envelopes[int(source_sequence)] = envelope
        return envelope

    @property
    def spans(self) -> tuple[TraceSpan, ...]:
        return tuple(self._spans)

    @property
    def envelopes(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._envelopes[key] for key in sorted(self._envelopes))


# Name aligned with the existing validation recorder while keeping the V5 core
# provider-neutral.
TraceRecorder = SourceTraceRecorder
