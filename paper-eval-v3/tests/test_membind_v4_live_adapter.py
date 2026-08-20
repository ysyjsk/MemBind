"""TDD contracts for the v4 live NodeResolve bridge.

The tests use injected callbacks, so they never contact Graphiti, vLLM, or
Neo4j.  They exercise the same lifecycle that the production bridge exposes:
prepare a read-only request, run stale work in the background, validate it
against the exact predecessor, and continue the Native bind only once.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.live_adapter import (
    V4LiveNodeResolveBridge,
    V4LiveNodeResolveError,
    build_v31_graphiti_v4_bridge,
    graphiti_node_resolve_capability,
)
from paper_eval.membind_v4.semantic_call import SemanticCall


def _sha(value: int) -> str:
    return f"{value:064x}"


def _prepared(sequence: int) -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=sequence,
        source_sha256=_sha(sequence + 1),
        evidence_sha256=_sha(sequence + 2),
        certification_sha256=_sha(sequence + 3),
        raw_nodes=({"uuid": f"raw-{sequence}", "name": "Ada"},),
        pure_intermediates={"episode": f"e{sequence}"},
    )


def _call(sequence: int, state: int, fingerprint_suffix: str = "same") -> SemanticCall:
    return SemanticCall.create(
        source_sequence=sequence,
        state_version=state,
        operator_identity={"adapter": "live-fixture", "graphiti_version": "0.29.3"},
        model_identity={"model": "fixture"},
        decoding_identity={"temperature": 0, "max_tokens": 32},
        response_schema={"type": "object"},
        rendered_request_sha256=_sha(10),
        token_sequence_sha256=_sha(11),
        prompt_tokens=12,
        extracted_nodes=({"name": "Ada", "fingerprint_suffix": fingerprint_suffix},),
        candidate_order=(),
        candidate_bindings=(),
        previous_episodes=(),
        episode_context={"sequence": sequence},
        entity_types=("Entity",),
        operator_revision="live-fixture-v1",
    )


@pytest.mark.asyncio
async def test_bridge_hit_never_continues_from_stale_result() -> None:
    executed: list[object] = []
    continued: list[object] = []

    async def materialize(_input, prepared, state_version):
        return {
            "call": _call(prepared.source_sequence, state_version),
            "request": (prepared.source_sequence, state_version),
        }

    async def execute(request):
        executed.append(request)
        return {"request": request}

    async def interpret(response, exact):
        return {"resolved": response, "state": exact.call.state_version}

    async def continue_bind(_input, prepared, result, *, logical_time_ns):
        continued.append((prepared.source_sequence, result.exact_execution_performed, logical_time_ns))
        return "published"

    bridge = V4LiveNodeResolveBridge(
        materialize_request=materialize,
        execute_request=execute,
        interpret_response=interpret,
        continue_native_bind=continue_bind,
    )
    await bridge.launch_speculation({}, _prepared(1), state_version=0)
    result = await bridge.bind({}, _prepared(1), state_version=1, logical_time_ns=9)
    assert result == "published"
    assert executed == [(1, 0)]
    assert continued == [(1, False, 9)]
    assert bridge.telemetry()["semantic_hit_count"] == 1


@pytest.mark.asyncio
async def test_bridge_hit_records_recomputable_hidden_critical_time() -> None:
    """Only pre-exact work on a validated HIT is counted as hidden."""

    class Clock:
        def __init__(self) -> None:
            self.value = 100

        def __call__(self) -> int:
            self.value += 10
            return self.value

    async def materialize(_input, prepared, state_version):
        return {
            "call": _call(prepared.source_sequence, state_version),
            "request": (prepared.source_sequence, state_version),
        }

    bridge = V4LiveNodeResolveBridge(
        materialize_request=materialize,
        execute_request=lambda request: {"request": request},
        interpret_response=lambda response, _call: response,
        continue_native_bind=lambda *_args, **_kwargs: "ok",
        clock_ns=Clock(),
    )
    await bridge.launch_speculation({}, _prepared(1), state_version=0)
    assert await bridge.bind({}, _prepared(1), state_version=1, logical_time_ns=10) == "ok"

    telemetry = bridge.telemetry()
    event = next(
        row for row in telemetry["events"] if row["event_type"] == "semantic_hit"
    )
    assert event["speculation_started_timestamp_ns"] <= event[
        "exact_ready_timestamp_ns"
    ]
    assert event["speculation_completed_timestamp_ns"] >= event[
        "speculation_started_timestamp_ns"
    ]
    assert event["hidden_critical_time_ns"] == min(
        event["speculation_service_span_ns"],
        event["exact_ready_timestamp_ns"]
        - event["speculation_started_timestamp_ns"],
    )
    assert event["token_sequence_hmac_sha256"] == _sha(11)
    assert telemetry["hidden_critical_time_ns"] == event["hidden_critical_time_ns"]
    assert telemetry["hidden_critical_time_ns"] > 0
    assert telemetry["exact_validation_completed_count"] == 1


@pytest.mark.asyncio
async def test_bridge_miss_never_reports_hidden_critical_time() -> None:
    async def materialize(_input, prepared, state_version):
        marker = "stale" if state_version == 0 else "exact"
        return {
            "call": _call(prepared.source_sequence, state_version, marker),
            "request": (prepared.source_sequence, state_version),
        }

    bridge = V4LiveNodeResolveBridge(
        materialize_request=materialize,
        execute_request=lambda request: request,
        interpret_response=lambda response, _call: response,
        continue_native_bind=lambda *_args, **_kwargs: "ok",
    )
    await bridge.launch_speculation({}, _prepared(1), state_version=0)
    await bridge.bind({}, _prepared(1), state_version=1, logical_time_ns=10)

    event = next(
        row
        for row in bridge.telemetry()["events"]
        if row["event_type"] == "semantic_miss"
    )
    assert event["hidden_critical_time_ns"] == 0
    assert bridge.telemetry()["hidden_critical_time_ns"] == 0


@pytest.mark.asyncio
async def test_bridge_miss_executes_exact_and_cleans_background_task() -> None:
    executed: list[object] = []

    async def materialize(_input, prepared, state_version):
        return {
            "call": _call(prepared.source_sequence, state_version, "state-dependent" if state_version else "stale"),
            "request": (prepared.source_sequence, state_version),
        }

    async def execute(request):
        executed.append(request)
        return request

    bridge = V4LiveNodeResolveBridge(
        materialize_request=materialize,
        execute_request=execute,
        interpret_response=lambda response, _call: response,
        continue_native_bind=lambda *_args, **_kwargs: "ok",
    )
    await bridge.launch_speculation({}, _prepared(1), state_version=0)
    assert await bridge.bind({}, _prepared(1), state_version=1, logical_time_ns=10) == "ok"
    assert executed == [(1, 0), (1, 1)]
    assert bridge.active_speculation_count == 0
    assert bridge.telemetry()["semantic_miss_count"] == 1


@pytest.mark.asyncio
async def test_bridge_cancellation_awaits_and_releases_task() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def materialize(_input, prepared, state_version):
        return {"call": _call(prepared.source_sequence, state_version), "request": prepared.source_sequence}

    async def execute(_request):
        started.set()
        await release.wait()
        return "response"

    bridge = V4LiveNodeResolveBridge(
        materialize_request=materialize,
        execute_request=execute,
        interpret_response=lambda response, _call: response,
        continue_native_bind=lambda *_args, **_kwargs: "ok",
    )
    await bridge.launch_speculation({}, _prepared(1), state_version=0)
    await started.wait()
    await bridge.cancel()
    assert bridge.active_speculation_count == 0
    assert bridge.telemetry()["speculation_cancelled_count"] == 1


def test_bridge_rejects_duplicate_speculation_before_live_callback() -> None:
    calls: list[int] = []

    def materialize(_input, prepared, state_version):
        calls.append(prepared.source_sequence)
        return {"call": _call(prepared.source_sequence, state_version), "request": prepared.source_sequence}

    bridge = V4LiveNodeResolveBridge(
        materialize_request=materialize,
        execute_request=lambda request: request,
        interpret_response=lambda response, _call: response,
        continue_native_bind=lambda *_args, **_kwargs: "ok",
    )
    asyncio.run(bridge.launch_speculation({}, _prepared(1), state_version=0))
    with pytest.raises(V4LiveNodeResolveError, match="speculation_duplicate"):
        asyncio.run(bridge.launch_speculation({}, _prepared(1), state_version=0))
    assert calls == [1]


def test_monolithic_v31_adapter_is_not_misused_as_v4_continuation() -> None:
    class NativeAdapter:
        async def prepare(self, compile_input):
            return compile_input

        async def bind(self, compile_input, artifact, *, logical_time_ns):
            return artifact

    native = NativeAdapter()
    capability = graphiti_node_resolve_capability(native)
    assert capability["native_prepare_available"] is True
    assert capability["native_bind_available"] is True
    assert capability["factorized"] is False
    with pytest.raises(V4LiveNodeResolveError, match="node_resolve_factorization_unavailable"):
        build_v31_graphiti_v4_bridge(native)


def test_explicit_factorized_callback_surface_is_the_only_live_factory_path() -> None:
    class NativeAdapter:
        async def prepare(self, compile_input):
            return compile_input

        async def bind(self, compile_input, artifact, *, logical_time_ns):
            return artifact

    def materialize(_input, prepared, state_version):
        return {"call": _call(prepared.source_sequence, state_version), "request": state_version}

    native = NativeAdapter()
    bridge = build_v31_graphiti_v4_bridge(
        native,
        materialize_request=materialize,
        execute_request=lambda request: request,
        interpret_response=lambda response, _call: response,
        continue_native_bind=lambda *_args, **_kwargs: "ok",
    )
    assert isinstance(bridge, V4LiveNodeResolveBridge)


def test_native_callback_surface_can_be_exposed_without_changing_v31_bind() -> None:
    class NativeAdapter:
        async def prepare(self, compile_input):
            return compile_input

        async def bind(self, compile_input, artifact, *, logical_time_ns):
            return artifact

        def v4_node_resolve_callbacks(self):
            return {
                "materialize_request": lambda _input, prepared, state: {
                    "call": _call(prepared.source_sequence, state),
                    "request": state,
                },
                "execute_request": lambda request: request,
                "interpret_response": lambda response, _call: response,
                "continue_native_bind": lambda *_args, **_kwargs: "ok",
            }

    native = NativeAdapter()
    capability = graphiti_node_resolve_capability(native)
    assert capability["factorized"] is True
    bridge = build_v31_graphiti_v4_bridge(native)
    assert isinstance(bridge, V4LiveNodeResolveBridge)
