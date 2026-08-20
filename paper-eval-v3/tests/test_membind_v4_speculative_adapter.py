"""TDD checks for the v3.1 coordinator-compatible v4 facade."""

from __future__ import annotations

import asyncio

import pytest

from paper_eval.membind_v1.evidence_fence import EvidenceFence, build_compile_input
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord
from paper_eval.membind_v31.admission import RequestKind
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v31 import request_runtime
from paper_eval.membind_v31.request_runtime import llm_request_scope
from paper_eval.membind_v4.admission import SpeculationValueEstimate
from paper_eval.membind_v4.residual_controller import V4ResidualReservation
from paper_eval.membind_v4.semantic_call import SemanticCall
from paper_eval.membind_v4.speculative_adapter import (
    V4ResidualSlotSignal,
    V4SpeculativeGraphitiAdapter,
)


def _sha(value: int) -> str:
    return f"{value:064x}"


def _inputs():
    records = [
        SourceRecord.create(
            source_sequence=sequence,
            episode_uuid=f"episode-{sequence}",
            group_id="v4-facade-test",
            reference_time_ns=sequence,
            source_filter="message",
            episode_projection={"body": f"source-{sequence}"},
        )
        for sequence in range(2)
    ]
    source_log = SourceLog.create(records)
    return tuple(
        build_compile_input(
            source_log.record(sequence),
            EvidenceFence.capture(source_log, target_source_sequence=sequence, last_n=10),
        )
        for sequence in range(2)
    )


def _artifact(compile_input) -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=compile_input.source.source_sequence,
        source_sha256=compile_input.source.source_sha256,
        evidence_sha256=compile_input.evidence.evidence_prefix_sha256,
        certification_sha256=_sha(10),
        raw_nodes=({"uuid": f"raw-{compile_input.source.source_sequence}"},),
        pure_intermediates={"node_episode_index_map": {}},
    )


def _call(source: int, state: int) -> SemanticCall:
    return SemanticCall.create(
        source_sequence=source,
        state_version=state,
        operator_identity={"graphiti_version": "0.29.3"},
        model_identity={"model": "fixture"},
        decoding_identity={"temperature": 0},
        response_schema={"type": "object"},
        rendered_request_sha256=_sha(20 + source),
        token_sequence_sha256=_sha(30 + source),
        prompt_tokens=10,
        extracted_nodes=({"uuid": f"raw-{source}"},),
        candidate_order=(),
        candidate_bindings=(),
        execution_mode="LLM",
    )


class _Factorized:
    def __init__(self, signal: V4ResidualSlotSignal, *, open_gate: bool) -> None:
        self.signal = signal
        self.open_gate = open_gate
        self.spec_started = asyncio.Event()
        self.continued: list[int] = []
        self.executed_scopes: list[tuple[int, RequestKind | None]] = []

    async def prepare(self, compile_input):
        return _artifact(compile_input)

    async def bind(self, *_args, **_kwargs):
        raise AssertionError("monolithic bind must not be called")

    def v4_node_resolve_callbacks(self):
        async def materialize(_input, prepared, state):
            source = prepared.source_sequence
            return {"call": _call(source, state), "request": {"source": source, "state": state}}

        async def execute(request):
            scope = request_runtime._SCOPE.get()
            self.executed_scopes.append(
                (request["source"], None if scope is None else scope.kind)
            )
            if request["source"] == 0:
                if self.open_gate:
                    self.signal.observe(
                        {
                            "configured_limit": 2,
                            "active_count": 1,
                            "active_frontier_count": 1,
                            "waiting_frontier_count": 0,
                            "frontier_bind_region_count": 1,
                            "frontier_transport_phase": "FRONTIER_LLM_PERMIT_ACTIVE",
                        }
                    )
                    await self.spec_started.wait()
                return {"source": 0}
            if request["source"] == 1 and request["state"] == 0:
                self.spec_started.set()
            return {"source": request["source"]}

        async def continue_bind(_input, prepared, result, *, logical_time_ns):
            assert logical_time_ns >= 0
            self.continued.append(prepared.source_sequence)
            return result.interpreted

        return {
            "materialize_request": materialize,
            "execute_request": execute,
            "interpret_response": lambda response, _call: response,
            "continue_native_bind": continue_bind,
        }


async def _run(*, open_gate: bool):
    signal = V4ResidualSlotSignal()
    native = _Factorized(signal, open_gate=open_gate)
    adapter = V4SpeculativeGraphitiAdapter(
        factorized_adapter=native,
        residual_slot_signal=signal,
        stream_id="facade-test",
    )
    first, second = _inputs()
    first_artifact, second_artifact = await asyncio.gather(
        adapter.prepare(first), adapter.prepare(second)
    )
    with llm_request_scope(
        kind=RequestKind.FRONTIER,
        stream_id="facade-test",
        source_sequence=0,
    ):
        await adapter.bind(first, first_artifact, logical_time_ns=1)
    with llm_request_scope(
        kind=RequestKind.FRONTIER,
        stream_id="facade-test",
        source_sequence=1,
    ):
        await adapter.bind(second, second_artifact, logical_time_ns=2)
    telemetry = adapter.telemetry()
    await adapter.close()
    return native, telemetry


def test_future_node_resolve_launches_only_in_proven_residual_slot() -> None:
    native, telemetry = asyncio.run(_run(open_gate=True))
    assert native.continued == [0, 1]
    assert telemetry["speculation_launched_count"] == 1
    assert telemetry["semantic_hit_count"] == 1
    assert telemetry["persistent_write_count"] == 0
    assert [
        event["source_sequence"]
        for event in telemetry["events"]
        if event["event_type"] == "speculation_overlap"
    ] == [1]
    assert native.executed_scopes == [
        (0, RequestKind.FRONTIER),
        (1, RequestKind.COMPILE),
    ]


def test_no_residual_slot_falls_back_to_exact_without_speculation() -> None:
    native, telemetry = asyncio.run(_run(open_gate=False))
    assert native.continued == [0, 1]
    assert telemetry["speculation_launched_count"] == 0
    assert telemetry["semantic_hit_count"] == 0
    assert telemetry["semantic_miss_count"] == 0
    assert sum(
        event["event_type"] == "exact_node_resolve"
        for event in telemetry["events"]
    ) == 2
    assert all(
        event["event_type"] != "speculation_overlap"
        for event in telemetry["events"]
    )
    assert native.executed_scopes == [
        (0, RequestKind.FRONTIER),
        (1, RequestKind.FRONTIER),
    ]


def _named_artifact(compile_input, name: str | None) -> PreparedArtifact:
    node = {
        "uuid": f"raw-{compile_input.source.source_sequence}",
        "group_id": "v4-facade-test",
        "labels": ["Entity"],
    }
    if name is not None:
        node["name"] = name
    return PreparedArtifact.create(
        source_sequence=compile_input.source.source_sequence,
        source_sha256=compile_input.source.source_sha256,
        evidence_sha256=compile_input.evidence.evidence_prefix_sha256,
        certification_sha256=_sha(10),
        raw_nodes=(node,),
        raw_edges=(),
        pure_intermediates={"node_episode_index_map": {}},
    )


def _stateful_call(
    source: int,
    state: int,
    *,
    miss: bool,
    existing_uuid: str | None = None,
) -> SemanticCall:
    marker = state if miss and source == 1 else source
    candidate_order = ("c0",) if existing_uuid is not None else ()
    candidate_bindings = (
        (
            {
                "candidate_id": "c0",
                "uuid": existing_uuid,
                "projection": {"name": "fixture"},
            },
        )
        if existing_uuid is not None
        else ()
    )
    return SemanticCall.create(
        source_sequence=source,
        state_version=state,
        operator_identity={"graphiti_version": "0.29.3"},
        model_identity={"model": "fixture"},
        decoding_identity={"temperature": 0},
        response_schema={"type": "object"},
        rendered_request_sha256=_sha(200 + marker),
        token_sequence_sha256=_sha(300 + marker),
        prompt_tokens=10,
        extracted_nodes=({"source": source},),
        candidate_order=candidate_order,
        candidate_bindings=candidate_bindings,
        execution_mode="LLM",
    )


class _ConflictAwareFactorized:
    def __init__(
        self,
        signal: V4ResidualSlotSignal,
        *,
        names: tuple[str | None, str | None],
        expect_launch: bool,
        miss: bool = False,
        launcher_failure: bool = False,
        open_gate: bool = True,
        existing_overlap: bool = False,
    ) -> None:
        self.signal = signal
        self.names = names
        self.expect_launch = expect_launch
        self.miss = miss
        self.launcher_failure = launcher_failure
        self.open_gate = open_gate
        self.existing_overlap = existing_overlap
        self.spec_started = asyncio.Event()
        self.launcher_attempted = asyncio.Event()
        self.executed: list[tuple[int, int]] = []
        self.prepared_sequences: list[int] = []

    async def prepare(self, compile_input):
        source = compile_input.source.source_sequence
        self.prepared_sequences.append(source)
        return _named_artifact(compile_input, self.names[source])

    async def bind(self, *_args, **_kwargs):
        raise AssertionError("monolithic bind must not be called")

    def v4_node_resolve_callbacks(self):
        async def materialize(_input, prepared, state):
            source = prepared.source_sequence
            if self.launcher_failure and (source, state) == (1, 0):
                self.launcher_attempted.set()
                raise RuntimeError("stale materialization failed")
            return {
                "call": _stateful_call(
                    source,
                    state,
                    miss=self.miss,
                    existing_uuid=(
                        "same-existing-uuid"
                        if self.existing_overlap and state == 0
                        else None
                    ),
                ),
                "request": {"source": source, "state": state},
            }

        async def execute(request):
            identity = (request["source"], request["state"])
            self.executed.append(identity)
            if identity == (0, 0):
                if self.open_gate:
                    self.signal.observe(
                        {
                            "configured_limit": 2,
                            "active_count": 1,
                            "active_frontier_count": 1,
                            "active_compile_count": 0,
                            "waiting_frontier_count": 0,
                            "waiting_compile_count": 7,
                            "frontier_bind_region_count": 1,
                            "frontier_transport_phase": "FRONTIER_LLM_PERMIT_ACTIVE",
                        }
                    )
                if self.launcher_failure:
                    await self.launcher_attempted.wait()
                elif self.expect_launch:
                    await self.spec_started.wait()
                else:
                    # Keep the frontier active for one scheduling turn so the
                    # policy, rather than frontier completion, rejects it.
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
            elif identity == (1, 0):
                self.spec_started.set()
            return identity

        return {
            "materialize_request": materialize,
            "execute_request": execute,
            "interpret_response": lambda response, _call: response,
            "continue_native_bind": lambda _input, _prepared, result, **_kwargs: result.interpreted,
        }


class _RecordingReservation(V4ResidualReservation):
    def __init__(self, future_prepared) -> None:
        self._future_prepared = future_prepared
        self.active_source: int | None = None
        self.events: list[tuple[str, int]] = []

    async def reserve(self, source_sequence: int) -> None:
        assert self._future_prepared()
        assert self.active_source is None
        self.active_source = source_sequence
        self.events.append(("reserve", source_sequence))

    async def release(self, source_sequence: int) -> None:
        if self.active_source is None:
            return
        assert self.active_source == source_sequence
        self.active_source = None
        self.events.append(("release", source_sequence))


async def _run_conflict_aware(
    *,
    names: tuple[str | None, str | None],
    expect_launch: bool,
    miss: bool = False,
    launcher_failure: bool = False,
    open_gate: bool = True,
    record_reservation: bool = False,
    existing_overlap: bool = False,
    benefit_ms: float = 25,
    cost_ms: float = 3,
):
    signal = V4ResidualSlotSignal()
    native = _ConflictAwareFactorized(
        signal,
        names=names,
        expect_launch=expect_launch,
        miss=miss,
        launcher_failure=launcher_failure,
        open_gate=open_gate,
        existing_overlap=existing_overlap,
    )
    reservation = (
        _RecordingReservation(lambda: 1 in native.prepared_sequences)
        if record_reservation
        else None
    )
    adapter = V4SpeculativeGraphitiAdapter(
        factorized_adapter=native,
        residual_slot_signal=signal,
        stream_id="facade-conflict-aware",
        conflict_value_estimate=SpeculationValueEstimate(
            expected_node_resolve_service_ms=benefit_ms,
            estimated_frontier_interference_ms=cost_ms,
        ),
        residual_reservation=reservation,
    )
    first, second = _inputs()
    first_artifact, second_artifact = await asyncio.gather(
        adapter.prepare(first), adapter.prepare(second)
    )
    for compile_input, artifact, logical_time in (
        (first, first_artifact, 1),
        (second, second_artifact, 2),
    ):
        with llm_request_scope(
            kind=RequestKind.FRONTIER,
            stream_id="facade-conflict-aware",
            source_sequence=artifact.source_sequence,
        ):
            await adapter.bind(
                compile_input,
                artifact,
                logical_time_ns=logical_time,
            )
    telemetry = adapter.telemetry()
    await adapter.close()
    return native, telemetry, reservation


def test_conflict_aware_facade_launches_low_conflict_despite_compile_waiter() -> None:
    native, telemetry, _reservation = asyncio.run(
        _run_conflict_aware(names=("Alice", "Bob"), expect_launch=True)
    )

    assert telemetry["low_conflict_count"] == 1
    assert telemetry["high_conflict_count"] == 0
    assert telemetry["speculation_admission_count"] == 1
    assert telemetry["speculation_launched_count"] == 1
    assert native.executed == [(0, 0), (1, 0)]


def test_conflict_aware_facade_never_launches_high_conflict_candidate() -> None:
    native, telemetry, _reservation = asyncio.run(
        _run_conflict_aware(names=("Alice", " ALICE "), expect_launch=False)
    )

    assert telemetry["high_conflict_count"] == 1
    assert telemetry["speculation_admission_count"] == 0
    assert telemetry["speculation_launched_count"] == 0
    assert native.executed == [(0, 0), (1, 1)]


def test_conflict_aware_low_prediction_still_exactly_validates_and_falls_back() -> None:
    native, telemetry, _reservation = asyncio.run(
        _run_conflict_aware(
            names=("Alice", "Bob"),
            expect_launch=True,
            miss=True,
        )
    )

    assert telemetry["low_conflict_count"] == 1
    assert telemetry["semantic_hit_count"] == 0
    assert telemetry["semantic_miss_count"] == 1
    assert telemetry["exact_validation_completed_count"] == 1
    assert native.executed == [(0, 0), (1, 0), (1, 1)]
    miss_event = next(
        event for event in telemetry["events"] if event["event_type"] == "semantic_miss"
    )
    assert miss_event["exact_execution_performed"] is True
    assert telemetry["persistent_write_count"] == 0


def test_reservation_starts_only_after_future_prepare_and_ignores_compile_waiter() -> None:
    async def scenario():
        signal = V4ResidualSlotSignal()
        native = _ConflictAwareFactorized(
            signal,
            names=("Alice", "Bob"),
            expect_launch=True,
        )
        reservation = _RecordingReservation(
            lambda: 1 in native.prepared_sequences
        )
        adapter = V4SpeculativeGraphitiAdapter(
            factorized_adapter=native,
            residual_slot_signal=signal,
            stream_id="facade-reservation-order",
            conflict_value_estimate=SpeculationValueEstimate(
                expected_node_resolve_service_ms=25,
                estimated_frontier_interference_ms=3,
            ),
            residual_reservation=reservation,
        )
        first, second = _inputs()
        first_artifact = await adapter.prepare(first)
        with llm_request_scope(
            kind=RequestKind.FRONTIER,
            stream_id="facade-reservation-order",
            source_sequence=0,
        ):
            first_bind = asyncio.create_task(
                adapter.bind(first, first_artifact, logical_time_ns=1)
            )
        await asyncio.sleep(0)
        assert reservation.events == []

        second_artifact = await adapter.prepare(second)
        await first_bind
        with llm_request_scope(
            kind=RequestKind.FRONTIER,
            stream_id="facade-reservation-order",
            source_sequence=1,
        ):
            await adapter.bind(second, second_artifact, logical_time_ns=2)
        telemetry = adapter.telemetry()
        await adapter.close()
        return native, telemetry, reservation

    native, telemetry, reservation = asyncio.run(scenario())

    assert reservation.events == [("reserve", 0), ("release", 0)]
    assert reservation.active_source is None
    assert telemetry["speculation_admission_count"] == 1
    assert native.executed == [(0, 0), (1, 0)]


def test_launcher_error_fails_closed_records_telemetry_and_releases_reservation() -> None:
    native, telemetry, reservation = asyncio.run(
        _run_conflict_aware(
            names=("Alice", "Bob"),
            expect_launch=False,
            launcher_failure=True,
            record_reservation=True,
        )
    )

    assert native.executed == [(0, 0), (1, 1)]
    assert reservation is not None
    assert reservation.events == [("reserve", 0), ("release", 0)]
    assert reservation.active_source is None
    assert telemetry["speculation_launcher_failure_count"] == 1
    assert telemetry["speculation_launcher_failures"] == (
        {
            "event_sequence": 0,
            "event_type": "speculation_launcher_failure",
            "source_sequence": 1,
            "error_class": (
                "paper_eval.membind_v4.live_adapter.V4LiveNodeResolveError"
            ),
            "fail_closed": True,
        },
    )
    assert telemetry["speculation_launched_count"] == 0


@pytest.mark.parametrize(
    ("names", "benefit_ms", "cost_ms", "expected_reason"),
    [
        (("Alice", " ALICE "), 25, 3, "DIRECT_ENTITY_OVERLAP"),
        (("Alice", None), 25, 3, "INCOMPLETE_SIGNAL"),
        (("Alice", "Bob"), 3, 3, "NOT_PROFITABLE"),
    ],
)
def test_pre_materialization_rejections_never_create_reservation(
    names,
    benefit_ms: float,
    cost_ms: float,
    expected_reason: str,
) -> None:
    _native, telemetry, reservation = asyncio.run(
        _run_conflict_aware(
            names=names,
            expect_launch=False,
            record_reservation=True,
            benefit_ms=benefit_ms,
            cost_ms=cost_ms,
        )
    )

    assert reservation is not None
    assert reservation.events == []
    assert reservation.active_source is None
    assert telemetry["conflict_admission_events"][0]["reason"] == expected_reason


def test_state_bound_high_conflict_releases_created_reservation() -> None:
    native, telemetry, reservation = asyncio.run(
        _run_conflict_aware(
            names=("Alice", "Bob"),
            expect_launch=False,
            record_reservation=True,
            existing_overlap=True,
        )
    )

    assert reservation is not None
    assert reservation.events == [("reserve", 0), ("release", 0)]
    assert reservation.active_source is None
    assert telemetry["conflict_admission_events"][0]["conflict_class"] == (
        "HIGH_CONFLICT"
    )
    assert telemetry["conflict_admission_events"][0]["reason"] == "HIGH_CONFLICT"
    assert telemetry["speculation_launched_count"] == 0
    assert native.executed == [(0, 0), (1, 1)]


def test_cancelled_resource_wait_releases_reservation() -> None:
    native, telemetry, reservation = asyncio.run(
        _run_conflict_aware(
            names=("Alice", "Bob"),
            expect_launch=False,
            open_gate=False,
            record_reservation=True,
        )
    )

    assert reservation is not None
    assert reservation.events == [("reserve", 0), ("release", 0)]
    assert reservation.active_source is None
    assert telemetry["speculation_launched_count"] == 0
    assert native.executed == [(0, 0), (1, 1)]
