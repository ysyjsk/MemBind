"""TDD for the isolated async validated-NodeResolve runtime prototype."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from paper_eval.membind_v31.node_resolve_speculation import SemanticCall
from paper_eval.membind_v31.validated_node_resolve_runtime import (
    ValidatedNodeResolveError,
    NodeResolvePrepared,
    ValidatedNodeResolveRuntime,
)


def _call(*, state_version: int, order: tuple[str, ...] = ("c0", "c1")) -> SemanticCall:
    bindings = {
        "c0": {"candidate_id": "c0", "uuid": "u0", "projection": {"name": "Alice"}},
        "c1": {"candidate_id": "c1", "uuid": "u1", "projection": {"name": "Bob"}},
    }
    return SemanticCall.create(
        source_sequence=0,
        state_version=state_version,
        rendered_request_sha256="a" * 64,
        token_sequence_sha256="b" * 64,
        response_schema={"type": "object"},
        model_identity={"model": "qwen3-32b-fp8"},
        decoding_identity={"temperature": 0, "top_p": 1, "seed": 7},
        operator_revision="graphiti-0.29.3-node-resolve-v1",
        candidate_order=order,
        candidate_bindings=[bindings[item] for item in order],
        extracted_node_mapping={"0": "c0", "1": "c1"},
        binding_context={"entity_types": ["Person"]},
        prompt_tokens=100,
        service_span_ns=1_000,
    )


def _prepared(state_version: int, request: str, *, order: tuple[str, ...] = ("c0", "c1")) -> NodeResolvePrepared:
    return NodeResolvePrepared(call=_call(state_version=state_version, order=order), request=request)


def test_hit_executes_speculation_once_and_commits_only_after_validation() -> None:
    events: list[str] = []

    async def execute(request: object) -> object:
        events.append(f"execute:{request}")
        return {"response_for": request}

    async def interpret(response: object, call: SemanticCall) -> object:
        events.append(f"interpret:{call.state_version}")
        return {"interpreted": response}

    async def commit(value: object) -> object:
        events.append("commit")
        return value

    runtime = ValidatedNodeResolveRuntime(
        execute=execute,
        interpret=interpret,
        commit=commit,
    )

    asyncio.run(runtime.speculate(_prepared(0, "stale")))
    assert events == ["execute:stale"]
    outcome = asyncio.run(runtime.validate_and_commit(_prepared(1, "exact")))

    assert outcome.status == "REUSED"
    assert events == ["execute:stale", "interpret:1", "commit"]
    assert outcome.exact_execution_performed is False


def test_miss_discards_speculative_response_and_executes_exact_request() -> None:
    events: list[str] = []

    async def execute(request: object) -> object:
        events.append(f"execute:{request}")
        return request

    async def interpret(response: object, call: SemanticCall) -> object:
        events.append(f"interpret:{response}:{call.state_version}")
        return response

    async def commit(value: object) -> object:
        events.append(f"commit:{value}")
        return value

    runtime = ValidatedNodeResolveRuntime(execute=execute, interpret=interpret, commit=commit)
    asyncio.run(runtime.speculate(_prepared(0, "stale")))
    outcome = asyncio.run(runtime.validate_and_commit(_prepared(1, "exact", order=("c1", "c0"))))

    assert outcome.status == "FALLBACK_EXACT"
    assert outcome.exact_execution_performed is True
    assert events == ["execute:stale", "execute:exact", "interpret:exact:1", "commit:exact"]


def test_runtime_rejects_duplicate_speculation_and_state_regression() -> None:
    async def execute(request: object) -> object:
        return request

    async def interpret(response: object, call: SemanticCall) -> object:
        return response

    async def commit(value: object) -> object:
        return value

    runtime = ValidatedNodeResolveRuntime(execute=execute, interpret=interpret, commit=commit)
    asyncio.run(runtime.speculate(_prepared(1, "stale")))
    with pytest.raises(ValidatedNodeResolveError, match="speculation_already_present"):
        asyncio.run(runtime.speculate(_prepared(1, "again")))
    with pytest.raises(ValidatedNodeResolveError, match="state_order_invalid"):
        asyncio.run(runtime.validate_and_commit(_prepared(1, "older")))


def test_execute_failure_never_reaches_interpret_or_commit() -> None:
    events: list[str] = []

    async def execute(request: object) -> object:
        raise RuntimeError("provider failure")

    async def interpret(response: object, call: SemanticCall) -> object:
        events.append("interpret")
        return response

    async def commit(value: object) -> object:
        events.append("commit")
        return value

    runtime = ValidatedNodeResolveRuntime(execute=execute, interpret=interpret, commit=commit)
    with pytest.raises(RuntimeError, match="provider failure"):
        asyncio.run(runtime.speculate(_prepared(0, "stale")))
    assert events == []
