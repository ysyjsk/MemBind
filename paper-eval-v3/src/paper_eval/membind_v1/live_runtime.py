"""Pinned, isolated Graphiti construction runtime for MemBind-v1 live blocks.

This factory intentionally does not consult the historical current-state gate.
The new user-authorized lane owns its own artifact roots and builds the same
public construction envelope for each fresh U0/P(C=2)/MemBind attempt.  It
never returns or persists credentials; the caller loads the existing ignored
environment file and this module projects only public configuration identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.admission import AdmittedLLMClient, RequestAdmission


CONSTRUCTION_BASE_URL = "http://10.87.5.247:8000/v1"
CONSTRUCTION_MODEL = "qwen3-32b-fp8"
EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
EMBEDDING_DIMENSION = 1024
NEO4J_URI = "bolt://localhost:7687"
REQUESTED_MAX_TOKENS = 16_384
MAX_COROUTINES = 8


class MemBindV1LiveRuntimeError(ValueError):
    """A secret-free construction envelope check or factory step failed."""


def _fail(code: str) -> MemBindV1LiveRuntimeError:
    return MemBindV1LiveRuntimeError(code)


class _SaltedCompletions:
    """New-lane-only cache salt injection around an OpenAI-compatible client."""

    def __init__(self, inner: object, cache_salt: str) -> None:
        self._inner = inner
        self._cache_salt = cache_salt

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def create(self, *args: object, **kwargs: object) -> object:
        request = dict(kwargs)
        extra_body = dict(request.get("extra_body") or {})
        extra_body["cache_salt"] = self._cache_salt
        request["extra_body"] = extra_body
        result = self._inner.create(*args, **request)
        if not hasattr(result, "__await__"):
            raise TypeError("salted completion transport must be async")
        return await result


class _SaltedChat:
    def __init__(self, inner: object, cache_salt: str) -> None:
        self._inner = inner
        self.completions = _SaltedCompletions(
            getattr(inner, "completions"), cache_salt
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _SaltedEmbeddings:
    def __init__(self, inner: object, cache_salt: str) -> None:
        self._inner = inner
        self._cache_salt = cache_salt

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def create(self, *args: object, **kwargs: object) -> object:
        request = dict(kwargs)
        extra_body = dict(request.get("extra_body") or {})
        extra_body["cache_salt"] = self._cache_salt
        request["extra_body"] = extra_body
        result = self._inner.create(*args, **request)
        if not hasattr(result, "__await__"):
            raise TypeError("salted embedding transport must be async")
        return await result


class _SaltedOpenAITransport:
    def __init__(self, inner: object, cache_salt: str) -> None:
        self._inner = inner
        if hasattr(inner, "chat"):
            self.chat = _SaltedChat(getattr(inner, "chat"), cache_salt)
        if hasattr(inner, "embeddings"):
            self.embeddings = _SaltedEmbeddings(
                getattr(inner, "embeddings"), cache_salt
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not isinstance(value, str) or not value:
        raise _fail(f"{name.casefold()} missing")
    return value


def _exact(env: Mapping[str, str], name: str, expected: str, code: str) -> str:
    observed = _required(env, name).rstrip("/")
    if observed != expected.rstrip("/"):
        raise _fail(code)
    return observed


def _integer(env: Mapping[str, str], name: str, expected: int, code: str) -> int:
    try:
        observed = int(_required(env, name))
    except ValueError:
        raise _fail(code) from None
    if observed != expected:
        raise _fail(code)
    return observed


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    """Lazy, injectable constructors used only after a live block is admitted."""

    graphiti_type: Any
    llm_config_type: Any
    qwen_client_type: Any
    embedder_config_type: Any
    embedder_type: Any
    reranker_type: Any


@dataclass(slots=True)
class MemBindV1LiveRuntime:
    """Owned live objects and the public identity usable in result artifacts."""

    graphiti: Any
    raw_llm: Any
    public_identity: dict[str, object]
    execution_envelope_sha256: str


def _production_components() -> RuntimeComponents:
    """Import vendor classes only when a live block calls the factory."""

    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_native import QwenVLLMClient

    return RuntimeComponents(
        graphiti_type=Graphiti,
        llm_config_type=LLMConfig,
        qwen_client_type=QwenVLLMClient,
        embedder_config_type=OpenAIEmbedderConfig,
        embedder_type=OpenAIEmbedder,
        reranker_type=OpenAIRerankerClient,
    )


def project_membind_v1_runtime_identity(
    env: Mapping[str, str],
) -> dict[str, object]:
    """Validate and project the secret-free shared execution envelope.

    Planning uses this function before any Graphiti object or namespace is
    created.  Live construction calls the same projection again, so the plan
    hash and the runtime identity cannot silently diverge.
    """

    if not isinstance(env, Mapping):
        raise _fail("environment invalid")
    construction_base_url = _exact(
        env,
        "CONSTRUCTION_LLM_BASE_URL",
        CONSTRUCTION_BASE_URL,
        "construction base URL mismatch",
    )
    construction_model = _exact(
        env,
        "CONSTRUCTION_LLM_MODEL",
        CONSTRUCTION_MODEL,
        "construction model mismatch",
    )
    embedding_base_url = _exact(
        env,
        "EMBEDDING_BASE_URL",
        EMBEDDING_BASE_URL,
        "embedding base URL mismatch",
    )
    embedding_model = _exact(
        env,
        "EMBEDDING_MODEL",
        EMBEDDING_MODEL,
        "embedding model mismatch",
    )
    embedding_dimension = _integer(
        env,
        "EMBEDDING_DIM",
        EMBEDDING_DIMENSION,
        "embedding dimension mismatch",
    )
    neo4j_uri = _exact(env, "NEO4J_URI", NEO4J_URI, "Neo4j URI mismatch")
    max_coroutines = _integer(
        env,
        "GRAPHITI_MAX_COROUTINES",
        MAX_COROUTINES,
        "Graphiti coroutine limit mismatch",
    )
    return {
        "schema_version": "membind.paper-eval-v3.membind-v1-live-runtime.v1",
        "construction": {
            "base_url": construction_base_url,
            "served_model_id": construction_model,
            "requested_max_tokens": REQUESTED_MAX_TOKENS,
            "structured_output_mode": "json_schema",
        },
        "embedding": {
            "base_url": embedding_base_url,
            "served_model_id": embedding_model,
            "dimension": embedding_dimension,
        },
        "neo4j": {"uri": neo4j_uri},
        "graphiti_max_coroutines": max_coroutines,
        "global_llm_admission_k": 2,
    }


def build_membind_v1_runtime(
    *,
    env: Mapping[str, str],
    admission: RequestAdmission,
    request_id_prefix: str,
    components: RuntimeComponents | None = None,
) -> MemBindV1LiveRuntime:
    """Construct the shared envelope and install real-call-level admission."""

    if not isinstance(admission, RequestAdmission) or admission.limit != 2:
        raise _fail("global LLM admission must be K=2")
    public_identity = project_membind_v1_runtime_identity(env)
    construction_key = _required(env, "CONSTRUCTION_LLM_API_KEY")
    embedding_key = _required(env, "EMBEDDING_API_KEY")
    neo4j_user = _required(env, "NEO4J_USER")
    neo4j_password = _required(env, "NEO4J_PASSWORD")
    construction = public_identity["construction"]
    embedding = public_identity["embedding"]
    neo4j = public_identity["neo4j"]
    if not isinstance(construction, Mapping) or not isinstance(embedding, Mapping) or not isinstance(neo4j, Mapping):
        raise _fail("runtime identity invalid")
    construction_base_url = str(construction["base_url"])
    construction_model = str(construction["served_model_id"])
    embedding_base_url = str(embedding["base_url"])
    embedding_model = str(embedding["served_model_id"])
    embedding_dimension = int(embedding["dimension"])
    neo4j_uri = str(neo4j["uri"])
    max_coroutines = int(public_identity["graphiti_max_coroutines"])
    selected = components or _production_components()
    if not isinstance(selected, RuntimeComponents):
        raise _fail("runtime components invalid")

    llm_config = selected.llm_config_type(
        api_key=construction_key,
        model=construction_model,
        small_model=construction_model,
        base_url=construction_base_url,
        temperature=0.0,
        max_tokens=REQUESTED_MAX_TOKENS,
    )
    raw_llm = selected.qwen_client_type(
        config=llm_config,
        max_tokens=REQUESTED_MAX_TOKENS,
        structured_output_mode="json_schema",
    )
    cache_salt = env.get("CONSTRUCTION_CACHE_SALT")
    if cache_salt is not None:
        if not isinstance(cache_salt, str) or not 1 <= len(cache_salt) <= 64:
            raise _fail("construction cache salt invalid")
        raw_transport = getattr(raw_llm, "client", None)
        if raw_transport is None:
            raise _fail("construction cache salt transport unavailable")
        raw_llm.client = _SaltedOpenAITransport(raw_transport, cache_salt)
    embedder_config = selected.embedder_config_type(
        api_key=embedding_key,
        base_url=embedding_base_url,
        embedding_model=embedding_model,
        embedding_dim=embedding_dimension,
    )
    embedder = selected.embedder_type(embedder_config)
    if cache_salt is not None:
        embedding_transport = getattr(embedder, "client", None)
        if embedding_transport is None:
            raise _fail("embedding cache salt transport unavailable")
        embedder.client = _SaltedOpenAITransport(embedding_transport, cache_salt)
    raw_transport = getattr(raw_llm, "client", None)
    if raw_transport is None:
        reranker = selected.reranker_type(llm_config)
    else:
        # Reuse the Qwen transport so request-level cache_salt applies to
        # Graphiti reranker calls as well as structured construction calls.
        reranker = selected.reranker_type(llm_config, client=raw_transport)
    graphiti = selected.graphiti_type(
        uri=neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=raw_llm,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=max_coroutines,
    )
    clients = getattr(graphiti, "clients", None)
    if graphiti is None or clients is None:
        raise _fail("Graphiti client surface missing")
    admitted_llm = AdmittedLLMClient(
        inner=raw_llm,
        admission=admission,
        request_id_prefix=request_id_prefix,
    )
    try:
        graphiti.llm_client = admitted_llm
        clients.llm_client = admitted_llm
    except Exception:
        raise _fail("Graphiti LLM admission installation failed") from None
    if getattr(graphiti, "llm_client", None) is not admitted_llm or getattr(clients, "llm_client", None) is not admitted_llm:
        raise _fail("Graphiti LLM admission installation failed")
    return MemBindV1LiveRuntime(
        graphiti=graphiti,
        raw_llm=raw_llm,
        public_identity=public_identity,
        execution_envelope_sha256=payload_sha256(public_identity),
    )


__all__ = [
    "MemBindV1LiveRuntime",
    "MemBindV1LiveRuntimeError",
    "RuntimeComponents",
    "build_membind_v1_runtime",
    "project_membind_v1_runtime_identity",
]
