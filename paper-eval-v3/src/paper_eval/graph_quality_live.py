"""Read-only Graphiti runtime for graph-derived LongMemEval evaluation.

The runtime retains the frozen Qwen3 embedding service needed by cosine
retrieval while making every construction-capable component fail immediately.
It must be built before ``asyncio.run`` so Graphiti's Neo4j driver cannot
schedule index or constraint creation.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


NEO4J_URI = "bolt://localhost:7687"
EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
EMBEDDING_DIMENSION = 1024
EMBEDDING_DEPLOYMENT_FINGERPRINT = (
    "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"
)


class _ForbiddenConstructionLLM:
    """Graphiti-compatible client that cannot issue a construction request."""

    def set_tracer(self, _tracer: object) -> None:
        return None

    async def generate_response(self, *_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("construction LLM is forbidden in graph-quality runtime")

    async def _generate_response(self, *_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("construction LLM is forbidden in graph-quality runtime")


class _ForbiddenCrossEncoder:
    """RRF retrieval must never invoke a model-based cross encoder."""

    async def rank(self, *_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("cross-encoder is forbidden in graph-quality runtime")


@dataclass(frozen=True)
class GraphQualityLiveComponents:
    """Injectable constructors keep the environment contract offline-testable."""

    driver_type: Any
    graphiti_type: Any
    embedder_config_type: Any
    embedder_type: Any
    embedding_client_factory: Callable[..., tuple[Any, Any]] | None = None
    llm_factory: Callable[[], Any] | None = None
    cross_encoder_factory: Callable[[], Any] | None = None


@dataclass
class GraphQualityRuntime:
    graphiti: Any
    public_identity: dict[str, Any]
    _embedding_openai_client: Any | None = field(default=None, repr=False)
    _embedding_http_client: Any | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def aclose(self) -> None:
        """Close each owned resource at most once, even on repeated cleanup."""

        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []

        async def close(target: object | None, method_name: str) -> None:
            if target is None:
                return
            method = getattr(target, method_name, None)
            if not callable(method):
                return
            try:
                result = method()
                if result is not None and hasattr(result, "__await__"):
                    await result
            except BaseException as error:  # cleanup continues for all resources
                errors.append(error)

        await close(self.graphiti, "close")
        await close(self._embedding_openai_client, "close")
        http_client = self._embedding_http_client
        if http_client is not None and not bool(
            getattr(http_client, "is_closed", False)
        ):
            await close(http_client, "aclose")
        if errors:
            raise errors[0]


def _create_production_embedding_clients(
    *,
    api_key: str,
    base_url: str,
) -> tuple[Any, Any]:
    """Own an explicit no-retry, environment-isolated embedding transport."""

    httpx = importlib.import_module("httpx")
    openai = importlib.import_module("openai")
    http_client = httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
    )
    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=f"{base_url.rstrip('/')}/",
        max_retries=0,
        http_client=http_client,
    )
    return client, http_client


def _production_components() -> GraphQualityLiveComponents:
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.client import CrossEncoderClient
    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.llm_client.config import LLMConfig

    class ForbiddenConstructionLLM(LLMClient):
        def __init__(self) -> None:
            super().__init__(
                LLMConfig(
                    api_key="graph-quality-forbidden",
                    model="graph-quality-forbidden",
                    small_model="graph-quality-forbidden",
                    max_tokens=1,
                )
            )

        async def generate_response(
            self, *_args: object, **_kwargs: object
        ) -> Any:
            raise RuntimeError(
                "construction LLM is forbidden in graph-quality runtime"
            )

        async def _generate_response(
            self, *_args: object, **_kwargs: object
        ) -> Any:
            raise RuntimeError(
                "construction LLM is forbidden in graph-quality runtime"
            )

    class ForbiddenCrossEncoder(CrossEncoderClient):
        async def rank(
            self, *_args: object, **_kwargs: object
        ) -> Any:
            raise RuntimeError(
                "cross-encoder is forbidden in graph-quality runtime"
            )

    return GraphQualityLiveComponents(
        driver_type=Neo4jDriver,
        graphiti_type=Graphiti,
        embedder_config_type=OpenAIEmbedderConfig,
        embedder_type=OpenAIEmbedder,
        embedding_client_factory=_create_production_embedding_clients,
        llm_factory=ForbiddenConstructionLLM,
        cross_encoder_factory=ForbiddenCrossEncoder,
    )


def _required(env: dict[str, str] | Any, name: str) -> str:
    value = env.get(name) if hasattr(env, "get") else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"graph-quality runtime identity missing: {name.casefold()}")
    return value


def build_graph_quality_runtime(
    *,
    env: dict[str, str] | Any,
    components: GraphQualityLiveComponents | None = None,
) -> GraphQualityRuntime:
    """Build the query-only runtime outside an active asyncio event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "graph-quality runtime must be built outside an active event loop"
        )
    uri = _required(env, "NEO4J_URI")
    user = _required(env, "NEO4J_USER")
    password = _required(env, "NEO4J_PASSWORD")
    embedding_base = _required(env, "EMBEDDING_BASE_URL").rstrip("/")
    embedding_key = _required(env, "EMBEDDING_API_KEY")
    embedding_model = _required(env, "EMBEDDING_MODEL")
    raw_dimension = _required(env, "EMBEDDING_DIM")
    try:
        embedding_dimension = int(raw_dimension)
    except ValueError:
        raise ValueError("graph-quality runtime embedding identity drift") from None
    if (
        uri != NEO4J_URI
        or embedding_base != EMBEDDING_BASE_URL
        or embedding_model != EMBEDDING_MODEL
        or embedding_dimension != EMBEDDING_DIMENSION
    ):
        raise ValueError("graph-quality runtime deployment identity drift")

    os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
    selected = components or _production_components()
    driver = selected.driver_type(uri, user, password)
    if getattr(driver, "_init_task", None) is not None:
        raise RuntimeError(
            "graph-quality Neo4j driver scheduled schema initialization init task"
        )
    embedder_config = selected.embedder_config_type(
        api_key=embedding_key,
        base_url=EMBEDDING_BASE_URL,
        embedding_model=EMBEDDING_MODEL,
        embedding_dim=EMBEDDING_DIMENSION,
    )
    embedding_openai_client: Any | None = None
    embedding_http_client: Any | None = None
    if selected.embedding_client_factory is None:
        embedder = selected.embedder_type(embedder_config)
    else:
        created = selected.embedding_client_factory(
            api_key=embedding_key,
            base_url=EMBEDDING_BASE_URL,
        )
        if not isinstance(created, tuple) or len(created) != 2:
            raise RuntimeError("graph-quality embedding client factory is invalid")
        embedding_openai_client, embedding_http_client = created
        if getattr(embedding_openai_client, "max_retries", None) != 0:
            raise RuntimeError("graph-quality embedding hidden retries are enabled")
        embedder = selected.embedder_type(
            embedder_config,
            client=embedding_openai_client,
        )
    llm = (
        selected.llm_factory()
        if selected.llm_factory is not None
        else _ForbiddenConstructionLLM()
    )
    cross_encoder = (
        selected.cross_encoder_factory()
        if selected.cross_encoder_factory is not None
        else _ForbiddenCrossEncoder()
    )
    graphiti = selected.graphiti_type(
        graph_driver=driver,
        llm_client=llm,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )
    if (
        getattr(graphiti, "driver", None) is not driver
        or getattr(graphiti, "embedder", None) is not embedder
        or getattr(graphiti, "llm_client", None) is not llm
        or getattr(graphiti, "cross_encoder", None) is not cross_encoder
    ):
        raise RuntimeError("graph-quality Graphiti component identity drift")

    endpoint_identity = hashlib.sha256(
        f"{EMBEDDING_BASE_URL}/".encode("utf-8")
    ).hexdigest()
    public_identity = {
        "implementation": "graphiti_read_only_graph_quality_runtime_v1",
        "driver_init_task_present": False,
        "construction_llm": "FORBIDDEN",
        "cross_encoder": "FORBIDDEN",
        "embedding": {
            "served_model_id": EMBEDDING_MODEL,
            "dimension": EMBEDDING_DIMENSION,
            "deployment_fingerprint": EMBEDDING_DEPLOYMENT_FINGERPRINT,
            "dtype": "bfloat16",
            "pooling": "last_token",
            "normalization": "l2",
            "instruction_policy": "none",
            "evidence_scope": "operator_supplied_deployment_path_hash",
            "runtime_observed": False,
            "sdk_hidden_retries": 0,
            "max_attempts": 1,
            "environment_proxy_trust": False,
            "endpoint_identity_sha256": endpoint_identity,
        },
        "schema_initialization": "FORBIDDEN",
        "database_access": "READ_ONLY_GUARDED_PER_QUERY",
    }
    return GraphQualityRuntime(
        graphiti=graphiti,
        public_identity=public_identity,
        _embedding_openai_client=embedding_openai_client,
        _embedding_http_client=embedding_http_client,
    )


__all__ = [
    "EMBEDDING_DEPLOYMENT_FINGERPRINT",
    "GraphQualityLiveComponents",
    "GraphQualityRuntime",
    "build_graph_quality_runtime",
]
