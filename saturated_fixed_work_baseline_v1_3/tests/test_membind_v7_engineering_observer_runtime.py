from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient

from saturated_fixed_work_baseline_v1_3.membind_v7.engineering_observer_runtime import (
    BailianChatCompletions,
    CompositeEngineeringError,
    CompositeRuntimeComponents,
    ExactDimensionEmbedder,
    ResponseModelValidatingLLMClient,
    build_composite_engineering_runtime,
    build_engineering_observer_artifact,
    load_composite_engineering_freeze,
    normalize_bailian_chat_request,
    summarize_provider_observations,
    verify_source_bindings,
)


def _freeze_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "v7/BAILIAN_SILICONFLOW_ENGINEERING_OBSERVER_FREEZE.json"
    )


class _StructuredPayload(BaseModel):
    value: int


class _CompletionEndpoint:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _completion_response(
    content: str = '{"value": 7}', *, finish_reason: str = "stop"
) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=5,
            total_tokens=16,
        ),
    )


def test_composite_freeze_binds_two_distinct_provider_authorities() -> None:
    frozen = load_composite_engineering_freeze(_freeze_path())

    assert frozen["provider_identity_kind"] == "COMPOSITE_ENGINEERING_ONLY"
    assert frozen["construction"]["authority"] == (
        "alibaba-bailian-openai-compatible-engineering-json-object-v1"
    )
    assert frozen["construction"]["model"] == "qwen3.5-35b-a3b"
    assert frozen["embedding"]["authority"] == "siliconflow-openai-compatible-v1"
    assert frozen["embedding"]["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert frozen["embedding"]["dimension"] == 1024
    assert frozen["workload"]["local_file_sha256"] == (
        "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8"
    )
    assert frozen["workload"]["context_index"] == 0
    assert frozen["workload"]["source_count"] == 2
    assert frozen["observer_only"] is True
    assert frozen["formal_r1_r3_eligible"] is False
    assert frozen["gate_a_e_evaluated"] is False
    assert frozen["gate_outcome"] == "NOT_EVALUATED"
    assert frozen["treatment_authorized"] is False
    assert frozen["scientific_method_selection_update_allowed"] is False


def test_composite_freeze_rejects_hash_or_authority_drift(tmp_path: Path) -> None:
    frozen = json.loads(_freeze_path().read_text(encoding="ascii"))
    frozen["embedding"]["authority"] = frozen["construction"]["authority"]
    path = tmp_path / "provider-drift.json"
    path.write_text(json.dumps(frozen), encoding="ascii")

    with pytest.raises(CompositeEngineeringError, match="drifted"):
        load_composite_engineering_freeze(path, verify_references=False)

    frozen = json.loads(_freeze_path().read_text(encoding="ascii"))
    frozen["construction"]["provider_freeze_sha256"] = "0" * 64
    path.write_text(json.dumps(frozen), encoding="ascii")
    with pytest.raises(CompositeEngineeringError, match="drifted"):
        load_composite_engineering_freeze(path, verify_references=False)


def test_bailian_structured_request_is_normalized_at_http_boundary() -> None:
    normalized = normalize_bailian_chat_request(
        {
            "model": "qwen3.5-35b-a3b",
            "messages": [{"role": "user", "content": "schema is already injected"}],
            "temperature": 0.0,
            "max_tokens": 16_384,
            "response_format": {"type": "json_object"},
        }
    )

    assert normalized["model"] == "qwen3.5-35b-a3b"
    assert normalized["response_format"] == {"type": "json_object"}
    assert normalized["top_p"] == 1.0
    assert normalized["extra_body"] == {"enable_thinking": False}
    assert "max_tokens" not in normalized

    with pytest.raises(CompositeEngineeringError, match="JSON Object"):
        normalize_bailian_chat_request(
            {
                "model": "qwen3.5-35b-a3b",
                "response_format": {"type": "json_schema"},
            }
        )
    with pytest.raises(CompositeEngineeringError, match="model"):
        normalize_bailian_chat_request(
            {
                "model": "different-model",
                "response_format": {"type": "json_object"},
            }
        )


@pytest.mark.asyncio
async def test_bailian_transport_is_single_attempt_and_fails_closed_on_length() -> None:
    observations: list[dict[str, object]] = []
    inner = _CompletionEndpoint(_completion_response())
    transport = BailianChatCompletions(inner, response_observer=observations.append)

    response = await transport.create(
        model="qwen3.5-35b-a3b",
        messages=[{"role": "user", "content": "schema is already injected"}],
        temperature=0.0,
        max_tokens=16_384,
        response_format={"type": "json_object"},
    )

    assert response.choices[0].finish_reason == "stop"
    assert len(inner.calls) == 1
    assert "max_tokens" not in inner.calls[0]
    assert observations == [
        {
            "lane": "construction",
            "structured": True,
            "finish_reason": "stop",
            "prompt_tokens": 11,
            "completion_tokens": 5,
            "content_bytes": 12,
            "content_sha256": hashlib.sha256(b'{"value": 7}').hexdigest(),
        }
    ]

    length_inner = _CompletionEndpoint(
        _completion_response('{"value": 7}', finish_reason="length")
    )
    with pytest.raises(CompositeEngineeringError, match="finish reason"):
        await BailianChatCompletions(length_inner).create(
            model="qwen3.5-35b-a3b",
            messages=[{"role": "user", "content": "schema is already injected"}],
            response_format={"type": "json_object"},
            max_tokens=16_384,
        )
    assert len(length_inner.calls) == 1


@pytest.mark.asyncio
async def test_graphiti_response_model_is_validated_before_use() -> None:
    class Inner:
        def __init__(self, value: dict[str, object]) -> None:
            self.value = value
            self.calls = 0

        async def generate_response(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.calls += 1
            return dict(self.value)

    valid_inner = Inner({"value": 7})
    valid = ResponseModelValidatingLLMClient(valid_inner)
    assert await valid.generate_response([], response_model=_StructuredPayload) == {
        "value": 7
    }
    assert valid_inner.calls == 1

    invalid_inner = Inner({"unexpected": 7})
    invalid = ResponseModelValidatingLLMClient(invalid_inner)
    with pytest.raises(CompositeEngineeringError, match="Pydantic"):
        await invalid.generate_response([], response_model=_StructuredPayload)
    assert invalid_inner.calls == 1


class _EmbeddingEndpoint:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=vector)
                for index, vector in enumerate(self.vectors)
            ]
        )


def test_runtime_wrappers_satisfy_graphiti_client_nominal_types() -> None:
    class InnerLLM:
        config = SimpleNamespace(
            api_key=None,
            model="qwen3.5-35b-a3b",
            small_model="qwen3.5-35b-a3b",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=0.0,
            max_tokens=16_384,
        )

        async def generate_response(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return {"value": 7}

        def set_tracer(self, tracer: object) -> None:
            self.tracer = tracer

    llm = ResponseModelValidatingLLMClient(InnerLLM())
    embedder = ExactDimensionEmbedder(
        _EmbeddingEndpoint([[0.0] * 1024]),
        model="Qwen/Qwen3-Embedding-0.6B",
        dimension=1024,
    )

    assert isinstance(llm, LLMClient)
    assert isinstance(embedder, EmbedderClient)


@pytest.mark.asyncio
async def test_siliconflow_embedder_requires_exact_dimension_without_truncation() -> None:
    endpoint = _EmbeddingEndpoint([[0.0] * 1024])
    embedder = ExactDimensionEmbedder(
        endpoint,
        model="Qwen/Qwen3-Embedding-0.6B",
        dimension=1024,
    )

    vector = await embedder.create("content-neutral input")

    assert len(vector) == 1024
    assert endpoint.calls == [
        {
            "input": "content-neutral input",
            "model": "Qwen/Qwen3-Embedding-0.6B",
        }
    ]
    mismatch = ExactDimensionEmbedder(
        _EmbeddingEndpoint([[0.0] * 1025]),
        model="Qwen/Qwen3-Embedding-0.6B",
        dimension=1024,
    )
    with pytest.raises(CompositeEngineeringError, match="dimension"):
        await mismatch.create("content-neutral input")


def test_source_hash_verification_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "runtime.py"
    source.write_text("frozen = True\n", encoding="ascii")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    assert verify_source_bindings(tmp_path, {"runtime.py": expected}) == {
        "runtime.py": expected
    }
    source.write_text("frozen = False\n", encoding="ascii")
    with pytest.raises(CompositeEngineeringError, match="source hash"):
        verify_source_bindings(tmp_path, {"runtime.py": expected})


def test_provider_observation_summary_retains_counts_not_response_hashes() -> None:
    result = summarize_provider_observations(
        [
            {
                "lane": "construction",
                "structured": True,
                "finish_reason": "stop",
                "prompt_tokens": 11,
                "completion_tokens": 5,
                "content_bytes": 12,
                "content_sha256": "a" * 64,
            },
            {
                "lane": "construction",
                "structured": False,
                "finish_reason": "length",
                "prompt_tokens": 7,
                "completion_tokens": 1,
                "content_bytes": 4,
                "content_sha256": "b" * 64,
            },
        ]
    )

    assert result == {
        "construction_call_count": 2,
        "structured_call_count": 1,
        "nonstructured_call_count": 1,
        "all_structured_finish_reason_stop": True,
        "prompt_tokens": 18,
        "completion_tokens": 6,
        "response_content_bytes": 16,
        "response_content_hashes_persisted": False,
    }
    assert "a" * 64 not in json.dumps(result, sort_keys=True)


def test_engineering_artifact_never_evaluates_gates_or_updates_method_selection() -> None:
    frozen = load_composite_engineering_freeze(_freeze_path())
    method_path = _freeze_path().parent / "METHOD_SELECTION.json"
    method_sha256 = hashlib.sha256(method_path.read_bytes()).hexdigest()
    block = {
        "schema_version": "membind.v7.observer-block.v1",
        "status": "OBSERVER_ONLY",
        "real_graphiti_evidence": True,
        "source_count": 2,
        "pairs": [{"source_sequence": 1}],
        "shadow_publication_calls": 0,
        "native_publication_calls": 2,
        "treatment_calls": 0,
        "provider_identity": {
            "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
            "construction": {"authority": frozen["construction"]["authority"]},
            "embedding": {"authority": frozen["embedding"]["authority"]},
        },
    }

    artifact = build_engineering_observer_artifact(
        run_id="v7-composite-engineering-test",
        composite_freeze_path=_freeze_path(),
        block_result=block,
        source_sha256={"engineering_observer_runtime.py": "a" * 64},
        method_selection_path=method_path,
    )

    assert artifact["status"] == "PASS"
    assert artifact["mode"] == "ENGINEERING_OBSERVER"
    assert artifact["formal_r1_r3_eligible"] is False
    assert artifact["gate_a_e_evaluated"] is False
    assert artifact["gates"] == {name: "NOT_EVALUATED" for name in "ABCDE"}
    assert artifact["gate_outcome"] == "NOT_EVALUATED"
    assert artifact["treatment_authorized"] is False
    assert artifact["scientific_method_selection_updated"] is False
    assert artifact["method_selection_sha256_before"] == method_sha256
    assert artifact["method_selection_sha256_after"] == method_sha256
    assert "pairs" not in artifact
    assert "provider" not in artifact["provider_identity"]


def test_composite_runtime_factory_keeps_provider_clients_and_identities_separate() -> None:
    calls: list[dict[str, object]] = []

    class FakeOpenAI:
        def __init__(self, base_url: str) -> None:
            self.chat = SimpleNamespace(
                completions=_CompletionEndpoint(_completion_response())
            )
            self.embeddings = _EmbeddingEndpoint([[0.0] * 1024])
            self.base_url = base_url

    def openai_factory(**kwargs: object) -> object:
        calls.append(dict(kwargs))
        return FakeOpenAI(str(kwargs["base_url"]))

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    class FakeQwen:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)
            self.model = kwargs["config"].model

        async def _generate_response(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            return {"value": 7}

        async def generate_response(
            self, *args: object, **kwargs: object
        ) -> dict[str, object]:
            del args, kwargs
            return {"value": 7}

    class FakeAdmission:
        def __init__(self, *, limit: int) -> None:
            self.limit = limit

    class FakeAdmitted:
        def __init__(
            self, *, inner: object, admission: object, request_id_prefix: str
        ) -> None:
            self.inner = inner
            self.admission = admission
            self.request_id_prefix = request_id_prefix

        def __getattr__(self, name: str) -> object:
            return getattr(self.inner, name)

    class FakeReranker:
        def __init__(self, config: object, *, client: object) -> None:
            self.config = config
            self.client = client

    class FakeGraphiti:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)
            self.clients = SimpleNamespace(
                llm_client=kwargs["llm_client"], embedder=kwargs["embedder"]
            )

    runtime = build_composite_engineering_runtime(
        env={
            "DASHSCOPE_API_KEY": "construction-secret",
            "SILICONFLOW_API_KEY": "embedding-secret",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "database-secret",
            "NEO4J_URI": "bolt://localhost:7687",
            "GRAPHITI_MAX_COROUTINES": "8",
        },
        request_id_prefix="v7-composite-test",
        composite_freeze_path=_freeze_path(),
        components=CompositeRuntimeComponents(
            graphiti_type=FakeGraphiti,
            llm_config_type=FakeConfig,
            qwen_client_type=FakeQwen,
            reranker_type=FakeReranker,
            admitted_client_type=FakeAdmitted,
            request_admission_type=FakeAdmission,
            openai_client_factory=openai_factory,
        ),
    )

    assert [(call["base_url"], call["api_key"]) for call in calls] == [
        (
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "construction-secret",
        ),
        ("https://api.siliconflow.cn/v1", "embedding-secret"),
    ]
    assert runtime.admitted_llm.admission.limit == 2
    assert runtime.graphiti.llm_client is runtime.admitted_llm
    assert runtime.graphiti.embedder is runtime.embedder
    assert runtime.raw_llm.structured_output_mode == "json_object"
    assert runtime.raw_llm.vllm_options_enabled is False
    assert runtime.public_identity["provider_identity_kind"] == (
        "COMPOSITE_ENGINEERING_ONLY"
    )
    assert runtime.public_identity["construction"]["authority"] != (
        runtime.public_identity["embedding"]["authority"]
    )
    assert "provider" not in runtime.public_identity
    encoded = json.dumps(runtime.public_identity, sort_keys=True)
    assert "construction-secret" not in encoded
    assert "embedding-secret" not in encoded
    assert "database-secret" not in encoded


def test_composite_runtime_factory_requires_both_provider_credentials() -> None:
    with pytest.raises(CompositeEngineeringError, match="SILICONFLOW_API_KEY"):
        build_composite_engineering_runtime(
            env={
                "DASHSCOPE_API_KEY": "construction-secret",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "database-secret",
            },
            request_id_prefix="v7-composite-test",
            composite_freeze_path=_freeze_path(),
        )
