"""TDD for the isolated, admitted live Graphiti runtime factory."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from paper_eval.membind_v1.admission import AdmittedLLMClient, RequestAdmission
from paper_eval.membind_v1.live_runtime import (
    MemBindV1LiveRuntimeError,
    RuntimeComponents,
    build_membind_v1_runtime,
    project_membind_v1_runtime_identity,
)


def _env() -> dict[str, str]:
    return {
        "CONSTRUCTION_LLM_API_KEY": "construction-secret",
        "CONSTRUCTION_LLM_BASE_URL": "http://10.87.5.247:8000/v1/",
        "CONSTRUCTION_LLM_MODEL": "qwen3-32b-fp8",
        "EMBEDDING_API_KEY": "embedding-secret",
        "EMBEDDING_BASE_URL": "http://10.87.5.247:8001/v1",
        "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
        "EMBEDDING_DIM": "1024",
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "database-secret",
        "GRAPHITI_MAX_COROUTINES": "8",
    }


class _LLMConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _RawLLM:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def generate_response(self, *_args, **_kwargs):
        return {"ok": True}


class _EmbedderConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Embedder:
    def __init__(self, config):
        self.config = config


class _Reranker:
    def __init__(self, config):
        self.config = config


class _Graphiti:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.llm_client = kwargs["llm_client"]
        self.clients = SimpleNamespace(llm_client=self.llm_client)


def _components() -> RuntimeComponents:
    return RuntimeComponents(
        graphiti_type=_Graphiti,
        llm_config_type=_LLMConfig,
        qwen_client_type=_RawLLM,
        embedder_config_type=_EmbedderConfig,
        embedder_type=_Embedder,
        reranker_type=_Reranker,
    )


def test_runtime_builds_pinned_components_and_installs_admission_at_both_graphiti_references() -> None:
    runtime = build_membind_v1_runtime(
        env=_env(),
        admission=RequestAdmission(limit=2),
        request_id_prefix="aligned-dev-001:U0:07741c45",
        components=_components(),
    )

    assert isinstance(runtime.graphiti.llm_client, AdmittedLLMClient)
    assert runtime.graphiti.llm_client is runtime.graphiti.clients.llm_client
    assert runtime.raw_llm is not runtime.graphiti.llm_client
    assert runtime.graphiti.kwargs["max_coroutines"] == 8
    assert runtime.public_identity["construction"] == {
        "base_url": "http://10.87.5.247:8000/v1",
        "served_model_id": "qwen3-32b-fp8",
        "requested_max_tokens": 16384,
        "structured_output_mode": "json_schema",
    }
    assert runtime.public_identity["global_llm_admission_k"] == 2
    assert "secret" not in repr(runtime.public_identity)


def test_runtime_identity_is_projected_before_graphiti_construction_without_secrets() -> None:
    identity = project_membind_v1_runtime_identity(_env())

    assert identity["construction"]["served_model_id"] == "qwen3-32b-fp8"
    assert identity["construction"]["requested_max_tokens"] == 16384
    assert identity["embedding"]["dimension"] == 1024
    assert identity["global_llm_admission_k"] == 2
    assert "secret" not in repr(identity)


def test_runtime_rejects_service_identity_drift_or_non_shared_k2_admission() -> None:
    env = _env()
    env["CONSTRUCTION_LLM_MODEL"] = "other"
    with pytest.raises(MemBindV1LiveRuntimeError, match="construction model"):
        build_membind_v1_runtime(
            env=env,
            admission=RequestAdmission(limit=2),
            request_id_prefix="aligned-dev-001:U0:07741c45",
            components=_components(),
        )
    with pytest.raises(MemBindV1LiveRuntimeError, match="LLM admission"):
        build_membind_v1_runtime(
            env=_env(),
            admission=RequestAdmission(limit=1),
            request_id_prefix="aligned-dev-001:U0:07741c45",
            components=_components(),
        )
