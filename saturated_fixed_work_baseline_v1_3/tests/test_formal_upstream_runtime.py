from __future__ import annotations

from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (
    FORMAL_ARM_A,
    FORMAL_ARM_B,
    FORMAL_ARM_C,
    P1_DEPLOYMENT_POLICY,
    P0_SAMPLING,
    _TransportTelemetry,
    _TransparentEndpointClient,
    resolve_deployment_policy,
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
async def test_transport_telemetry_classifies_malformed_success_without_repair() -> None:
    malformed = '{"edges":[{"fact":"truncated"}'

    class Completions:
        async def create(self, **_kwargs: object) -> object:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=malformed),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=321,
                    completion_tokens=123,
                    total_tokens=444,
                ),
            )

    telemetry = _TransportTelemetry([])
    endpoint = _TransparentEndpointClient(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        endpoint_id="prepare-replica",
        telemetry=telemetry,
    )
    with logical_request_context(
        _chunk_identity() | {"prompt_name": "extract_edges.edge"}
    ):
        response = await endpoint.create(
            model="qwen3-8b-awq",
            messages=[{"role": "user", "content": "edge request"}],
            max_tokens=16384,
            response_format={"type": "json_schema"},
        )

    assert response.choices[0].message.content == malformed
    row = telemetry.rows[0]
    assert row["finish_reason"] == "stop"
    assert row["response_characters"] == len(malformed)
    assert row["response_json_valid"] is False
    assert row["response_json_error"].startswith("Expecting ',' delimiter")
    assert row["usage"] == {
        "prompt_tokens": 321,
        "completion_tokens": 123,
        "total_tokens": 444,
    }


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
async def test_p1_transport_uses_only_official_qwen25_sampling_fields() -> None:
    calls: list[dict[str, object]] = []

    class Completions:
        async def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(ok=True)

    endpoint = _TransparentEndpointClient(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        endpoint_id="native-replica",
        telemetry=_TransportTelemetry([]),
        deployment_policy=P1_DEPLOYMENT_POLICY,
    )
    with logical_request_context(
        _chunk_identity() | {"prompt_name": "extract_nodes.extract_message"}
    ):
        await endpoint.create(
            model="qwen2.5-7b-instruct-awq",
            messages=[{"role": "user", "content": "extract"}],
            response_format={"type": "json_schema"},
        )

    wire = calls[0]
    assert wire["temperature"] == 0.7
    assert wire["top_p"] == 0.8
    assert wire["extra_body"] == {
        "top_k": 20,
        "repetition_penalty": 1.05,
    }
    assert "presence_penalty" not in wire
    assert "min_p" not in wire["extra_body"]
    assert "chat_template_kwargs" not in wire["extra_body"]


def test_deployment_policy_rejects_profile_model_mismatch() -> None:
    with pytest.raises(RuntimeError, match="model identity"):
        resolve_deployment_policy(
            {
                "MEMBIND_DEPLOYMENT_POLICY_ID": "P1_QWEN25_7B_AWQ",
                "MEMBIND_PROFILE_ID": "local-qwen25-7b-awq-dualreplica-v1",
                "MEMBIND_LLM_MODEL_NAME": "qwen3-8b-awq",
            }
        )


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
    assert FORMAL_ARM_A == "GRAPHITI_SERIAL_SHARED_BOUNDED_SO"


def test_all_arms_share_logical_seed_identity() -> None:
    seeds = {
        logical_request_seed({**_identity(), "arm": arm})
        for arm in (FORMAL_ARM_A, FORMAL_ARM_C, FORMAL_ARM_B)
    }
    assert len(seeds) == 1


def test_upstream_extracted_edges_has_no_pair_or_relation_cap() -> None:
    from graphiti_core.prompts.extract_edges import ExtractedEdges

    edges = [
        {
            "source_entity_name": "Alice",
            "target_entity_name": "Bob",
            "relation_type": relation,
            "fact": fact,
            "episode_indices": [0],
        }
        for relation, fact in (
            ("WORKS_WITH", "Alice works with Bob"),
            ("LIVES_NEAR", "Alice lives near Bob"),
            ("MENTORS", "Alice mentors Bob"),
        )
    ]
    parsed = ExtractedEdges(edges=edges)
    assert len(parsed.edges) == 3
    edge_array = ExtractedEdges.model_json_schema()["properties"]["edges"]
    assert "maxItems" not in edge_array


def test_upstream_prompt_accepts_46_entities_without_pair_enumeration() -> None:
    from graphiti_core.prompts.extract_edges import edge

    nodes = [{"name": f"Entity-{index:02d}"} for index in range(46)]
    messages = edge(
        {
            "previous_episodes": [],
            "episode_content": "Entity-00 works with Entity-45.",
            "nodes": nodes,
            "reference_time": "2026-01-01T00:00:00Z",
            "edge_types": {},
            "custom_extraction_instructions": "",
        }
    )
    rendered = "\n".join(message.content for message in messages)
    assert "Entity-00" in rendered
    assert "Entity-45" in rendered
    assert "pairs_completed" not in rendered
    assert "pair-task" not in rendered.casefold()
