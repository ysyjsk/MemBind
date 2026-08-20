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
from paper_eval.membind_v4.admission import (
    SemanticAdmissionFacts,
    SpeculationValueEstimate,
    decide_conflict_aware_speculation,
)
from paper_eval.membind_v4.conflict_classifier import (
    ConflictClass,
    RecentConflictTelemetry,
    classify_conflict,
)
from paper_eval.membind_v4.conflict_signature import (
    enrich_conflict_signature,
    extract_conflict_signature,
)
from paper_eval.membind_v4.live_adapter import (
    V4LiveNodeResolveBridge,
    build_v31_graphiti_v4_bridge,
)
from paper_eval.membind_v4.residual_controller import (
    V4ResidualReservation,
    v4_speculative_transport_scope,
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
        conflict_value_estimate: SpeculationValueEstimate | None = None,
        residual_reservation: V4ResidualReservation | None = None,
    ) -> None:
        if not callable(getattr(factorized_adapter, "prepare", None)):
            raise _fail("factorized_prepare_missing")
        if not callable(getattr(factorized_adapter, "bind", None)):
            raise _fail("factorized_bind_missing")
        if not isinstance(residual_slot_signal, V4ResidualSlotSignal):
            raise _fail("residual_slot_signal_invalid")
        if not isinstance(stream_id, str) or not stream_id:
            raise _fail("stream_id_invalid")
        if conflict_value_estimate is not None and not isinstance(
            conflict_value_estimate, SpeculationValueEstimate
        ):
            raise _fail("conflict_value_estimate_invalid")
        if residual_reservation is not None and not isinstance(
            residual_reservation, V4ResidualReservation
        ):
            raise _fail("residual_reservation_invalid")
        if residual_reservation is not None and conflict_value_estimate is None:
            raise _fail("residual_reservation_without_conflict_policy")
        self._native = factorized_adapter
        self._signal = residual_slot_signal
        self._stream_id = stream_id
        self._bridge = bridge or build_v31_graphiti_v4_bridge(factorized_adapter)
        self._conflict_value_estimate = conflict_value_estimate
        self._residual_reservation = residual_reservation
        self._recent_conflicts = RecentConflictTelemetry()
        self._conflict_events: list[dict[str, object]] = []
        self._launcher_failures: list[dict[str, object]] = []
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

    def _record_conflict_decision(
        self,
        *,
        source_sequence: int,
        conflict_class: ConflictClass,
        reason: str,
        admit: bool,
        expected_benefit_ms: float,
        expected_cost_ms: float,
    ) -> None:
        self._conflict_events.append(
            {
                "event_sequence": len(self._conflict_events),
                "event_type": "conflict_admission",
                "source_sequence": source_sequence,
                "conflict_class": conflict_class.value,
                "reason": reason,
                "admit": admit,
                "expected_benefit_ms": expected_benefit_ms,
                "expected_cost_ms": expected_cost_ms,
            }
        )

    def _record_completed_publication(self, artifact: PreparedArtifact) -> None:
        if self._conflict_value_estimate is None:
            return
        signature = extract_conflict_signature(artifact)
        if not signature.complete:
            return
        outcome = "NO_SPECULATION"
        for event in reversed(self._bridge.telemetry()["events"]):
            if event.get("source_sequence") != artifact.source_sequence:
                continue
            if event.get("event_type") == "semantic_hit":
                outcome = "HIT"
                break
            if event.get("event_type") == "semantic_miss":
                outcome = "MISS"
                break
        self._recent_conflicts.record_publication(
            signature,
            validation_outcome=outcome,
        )

    async def _release_reservation(self, source_sequence: int) -> None:
        if self._residual_reservation is not None:
            await self._residual_reservation.release(source_sequence)

    def _record_launcher_failure(
        self,
        *,
        source_sequence: int,
        error: BaseException,
    ) -> None:
        self._launcher_failures.append(
            {
                "event_sequence": len(self._launcher_failures),
                "event_type": "speculation_launcher_failure",
                "source_sequence": source_sequence,
                "error_class": (
                    f"{type(error).__module__}.{type(error).__qualname__}"
                ),
                "fail_closed": True,
            }
        )

    async def _release_reservation_fail_closed(
        self,
        *,
        frontier_sequence: int,
        future_sequence: int,
    ) -> None:
        try:
            await self._release_reservation(frontier_sequence)
        except Exception as error:
            self._record_launcher_failure(
                source_sequence=future_sequence,
                error=error,
            )

    async def _run_launcher_fail_closed(
        self,
        *,
        sequence: int,
        frontier_task: asyncio.Task[object],
    ) -> bool:
        """Keep optional speculation failures outside the frontier outcome."""

        future_sequence = sequence + 1
        try:
            return await self._launch_future_when_eligible(
                sequence=sequence,
                frontier_task=frontier_task,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._record_launcher_failure(
                source_sequence=future_sequence,
                error=error,
            )
            return False
        finally:
            await self._release_reservation_fail_closed(
                frontier_sequence=sequence,
                future_sequence=future_sequence,
            )

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
        future = self._prepared.get(future_sequence)
        if future is None or frontier_task.done():
            return False
        compile_input, artifact = future
        frontier_signature = None
        candidate_signature = None
        estimate = self._conflict_value_estimate
        if estimate is not None:
            frontier = self._prepared.get(sequence)
            if frontier is None:
                return False
            frontier_signature = extract_conflict_signature(frontier[1])
            candidate_signature = extract_conflict_signature(artifact)
            classification = classify_conflict(
                frontier_signature,
                candidate_signature,
                telemetry=self._recent_conflicts,
            )
            if classification.conflict_class is not ConflictClass.LOW_CONFLICT:
                self._record_conflict_decision(
                    source_sequence=future_sequence,
                    conflict_class=classification.conflict_class,
                    reason=classification.reason,
                    admit=False,
                    expected_benefit_ms=0.0,
                    expected_cost_ms=estimate.estimated_frontier_interference_ms,
                )
                return False
            if self._bridge.active_speculation_count != 0:
                self._record_conflict_decision(
                    source_sequence=future_sequence,
                    conflict_class=ConflictClass.LOW_CONFLICT,
                    reason="ACTIVE_SPECULATION",
                    admit=False,
                    expected_benefit_ms=estimate.expected_node_resolve_service_ms,
                    expected_cost_ms=estimate.estimated_frontier_interference_ms,
                )
                return False
            if (
                estimate.expected_node_resolve_service_ms
                <= estimate.estimated_frontier_interference_ms
            ):
                self._record_conflict_decision(
                    source_sequence=future_sequence,
                    conflict_class=ConflictClass.LOW_CONFLICT,
                    reason="NOT_PROFITABLE",
                    admit=False,
                    expected_benefit_ms=estimate.expected_node_resolve_service_ms,
                    expected_cost_ms=estimate.estimated_frontier_interference_ms,
                )
                return False
            if self._residual_reservation is not None:
                await self._residual_reservation.reserve(sequence)

        if not await self._signal.wait_ready_while(frontier_task):
            if estimate is not None:
                self._record_conflict_decision(
                    source_sequence=future_sequence,
                    conflict_class=ConflictClass.LOW_CONFLICT,
                    reason="RESIDUAL_WINDOW_UNAVAILABLE",
                    admit=False,
                    expected_benefit_ms=estimate.expected_node_resolve_service_ms,
                    expected_cost_ms=estimate.estimated_frontier_interference_ms,
                )
            return False

        if estimate is not None:
            assert frontier_signature is not None
            assert candidate_signature is not None
            with llm_request_scope(
                kind=RequestKind.COMPILE,
                stream_id=self._stream_id,
                source_sequence=future_sequence,
            ):
                candidate_call = await self._bridge.materialize_speculation_request(
                    compile_input,
                    artifact,
                    state_version=sequence,
                )
                frontier_call = self._bridge.frontier_materialized_call(sequence)
                if frontier_call is None or frontier_task.done():
                    self._record_conflict_decision(
                        source_sequence=future_sequence,
                        conflict_class=ConflictClass.UNKNOWN,
                        reason="FRONTIER_MATERIALIZATION_UNAVAILABLE",
                        admit=False,
                        expected_benefit_ms=0.0,
                        expected_cost_ms=(
                            estimate.estimated_frontier_interference_ms
                        ),
                    )
                    return False
                classification = classify_conflict(
                    enrich_conflict_signature(frontier_signature, frontier_call.call),
                    enrich_conflict_signature(candidate_signature, candidate_call.call),
                    telemetry=self._recent_conflicts,
                )
                snapshot = self._signal.last_snapshot or {}
                decision = decide_conflict_aware_speculation(
                    semantic=SemanticAdmissionFacts(
                        future_arrived=True,
                        prepared_ready=True,
                        speculation_distance=1,
                        node_resolve_materializable=True,
                        execution_mode=candidate_call.call.execution_mode,
                    ),
                    conflict_class=classification.conflict_class,
                    resource_snapshot=snapshot,
                    active_speculation_count=self._bridge.active_speculation_count,
                    value=estimate,
                )
                self._record_conflict_decision(
                    source_sequence=future_sequence,
                    conflict_class=classification.conflict_class,
                    reason=decision.reason,
                    admit=decision.admit,
                    expected_benefit_ms=decision.expected_benefit_ms,
                    expected_cost_ms=decision.expected_cost_ms,
                )
                if not decision.admit or frontier_task.done():
                    return False
                with v4_speculative_transport_scope():
                    await self._bridge.launch_materialized_speculation(
                        artifact,
                        candidate_call,
                        state_version=sequence,
                    )
            if not frontier_task.done():
                self._bridge.record_overlap(
                    source_sequence=future_sequence,
                    frontier_source_sequence=sequence,
                )
            return True

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
        if not isinstance(compile_input, CompileInput) or not isinstance(
            artifact, PreparedArtifact
        ):
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
            self._run_launcher_fail_closed(
                sequence=sequence,
                frontier_task=frontier_task,
            )
        )
        try:
            result = await frontier_task
            self._record_completed_publication(artifact)
            return result
        finally:
            if not launcher.done():
                launcher.cancel()
            await asyncio.gather(launcher, return_exceptions=True)
            await self._release_reservation_fail_closed(
                frontier_sequence=sequence,
                future_sequence=sequence + 1,
            )
            self._prepared.pop(sequence, None)

    async def close(self) -> None:
        await self._bridge.cancel()

    def telemetry(self) -> dict[str, object]:
        selected = dict(self._bridge.telemetry())
        classes = tuple(
            str(event["conflict_class"]) for event in self._conflict_events
        )
        selected.update(
            {
                "conflict_policy_enabled": self._conflict_value_estimate is not None,
                "conflict_policy": (
                    "CONFLICT_AWARE_VALIDATED_SPEC"
                    if self._conflict_value_estimate is not None
                    else None
                ),
                "conflict_considered_count": len(self._conflict_events),
                "low_conflict_count": classes.count(ConflictClass.LOW_CONFLICT.value),
                "high_conflict_count": classes.count(ConflictClass.HIGH_CONFLICT.value),
                "unknown_conflict_count": classes.count(ConflictClass.UNKNOWN.value),
                "speculation_admission_count": sum(
                    event["admit"] is True for event in self._conflict_events
                ),
                "speculation_launcher_failure_count": len(
                    self._launcher_failures
                ),
                "speculation_launcher_failures": tuple(
                    dict(event) for event in self._launcher_failures
                ),
                "conflict_admission_events": tuple(
                    dict(event) for event in self._conflict_events
                ),
            }
        )
        return selected


__all__ = [
    "V4ResidualSlotSignal",
    "V4SpeculativeAdapterError",
    "V4SpeculativeGraphitiAdapter",
]
