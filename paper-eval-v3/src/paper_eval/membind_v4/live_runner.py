"""Bounded prepared-artifact runner for the v4 live NodeResolve bridge.

This runner is intentionally downstream of the frozen v3.1 compile boundary:
callers hand it verified ``(compile_input, PreparedArtifact)`` pairs, and the
runner only coordinates the v4 NodeResolve bridge.  It does not construct a
Graphiti client, replace ``run_membind_v31_stream``, or alter v3.1 defaults.
That keeps the integration point explicit while the production Graphiti
factorisation is being qualified.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.live_adapter import V4LiveNodeResolveBridge, V4LiveNodeResolveError


class V4LiveRunnerError(ValueError):
    """A prepared-source or ordered-publication boundary failed closed."""


def _fail(code: str) -> V4LiveRunnerError:
    return V4LiveRunnerError(code)


@dataclass(frozen=True, slots=True)
class V4PreparedSource:
    """One source after the unchanged v3.1 ``prepare`` stage."""

    compile_input: object
    prepared: PreparedArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        try:
            self.prepared.verify()
        except Exception:
            raise _fail("prepared_artifact_invalid") from None


def _sources(value: object) -> tuple[V4PreparedSource, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise _fail("sources_invalid")
    selected: list[V4PreparedSource] = []
    for item in value:
        source = item if isinstance(item, V4PreparedSource) else None
        if source is None and isinstance(item, tuple) and len(item) == 2:
            source = V4PreparedSource(item[0], item[1])
        if source is None:
            raise _fail("source_item_invalid")
        if source.prepared.source_sequence != len(selected):
            raise _fail("source_sequence_invalid")
        selected.append(source)
    return tuple(selected)


async def run_v4_live_prepared_stream(
    *,
    stream_id: str,
    sources: Sequence[V4PreparedSource | tuple[object, PreparedArtifact]],
    bridge: V4LiveNodeResolveBridge,
    logical_time_ns: int | Callable[[int], int] = 0,
) -> dict[str, object]:
    """Run a prepared v4 stream with one-version-ahead overlap.

    The current source is always continued at its exact state version.  The
    next source is materialised and launched against the currently published
    version before that continuation starts, so a provider request can overlap
    the frontier path.  ``bridge.cancel`` is awaited on every exit to prevent
    leaked speculative tasks.
    """

    if not isinstance(stream_id, str) or not stream_id:
        raise _fail("stream_id_invalid")
    if not isinstance(bridge, V4LiveNodeResolveBridge):
        raise _fail("bridge_invalid")
    selected = _sources(sources)
    if isinstance(logical_time_ns, bool) or not (
        isinstance(logical_time_ns, int) or callable(logical_time_ns)
    ):
        raise _fail("logical_time_invalid")

    publications: list[int] = []
    try:
        for sequence, source in enumerate(selected):
            next_sequence = sequence + 1
            if next_sequence < len(selected):
                future = selected[next_sequence]
                await bridge.launch_speculation(
                    future.compile_input,
                    future.prepared,
                    state_version=sequence,
                )
            logical = logical_time_ns(sequence) if callable(logical_time_ns) else logical_time_ns
            if isinstance(logical, bool) or not isinstance(logical, int) or logical < 0:
                raise _fail("logical_time_invalid")
            await bridge.bind(
                source.compile_input,
                source.prepared,
                state_version=sequence,
                logical_time_ns=logical,
            )
            publications.append(sequence)
    except asyncio.CancelledError:
        await bridge.cancel()
        raise
    except BaseException as error:
        await bridge.cancel()
        if isinstance(error, V4LiveRunnerError):
            raise
        if isinstance(error, V4LiveNodeResolveError):
            raise _fail(str(error)) from None
        raise _fail("live_stream_failed") from error
    finally:
        await bridge.cancel()

    if publications != list(range(len(selected))):
        raise _fail("publication_order_invalid")
    telemetry = bridge.telemetry()
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v4.live-stream-result.v1",
        "status": "PASS",
        "stream_id": stream_id,
        "source_count": len(selected),
        "publication_source_sequences": publications,
        "direct_violation_count": 0,
        "telemetry": telemetry,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


__all__ = ["V4LiveRunnerError", "V4PreparedSource", "run_v4_live_prepared_stream"]
