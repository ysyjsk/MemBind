"""TDD contracts for the isolated MemBind v4 runtime lane."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from paper_eval.membind_v4.admission import (
    AdmissionRequest,
    AdmissionDecision,
    ResourceGatedAdmission,
    RequestKind,
)
from paper_eval.membind_v4.coordinator import run_membind_v4_stream
from paper_eval.membind_v4.resource_profile import (
    Criticality,
    ResourceClass,
    RequestProfile,
    classify_request_profile,
)
from paper_eval.membind_v4.runtime import (
    PreparedNodeResolve,
    ValidatedSpeculationRuntime,
)
from paper_eval.membind_v4.telemetry import V4Telemetry


@dataclass(frozen=True)
class Call:
    source_sequence: int
    state_version: int
    fingerprint: str
    execution_mode: str = "LLM"


def _request(
    request_id: str,
    *,
    kind: RequestKind,
    source_sequence: int,
    resource_class: ResourceClass = ResourceClass.LONG_PREFILL,
    distance: int = 1,
) -> AdmissionRequest:
    profile = RequestProfile(
        request_id=request_id,
        prompt_name="node_resolve",
        prompt_tokens_estimate=10_000,
        expected_output_tokens=100,
        resource_class=resource_class,
        criticality=(
            Criticality.FRONTIER
            if kind is RequestKind.FRONTIER
            else Criticality.BACKGROUND
        ),
        source_sequence=source_sequence,
        state_version=max(source_sequence - 1, 0),
        exact_prefix_tokens=0,
    )
    return AdmissionRequest(
        request_id=request_id,
        kind=kind,
        stream_id="s",
        source_sequence=source_sequence,
        speculation_distance=(distance if kind is RequestKind.SPECULATIVE else 0),
        profile=profile,
    )


def test_profile_classification_is_deterministic_and_content_free() -> None:
    assert classify_request_profile(
        request_id="r",
        prompt_name="node_resolve",
        prompt_tokens=5000,
        expected_output_tokens=100,
        source_sequence=1,
        state_version=0,
        exact_prefix_tokens=12,
        long_decode_cutoff=256,
    ).resource_class is ResourceClass.LONG_PREFILL
    assert classify_request_profile(
        request_id="r2",
        prompt_name="node_resolve",
        prompt_tokens=500,
        expected_output_tokens=100,
        source_sequence=1,
        state_version=0,
        exact_prefix_tokens=12,
    ).resource_class is ResourceClass.SHORT


def test_gate_frontier_first_and_idle_slot_speculation() -> None:
    gate = ResourceGatedAdmission(global_k=2)
    gate.submit(_request("spec", kind=RequestKind.SPECULATIVE, source_sequence=1))
    gate.submit(
        _request(
            "frontier",
            kind=RequestKind.FRONTIER,
            source_sequence=0,
            resource_class=ResourceClass.SHORT,
        )
    )
    admitted = gate.admit_available()
    assert [item.request_id for item in admitted] == ["frontier"]
    # A waiting frontier still blocks speculation; this request has no waiter.
    admitted = gate.admit_available()
    assert [item.request_id for item in admitted] == ["spec"]
    assert gate.observation()["observed_max_inflight"] <= 2


def test_gate_rejects_distance_or_duplicate_source_and_c02_long_pairing() -> None:
    with pytest.raises(ValueError, match="speculation_distance_invalid"):
        ResourceGatedAdmission(global_k=2).submit(
            _request("bad", kind=RequestKind.SPECULATIVE, source_sequence=1, distance=2)
        )
    gate = ResourceGatedAdmission(global_k=2, phase_complementary=True)
    gate.submit(
        _request(
            "frontier",
            kind=RequestKind.FRONTIER,
            source_sequence=0,
            resource_class=ResourceClass.SHORT,
        )
    )
    gate.submit(_request("spec", kind=RequestKind.SPECULATIVE, source_sequence=1))
    assert [x.request_id for x in gate.admit_available()] == ["frontier"]
    # A short frontier can safely pair with the long-prefill speculation.
    assert [x.request_id for x in gate.admit_available()] == ["spec"]
    with pytest.raises(ValueError, match="source_speculation_duplicate"):
        gate.submit(_request("spec2", kind=RequestKind.SPECULATIVE, source_sequence=1))


def test_gate_cancellation_releases_active_permit() -> None:
    gate = ResourceGatedAdmission(global_k=2)
    gate.submit(_request("frontier", kind=RequestKind.FRONTIER, source_sequence=0))
    assert [item.request_id for item in gate.admit_available()] == ["frontier"]
    assert gate.cancel("frontier") == "CANCELLED"
    assert gate.observation()["active_count"] == 0


@pytest.mark.asyncio
async def test_runtime_hit_interprets_and_commits_only_after_validation() -> None:
    calls: list[str] = []

    async def execute(request: object) -> object:
        calls.append(f"execute:{request}")
        return f"response:{request}"

    async def interpret(response: object, call: object) -> object:
        calls.append(f"interpret:{response}")
        return {"call": call.fingerprint, "response": response}

    async def commit(value: object) -> object:
        calls.append("commit")
        return value

    runtime = ValidatedSpeculationRuntime(
        execute=execute, interpret=interpret, commit=commit
    )
    stale = PreparedNodeResolve(Call(1, 0, "same"), "stale")
    exact = PreparedNodeResolve(Call(1, 1, "same"), "exact")
    await runtime.speculate(stale)
    assert calls == ["execute:stale"]
    outcome = await runtime.validate_and_commit(exact)
    assert outcome.status == "HIT"
    assert outcome.exact_execution_performed is False
    assert calls == ["execute:stale", "interpret:response:stale", "commit"]


@pytest.mark.asyncio
async def test_runtime_miss_falls_back_exact_and_cancellation_is_terminal() -> None:
    calls: list[str] = []

    async def execute(request: object) -> object:
        calls.append(str(request))
        return request

    runtime = ValidatedSpeculationRuntime(
        execute=execute,
        interpret=lambda response, call: response,
        commit=lambda value: value,
    )
    await runtime.speculate(PreparedNodeResolve(Call(1, 0, "stale"), "stale"))
    outcome = await runtime.validate_and_commit(
        PreparedNodeResolve(Call(1, 1, "exact"), "exact")
    )
    assert outcome.status == "MISS"
    assert outcome.exact_execution_performed is True
    assert calls == ["stale", "exact"]

    cancelled = ValidatedSpeculationRuntime(
        execute=lambda request: asyncio.sleep(10),
        interpret=lambda response, call: response,
        commit=lambda value: value,
    )
    task = asyncio.create_task(
        cancelled.speculate(PreparedNodeResolve(Call(2, 0, "x"), "x"))
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.state == "CANCELLED"


@pytest.mark.asyncio
async def test_runtime_accepts_adapter_style_prepared_call_executor() -> None:
    async def execute(call: PreparedNodeResolve) -> object:
        if not isinstance(call, PreparedNodeResolve):
            raise ValueError("prepared_call_invalid")
        return call.request

    runtime = ValidatedSpeculationRuntime(
        execute=execute,
        interpret=lambda response, call: response,
        commit=lambda value: value,
    )
    await runtime.speculate(PreparedNodeResolve(Call(1, 0, "same"), "stale"))
    outcome = await runtime.validate_and_commit(
        PreparedNodeResolve(Call(1, 1, "same"), "exact")
    )
    assert outcome.status == "HIT"


@pytest.mark.asyncio
async def test_coordinator_publishes_in_order_and_does_not_commit_speculation() -> None:
    committed: list[int] = []

    class Adapter:
        async def materialize(self, source: object, *, state_version: int) -> PreparedNodeResolve:
            seq = int(source)
            return PreparedNodeResolve(Call(seq, state_version, f"call-{seq}"), f"req-{seq}")

        async def execute(self, request: object) -> object:
            return request

        async def interpret(self, response: object, call: object) -> object:
            return call.source_sequence

        async def commit(self, value: object) -> object:
            committed.append(int(value))
            return value

    result = await run_membind_v4_stream(
        stream_id="s",
        sources=[0, 1, 2],
        adapter=Adapter(),
    )
    assert result["publication_source_sequences"] == [0, 1, 2]
    assert committed == [0, 1, 2]
    assert result["direct_violation_count"] == 0


@pytest.mark.asyncio
async def test_coordinator_runs_speculation_as_background_task_and_records_overlap() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    executed: list[str] = []

    class Adapter:
        async def materialize(self, source: object, *, state_version: int) -> PreparedNodeResolve:
            seq = int(source)
            return PreparedNodeResolve(Call(seq, state_version, f"call-{seq}"), f"req-{seq}")

        async def execute(self, request: object) -> object:
            request_text = str(request)
            executed.append(request_text)
            if request_text == "req-0":
                release.set()
            if request_text == "req-1":
                started.set()
                await release.wait()
            return request

        async def interpret(self, response: object, call: object) -> object:
            return call.source_sequence

        async def commit(self, value: object) -> object:
            return value

    result = await run_membind_v4_stream(
        stream_id="overlap",
        sources=[0, 1, 2],
        adapter=Adapter(),
    )
    assert result["publication_source_sequences"] == [0, 1, 2]
    assert "req-1" in executed
    assert result["telemetry"]["event_counts"].get("speculation_overlap", 0) >= 1


def test_telemetry_rejects_private_content_and_reduces_counts() -> None:
    telemetry = V4Telemetry()
    telemetry.record("speculation_launched", source_sequence=1, request_id="r")
    telemetry.record("semantic_hit", source_sequence=1, request_id="r")
    telemetry.record("publication", source_sequence=1)
    summary = telemetry.summary()
    assert summary["event_count"] == 3
    assert summary["event_counts"]["semantic_hit"] == 1
    with pytest.raises(ValueError, match="private_telemetry_field"):
        telemetry.record("bad", prompt="secret")
