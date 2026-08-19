"""v4 facade for the unchanged v3.1 arrival/ROB coordinator.

The facade stores already-certified future ``PreparedArtifact`` values and
launches one-version-ahead NodeResolve only while the live admission observer
proves that a frontier transport owns the first slot of the frozen ``K=2``
envelope.  If the future artifact or residual slot never becomes available,
the frontier simply completes without speculation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from paper_eval.membind_v1.evidence_fence import CompileInput
from paper_eval.membind_v31.admission import RequestKind
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v31.request_runtime import llm_request_scope
from paper_eval.membind_v4.live_adapter import (
    V4LiveNodeResolveBridge,
    build_v31_graphiti_v4_bridge,
)


class V4SpeculativeAdapterError(ValueError):
    """The production facade or residual-slot signal failed closed."""


def _fail(code: str) -> V4SpeculativeAdapterError:
    return V4SpeculativeAdapterError(code)


class V4ResidualSlotSignal:
    """Translate v3.1 content-safe admission snapshots into c01 readiness."""

    def __init__(self) -> None:
        self._ready = False
        self._changed = asyncio.Event()
        self._last: dict[str, object] | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def last_snapshot(self) -> dict[str, object] | None:
        return None if self._last is None else dict(self._last)

    def observe(self, snapshot: Mapping[str, object]) -> None:
        if not isinstance(snapshot, Mapping):
            raise _fail("admission_snapshot_invalid")
        selected = dict(snapshot)
        ready = (
            selected.get("configured_limit") == 2
            and selected.get("active_count") == 1
            and selected.get("active_frontier_count") == 1
            and selected.get("waiting_frontier_count") == 0
            and selected.get("frontier_bind_region_count") == 1
            and selected.get("frontier_transport_phase") == "FRONTIER_LLM_PERMIT_ACTIVE"
        )
        self._last = selected
        self._ready = ready
        self._changed.set()

    async def wait_ready_while(self, frontier_task: asyncio.Task[object]) -> bool:
        if not isinstance(frontier_task, asyncio.Task):
            raise _fail("frontier_task_invalid")
        while not frontier_task.done():
            if self._ready:
                return True
            self._changed.clear()
            changed = asyncio.create_task(self._changed.wait())
            done, _pending = await asyncio.wait(
                {frontier_task, changed},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if changed not in done:
                changed.cancel()
                await asyncio.gather(changed, return_exceptions=True)
            if frontier_task in done:
                return False
        return False


class V4SpeculativeGraphitiAdapter:
    """Expose v3.1 ``prepare/bind`` while adding only the v4 NodeResolve hook."""

    def __init__(
        self,
        *,
        factorized_adapter: object,
        residual_slot_signal: V4ResidualSlotSignal,
        stream_id: str,
        bridge: V4LiveNodeResolveBridge | None = None,
    ) -> None:
        if not callable(getattr(factorized_adapter, "prepare", None)):
            raise _fail("factorized_prepare_missing")
        if not callable(getattr(factorized_adapter, "bind", None)):
            raise _fail("factorized_bind_missing")
        if not isinstance(residual_slot_signal, V4ResidualSlotSignal):
            raise _fail("residual_slot_signal_invalid")
        if not isinstance(stream_id, str) or not stream_id:
            raise _fail("stream_id_invalid")
        self._native = factorized_adapter
        self._signal = residual_slot_signal
        self._stream_id = stream_id
        self._bridge = bridge or build_v31_graphiti_v4_bridge(factorized_adapter)
        self._prepared: dict[int, tuple[CompileInput, PreparedArtifact]] = {}
        self._prepared_events: dict[int, asyncio.Event] = {}

    @property
    def bridge(self) -> V4LiveNodeResolveBridge:
        return self._bridge

    def _prepared_event(self, sequence: int) -> asyncio.Event:
        event = self._prepared_events.get(sequence)
        if event is None:
            event = asyncio.Event()
            self._prepared_events[sequence] = event
        return event

    async def prepare(self, compile_input: CompileInput) -> PreparedArtifact:
        if not isinstance(compile_input, CompileInput):
            raise _fail("compile_input_invalid")
        produced = self._native.prepare(compile_input)
        if not hasattr(produced, "__await__"):
            raise _fail("factorized_prepare_must_be_async")
        artifact = await produced
        if not isinstance(artifact, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        try:
            artifact.verify()
        except Exception:
            raise _fail("prepared_artifact_invalid") from None
        sequence = artifact.source_sequence
        if sequence != compile_input.source.source_sequence:
            raise _fail("prepared_source_mismatch")
        if sequence in self._prepared:
            raise _fail("prepared_source_duplicate")
        self._prepared[sequence] = (compile_input, artifact)
        self._prepared_event(sequence).set()
        return artifact

    async def _launch_future_when_eligible(
        self,
        *,
        sequence: int,
        frontier_task: asyncio.Task[object],
    ) -> bool:
        future_sequence = sequence + 1
        prepared_event = self._prepared_event(future_sequence)
        while not frontier_task.done() and not prepared_event.is_set():
            waiting = asyncio.create_task(prepared_event.wait())
            done, _pending = await asyncio.wait(
                {frontier_task, waiting}, return_when=asyncio.FIRST_COMPLETED
            )
            if waiting not in done:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            if frontier_task in done:
                return False
        if frontier_task.done():
            return False
        if not await self._signal.wait_ready_while(frontier_task):
            return False
        future = self._prepared.get(future_sequence)
        if future is None or frontier_task.done():
            return False
        compile_input, artifact = future
        # ``bind`` runs under the coordinator's FRONTIER context.  Child tasks
        # inherit contextvars, so the speculative transport must be explicitly
        # reclassified before the bridge creates its background task.
        with llm_request_scope(
            kind=RequestKind.COMPILE,
            stream_id=self._stream_id,
            source_sequence=future_sequence,
        ):
            await self._bridge.launch_speculation(
                compile_input,
                artifact,
                state_version=sequence,
            )
        if not frontier_task.done():
            self._bridge.record_overlap(
                source_sequence=future_sequence,
                frontier_source_sequence=sequence,
            )
        return True

    async def bind(
        self,
        compile_input: CompileInput,
        artifact: PreparedArtifact,
        *,
        logical_time_ns: int,
    ) -> object:
        if not isinstance(compile_input, CompileInput) or not isinstance(artifact, PreparedArtifact):
            raise _fail("bind_input_invalid")
        sequence = artifact.source_sequence
        stored = self._prepared.get(sequence)
        if stored is None or stored[0] is not compile_input or stored[1] is not artifact:
            raise _fail("bind_prepared_identity_mismatch")
        frontier_task = asyncio.create_task(
            self._bridge.bind(
                compile_input,
                artifact,
                state_version=sequence,
                logical_time_ns=logical_time_ns,
            )
        )
        launcher = asyncio.create_task(
            self._launch_future_when_eligible(
                sequence=sequence,
                frontier_task=frontier_task,
            )
        )
        try:
            return await frontier_task
        finally:
            if not launcher.done():
                launcher.cancel()
            await asyncio.gather(launcher, return_exceptions=True)
            self._prepared.pop(sequence, None)

    async def close(self) -> None:
        await self._bridge.cancel()

    def telemetry(self) -> dict[str, object]:
        return self._bridge.telemetry()


__all__ = [
    "V4ResidualSlotSignal",
    "V4SpeculativeAdapterError",
    "V4SpeculativeGraphitiAdapter",
]
