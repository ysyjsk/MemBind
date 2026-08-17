"""RED contracts for the read-only graph-quality production runtime."""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from typing import Any

import pytest


FROZEN_EMBEDDING_FINGERPRINT = (
    "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"
)


def _live() -> Any:
    # Deliberately lazy so the RED report retains one failure per contract.
    return importlib.import_module("paper_eval.graph_quality_live")


def _env() -> dict[str, str]:
    return {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "private-neo4j-password",
        "EMBEDDING_BASE_URL": "http://10.87.5.247:8001/v1",
        "EMBEDDING_API_KEY": "private-embedding-key",
        "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
        "EMBEDDING_DIM": "1024",
    }


class _Driver:
    def __init__(self, uri: str, user: str, password: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:  # pragma: no cover - the runtime must reject before this path
            raise AssertionError("Neo4jDriver was constructed inside an event loop")
        self.identity = (uri, user, password)
        self._init_task = None


class _EmbedderConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)


class _Embedder:
    def __init__(self, config: _EmbedderConfig) -> None:
        self.config = config


class _Graphiti:
    def __init__(self, **kwargs: object) -> None:
        self.driver = kwargs["graph_driver"]
        self.llm_client = kwargs["llm_client"]
        self.embedder = kwargs["embedder"]
        self.cross_encoder = kwargs["cross_encoder"]


def _components(live: Any, *, driver_type: type = _Driver) -> Any:
    return live.GraphQualityLiveComponents(
        driver_type=driver_type,
        graphiti_type=_Graphiti,
        embedder_config_type=_EmbedderConfig,
        embedder_type=_Embedder,
    )


def _build() -> Any:
    live = _live()
    return live.build_graph_quality_runtime(
        env=_env(),
        components=_components(live),
    )


def test_runtime_constructs_neo4j_driver_outside_event_loop_without_init_task() -> None:
    runtime = _build()

    assert runtime.graphiti.driver._init_task is None
    assert runtime.graphiti.driver.identity == (
        "bolt://localhost:7687",
        "neo4j",
        "private-neo4j-password",
    )
    assert runtime.public_identity["driver_init_task_present"] is False


@pytest.mark.asyncio
async def test_runtime_builder_rejects_an_active_event_loop() -> None:
    live = _live()

    with pytest.raises(RuntimeError, match="outside an active event loop"):
        live.build_graph_quality_runtime(
            env=_env(),
            components=_components(live),
        )


def test_runtime_uses_exact_frozen_qwen3_embedding_configuration() -> None:
    runtime = _build()

    assert runtime.graphiti.embedder.config.kwargs == {
        "api_key": "private-embedding-key",
        "base_url": "http://10.87.5.247:8001/v1",
        "embedding_model": "qwen3-embedding-0.6b",
        "embedding_dim": 1024,
    }
    assert runtime.public_identity["embedding"] == {
        "served_model_id": "qwen3-embedding-0.6b",
        "dimension": 1024,
        "deployment_fingerprint": FROZEN_EMBEDDING_FINGERPRINT,
        "dtype": "bfloat16",
        "pooling": "last_token",
        "normalization": "l2",
        "instruction_policy": "none",
        "evidence_scope": "operator_supplied_deployment_path_hash",
        "runtime_observed": False,
        "sdk_hidden_retries": 0,
        "max_attempts": 1,
        "environment_proxy_trust": False,
        "endpoint_identity_sha256": runtime.public_identity["embedding"][
            "endpoint_identity_sha256"
        ],
    }
    endpoint_hash = runtime.public_identity["embedding"]["endpoint_identity_sha256"]
    assert isinstance(endpoint_hash, str) and len(endpoint_hash) == 64


def test_construction_llm_and_cross_encoder_are_fail_fast_forbidden() -> None:
    runtime = _build()

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="construction LLM.*forbidden"):
            await runtime.graphiti.llm_client.generate_response([])
        with pytest.raises(RuntimeError, match="cross.encoder.*forbidden"):
            await runtime.graphiti.cross_encoder.rank("query", ["passage"])

    asyncio.run(exercise())
    assert runtime.public_identity["construction_llm"] == "FORBIDDEN"
    assert runtime.public_identity["cross_encoder"] == "FORBIDDEN"


def test_public_identity_contains_no_key_or_credential_material() -> None:
    runtime = _build()
    encoded = json.dumps(runtime.public_identity, sort_keys=True)
    lowered = encoded.casefold()

    assert "private-neo4j-password" not in encoded
    assert "private-embedding-key" not in encoded
    for forbidden in (
        "api_key",
        "apikey",
        "password",
        "credential",
        "authorization",
    ):
        assert forbidden not in lowered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("NEO4J_URI", "bolt://other-host:7687"),
        ("EMBEDDING_BASE_URL", "http://other-host:8001/v1"),
        ("EMBEDDING_MODEL", "another-embedding-model"),
        ("EMBEDDING_DIM", "768"),
        ("NEO4J_USER", ""),
        ("NEO4J_PASSWORD", ""),
        ("EMBEDDING_API_KEY", ""),
    ],
)
def test_runtime_environment_identity_drift_fails_closed(
    field: str,
    value: str,
) -> None:
    live = _live()
    env = _env()
    env[field] = value

    with pytest.raises(ValueError, match="identity|missing|drift"):
        live.build_graph_quality_runtime(
            env=env,
            components=_components(live),
        )


def test_driver_with_scheduled_schema_initialization_fails_closed() -> None:
    live = _live()

    class _InitializingDriver(_Driver):
        def __init__(self, uri: str, user: str, password: str) -> None:
            super().__init__(uri, user, password)
            self._init_task = SimpleNamespace(done=lambda: False)

    with pytest.raises(RuntimeError, match="schema initialization|init task"):
        live.build_graph_quality_runtime(
            env=_env(),
            components=_components(live, driver_type=_InitializingDriver),
        )


def test_runtime_uses_injected_graphiti_compatible_forbidden_client_types() -> None:
    live = _live()

    class RequiredLLM:
        async def generate_response(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("construction LLM is forbidden")

    class RequiredCrossEncoder:
        async def rank(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("cross-encoder is forbidden")

    class StrictGraphiti(_Graphiti):
        def __init__(self, **kwargs: object) -> None:
            assert isinstance(kwargs["llm_client"], RequiredLLM)
            assert isinstance(kwargs["cross_encoder"], RequiredCrossEncoder)
            super().__init__(**kwargs)

    runtime = live.build_graph_quality_runtime(
        env=_env(),
        components=live.GraphQualityLiveComponents(
            driver_type=_Driver,
            graphiti_type=StrictGraphiti,
            embedder_config_type=_EmbedderConfig,
            embedder_type=_Embedder,
            llm_factory=RequiredLLM,
            cross_encoder_factory=RequiredCrossEncoder,
        ),
    )

    assert isinstance(runtime.graphiti.llm_client, RequiredLLM)
    assert isinstance(runtime.graphiti.cross_encoder, RequiredCrossEncoder)


def test_production_embedding_clients_disable_retries_and_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live()
    captured: dict[str, object] = {}

    class FakeHttpx:
        class AsyncClient:
            def __init__(self, **kwargs: object) -> None:
                captured["http"] = dict(kwargs)

    class FakeOpenAI:
        class AsyncOpenAI:
            def __init__(self, **kwargs: object) -> None:
                captured["openai"] = dict(kwargs)

    def import_module(name: str) -> object:
        return {"httpx": FakeHttpx, "openai": FakeOpenAI}[name]

    monkeypatch.setattr(live.importlib, "import_module", import_module)
    client, http_client = live._create_production_embedding_clients(
        api_key="private-key",
        base_url="http://10.87.5.247:8001/v1",
    )

    assert captured["http"] == {
        "follow_redirects": False,
        "trust_env": False,
    }
    assert captured["openai"] == {
        "api_key": "private-key",
        "base_url": "http://10.87.5.247:8001/v1/",
        "max_retries": 0,
        "http_client": http_client,
    }
    assert client is not None


def test_runtime_closes_graphiti_and_embedding_clients_once() -> None:
    live = _live()
    closed: list[str] = []

    class HttpClient:
        is_closed = False

        async def aclose(self) -> None:
            assert not self.is_closed
            self.is_closed = True
            closed.append("http")

    http_client = HttpClient()

    class OpenAIClient:
        max_retries = 0

        async def close(self) -> None:
            closed.append("openai")

    openai_client = OpenAIClient()

    class ClosableEmbedder(_Embedder):
        def __init__(self, config: _EmbedderConfig, *, client: object) -> None:
            super().__init__(config)
            self.client = client

    class ClosableGraphiti(_Graphiti):
        async def close(self) -> None:
            closed.append("graphiti")

    runtime = live.build_graph_quality_runtime(
        env=_env(),
        components=live.GraphQualityLiveComponents(
            driver_type=_Driver,
            graphiti_type=ClosableGraphiti,
            embedder_config_type=_EmbedderConfig,
            embedder_type=ClosableEmbedder,
            embedding_client_factory=lambda **_kwargs: (
                openai_client,
                http_client,
            ),
        ),
    )

    assert runtime.graphiti.embedder.client is openai_client

    async def close_twice() -> None:
        await runtime.aclose()
        await runtime.aclose()

    asyncio.run(close_twice())

    assert closed == ["graphiti", "openai", "http"]


def test_runtime_does_not_double_close_http_owned_by_openai_client() -> None:
    live = _live()
    closed: list[str] = []

    class HttpClient:
        is_closed = False

        async def aclose(self) -> None:  # pragma: no cover - must be skipped
            raise AssertionError("HTTP client was closed twice")

    http_client = HttpClient()

    class OpenAIClient:
        max_retries = 0

        async def close(self) -> None:
            http_client.is_closed = True
            closed.append("openai-owned-http")

    class ClosableEmbedder(_Embedder):
        def __init__(self, config: _EmbedderConfig, *, client: object) -> None:
            super().__init__(config)
            self.client = client

    runtime = live.build_graph_quality_runtime(
        env=_env(),
        components=live.GraphQualityLiveComponents(
            driver_type=_Driver,
            graphiti_type=_Graphiti,
            embedder_config_type=_EmbedderConfig,
            embedder_type=ClosableEmbedder,
            embedding_client_factory=lambda **_kwargs: (
                OpenAIClient(),
                http_client,
            ),
        ),
    )

    asyncio.run(runtime.aclose())

    assert closed == ["openai-owned-http"]
