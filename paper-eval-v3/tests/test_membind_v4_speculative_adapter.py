"""TDD checks for the v3.1 coordinator-compatible v4 facade."""

from __future__ import annotations

import asyncio

from paper_eval.membind_v1.evidence_fence import EvidenceFence, build_compile_input
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord
from paper_eval.membind_v31.admission import RequestKind
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v31 import request_runtime
from paper_eval.membind_v31.request_runtime import llm_request_scope
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
