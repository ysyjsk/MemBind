"""Fail-closed construction of the frozen upstream-qualified Graphiti U0.

The factory deliberately contains no project cache or candidate-order adapter.
It opens no client and does not load `.env` until the current-state gate grants
the exact Native C0 live action.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from current_state_gate import LiveAction, require_live_action


CONSTRUCTION_BASE_URL = "http://10.87.5.247:8000/v1/"
CONSTRUCTION_MODEL = "qwen3-32b-fp8"
CONSTRUCTION_MODEL_REVISION = "6e2312b85c2ae9a31f629f24493b79d8b02eab1a"
EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
EMBEDDING_DIMENSION = 1024
NEO4J_URI = "bolt://localhost:7687"
REQUESTED_MAX_TOKENS = 16_384
CONTEXT_LIMIT = 65_536
CONTEXT_SAFETY_TOKENS = 32
MAX_COROUTINES = 8


class U0ConfigurationError(RuntimeError):
    """Sanitized non-secret configuration mismatch."""


@dataclass(frozen=True)
class U0Components:
    """Lazy production constructors, injectable for offline TDD."""

    graphiti_type: Any
    llm_config_type: Any
    qwen_client_type: Any
    embedder_config_type: Any
    embedder_type: Any
    reranker_type: Any


@dataclass(frozen=True)
class U0Config:
    construction_base_url: str
    construction_model: str
    construction_model_revision: str
    embedding_base_url: str
    embedding_model: str
    embedding_dimension: int
    neo4j_uri: str
    max_coroutines: int
    structured_output_mode: str = "json_schema"
    requested_max_tokens: int = REQUESTED_MAX_TOKENS
    context_limit: int = CONTEXT_LIMIT
    safety_margin_tokens: int = CONTEXT_SAFETY_TOKENS
    prompt_cache: bool = False
    embedding_cache: bool = False
    deterministic_candidate_ordering: bool = False

    def to_artifact(self) -> dict[str, Any]:
        return {
            "classification": "U0",
            "construction": {
                "base_url": self.construction_base_url,
                "served_model_id": self.construction_model,
                "model_revision": self.construction_model_revision,
                "requested_max_tokens": self.requested_max_tokens,
                "context_limit": self.context_limit,
                "safety_margin_tokens": self.safety_margin_tokens,
                "temperature": 0.0,
                "top_p": 1.0,
                "seed": 20260806,
                "enable_thinking": False,
                "structured_output_mode": self.structured_output_mode,
            },
            "embedding": {
                "base_url": self.embedding_base_url,
                "served_model_id": self.embedding_model,
                "dimension": self.embedding_dimension,
            },
            "neo4j": {"uri": self.neo4j_uri},
            "max_coroutines": self.max_coroutines,
            "policies": {
                "prompt_cache": self.prompt_cache,
                "embedding_cache": self.embedding_cache,
                "deterministic_candidate_ordering": (
                    self.deterministic_candidate_ordering
                ),
                "cross_run_cache_carry_over": "prohibited",
            },
        }


@dataclass
class U0Runtime:
    graphiti: Any
    llm_client: Any
    embedder: Any
    reranker: Any
    config: U0Config
    classification: str = "U0"


def _fail(reason: str) -> U0ConfigurationError:
    return U0ConfigurationError(f"U0 configuration denied: {reason}")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise _fail(f"{name.casefold()}_missing")
    return value


def _exact(name: str, expected: str, reason: str) -> str:
    value = os.environ.get(name, expected)
    if value != expected:
        raise _fail(reason)
    return value


def _integer(name: str, expected: int, reason: str) -> int:
    raw = os.environ.get(name, str(expected))
    try:
        value = int(raw)
    except ValueError:
        raise _fail(reason) from None
    if value != expected:
        raise _fail(reason)
    return value


def _load_production_components() -> U0Components:
    """Import Graphiti constructors only after live authorization and config checks."""

    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import (
        OpenAIRerankerClient,
    )
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_native import QwenVLLMClient

    return U0Components(
        graphiti_type=Graphiti,
        llm_config_type=LLMConfig,
        qwen_client_type=QwenVLLMClient,
        embedder_config_type=OpenAIEmbedderConfig,
        embedder_type=OpenAIEmbedder,
        reranker_type=OpenAIRerankerClient,
    )


def build_u0_graphiti_from_env(
    *,
    authorization_checker: Callable[[LiveAction], Any] = require_live_action,
    live_action: LiveAction = LiveAction.NATIVE_CHARACTERIZATION_C0,
    env_loader: Callable[[], Any] | None = None,
    component_loader: Callable[[], U0Components] = _load_production_components,
    structured_output_mode: str = "json_schema",
) -> U0Runtime:
    """Build U0 after the exact live gate, with no cache or stabilizer path."""

    authorization_checker(live_action)
    if structured_output_mode not in {"json_schema", "json_object"}:
        raise _fail("structured_output_mode_invalid")
    if env_loader is None:
        # Keep the legacy loader lazy: importing its module is harmless, but
        # reading `.env` before the live gate would violate the authority order.
        from graphiti_native import load_env_file

        env_loader = load_env_file
    env_loader()

    construction_key = _required("CONSTRUCTION_LLM_API_KEY")
    embedding_key = _required("EMBEDDING_API_KEY")
    neo4j_user = _required("NEO4J_USER")
    neo4j_password = _required("NEO4J_PASSWORD")
    for name, expected in (
        ("CONSTRUCTION_TOP_P", "1.0"),
        ("CONSTRUCTION_SEED", "20260806"),
        ("CONSTRUCTION_OVERFLOW_MAX_TOKENS", "8192"),
        ("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32"),
        ("CONSTRUCTION_EXPECTED_VLLM_VERSION", "0.26.0"),
        ("CONSTRUCTION_MIN_CONTEXT_TOKENS", "65536"),
    ):
        _exact(name, expected, "wire_policy_mismatch")
    config = U0Config(
        construction_base_url=_exact(
            "CONSTRUCTION_LLM_BASE_URL",
            CONSTRUCTION_BASE_URL,
            "construction_base_url_mismatch",
        ),
        construction_model=_exact(
            "CONSTRUCTION_LLM_MODEL",
            CONSTRUCTION_MODEL,
            "construction_model_mismatch",
        ),
        construction_model_revision=CONSTRUCTION_MODEL_REVISION,
        embedding_base_url=_exact(
            "EMBEDDING_BASE_URL",
            EMBEDDING_BASE_URL,
            "embedding_base_url_mismatch",
        ),
        embedding_model=_exact(
            "EMBEDDING_MODEL",
            EMBEDDING_MODEL,
            "embedding_model_mismatch",
        ),
        embedding_dimension=_integer(
            "EMBEDDING_DIM", EMBEDDING_DIMENSION, "embedding_dimension_mismatch"
        ),
        neo4j_uri=_exact("NEO4J_URI", NEO4J_URI, "neo4j_uri_mismatch"),
        max_coroutines=_integer(
            "GRAPHITI_MAX_COROUTINES",
            MAX_COROUTINES,
            "max_coroutines_mismatch",
        ),
        structured_output_mode=structured_output_mode,
    )

    components = component_loader()
    llm_config = components.llm_config_type(
        api_key=construction_key,
        model=config.construction_model,
        small_model=config.construction_model,
        base_url=config.construction_base_url,
        temperature=0.0,
        max_tokens=config.requested_max_tokens,
    )
    llm_client = components.qwen_client_type(
        config=llm_config,
        max_tokens=config.requested_max_tokens,
        structured_output_mode=config.structured_output_mode,
    )
    embedder_config = components.embedder_config_type(
        api_key=embedding_key,
        base_url=config.embedding_base_url,
        embedding_model=config.embedding_model,
        embedding_dim=config.embedding_dimension,
    )
    embedder = components.embedder_type(embedder_config)
    reranker = components.reranker_type(llm_config)
    graphiti = components.graphiti_type(
        uri=config.neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=config.max_coroutines,
    )
    return U0Runtime(
        graphiti=graphiti,
        llm_client=llm_client,
        embedder=embedder,
        reranker=reranker,
        config=config,
    )
