"""Provider-free/live-composable V5 block runner with shared timer semantics."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from .campaign import V5_METHOD, validate_block_timer_and_traces
from .runtime.core.admission import CapacityAuthority
from .runtime.core.executor import FrontierExecutor
from .runtime.core.trace import SourceTraceRecorder


class V5LiveBlockError(RuntimeError):
    pass


def _write_new(path: Path, body: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise V5LiveBlockError(f"artifact already exists: {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _write_new_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = "".join(json.dumps(dict(row), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise V5LiveBlockError(f"artifact already exists: {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


@dataclass(slots=True)
class V5LiveBlock:
    root: Path
    namespace: str
    source_count: int
    capacity: CapacityAuthority
    clock: Callable[[], int] = time.monotonic_ns

    async def run(
        self,
        prepare: Callable[[int], Awaitable[Any]],
        publish: Callable[[int, Any], Awaitable[Any]],
        *,
        canonical_graph: Mapping[str, Any] | None = None,
        validate: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.root = Path(self.root)
        if self.root.exists() and any(self.root.iterdir()):
            raise V5LiveBlockError("attempt root must be new")
        recorder = SourceTraceRecorder(clock=self.clock)
        executor = FrontierExecutor(self.source_count, self.capacity, clock=self.clock)

        async def traced_prepare(sequence: int) -> Any:
            with recorder.episode_scope(self.namespace, f"episode-{sequence}", sequence):
                with recorder.span("PREPARE", "certified_oracle_capture"):
                    return await prepare(sequence)

        async def traced_publish(sequence: int, value: Any) -> Any:
            with recorder.episode_scope(self.namespace, f"episode-{sequence}", sequence):
                with recorder.span("NATIVE", "Graphiti.add_episode"):
                    return await publish(sequence, value)

        result = await executor.run(traced_prepare, traced_publish)
        envelopes = [recorder.materialize(source_sequence=sequence) for sequence in range(self.source_count)]
        final_publication_ns = max((int(event["monotonic_ns"]) for event in result.events if event["event"] == "PUBLICATION_DURABLE"), default=result.timer_stop_ns)
        gate = validate_block_timer_and_traces(
            timer_start_ns=result.timer_start_ns,
            timer_stop_ns=result.timer_stop_ns,
            final_publication_ns=final_publication_ns,
            source_trace_envelopes=envelopes,
            episode_count=self.source_count,
        )
        validation = await validate({"canonical_graph": canonical_graph or {}, "method": V5_METHOD}) if validate else {"status": "PASS"}
        body = {
            "schema_version": "membind.v5.block-result.v1",
            "method": V5_METHOD,
            "namespace": self.namespace,
            "source_count": self.source_count,
            "capacity_authority": self.capacity.to_dict(),
            "frontier": result.events,
            "timer_start_ns": result.timer_start_ns,
            "timer_stop_ns": result.timer_stop_ns,
            "t0_ns": result.timer_start_ns,
            "t_durable_complete_ns": result.timer_stop_ns,
            "final_publication_ns": final_publication_ns,
            "build_makespan_ns": result.build_makespan_ns,
            "trace_envelope_count": len(envelopes),
            "trace_envelopes": envelopes,
            "canonical_graph": dict(canonical_graph or {}),
            "validation": dict(validation),
            "gate": gate,
        }
        self.root.mkdir(parents=True, exist_ok=False)
        _write_new(self.root / "frontier.json", {"events": result.events})
        _write_new(self.root / "lifecycle.json", {key: body[key] for key in ("timer_start_ns", "timer_stop_ns", "t0_ns", "t_durable_complete_ns", "final_publication_ns", "build_makespan_ns")})
        _write_new_jsonl(self.root / "native_trace.jsonl", envelopes)
        _write_new(self.root / "block_metrics.json", body)
        _write_new(self.root / "seal.json", {"schema_version": "membind.v5.block-seal.v1", "status": "SEALED", "method": V5_METHOD, "build_makespan_ns": result.build_makespan_ns})
        return body
