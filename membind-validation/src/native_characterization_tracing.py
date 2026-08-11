"""Characterization-only trace records, durability, and time accounting."""

from __future__ import annotations

import asyncio
import contextvars
import fcntl
import json
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


_FORBIDDEN_FIELDS = {
    "api_key",
    "authorization",
    "content",
    "cypher",
    "exception_message",
    "messages",
    "parameters",
    "params",
    "query",
    "raw_prompt",
    "raw_response",
    "system_prompt",
    "traceback",
    "user_prompt",
}


def _validate_field_name(name: str) -> None:
    if name.casefold() in _FORBIDDEN_FIELDS:
        raise ValueError(f"content-bearing field is forbidden: {name}")


def _validate_scalar(value: Any) -> None:
    if value is None or isinstance(value, str | int | bool):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise TypeError("trace metadata values must be finite JSON scalars")


def _validate_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_field_name(str(key))
            _validate_payload(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _validate_payload(item)
        return
    _validate_scalar(value)


@dataclass(frozen=True)
class TraceContext:
    run_id: str
    episode_id: str
    source_sequence: int


@dataclass
class SpanRecord:
    sequence: int
    span_id: str
    parent_span_id: str | None
    run_id: str
    episode_id: str
    source_sequence: int
    phase: str
    operation_class: str | None
    start_ns: int
    end_ns: int
    status: str
    error_code: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "source_sequence": self.source_sequence,
            "phase": self.phase,
            "operation_class": self.operation_class,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "duration_ns": self.end_ns - self.start_ns,
            "status": self.status,
            "error_code": self.error_code,
            "metadata": dict(self.metadata),
        }


class _SpanHandle:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata

    def add_metadata(self, name: str, value: Any) -> None:
        _validate_field_name(name)
        _validate_scalar(value)
        self._metadata[name] = value


class _NullSpanHandle:
    def add_metadata(self, name: str, value: Any) -> None:
        return None


class TraceRecorder:
    """In-memory recorder whose ContextVars isolate concurrent episodes and spans."""

    def __init__(self, *, clock: Callable[[], int] | None = None) -> None:
        self._clock = clock or time.monotonic_ns
        self._episode: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
            f"native_characterization_episode_{id(self)}", default=None
        )
        self._span: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"native_characterization_span_{id(self)}", default=None
        )
        self._records: list[SpanRecord] = []
        self._sequence = 0
        self._identifier = 0
        self._lock = threading.Lock()

    @property
    def records(self) -> list[SpanRecord]:
        with self._lock:
            return list(self._records)

    def current_episode(self) -> TraceContext | None:
        return self._episode.get()

    def current_span_id(self) -> str | None:
        return self._span.get()

    def next_identifier(self, prefix: str) -> str:
        with self._lock:
            identifier = self._identifier
            self._identifier += 1
        return f"{prefix}-{identifier:08d}"

    def _next_span_identity(self) -> tuple[int, str]:
        with self._lock:
            sequence = self._sequence
            self._sequence += 1
        return sequence, f"span-{sequence:08d}"

    @contextmanager
    def episode_scope(
        self, run_id: str, episode_id: str, source_sequence: int
    ) -> Iterator[None]:
        context = TraceContext(str(run_id), str(episode_id), int(source_sequence))
        episode_token = self._episode.set(context)
        span_token = self._span.set(None)
        try:
            yield
        finally:
            self._span.reset(span_token)
            self._episode.reset(episode_token)

    @contextmanager
    def span(
        self,
        phase: str,
        *,
        operation_class: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Iterator[_SpanHandle | _NullSpanHandle]:
        context = self.current_episode()
        if context is None:
            yield _NullSpanHandle()
            return

        safe_metadata = dict(metadata or {})
        _validate_payload(safe_metadata)
        sequence, span_id = self._next_span_identity()
        parent_span_id = self.current_span_id()
        start_ns = int(self._clock())
        token = self._span.set(span_id)
        status = "ok"
        error_code: str | None = None
        try:
            yield _SpanHandle(safe_metadata)
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            error_code = f"{type(exc).__module__}.{type(exc).__qualname__}"
            raise
        finally:
            end_ns = int(self._clock())
            self._span.reset(token)
            record = SpanRecord(
                sequence=sequence,
                span_id=span_id,
                parent_span_id=parent_span_id,
                run_id=context.run_id,
                episode_id=context.episode_id,
                source_sequence=context.source_sequence,
                phase=str(phase),
                operation_class=str(operation_class) if operation_class is not None else None,
                start_ns=start_ns,
                end_ns=end_ns,
                status=status,
                error_code=error_code,
                metadata=safe_metadata,
            )
            with self._lock:
                self._records.append(record)

    def episode_envelope(
        self, run_id: str, episode_id: str, source_sequence: int
    ) -> dict[str, Any]:
        matching = [
            record
            for record in self.records
            if record.run_id == str(run_id)
            and record.episode_id == str(episode_id)
            and record.source_sequence == int(source_sequence)
        ]
        matching.sort(key=lambda record: record.sequence)
        return {
            "schema_version": "membind.native_characterization.trace.v1",
            "run_id": str(run_id),
            "episode_id": str(episode_id),
            "source_sequence": int(source_sequence),
            "spans": [record.to_dict() for record in matching],
        }


class DurableJsonlEnvelopeWriter:
    """Append one sanitized episode envelope and fsync before returning."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, envelope: Mapping[str, Any]) -> None:
        _validate_payload(envelope)
        encoded = (
            json.dumps(
                envelope,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
            + b"\n"
        )
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        with self._lock:
            descriptor = os.open(self.path, flags, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("trace append made no progress")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


def _validated_interval(interval: tuple[int, int]) -> tuple[int, int]:
    start, end = int(interval[0]), int(interval[1])
    if end < start:
        raise ValueError(f"interval has end before start: {interval}")
    return start, end


def _merged_intervals(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    nonempty: list[tuple[int, int]] = []
    for item in intervals:
        validated = _validated_interval(item)
        if validated[0] != validated[1]:
            nonempty.append(validated)
    ordered = sorted(nonempty, key=lambda item: (item[0], item[1]))
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
    return merged


def interval_union_ns(intervals: Iterable[tuple[int, int]]) -> int:
    return sum(end - start for start, end in _merged_intervals(intervals))


def exclusive_duration_ns(
    parent: tuple[int, int], children: Iterable[tuple[int, int]]
) -> int:
    parent_start, parent_end = _validated_interval(parent)
    clipped: list[tuple[int, int]] = []
    for child in children:
        child_start, child_end = _validated_interval(child)
        start = max(parent_start, child_start)
        end = min(parent_end, child_end)
        if start < end:
            clipped.append((start, end))
    return (parent_end - parent_start) - interval_union_ns(clipped)


def critical_path_ns(root_span_id: str, records: Sequence[SpanRecord]) -> int:
    """Compute the frozen fork/join trace-DAG critical-path approximation."""

    by_id = {record.span_id: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("span ids must be unique")
    if root_span_id not in by_id:
        raise KeyError(root_span_id)
    children: dict[str, list[SpanRecord]] = {}
    for record in records:
        if record.parent_span_id is not None:
            children.setdefault(record.parent_span_id, []).append(record)

    visiting: set[str] = set()

    def visit(span_id: str) -> int:
        if span_id in visiting:
            raise ValueError("span parent graph contains a cycle")
        visiting.add(span_id)
        record = by_id[span_id]
        direct = children.get(span_id, [])
        exclusive = exclusive_duration_ns(
            (record.start_ns, record.end_ns),
            [(child.start_ns, child.end_ns) for child in direct],
        )
        clipped = sorted(
            [
                (
                    max(record.start_ns, child.start_ns),
                    min(record.end_ns, child.end_ns),
                    child,
                )
                for child in direct
                if max(record.start_ns, child.start_ns)
                < min(record.end_ns, child.end_ns)
            ],
            key=lambda item: (item[0], item[1], item[2].sequence),
        )
        clusters: list[list[SpanRecord]] = []
        cluster_end: int | None = None
        for start, end, child in clipped:
            if cluster_end is None or start >= cluster_end:
                clusters.append([child])
                cluster_end = end
            else:
                clusters[-1].append(child)
                cluster_end = max(cluster_end, end)
        result = exclusive + sum(max(visit(child.span_id) for child in cluster) for cluster in clusters)
        visiting.remove(span_id)
        return result

    return visit(root_span_id)
