"""Exact Bind capture and read-only stale Probe observation for MemBind-VDC."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.graphiti_factorization import CapturedGraphitiRequest
from paper_eval.membind_v4.node_resolve_adapter import (
    ExactNodeResolveResult,
    NodeResolveV4Adapter,
    PreparedSemanticCall,
)
from paper_eval.membind_v4.semantic_call import SemanticCallDecision

from .capture import CapturedBindReplay
from .certificate import (
    VersionedReadCertificate,
    read_certificate_from_prepared_call,
)


class VDCObservationAdapterError(ValueError):
    """The capture overlay could not preserve the factorized Bind contract."""


def _fail(code: str) -> VDCObservationAdapterError:
    return VDCObservationAdapterError(code)


def _sequence(value: object, code: str = "source_sequence_invalid") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _timestamp(value: object) -> int:
    return _sequence(value, "clock_invalid")


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    return await value


def _emit(observer: Callable[[object], object] | None, value: object) -> None:
    if observer is None:
        return
    try:
        result = observer(value)
    except Exception:
        raise _fail("observation_callback_failed") from None
    if inspect.isawaitable(result):
        raise _fail("observation_callback_must_be_synchronous")


def _source(compile_input: object) -> tuple[int, str]:
    source = getattr(compile_input, "source", None)
    sequence = _sequence(getattr(source, "source_sequence", None))
    group_id = getattr(source, "group_id", None)
    if not isinstance(group_id, str) or not group_id:
        raise _fail("group_id_invalid")
    return sequence, group_id


@dataclass(frozen=True, slots=True)
class VDCPreparedObservation:
    source_sequence: int
    artifact_ready_ns: int
    artifact_sha256: str


@dataclass(frozen=True, slots=True)
class VDCStaleProbeObservation:
    source_sequence: int
    state_version: int
    predecessor_source_sequence: int
    probe_started_ns: int
    probe_completed_ns: int
    certificate: VersionedReadCertificate


@dataclass(frozen=True, slots=True)
class VDCExactReadObservation:
    source_sequence: int
    state_version: int
    probe_started_ns: int
    probe_completed_ns: int
    resolve_started_ns: int
    resolve_completed_ns: int
    certificate: VersionedReadCertificate
    capture_sha256: str


class VDCObservationAdapter:
    """Run exact factorized Bind and opportunistically capture legal stale Probes.

    A stale Probe only performs Graphiti's read-only materialization. It never
    calls ``execute_request`` and therefore sends no NodeResolve LLM request.
    The state gate prevents a started stale Probe from racing the persistent
    native suffix.
    """

    def __init__(
        self,
        *,
        factorized_adapter: object,
        capture_observer: Callable[[CapturedBindReplay], object],
        stale_probe_observer: Callable[[VDCStaleProbeObservation], object]
        | None = None,
        prepared_observer: Callable[[VDCPreparedObservation], object] | None = None,
        exact_read_observer: Callable[[VDCExactReadObservation], object] | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if factorized_adapter is None or not callable(
            getattr(factorized_adapter, "prepare", None)
        ):
            raise _fail("factorized_adapter_invalid")
        callback_factory = getattr(
            factorized_adapter, "v4_node_resolve_callbacks", None
        )
        if not callable(callback_factory):
            raise _fail("factorized_callback_surface_missing")
        try:
            callbacks = callback_factory()
        except Exception:
            raise _fail("factorized_callback_surface_invalid") from None
        if not isinstance(callbacks, Mapping):
            raise _fail("factorized_callback_surface_invalid")
        required = (
            "materialize_request",
            "execute_request",
            "interpret_response",
            "continue_native_bind",
        )
        if any(not callable(callbacks.get(name)) for name in required):
            raise _fail("factorized_callback_surface_invalid")
        if not callable(capture_observer) or not callable(clock_ns):
            raise _fail("observation_callback_invalid")
        for observer in (stale_probe_observer, prepared_observer, exact_read_observer):
            if observer is not None and not callable(observer):
                raise _fail("observation_callback_invalid")
        self._factorized = factorized_adapter
        self._node = NodeResolveV4Adapter(**dict(callbacks))
        self._capture_observer = capture_observer
        self._stale_probe_observer = stale_probe_observer
        self._prepared_observer = prepared_observer
        self._exact_read_observer = exact_read_observer
        self._clock_ns = clock_ns
        self._active_frontier: int | None = None
        self._committing = False
        self._state_gate = asyncio.Lock()
        self._observation_tasks: set[asyncio.Task[None]] = set()
        self._probe_by_source: dict[int, asyncio.Task[None]] = {}

    def _now(self) -> int:
        return _timestamp(self._clock_ns())

    async def prepare(self, compile_input: object) -> PreparedArtifact:
        sequence, _group = _source(compile_input)
        prepared = await _await(
            self._factorized.prepare(compile_input),
            "factorized_prepare_not_awaitable",
        )
        if not isinstance(prepared, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        prepared.verify()
        if prepared.source_sequence != sequence:
            raise _fail("prepared_source_sequence_mismatch")
        ready_ns = self._now()
        _emit(
            self._prepared_observer,
            VDCPreparedObservation(
                source_sequence=sequence,
                artifact_ready_ns=ready_ns,
                artifact_sha256=prepared.artifact_sha256,
            ),
        )
        if (
            self._stale_probe_observer is not None
            and self._active_frontier is not None
            and sequence == self._active_frontier + 1
            and not self._committing
            and sequence not in self._probe_by_source
        ):
            task = asyncio.create_task(
                self._observe_stale_probe(compile_input, prepared, self._active_frontier)
            )
            self._probe_by_source[sequence] = task
            self._observation_tasks.add(task)
        return prepared

    async def _observe_stale_probe(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        predecessor_source_sequence: int,
    ) -> None:
        sequence, group_id = _source(compile_input)
        async with self._state_gate:
            if (
                self._active_frontier != predecessor_source_sequence
                or self._committing
            ):
                return
            start = self._now()
            call = await self._node.materialize(
                compile_input,
                prepared,
                state_version=predecessor_source_sequence,
            )
            end = self._now()
            certificate = read_certificate_from_prepared_call(
                call,
                group_id=group_id,
            )
            _emit(
                self._stale_probe_observer,
                VDCStaleProbeObservation(
                    source_sequence=sequence,
                    state_version=predecessor_source_sequence,
                    predecessor_source_sequence=predecessor_source_sequence,
                    probe_started_ns=start,
                    probe_completed_ns=end,
                    certificate=certificate,
                ),
            )

    async def wait_for_observation_tasks(self) -> None:
        tasks = tuple(self._observation_tasks)
        if tasks:
            await asyncio.gather(*tasks)
            self._observation_tasks.difference_update(tasks)

    @staticmethod
    def _capture(
        *,
        prepared: PreparedArtifact,
        state_version: int,
        group_id: str,
        exact_call: PreparedSemanticCall,
        response: object,
        interpreted: object,
        service_ns: int,
    ) -> CapturedBindReplay:
        request = exact_call.request
        values = {
            "captured_request": getattr(request, "captured_request", None),
            "extracted_nodes": getattr(request, "extracted_nodes", None),
            "candidate_nodes_by_extracted": getattr(
                request, "candidate_nodes_by_extracted", None
            ),
            "episode": getattr(request, "episode", None),
            "previous": getattr(request, "previous", None),
        }
        if values["captured_request"] is not None and not isinstance(
            values["captured_request"], CapturedGraphitiRequest
        ):
            raise _fail("factorized_captured_request_invalid")
        if any(values[key] is None for key in ("extracted_nodes", "candidate_nodes_by_extracted", "episode", "previous")):
            raise _fail("factorized_private_context_incomplete")
        return CapturedBindReplay.create(
            prepared_artifact=prepared,
            state_version=state_version,
            group_id=group_id,
            episode=values["episode"],
            previous_episodes=values["previous"],
            extracted_nodes=values["extracted_nodes"],
            candidate_nodes_by_extracted=values["candidate_nodes_by_extracted"],
            captured_request=values["captured_request"],
            llm_response=response,
            interpreted=interpreted,
            node_resolve_service_ns=service_ns,
        )

    async def bind(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        *,
        logical_time_ns: int,
    ) -> object:
        sequence, group_id = _source(compile_input)
        if not isinstance(prepared, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        prepared.verify()
        if prepared.source_sequence != sequence:
            raise _fail("prepared_source_sequence_mismatch")
        _sequence(logical_time_ns, "logical_time_invalid")
        if self._active_frontier is not None:
            raise _fail("frontier_bind_busy")
        self._active_frontier = sequence
        try:
            probe_start = self._now()
            exact_call = await self._node.materialize(
                compile_input,
                prepared,
                state_version=sequence,
            )
            probe_end = self._now()
            certificate = read_certificate_from_prepared_call(
                exact_call,
                group_id=group_id,
            )
            resolve_start = self._now()
            response = await self._node.execute(exact_call)
            resolve_end = self._now()
            interpreted = await self._node.interpret(response, exact_call)
            capture = self._capture(
                prepared=prepared,
                state_version=sequence,
                group_id=group_id,
                exact_call=exact_call,
                response=response,
                interpreted=interpreted,
                service_ns=(
                    0
                    if exact_call.call.execution_mode == "NO_LLM"
                    else resolve_end - resolve_start
                ),
            )
            pending = self._probe_by_source.get(sequence + 1)
            if pending is not None:
                await pending
            async with self._state_gate:
                self._committing = True
                decision = SemanticCallDecision(
                    decision="EXACT_EXECUTION",
                    reason="CAPTURED_EXACT_PREDECESSOR",
                    speculative_fingerprint=exact_call.call.fingerprint,
                    exact_fingerprint=exact_call.call.fingerprint,
                    request_identity_match=True,
                    effect_context_identity_match=True,
                    speculative_request_identity=exact_call.call.request_identity,
                    exact_request_identity=exact_call.call.request_identity,
                    speculative_effect_context_identity=(
                        exact_call.call.effect_context_identity
                    ),
                    exact_effect_context_identity=exact_call.call.effect_context_identity,
                )
                exact_result = ExactNodeResolveResult(
                    response=response,
                    exact_call=exact_call,
                    interpreted=interpreted,
                    decision=decision,
                    exact_execution_performed=True,
                )
                result = await self._node.continue_native_bind(
                    compile_input,
                    prepared,
                    exact_result,
                    logical_time_ns=logical_time_ns,
                )
            _emit(self._capture_observer, capture)
            _emit(
                self._exact_read_observer,
                VDCExactReadObservation(
                    source_sequence=sequence,
                    state_version=sequence,
                    probe_started_ns=probe_start,
                    probe_completed_ns=probe_end,
                    resolve_started_ns=resolve_start,
                    resolve_completed_ns=resolve_end,
                    certificate=certificate,
                    capture_sha256=capture.capture_sha256,
                ),
            )
            return result
        finally:
            self._committing = False
            self._active_frontier = None


__all__ = [
    "VDCExactReadObservation",
    "VDCObservationAdapter",
    "VDCObservationAdapterError",
    "VDCPreparedObservation",
    "VDCStaleProbeObservation",
]
