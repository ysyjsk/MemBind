"""SiliconFlow-backed, construction-forbidden Graphiti runtime for QA reuse."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
from dataclasses import dataclass, field
from typing import Any


EXACT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_DIMENSION = 1024


class _ForbiddenCrossEncoder:
    async def rank(self, *_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("cross encoder is forbidden")


@dataclass
class ExpandedRuntime:
    graphiti: Any
    public_identity: dict[str, Any]
    embedding_client: Any | None = field(default=None, repr=False)
    embedding_http_client: Any | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []

        async def close(value: Any, method_name: str) -> None:
            method = getattr(value, method_name, None) if value is not None else None
            if not callable(method):
                return
            try:
                result = method()
                if result is not None and hasattr(result, "__await__"):
                    await result
            except BaseException as error:
                errors.append(error)

        await close(self.graphiti, "close")
        await close(self.embedding_client, "close")
        if self.embedding_http_client is not None and not getattr(
            self.embedding_http_client, "is_closed", False
        ):
            await close(self.embedding_http_client, "aclose")
        if errors:
            raise errors[0]


def build_expanded_runtime(
    *,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    embedding_base_url: str,
    embedding_api_key: str,
) -> ExpandedRuntime:
    """Build Graphiti outside an event loop with no schema initialization."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("runtime must be built outside an active event loop")
    if not all(
        isinstance(value, str) and value
        for value in (
            neo4j_uri,
            neo4j_user,
            neo4j_password,
            embedding_base_url,
            embedding_api_key,
        )
    ):
        raise ValueError("expanded runtime configuration is incomplete")

    os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
    graphiti_core = importlib.import_module("graphiti_core")
    _ = graphiti_core
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
                    api_key="expanded-runtime-forbidden",
                    model="expanded-runtime-forbidden",
                    small_model="expanded-runtime-forbidden",
                    max_tokens=1,
                )
            )

        async def generate_response(self, *_args: object, **_kwargs: object) -> Any:
            raise RuntimeError("construction LLM is forbidden")

        async def _generate_response(self, *_args: object, **_kwargs: object) -> Any:
            raise RuntimeError("construction LLM is forbidden")

    class ForbiddenCrossEncoder(CrossEncoderClient):
        async def rank(self, *_args: object, **_kwargs: object) -> Any:
            raise RuntimeError("cross encoder is forbidden")

    httpx = importlib.import_module("httpx")
    openai = importlib.import_module("openai")
    http_client = httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(connect=5.0, read=180.0, write=180.0, pool=180.0),
    )
    embedding_client = openai.AsyncOpenAI(
        api_key=embedding_api_key,
        base_url=f"{embedding_base_url.rstrip('/')}/",
        max_retries=0,
        http_client=http_client,
    )
    if getattr(embedding_client, "max_retries", None) != 0:
        raise RuntimeError("embedding hidden retries are enabled")

    driver = Neo4jDriver(neo4j_uri, neo4j_user, neo4j_password)
    if getattr(driver, "_init_task", None) is not None:
        raise RuntimeError("Neo4j schema initialization is forbidden")
    config = OpenAIEmbedderConfig(
        api_key=embedding_api_key,
        base_url=embedding_base_url,
        embedding_model=EXACT_EMBEDDING_MODEL,
        embedding_dim=EMBEDDING_DIMENSION,
    )
    embedder = OpenAIEmbedder(config, client=embedding_client)
    graphiti = Graphiti(
        graph_driver=driver,
        llm_client=ForbiddenConstructionLLM(),
        embedder=embedder,
        cross_encoder=ForbiddenCrossEncoder(),
    )
    if getattr(graphiti, "driver", None) is not driver:
        raise RuntimeError("Graphiti driver identity drift")
    return ExpandedRuntime(
        graphiti=graphiti,
        public_identity={
            "implementation": "baseline_reuse_expanded_read_only_runtime_v1",
            "database_access": "READ_ONLY_GUARDED_PER_QUERY",
            "schema_initialization": "FORBIDDEN",
            "construction_llm": "FORBIDDEN",
            "cross_encoder": "FORBIDDEN",
            "embedding": {
                "served_model_id": EXACT_EMBEDDING_MODEL,
                "dimension": EMBEDDING_DIMENSION,
                "endpoint_identity_sha256": hashlib.sha256(
                    f"{embedding_base_url.rstrip('/')}/".encode("utf-8")
                ).hexdigest(),
                "sdk_hidden_retries": 0,
                "max_attempts": 1,
            },
        },
        embedding_client=embedding_client,
        embedding_http_client=http_client,
    )


__all__ = ["EMBEDDING_DIMENSION", "EXACT_EMBEDDING_MODEL", "ExpandedRuntime", "build_expanded_runtime"]
