"""Minimal v4 coordinator composing resource admission and validated runtime.

This is an adapter seam, not a second Graphiti implementation.  It accepts a
small adapter with ``materialize``, ``execute``, ``interpret`` and ``commit``
callbacks and preserves ordered publication.  Live integrations can replace
the callbacks with the frozen v3.1 adapter methods.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from .admission import AdmissionRequest, RequestKind, ResourceGatedAdmission
from .resource_profile import Criticality, RequestProfile, ResourceClass
from .runtime import PreparedNodeResolve, ValidatedSpeculationRuntime
from .telemetry import V4Telemetry


class V4CoordinatorError(ValueError):
    """A coordinator callback or ordered-publication invariant failed."""


def _fail(code: str) -> V4CoordinatorError:
    return V4CoordinatorError(code)


async def _await(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


def _source_sequence(source: object, fallback: int) -> int:
    if isinstance(source, int) and not isinstance(source, bool):
        return source
    value = getattr(source, "source_sequence", fallback)
    if isinstance(value, bool) or not isinstance(value, int) or value != fallback:
        raise _fail("source_sequence_invalid")
    return value


def _profile(request_id: str, source_sequence: int, *, frontier: bool, state: int) -> RequestProfile:
    return RequestProfile(
        request_id=request_id,
        prompt_name="node_resolve",
        prompt_tokens_estimate=0,
        expected_output_tokens=0,
        resource_class=ResourceClass.SHORT,
        criticality=Criticality.FRONTIER if frontier else Criticality.BACKGROUND,
        source_sequence=source_sequence,
        state_version=state,
        exact_prefix_tokens=0,
        execution_mode="NO_LLM" if False else "LLM",
    )


async def _materialize(adapter: object, source: object, *, state_version: int) -> PreparedNodeResolve:
    callback = getattr(adapter, "materialize", None)
    if not callable(callback):
        raise _fail("adapter_materialize_missing")
    prepared = await _await(callback(source, state_version=state_version))
    if isinstance(prepared, PreparedNodeResolve):
        return prepared
    # Accept the v4 adapter's ``PreparedSemanticCall`` without importing it,
    # keeping this coordinator independent from the Graphiti-facing adapter.
    call = getattr(prepared, "call", None)
    if call is not None and hasattr(prepared, "request"):
        return PreparedNodeResolve(call=call, request=getattr(prepared, "request"))
    raise _fail("prepared_request_invalid")


async def _execute(adapter: object, request: object) -> object:
    callback = getattr(adapter, "execute", None)
    if not callable(callback):
        raise _fail("adapter_execute_missing")
    return await _await(callback(request))


async def _interpret(adapter: object, response: object, call: object) -> object:
    callback = getattr(adapter, "interpret", None)
    if not callable(callback):
        raise _fail("adapter_interpret_missing")
    return await _await(callback(response, call))


async def _commit(adapter: object, value: object) -> object:
    callback = getattr(adapter, "commit", None)
    if not callable(callback):
        raise _fail("adapter_commit_missing")
    return await _await(callback(value))


async def run_membind_v4_stream(
    *,
    stream_id: str,
    sources: Sequence[object],
    adapter: object,
    observer: Callable[[dict[str, object]], object] | None = None,
    telemetry: V4Telemetry | None = None,
    phase_complementary: bool = False,
) -> dict[str, object]:
    """Run a deterministic synthetic stream through the v4 state machine.

    The function intentionally uses one-version-ahead speculation only.  The
    next source is materialized against the currently published version,
    while its predecessor is the sole frontier.  Every publication is ordered
    and exactly one ``commit`` callback is made per source.
    """

    if not isinstance(stream_id, str) or not stream_id:
        raise _fail("stream_id_invalid")
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence) or not sources:
        raise _fail("sources_invalid")
    if observer is not None and not callable(observer):
        raise _fail("observer_invalid")
    if telemetry is not None and not isinstance(telemetry, V4Telemetry):
        raise _fail("telemetry_invalid")
    sequences = tuple(_source_sequence(source, index) for index, source in enumerate(sources))
    if sequences != tuple(range(len(sources))):
        raise _fail("source_sequence_invalid")
    gate = ResourceGatedAdmission(global_k=2, phase_complementary=phase_complementary)
    owned_runtimes: dict[int, ValidatedSpeculationRuntime] = {}
    prepared_speculations: dict[int, PreparedNodeResolve] = {}
    speculation_tasks: dict[int, asyncio.Task[None]] = {}
    publications: list[int] = []
    direct_violations: list[dict[str, object]] = []
    local_telemetry = telemetry or V4Telemetry()

    def emit(event_type: str, **fields: object) -> None:
        event = {"stream_id": stream_id, "event_type": event_type, **fields}
        local_telemetry.record(event_type, **{k: v for k, v in event.items() if k != "event_type"})
        if observer is not None:
            result = observer(event)
            if inspect.isawaitable(result):
                raise _fail("async_observer_unsupported")

    async def execute(request: object) -> object:
        return await _execute(adapter, request)

    async def interpret(response: object, call: object) -> object:
        return await _interpret(adapter, response, call)

    async def commit(value: object) -> object:
        return await _commit(adapter, value)

    try:
        for sequence, source in enumerate(sources):
            # A future source can be speculative only one version ahead.
            next_sequence = sequence + 1
            if next_sequence < len(sources) and next_sequence not in owned_runtimes:
                stale = await _materialize(
                    adapter,
                    sources[next_sequence],
                    state_version=sequence,
                )
                request = AdmissionRequest(
                    request_id=f"spec-{next_sequence}",
                    kind=RequestKind.SPECULATIVE,
                    stream_id=stream_id,
                    source_sequence=next_sequence,
                    speculation_distance=1,
                    profile=_profile(
                        f"spec-{next_sequence}",
                        next_sequence,
                        frontier=False,
                        state=sequence,
                    ),
                )
                gate.submit(request)
                prepared_speculations[next_sequence] = stale
                runtime = ValidatedSpeculationRuntime(
                    execute=execute,
                    interpret=interpret,
                    commit=commit,
                    observer=lambda event, seq=next_sequence: emit(
                        (
                            "runtime_semantic_hit"
                            if event.get("event_type") == "semantic_hit"
                            else "runtime_semantic_miss"
                            if event.get("event_type") == "semantic_miss"
                            else str(event.get("event_type", "runtime"))
                        ),
                        source_sequence=seq,
                    ),
                )
                owned_runtimes[next_sequence] = runtime

            frontier_request = AdmissionRequest(
                request_id=f"frontier-{sequence}",
                kind=RequestKind.FRONTIER,
                stream_id=stream_id,
                source_sequence=sequence,
                speculation_distance=0,
                profile=_profile(
                    f"frontier-{sequence}",
                    sequence,
                    frontier=True,
                    state=sequence,
                ),
            )
            gate.submit(frontier_request)
            admitted = list(gate.admit_available())
            # If the speculative candidate was submitted before its frontier,
            # a second scheduler tick admits it into the residual slot.
            if not any(item.request_id == frontier_request.request_id for item in admitted):
                admitted.extend(gate.admit_available())
            if not any(item.request_id == frontier_request.request_id for item in admitted):
                raise _fail("frontier_not_admitted")
            if next_sequence in owned_runtimes:
                if not any(item.request_id == f"spec-{next_sequence}" for item in admitted):
                    admitted.extend(gate.admit_available())
                if any(item.request_id == f"spec-{next_sequence}" for item in admitted):
                    task = asyncio.create_task(
                        owned_runtimes[next_sequence].speculate(
                            prepared_speculations[next_sequence]
                        )
                    )
                    speculation_tasks[next_sequence] = task
                    emit("speculation_launched", source_sequence=next_sequence)

            exact = await _materialize(adapter, source, state_version=sequence)
            if sequence == 0:
                if next_sequence in speculation_tasks and not speculation_tasks[next_sequence].done():
                    emit("speculation_overlap", source_sequence=next_sequence)
                response = await execute(exact.request)
                interpreted = await interpret(response, exact.call)
                await commit(interpreted)
                emit("publication", source_sequence=sequence)
            else:
                runtime = owned_runtimes[sequence]
                task = speculation_tasks.get(sequence)
                if task is not None:
                    await task
                outcome = await runtime.validate_and_commit(exact)
                emit(
                    "semantic_hit" if outcome.status == "HIT" else "semantic_miss",
                    source_sequence=sequence,
                )
                gate.finish(f"spec-{sequence}")
                # Validation releases the residual permit.  If the next
                # candidate was prepared while this frontier was active, use
                # that permit before publishing the frontier so the next
                # speculation remains one version ahead without ever taking
                # the frontier slot.
                if next_sequence in owned_runtimes:
                    next_runtime = owned_runtimes[next_sequence]
                    if next_runtime.state == "NEW":
                        admitted_next = gate.admit_available()
                        if any(
                            item.request_id == f"spec-{next_sequence}"
                            for item in admitted_next
                        ):
                            task = asyncio.create_task(
                                next_runtime.speculate(
                                    prepared_speculations[next_sequence]
                                )
                            )
                            speculation_tasks[next_sequence] = task
                            emit("speculation_launched", source_sequence=next_sequence)
                emit("publication", source_sequence=sequence)
            gate.finish(f"frontier-{sequence}")
            publications.append(sequence)
    except asyncio.CancelledError:
        for task in speculation_tasks.values():
            if not task.done():
                task.cancel()
        if speculation_tasks:
            await asyncio.gather(*speculation_tasks.values(), return_exceptions=True)
        for runtime in owned_runtimes.values():
            runtime.cancel()
        for request_id in tuple(gate.observation()["active_request_ids"]):
            try:
                gate.cancel(str(request_id))
            except ValueError:
                pass
        raise
    except BaseException as error:
        for task in speculation_tasks.values():
            if not task.done():
                task.cancel()
        if speculation_tasks:
            await asyncio.gather(*speculation_tasks.values(), return_exceptions=True)
        for request_id in tuple(gate.observation()["active_request_ids"]):
            try:
                gate.cancel(str(request_id))
            except ValueError:
                pass
        raise error if isinstance(error, V4CoordinatorError) else _fail("stream_failed") from error

    if publications != list(range(len(sources))):
        direct_violations.append({"violation": "publication_order"})
    return {
        "schema_version": "membind.paper-eval-v4.stream-result.v1",
        "status": "PASS" if not direct_violations else "FAIL",
        "stream_id": stream_id,
        "source_count": len(sources),
        "publication_source_sequences": publications,
        "direct_violation_count": len(direct_violations),
        "direct_violations": direct_violations,
        "admission_observation": gate.observation(),
        "telemetry": local_telemetry.summary(),
    }


class V4Coordinator:
    """Reusable wrapper around :func:`run_membind_v4_stream`."""

    def __init__(
        self,
        *,
        stream_id: str,
        adapter: object,
        observer: Callable[[dict[str, object]], object] | None = None,
        telemetry: V4Telemetry | None = None,
        phase_complementary: bool = False,
    ) -> None:
        self.stream_id = stream_id
        self.adapter = adapter
        self.observer = observer
        self.telemetry = telemetry
        self.phase_complementary = phase_complementary

    async def run(self, sources: Sequence[object]) -> dict[str, object]:
        return await run_membind_v4_stream(
            stream_id=self.stream_id,
            sources=sources,
            adapter=self.adapter,
            observer=self.observer,
            telemetry=self.telemetry,
            phase_complementary=self.phase_complementary,
        )


run_v4_stream = run_membind_v4_stream


__all__ = ["V4Coordinator", "V4CoordinatorError", "run_membind_v4_stream", "run_v4_stream"]
