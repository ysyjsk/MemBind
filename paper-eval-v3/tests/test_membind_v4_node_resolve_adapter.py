"""Offline adapter boundary and factorized-serial parity tests for v4."""

from __future__ import annotations

import asyncio

import pytest

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.node_resolve_adapter import (
    ExactNodeResolveResult,
    NodeResolveAdapterError,
    NodeResolveV4Adapter,
    PreparedSemanticCall,
    assert_serial_factorized_parity,
)
from paper_eval.membind_v4.semantic_call import SemanticCall


def _sha(index: int) -> str:
    return f"{index:064x}"


def _prepared() -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=2,
        source_sha256=_sha(1),
        evidence_sha256=_sha(2),
        certification_sha256=_sha(3),
        raw_nodes=({"uuid": "raw-a", "name": "Ada"},),
        pure_intermediates={"episode": "e2"},
    )


def _call(state_version: int = 1) -> SemanticCall:
    return SemanticCall.create(
        source_sequence=2,
        state_version=state_version,
        operator_identity={"graphiti_version": "0.29.3", "adapter": "v4-node-v1"},
        model_identity={"model": "qwen3-32b-fp8"},
        decoding_identity={"temperature": 0, "max_tokens": 16384},
        response_schema={"type": "object"},
        rendered_request_sha256=_sha(4),
        token_sequence_sha256=_sha(5),
        prompt_tokens=100,
        extracted_nodes=({"runtime_uuid": "raw-a", "name": "Ada"},),
        candidate_order=("c0",),
        candidate_bindings=({"candidate_id": "c0", "uuid": "u0", "projection": {"name": "Ada"}},),
        previous_episodes=({"sequence": 1, "timestamp": 1},),
        episode_context={"episode": "e2"},
        entity_types=("Entity",),
        execution_mode="LLM",
        operator_revision="node-resolve-v4-1",
    )


def test_materialize_is_read_only_and_returns_prepared_semantic_call() -> None:
    writes: list[object] = []

    async def materialize(compile_input, prepared, state_version):
        assert prepared.verify() is prepared
        assert compile_input == {"read_only": True}
        return PreparedSemanticCall(call=_call(state_version), request={"state": state_version})

    adapter = NodeResolveV4Adapter(materialize_request=materialize)
    result = asyncio.run(adapter.materialize({"read_only": True}, _prepared(), state_version=1))
    assert result.call.source_sequence == 2
    assert result.request == {"state": 1}
    assert writes == []


def test_materialize_rejects_source_or_state_mismatch_before_live_work() -> None:
    called: list[bool] = []

    async def materialize(*_args):
        called.append(True)
        return PreparedSemanticCall(call=_call(state_version=3), request={})

    adapter = NodeResolveV4Adapter(materialize_request=materialize)
    with pytest.raises(NodeResolveAdapterError, match="state_version_mismatch"):
        asyncio.run(adapter.materialize({}, _prepared(), state_version=1))
    assert called == [True]


def test_speculative_response_cannot_be_interpreted_or_committed_without_exact_validation() -> None:
    interpreted: list[object] = []
    committed: list[object] = []

    async def execute(request):
        return {"response_for": request["state"]}

    async def interpret(response, exact_call):
        interpreted.append((response, exact_call.call.state_version))
        return {"uuid": "u0"}

    async def continue_bind(*args):
        committed.append(args)
        return "published"

    adapter = NodeResolveV4Adapter(
        materialize_request=lambda *_: PreparedSemanticCall(call=_call(1), request={"state": 1}),
        execute_request=execute,
        interpret_response=interpret,
        continue_native_bind=continue_bind,
    )
    stale = PreparedSemanticCall(call=_call(1), request={"state": 1})
    exact = PreparedSemanticCall(call=_call(2), request={"state": 2})
    response = asyncio.run(adapter.execute(stale))
    with pytest.raises(NodeResolveAdapterError, match="speculative_response_unvalidated"):
        asyncio.run(adapter.interpret(response, exact))
    assert interpreted == []
    assert committed == []

    result = asyncio.run(adapter.validate_and_interpret(response, stale, exact))
    assert isinstance(result, ExactNodeResolveResult)
    assert interpreted == [({"response_for": 1}, 2)]
    assert asyncio.run(adapter.continue_native_bind({}, _prepared(), result, logical_time_ns=4)) == "published"
    assert len(committed) == 1


def test_provider_failure_never_enters_interpret_or_bind() -> None:
    called = {"interpret": 0, "bind": 0}

    async def execute(_request):
        raise RuntimeError("transport")

    async def interpret(*_args):
        called["interpret"] += 1

    async def bind(*_args):
        called["bind"] += 1

    adapter = NodeResolveV4Adapter(execute_request=execute, interpret_response=interpret, continue_native_bind=bind)
    with pytest.raises(RuntimeError, match="transport"):
        asyncio.run(adapter.execute(PreparedSemanticCall(call=_call(), request={})))
    assert called == {"interpret": 0, "bind": 0}


def test_serial_factorized_request_identity_matches_native_fixture() -> None:
    prepared = _prepared()
    assert assert_serial_factorized_parity(
        native_materialize=lambda _input, _prepared, _version: PreparedSemanticCall(call=_call(_version), request={"state": _version}),
        adapter_materialize=lambda _input, _prepared, _version: PreparedSemanticCall(call=_call(_version), request={"state": _version}),
        compile_input={"source": 2},
        prepared=prepared,
        state_version=1,
    )["semantic_call_fingerprint_equal"] is True
