from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import (
    AdmissionClass,
    CapacityAuthority,
    CapacityAuthorityError,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import (
    BindingMismatch,
    NativeBindingScope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.contracts import (
    HoistCertificate,
    OperatorContract,
    PreviousSourceProjector,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.frontier import (
    FrontierRuntime,
    FrontierViolation,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.executor import FrontierExecutor
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.request_identity import (
    RequestIdentity,
    build_request_identity,
    semantic_wire_hash,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import (
    CaptureSession,
    TranscriptStore,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.trace import SourceTraceRecorder
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.capabilities import (
    CapabilityViolation,
    LLMOnlyFacade,
    NonEscapingValue,
    assert_non_escaping,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (
    FrontierAwareLLMClient,
    provider_scope,
)


def test_operator_certificate_rejects_derived_state_and_non_bindable_effect() -> None:
    contract = OperatorContract(
        name="resolve_nodes",
        reads_memory=frozenset({"neo4j"}),
        writes_memory=frozenset(),
        local_effects=frozenset(),
        oracle_effects=frozenset({"llm"}),
        inputs=frozenset({"source"}),
        control_dependencies=frozenset({"resolved_uuid"}),
        bindable=False,
        certified=False,
    )
    assert contract.classification == "OPAQUE"
    with pytest.raises(ValueError, match="not hoistable"):
        HoistCertificate.from_contracts([contract])


def test_projector_fails_closed_on_timestamp_ties_without_secondary_order() -> None:
    projector = PreviousSourceProjector(
        [
            {"sequence": 0, "valid_at": "2026-01-01T00:00:00+00:00", "body": "a"},
            {"sequence": 1, "valid_at": "2026-01-01T00:00:00+00:00", "body": "b"},
        ],
        stable_tie_breaker=None,
    )
    with pytest.raises(ValueError, match="timestamp tie"):
        projector.project(sequence=2, valid_at=datetime(2026, 1, 2, tzinfo=timezone.utc))


def test_request_identity_is_immutable_and_semantic_fields_change_digest() -> None:
    base = build_request_identity(
        source_sequence=0,
        callsite="extract_nodes.extract_message",
        ordinal=0,
        messages=[{"role": "user", "content": "hello"}],
        response_model={"type": "object"},
        max_tokens=16,
        model_size="large",
        group_id="g",
        prompt_name="extract_nodes.extract_message",
        flags={"attribute_extraction": False},
        client_identity={"class": "FakeClient", "source_hash": "abc"},
        transport_identity={"top_p": 1.0, "seed": 7},
        cache_salt="salt",
        previous_context_digest="prev",
    )
    altered = build_request_identity(
        source_sequence=0,
        callsite="extract_nodes.extract_message",
        ordinal=0,
        messages=[{"role": "user", "content": "changed"}],
        response_model={"type": "object"},
        max_tokens=16,
        model_size="large",
        group_id="g",
        prompt_name="extract_nodes.extract_message",
        flags={"attribute_extraction": False},
        client_identity={"class": "FakeClient", "source_hash": "abc"},
        transport_identity={"top_p": 1.0, "seed": 7},
        cache_salt="salt",
        previous_context_digest="prev",
    )
    assert isinstance(base, RequestIdentity)
    assert base.digest != altered.digest
    with pytest.raises(TypeError):
        base.messages[0]["content"] = "mutate"  # type: ignore[index]


def test_transcript_retry_is_one_logical_call_and_exact_consume() -> None:
    store = TranscriptStore()
    identity = build_request_identity(
        source_sequence=0,
        callsite="extract_nodes.extract_message",
        ordinal=0,
        messages=[{"role": "user", "content": "hello"}],
        response_model={"type": "object"},
        max_tokens=16,
        model_size="large",
        group_id="g",
        prompt_name="extract_nodes.extract_message",
        flags={},
        client_identity={"class": "FakeClient", "source_hash": "abc"},
        transport_identity={"top_p": 1.0, "seed": 7},
        cache_salt="salt",
        previous_context_digest="prev",
    )
    store.capture(identity, {"nodes": []}, transport_attempts=2)
    assert store.summary()["logical_captured"] == 1
    assert store.consume(identity) == {"nodes": []}
    with pytest.raises(BindingMismatch, match="duplicate"):
        store.consume(identity)


def test_capacity_authority_uses_runtime_value_and_rejects_mismatch() -> None:
    authority = CapacityAuthority.from_runtime(runtime_max_coroutines=8, graphiti_max_coroutines=8)
    assert authority.value == 8
    assert authority.source == "runtime.config.max_coroutines"
    with pytest.raises(CapacityAuthorityError, match="equality"):
        CapacityAuthority.from_runtime(runtime_max_coroutines=8, graphiti_max_coroutines=20)
    with pytest.raises(CapacityAuthorityError, match="authority"):
        CapacityAuthority.from_runtime(runtime_max_coroutines=8, graphiti_max_coroutines=8, claimed=20)


@pytest.mark.asyncio
async def test_frontier_runtime_publishes_only_next_sequence_and_stops_after_failure() -> None:
    runtime = FrontierRuntime(source_count=3)
    await runtime.mark_prepared(1, {"payload": "future"})
    with pytest.raises(FrontierViolation, match="predecessor"):
        await runtime.publish(1, lambda value: asyncio.sleep(0, result=value))
    await runtime.mark_prepared(0, {"payload": "first"})
    await runtime.publish(0, lambda value: asyncio.sleep(0, result=value))
    with pytest.raises(RuntimeError, match="native"):
        await runtime.publish(1, lambda _value: (_ for _ in ()).throw(RuntimeError("native")))
    assert runtime.durable_frontier == 0
    assert runtime.failed_sequence == 1
    with pytest.raises(FrontierViolation, match="failed"):
        await runtime.publish(2, lambda value: asyncio.sleep(0, result=value))


@pytest.mark.asyncio
async def test_executor_orders_native_publication_and_records_admission_classes() -> None:
    authority = CapacityAuthority.from_runtime(2, 2)
    executor = FrontierExecutor(4, authority)
    prepared: list[int] = []
    published: list[int] = []

    async def prepare(sequence: int) -> dict[str, int]:
        await asyncio.sleep(0 if sequence == 3 else 0.001)
        prepared.append(sequence)
        return {"sequence": sequence}

    async def publish(sequence: int, value: dict[str, int]) -> None:
        assert value["sequence"] == sequence
        published.append(sequence)

    result = await executor.run(prepare, publish)
    assert published == [0, 1, 2, 3]
    assert result.durable_frontier == 3
    assert [event["source_sequence"] for event in result.events if event["event"] == "PUBLICATION_DURABLE"] == [0, 1, 2, 3]
    assert all(event["admission_class"] in {"NATIVE_FRONTIER", "FRONTIER_PREPARE", "FUTURE_PREPARE"} for event in result.events if event["event"] == "ADMITTED")


@pytest.mark.asyncio
async def test_executor_c1_degenerates_to_serial_and_cancels_after_failure() -> None:
    authority = CapacityAuthority.from_runtime(1, 1)
    executor = FrontierExecutor(3, authority)
    published: list[int] = []

    async def prepare(sequence: int) -> int:
        return sequence

    async def publish(sequence: int, _value: int) -> None:
        published.append(sequence)
        if sequence == 1:
            raise RuntimeError("publish failure")

    with pytest.raises(RuntimeError, match="publish failure"):
        await executor.run(prepare, publish)
    assert published == [0, 1]
    assert executor.frontier.durable_frontier == 0
    assert executor.frontier.failed_sequence == 1


@pytest.mark.asyncio
async def test_provider_admission_classifies_frontier_prepare_dynamically_and_replay_is_free() -> None:
    authority = CapacityAuthority.from_runtime(2, 2)
    arbiter = __import__(
        "saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission",
        fromlist=["AdmissionArbiter"],
    ).AdmissionArbiter(authority)
    store = TranscriptStore()
    frontier = {"value": -1}

    class Client:
        async def generate_response(self, messages, **kwargs):
            return {"ok": kwargs["prompt_name"]}

    capture = FrontierAwareLLMClient(Client(), store=store, arbiter=arbiter, mode="capture", durable_frontier=lambda: frontier["value"], client_identity={"class": "Fake", "source_hash": "x"})
    with provider_scope(region="PREPARE", source_sequence=0):
        await capture.generate_response([{"role": "user", "content": "x"}], prompt_name="extract_nodes.extract_message")
    with provider_scope(region="PREPARE", source_sequence=2):
        await capture.generate_response([{"role": "user", "content": "z"}], prompt_name="extract_nodes.extract_message")
    assert [row["admission_class"] for row in capture.provider_calls] == ["FRONTIER_PREPARE", "FUTURE_PREPARE"]
    replay = FrontierAwareLLMClient(Client(), store=store, arbiter=arbiter, mode="replay", durable_frontier=lambda: frontier["value"], client_identity={"class": "Fake", "source_hash": "x"})
    with NativeBindingScope(store, source_sequence=0):
        with provider_scope(region="NATIVE", source_sequence=0):
            await replay.generate_response([{"role": "user", "content": "x"}], prompt_name="extract_nodes.extract_message")
    assert replay.provider_calls[-1]["admitted"] is False


def test_core_architecture_has_no_graphiti_import() -> None:
    root = Path(__file__).parents[1] / "src/saturated_fixed_work_baseline_v1_3/membind_v5/runtime/core"
    for path in root.glob("*.py"):
        assert "graphiti_core" not in path.read_text(encoding="utf-8")


def test_preparation_capability_trap_and_non_escape_are_fail_closed() -> None:
    facade = LLMOnlyFacade(object())
    with pytest.raises(CapabilityViolation, match="forbidden"):
        _ = facade.driver
    value = NonEscapingValue({"temporary": True}, "prepare")
    assert value.publish() == {"temporary": True}
    with pytest.raises(CapabilityViolation, match="escaped"):
        assert_non_escaping(NonEscapingValue({}, "prepare", escaped=True))


def test_wire_hash_tracks_frozen_transport_environment() -> None:
    args = {"response_model": {"type": "object"}, "max_tokens": 8}
    first = semantic_wire_hash([{"role": "user", "content": "x"}], transport_identity={"seed": 1, "top_p": 1.0}, **args)
    second = semantic_wire_hash([{"role": "user", "content": "x"}], transport_identity={"seed": 2, "top_p": 1.0}, **args)
    assert first != second


@pytest.mark.asyncio
async def test_capture_deep_copies_response_and_trace_has_one_envelope_for_two_regions() -> None:
    recorder = SourceTraceRecorder(clock=iter(range(10)).__next__)
    store = TranscriptStore()
    session = CaptureSession(store, source_sequence=0, recorder=recorder)
    identity = build_request_identity(
        source_sequence=0,
        callsite="extract_nodes.generate_response",
        ordinal=0,
        messages=[{"role": "user", "content": "x"}],
        response_model={"type": "object"},
        max_tokens=8,
        model_size="large",
        group_id="g",
        prompt_name="extract_nodes.extract_message",
        flags={},
        client_identity={"class": "Fake", "source_hash": "x"},
        transport_identity={"seed": 1, "top_p": 1.0},
        cache_salt="s",
        previous_context_digest="p",
    )
    response = {"nodes": []}
    with recorder.episode_scope("ns", "ep", 0):
        with session.logical_call("extract_nodes.generate_response"):
            returned = await session.capture_call(identity, lambda: asyncio.sleep(0, result=response), transport_attempts=2)
        with recorder.span("NATIVE", "Graphiti.add_episode"):
            pass
    returned["nodes"].append("local")
    assert response == {"nodes": []}
    envelope = recorder.materialize(source_sequence=0)
    assert len(envelope["spans"]) == 2
    assert {span["phase"] for span in envelope["spans"]} == {"PREPARE", "NATIVE"}
    assert store.summary()["logical_consumed"] == 0
