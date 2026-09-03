from __future__ import annotations

from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (
    FORMAL_ARM_A,
    P0_SAMPLING,
    _TransportTelemetry,
    _TransparentEndpointClient,
    install_logical_llm_context,
    logical_request_context,
    logical_request_seed,
    request_hash,
)


def _identity() -> dict[str, object]:
    return {
        "dataset_revision": "dataset@r1",
        "context_id": "ctx-0",
        "source_sequence": 3,
        "chunk_ordinal": 1,
        "prompt_name": "extract_edges.edge",
        "canonical_messages_hash": "a" * 64,
    }


def _chunk_identity() -> dict[str, object]:
    value = _identity()
    value.pop("prompt_name")
    value.pop("canonical_messages_hash")
    return value


def test_logical_seed_is_stable_and_physical_order_independent() -> None:
    first = logical_request_seed(_identity())
    second = logical_request_seed(dict(reversed(list(_identity().items()))))
    assert first == second
    assert 0 <= first <= 2**32 - 1


@pytest.mark.asyncio
async def test_transparent_transport_adds_only_sampling_and_seed() -> None:
    calls: list[dict[str, object]] = []

    class Completions:
        async def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(ok=True)

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    telemetry = _TransportTelemetry([])
    endpoint = _TransparentEndpointClient(client, endpoint_id="native-replica", telemetry=telemetry)
    wire_messages = [
        {"role": "user", "content": "hello"},
        {"role": "user", "content": "Respond in the same language as the input."},
    ]
    with logical_request_context(_chunk_identity() | {"prompt_name": "extract_edges.edge"}):
        result = await endpoint.create(
            model="qwen3-8b-awq",
            messages=wire_messages,
            response_format={"type": "json_schema"},
        )
    assert result.ok is True
    assert len(calls) == 1
    wire = calls[0]
    assert wire["temperature"] == P0_SAMPLING["temperature"]
    assert wire["top_p"] == P0_SAMPLING["top_p"]
    assert wire["presence_penalty"] == P0_SAMPLING["presence_penalty"]
    wire_identity = _chunk_identity() | {
        "prompt_name": "extract_edges.edge",
        "canonical_messages_hash": request_hash({"messages": wire_messages}),
    }
    assert wire["seed"] == logical_request_seed(wire_identity)
    assert wire["extra_body"] == {
        "top_k": 20,
        "min_p": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert telemetry.rows[0]["logical_identity"]["context_id"] == "ctx-0"
    assert telemetry.rows[0]["logical_identity"] == wire_identity
    assert telemetry.rows[0]["semantic_request_sha256"] == request_hash(
        {
            "model": "qwen3-8b-awq",
            "messages": wire_messages,
            "max_tokens": None,
            "response_format": {"type": "json_schema"},
        }
    )


@pytest.mark.asyncio
async def test_transparent_transport_fails_closed_without_task_identity() -> None:
    class Completions:
        async def create(self, **_kwargs: object) -> object:
            return SimpleNamespace()

    endpoint = _TransparentEndpointClient(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        endpoint_id="native-replica",
        telemetry=_TransportTelemetry([]),
    )
    with pytest.raises(RuntimeError, match="logical request identity"):
        await endpoint.create(model="x", messages=[])


@pytest.mark.asyncio
async def test_transparent_transport_rejects_conflicting_sampling() -> None:
    class Completions:
        async def create(self, **_kwargs: object) -> object:
            raise AssertionError("conflicting request must not reach transport")

    endpoint = _TransparentEndpointClient(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        endpoint_id="native-replica",
        telemetry=_TransportTelemetry([]),
    )
    with logical_request_context(_chunk_identity() | {"prompt_name": "extract_edges.edge"}):
        with pytest.raises(RuntimeError, match="temperature"):
            await endpoint.create(model="x", messages=[], temperature=0)


@pytest.mark.asyncio
async def test_llm_context_completes_prompt_identity_from_task_creation() -> None:
    observed: list[dict[str, object] | None] = []

    class Client:
        async def generate_response(self, _messages: object, **_kwargs: object) -> object:
            from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (
                current_logical_request_identity,
            )

            observed.append(current_logical_request_identity())
            return {"ok": True}

    client = Client()
    restore = install_logical_llm_context(client)
    messages = [SimpleNamespace(role="user", content="hello")]
    with logical_request_context(_chunk_identity()):
        assert await client.generate_response(messages, prompt_name="extract_nodes.extract_message") == {"ok": True}
    restore()
    assert observed[0] is not None
    assert observed[0]["prompt_name"] == "extract_nodes.extract_message"
    assert "canonical_messages_hash" not in observed[0]


def test_formal_arm_name_is_explicit() -> None:
    assert FORMAL_ARM_A == "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192"
