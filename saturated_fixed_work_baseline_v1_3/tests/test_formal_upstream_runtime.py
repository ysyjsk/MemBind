from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (
    FORMAL_ARM_A,
    FORMAL_ARM_B,
    FORMAL_ARM_C,
    P1_DEPLOYMENT_POLICY,
    P2_DEPLOYMENT_POLICY,
    P0_SAMPLING,
    _TransportTelemetry,
    _TransparentEndpointClient,
    build_formal_upstream_runtime,
    deployment_wire_fields,
    formal_runtime_identity,
    resolve_deployment_policy,
    install_logical_llm_context,
    logical_request_context,
    logical_request_seed,
    request_hash,
    strict_formal_runtime_identity_errors,
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


def test_p2_deployment_policy_uses_only_official_qwen3_14b_sampling() -> None:
    assert P2_DEPLOYMENT_POLICY.policy_id == "P2_QWEN3_14B_AWQ"
    assert P2_DEPLOYMENT_POLICY.profile_id == "local-qwen3-14b-awq-dualreplica-v1"
    assert P2_DEPLOYMENT_POLICY.source_model == "Qwen/Qwen3-14B-AWQ"
    assert P2_DEPLOYMENT_POLICY.served_model == "qwen3-14b-awq"
    assert P2_DEPLOYMENT_POLICY.revision == (
        "31c69efc29464b6bb0aee1398b5a7b50a99340c3"
    )
    assert dict(P2_DEPLOYMENT_POLICY.sampling) == {
        "enable_thinking": False,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0,
        "presence_penalty": 1.5,
        "structured_output_backend": "xgrammar",
    }
    assert deployment_wire_fields(P2_DEPLOYMENT_POLICY, seed=123) == {
        "temperature": 0.7,
        "top_p": 0.8,
        "seed": 123,
        "presence_penalty": 1.5,
        "extra_body": {
            "top_k": 20,
            "min_p": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    }


def test_p2_declared_transport_fields_match_wire_fields_exactly() -> None:
    wire = deployment_wire_fields(P2_DEPLOYMENT_POLICY, seed=123)
    actual = {
        "temperature",
        "top_p",
        "seed",
        "presence_penalty",
        "extra_body.top_k",
        "extra_body.min_p",
        "extra_body.chat_template_kwargs.enable_thinking",
    }
    assert set(P2_DEPLOYMENT_POLICY.transport_only_fields) == actual
    assert set(P2_DEPLOYMENT_POLICY.transport_only_fields) == {
        "temperature",
        "top_p",
        "seed",
        "presence_penalty",
        "extra_body.top_k",
        "extra_body.min_p",
        "extra_body.chat_template_kwargs.enable_thinking",
    }
    assert wire["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


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
    assert FORMAL_ARM_C == "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192"
    assert FORMAL_ARM_B == "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192"


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


def test_formal_builder_source_excludes_compatibility_algorithm_patches() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "saturated_fixed_work_baseline_v1_3"
        / "membind_v6_1"
        / "upstream_runtime.py"
    ).read_text(encoding="utf-8")
    builder = source[source.index("def build_formal_upstream_runtime(") :]
    builder = builder[: builder.index("async def close_formal_upstream_runtime")]
    prohibited = (
        "install_local_extraction_chunking_policy",
        "partition_extraction_by_turns",
        "partition_edge_candidates",
        "edge_duplicate_recovery",
        "edge_endpoint_schema_grounding",
    )
    assert all(value not in builder for value in prohibited)


def test_runtime_identity_validator_rejects_forged_upstream_callable() -> None:
    identity = {
        "schema_version": "membind.formal-runtime-identity.v1",
        "status": "PASS",
        "arm": FORMAL_ARM_A,
        "strict_upstream_core": True,
        "graphiti": {
            "version": "0.29.3",
            "installed_version": "0.29.3",
            "commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
            "class_module": "graphiti_core.graphiti",
            "class_qualname": "Graphiti",
            "add_episode_module": "local.compatibility",
            "add_episode_qualname": "Graphiti.add_episode",
        },
        "llm_client_class": (
            "graphiti_core.llm_client.openai_generic_client.OpenAIGenericClient"
        ),
        "edge_response_model": {
            "module": "graphiti_core.prompts.extract_edges",
            "qualname": "ExtractedEdges",
            "schema_sha256": "e" * 64,
            "schema": {"properties": {"edges": {"type": "array"}}},
            "edges_has_max_items": False,
        },
        "upstream_prompt_source_sha256": {
            "extract_nodes": "n" * 64,
            "extract_edges": "e" * 64,
        },
        "deployment_policy_id": P1_DEPLOYMENT_POLICY.policy_id,
        "model": P1_DEPLOYMENT_POLICY.served_model,
        "model_revision": P1_DEPLOYMENT_POLICY.revision,
        "sampling": dict(P1_DEPLOYMENT_POLICY.sampling),
        "max_tokens": 16384,
        "structured_output_mode": "json_schema",
        "logical_seed_policy": (
            "uint32_sha256_dataset_context_source_chunk_prompt_messages"
        ),
        "sdk_retries": 0,
        "mab8192_manifest_sha256": "m" * 64,
        "extraction_chunking_installed": False,
        "finite_pair_tasks_enabled": False,
        "response_repair_enabled": False,
        "patch_inventory": {
            "strict_upstream_core": True,
            "graphiti_algorithm_mutated": False,
            "shared_compatibility_substrate": False,
            "algorithm_patches": [],
            "prohibited_algorithm_patches": [],
            "deployment_policy_id": P1_DEPLOYMENT_POLICY.policy_id,
        },
    }
    identity["runtime_identity_sha256"] = request_hash(identity)

    errors = strict_formal_runtime_identity_errors(
        identity,
        expected_arm=FORMAL_ARM_A,
        expected_manifest_sha256="m" * 64,
        expected_deployment_policy=P1_DEPLOYMENT_POLICY,
    )

    assert "graphiti.add_episode identity mismatch" in errors


@pytest.mark.asyncio
async def test_instantiated_formal_arms_share_upstream_prompt_schema_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import graphiti_core
    import graphiti_core.cross_encoder.openai_reranker_client as reranker_module
    import graphiti_core.embedder.openai as embedder_module
    import graphiti_core.llm_client.openai_generic_client as llm_module
    from graphiti_core.prompts.extract_edges import ExtractedEdges, edge
    from saturated_fixed_work_baseline_v1_3.membind_v6_1 import upstream_runtime

    intercepted: list[dict[str, object]] = []

    class FakeLLM:
        def __init__(self, *, config, client, max_tokens, structured_output_mode):
            self.config = config
            self.client = client
            self.max_tokens = max_tokens
            self.structured_output_mode = structured_output_mode
            self._generate_response = self._call
            self._generate_response_with_retry = self._call

        async def _call(self, messages, **kwargs):
            intercepted.append({"messages": messages, **kwargs})
            return {"edges": []}

        async def generate_response(self, messages, **kwargs):
            return await self._generate_response_with_retry(messages, **kwargs)

    class FakeEmbedderConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeEmbedder:
        def __init__(self, config, client):
            self.config = config
            self.client = client

    class FakeReranker:
        def __init__(self, *, config, client):
            self.config = config
            self.client = client

    class FakeGraphiti:
        def __init__(self, *, llm_client, embedder, cross_encoder, max_coroutines, **_kwargs):
            self.llm_client = llm_client
            self.clients = SimpleNamespace(llm_client=llm_client)
            self.embedder = embedder
            self.cross_encoder = cross_encoder
            self.max_coroutines = max_coroutines

        async def add_episode(self, **_kwargs):
            return None

    FakeLLM.__module__ = "graphiti_core.llm_client.openai_generic_client"
    FakeLLM.__qualname__ = "OpenAIGenericClient"
    FakeGraphiti.__module__ = "graphiti_core.graphiti"
    FakeGraphiti.__qualname__ = "Graphiti"
    FakeGraphiti.add_episode.__module__ = "graphiti_core.graphiti"
    FakeGraphiti.add_episode.__qualname__ = "Graphiti.add_episode"

    monkeypatch.setattr(graphiti_core, "Graphiti", FakeGraphiti)
    monkeypatch.setattr(llm_module, "OpenAIGenericClient", FakeLLM)
    monkeypatch.setattr(embedder_module, "OpenAIEmbedderConfig", FakeEmbedderConfig)
    monkeypatch.setattr(embedder_module, "OpenAIEmbedder", FakeEmbedder)
    monkeypatch.setattr(reranker_module, "OpenAIRerankerClient", FakeReranker)
    monkeypatch.setattr(
        upstream_runtime,
        "build_local_openai_transport",
        lambda **_kwargs: SimpleNamespace(close=lambda: None),
    )
    environment = {
        "MEMBIND_DEPLOYMENT_POLICY_ID": "P1_QWEN25_7B_AWQ",
        "MEMBIND_PROFILE_ID": "local-qwen25-7b-awq-dualreplica-v1",
        "MEMBIND_LLM_MODEL_NAME": "qwen2.5-7b-instruct-awq",
        "MEMBIND_LLM_MODEL_REVISION": P1_DEPLOYMENT_POLICY.revision,
        "CONSTRUCTION_LLM_API_KEY": "test",
        "CONSTRUCTION_MODEL_REVISION": P1_DEPLOYMENT_POLICY.revision,
        "CONSTRUCTION_MAX_TOKENS": "16384",
        "CONSTRUCTION_MIN_CONTEXT_TOKENS": "65536",
        "CONSTRUCTION_CONTEXT_SAFETY_TOKENS": "256",
        "EMBEDDING_API_KEY": "test",
        "EMBEDDING_BASE_URL": "http://127.0.0.1:18202/v1",
        "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
        "EMBEDDING_DIM": "1024",
        "NEO4J_URI": "bolt://127.0.0.1:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "test",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    route = {
        "router": {"policy": "capacity_weighted_least_outstanding"},
        "endpoint_set": [
            {
                "id": "native-replica",
                "base_url": "http://127.0.0.1:18200/v1",
                "served_model": P1_DEPLOYMENT_POLICY.served_model,
                "physical_gpu": 0,
            },
            {
                "id": "prepare-replica",
                "base_url": "http://127.0.0.1:18201/v1",
                "served_model": P1_DEPLOYMENT_POLICY.served_model,
                "physical_gpu": 1,
            },
        ],
    }
    messages = edge(
        {
            "previous_episodes": [],
            "episode_content": "Alice works with Bob.",
            "nodes": [{"name": "Alice"}, {"name": "Bob"}],
            "reference_time": "2026-01-01T00:00:00Z",
            "edge_types": {},
            "custom_extraction_instructions": "",
        }
    )
    expected_prompt_sha256 = hashlib.sha256(
        json.dumps(
            [{"role": item.role, "content": item.content} for item in messages],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    identities = []
    for arm in (FORMAL_ARM_A, FORMAL_ARM_C, FORMAL_ARM_B):
        runtime = build_formal_upstream_runtime(routing_contract=route, arm=arm)
        identity = formal_runtime_identity(
            runtime, mab8192_manifest_sha256="m" * 64
        )
        identities.append(identity)
        with logical_request_context(_chunk_identity()):
            assert await runtime.llm_client.generate_response(
                messages,
                response_model=ExtractedEdges,
                max_tokens=16384,
                group_id="namespace",
                prompt_name="extract_edges.edge",
            ) == {"edges": []}

    assert len(intercepted) == 3
    assert all(row["response_model"] is ExtractedEdges for row in intercepted)
    assert all(row["max_tokens"] == 16384 for row in intercepted)
    for row in intercepted:
        payload = [{"role": item.role, "content": item.content} for item in row["messages"]]
        assert hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest() == expected_prompt_sha256
    comparable = {
        key
        for key in identities[0]
        if key not in {"arm", "runtime_identity_sha256"}
    }
    assert all(
        {key: identity[key] for key in comparable}
        == {key: identities[0][key] for key in comparable}
        for identity in identities[1:]
    )
    assert all(identity["strict_upstream_core"] is True for identity in identities)
    assert all(identity["finite_pair_tasks_enabled"] is False for identity in identities)
    assert all(identity["response_repair_enabled"] is False for identity in identities)
    assert all(identity["extraction_chunking_installed"] is False for identity in identities)
    edge_schema = identities[0]["edge_response_model"]["schema"]
    assert "maxItems" not in edge_schema["properties"]["edges"]
    assert not any(
        marker in json.dumps(edge_schema, sort_keys=True).casefold()
        for marker in ("pairs_completed", "finite-pair-task", "cursor", "terminal")
    )
