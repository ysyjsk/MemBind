"""Lazy production wiring for the isolated S4 U0-capture/D0-replay phases.

The module has no import-time dependency on the legacy Graphiti environment.
Live constructors are loaded only after a stage controller consumes authority.
"""

from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .artifacts import sha256_file
from .s4_candidate_oracle import (
    EDGE_PROMPT,
    NODE_PROMPT,
    CandidateAwareReplayCache,
)


ROOT = Path(__file__).resolve().parents[3]
LEGACY = ROOT / "membind-validation"

CONSTRUCTION_MODEL_REVISION = "6e2312b85c2ae9a31f629f24493b79d8b02eab1a"
EMBEDDING_DEPLOYMENT_FINGERPRINT = (
    "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"
)


@dataclass(frozen=True)
class S4CachePaths:
    """The one shared prompt/embedding oracle pair for capture and replay."""

    prompt: Path
    embedding: Path

    def __post_init__(self) -> None:
        prompt = Path(self.prompt)
        embedding = Path(self.embedding)
        if prompt == embedding:
            raise ValueError("S4 prompt and embedding cache paths must differ")
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "embedding", embedding)


@dataclass(frozen=True)
class S4Oracles:
    prompt: Any
    embedding: Any


@dataclass(frozen=True)
class S4ProductionFactories:
    """Injectable legacy surface; tests never import or contact Graphiti."""

    prompt_cache_type: Any
    prompt_llm_type: Any
    embedding_namespace_type: Any
    embedding_cache_type: Any
    caching_embedder_type: Any
    cross_encoder_audit_type: Any
    build_u0: Callable[..., Any]
    build_d0: Callable[..., Any]
    namespace_state: Callable[[Any, str], Awaitable[Mapping[str, Any]] | Mapping[str, Any]]
    graph_exporter: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]]
    clear_data: Callable[..., Awaitable[Any] | Any]
    episode_kwargs: Callable[[Any], Mapping[str, Any]]


@dataclass(frozen=True)
class S4PhaseRuntime:
    graph: Any
    episode_kwargs: Callable[[Any], Mapping[str, Any]]
    namespace_probe: Callable[[], Awaitable[Mapping[str, Any]]]
    graph_exporter: Callable[..., Awaitable[Mapping[str, Any]]]
    runtime_evidence: Callable[[], dict[str, int]]
    cache_evidence: Callable[[], dict[str, str | None]]
    cleanup_namespace: Callable[[str], Awaitable[Any]]
    prompt_cache: Any
    embedding_cache: Any


class _ResolvedPromptCounter:
    """Count logical Graphiti prompt resolutions around the existing oracle."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.resolved_prompt_count = 0
        self.config = getattr(inner, "config", None)
        self.model = getattr(inner, "model", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def generate_response(self, *args: Any, **kwargs: Any) -> Any:
        self.resolved_prompt_count += 1
        value = self.inner.generate_response(*args, **kwargs)
        return await value if inspect.isawaitable(value) else value


class NamespaceNormalizedPromptCache:
    """Project only the isolated group ID in persistent oracle cache keys."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    @staticmethod
    def _parts(parts: Any) -> Any:
        decoding = dict(getattr(parts, "decoding_config"))
        if decoding.get("group_id") is not None:
            decoding["group_id"] = "__S4_ISOLATED_NAMESPACE__"
        return replace(parts, decoding_config=decoding)

    def get(self, parts: Any) -> Any:
        return self.inner.get(self._parts(parts))

    def put(self, parts: Any, *args: Any, **kwargs: Any) -> Any:
        return self.inner.put(self._parts(parts), *args, **kwargs)

    def record_unexpected(self, parts: Any) -> Any:
        return self.inner.record_unexpected(self._parts(parts))

    def resolve(self, parts: Any, *args: Any, **kwargs: Any) -> Any:
        return self.inner.resolve(self._parts(parts), *args, **kwargs)


def build_embedding_namespace(namespace_type: Any) -> Any:
    """Build the exact operator-attested Qwen3 embedding identity."""

    return namespace_type(
        served_model_id="qwen3-embedding-0.6b",
        identity_kind="deployment_fingerprint",
        identity_value=EMBEDDING_DEPLOYMENT_FINGERPRINT,
        dimension=1024,
        dtype="bfloat16",
        pooling="last_token",
        normalization="l2",
        instruction_policy="none",
        input_transform="utf8_exact_v1",
    )


def _require_cache_files(paths: S4CachePaths) -> None:
    missing = [path for path in (paths.prompt, paths.embedding) if not path.is_file()]
    if missing:
        raise FileNotFoundError(str(missing[0]))


def open_s4_oracles(
    *,
    paths: S4CachePaths,
    mode: str,
    resume_capture: bool,
    namespace: Any,
    prompt_cache_type: Any,
    embedding_cache_type: Any,
) -> S4Oracles:
    """Open a new capture, an explicit same-run resume, or read-only replay."""

    if mode not in {"capture", "replay"}:
        raise ValueError("unsupported S4 oracle mode")
    if mode == "replay" and resume_capture:
        raise ValueError("resume_capture is invalid during replay")

    if mode == "capture" and not resume_capture:
        existing = [path for path in (paths.prompt, paths.embedding) if path.exists()]
        if existing:
            raise FileExistsError(str(existing[0]))
        paths.prompt.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            paths.prompt,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(paths.prompt.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        prompt = prompt_cache_type(paths.prompt, read_only=False)
        embedding = embedding_cache_type(
            paths.embedding,
            read_only=False,
            namespace=namespace,
        )
        return S4Oracles(prompt=prompt, embedding=embedding)

    _require_cache_files(paths)
    if mode == "replay":
        return S4Oracles(
            prompt=prompt_cache_type(paths.prompt, read_only=True),
            embedding=embedding_cache_type(
                paths.embedding,
                read_only=True,
                namespace=namespace,
            ),
        )

    # EmbeddingCache intentionally creates captures with O_EXCL. Reopen it
    # read-only to validate every record, then grant writes only to this
    # explicitly authorized same-run resume.
    prompt = prompt_cache_type(paths.prompt, read_only=False)
    embedding = embedding_cache_type(
        paths.embedding,
        read_only=True,
        namespace=namespace,
    )
    embedding.read_only = False
    return S4Oracles(prompt=prompt, embedding=embedding)


def _nested_int(value: Any, field: str) -> int:
    current = value
    seen: set[int] = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        selected = getattr(current, field, None)
        if selected is not None:
            return int(selected)
        current = getattr(current, "inner", None)
    return 0


def _install_graph_clients(
    graph: Any,
    *,
    llm_client: Any,
    embedder: Any,
    cross_encoder: Any,
) -> None:
    """Replace every client reference retained by pinned Graphiti 0.29.3."""

    graph.llm_client = llm_client
    graph.embedder = embedder
    graph.cross_encoder = cross_encoder
    clients = getattr(graph, "clients", None)
    if clients is None:
        raise ValueError("Graphiti runtime is missing its retained client bundle")
    clients.llm_client = llm_client
    clients.embedder = embedder
    clients.cross_encoder = cross_encoder
    for container_name in ("nodes", "edges"):
        container = getattr(graph, container_name, None)
        if container is None:
            continue
        for child_name in ("entity", "community"):
            child = getattr(container, child_name, None)
            if child is not None and hasattr(child, "_embedder"):
                child._embedder = embedder


def _cache_sha(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def _validate_spec(spec: Mapping[str, Any]) -> dict[str, str]:
    selected = {key: str(value) for key, value in dict(spec).items()}
    expected_fields = {
        "phase",
        "run_id",
        "history_id",
        "namespace",
        "method",
        "mode",
        "cache_id",
    }
    if set(selected) != expected_fields:
        raise ValueError("S4 production phase spec shape drift")
    expected = {
        "capture": ("U0_CAPTURE", "U0"),
        "replay": ("D0_READ_ONLY_REPLAY", "D0"),
    }
    if selected["mode"] not in expected or (
        selected["phase"],
        selected["method"],
    ) != expected[selected["mode"]]:
        raise ValueError("S4 production method/mode drift")
    if not selected["namespace"].startswith("pev3-s4-"):
        raise ValueError("S4 production namespace is outside the isolated prefix")
    return selected


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _production_factories() -> S4ProductionFactories:
    source = str(LEGACY / "src")
    if source not in sys.path:
        sys.path.insert(0, source)

    from embedding_cache import (
        CachingCountingEmbedder,
        EmbeddingCache,
        EmbeddingNamespace,
    )
    from graphiti_core.utils.maintenance.graph_data_operations import clear_data
    from graphiti_native import (
        build_qwen_graphiti_from_env,
        graphiti_episode_kwargs,
        load_env_file,
    )
    from live_outputs import export_canonical_graph
    from model_oracle_audit import CrossEncoderAuditWrapper
    from native_characterization_runtime import build_u0_graphiti_from_env
    from response_cache import GraphitiPromptCacheLLM, PromptCache

    async def namespace_state(driver: Any, namespace: str) -> Mapping[str, Any]:
        from paper_eval.s1_live import S1LiveAdapter

        return await S1LiveAdapter(namespace).namespace_state(driver)

    def build_u0(**kwargs: Any) -> Any:
        return build_u0_graphiti_from_env(
            authorization_checker=kwargs["authorization_checker"],
            env_loader=lambda: load_env_file(LEGACY / ".env"),
        )

    return S4ProductionFactories(
        prompt_cache_type=PromptCache,
        prompt_llm_type=GraphitiPromptCacheLLM,
        embedding_namespace_type=EmbeddingNamespace,
        embedding_cache_type=EmbeddingCache,
        caching_embedder_type=CachingCountingEmbedder,
        cross_encoder_audit_type=CrossEncoderAuditWrapper,
        build_u0=build_u0,
        build_d0=build_qwen_graphiti_from_env,
        namespace_state=namespace_state,
        graph_exporter=export_canonical_graph,
        clear_data=clear_data,
        episode_kwargs=graphiti_episode_kwargs,
    )


def build_s4_phase_runtime(
    *,
    spec: Mapping[str, Any],
    cache_paths: S4CachePaths,
    resume_capture: bool,
    factories: S4ProductionFactories | None = None,
) -> S4PhaseRuntime:
    """Build clients after authority; this function itself issues no request."""

    selected = _validate_spec(spec)
    components = factories or _production_factories()
    namespace_identity = build_embedding_namespace(
        components.embedding_namespace_type
    )
    oracles = open_s4_oracles(
        paths=cache_paths,
        mode=selected["mode"],
        resume_capture=resume_capture,
        namespace=namespace_identity,
        prompt_cache_type=components.prompt_cache_type,
        embedding_cache_type=components.embedding_cache_type,
    )
    candidate_prompt_cache = (
        CandidateAwareReplayCache(oracles.prompt)
        if selected["mode"] == "replay"
        else None
    )
    prompt_cache = NamespaceNormalizedPromptCache(
        candidate_prompt_cache or oracles.prompt
    )

    no_gate = lambda _action: None
    if selected["mode"] == "capture":
        native = components.build_u0(authorization_checker=no_gate)
        graph = native.graphiti
        decoding = {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": int(native.config.requested_max_tokens),
            "seed": 20260806,
        }
        prompt_oracle = components.prompt_llm_type(
            native.llm_client,
            prompt_cache,
            str(native.config.construction_model_revision),
            decoding,
        )
        prompt_counter = _ResolvedPromptCounter(prompt_oracle)
        embedder = components.caching_embedder_type(
            native.embedder,
            persistent_cache=oracles.embedding,
        )
        cross_encoder = components.cross_encoder_audit_type(native.reranker)
    else:
        prior_max_tokens = os.environ.get("CONSTRUCTION_MAX_TOKENS")
        os.environ["CONSTRUCTION_MAX_TOKENS"] = "16384"
        try:
            graph = components.build_d0(
                prompt_cache,
                oracles.embedding,
                authorization_checker=no_gate,
            )
        finally:
            if prior_max_tokens is None:
                os.environ.pop("CONSTRUCTION_MAX_TOKENS", None)
            else:
                os.environ["CONSTRUCTION_MAX_TOKENS"] = prior_max_tokens
        prompt_counter = _ResolvedPromptCounter(graph.llm_client)
        graph.llm_client = prompt_counter
        embedder = graph.embedder
        cross_encoder = graph.cross_encoder

    _install_graph_clients(
        graph,
        llm_client=prompt_counter,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )

    def runtime_evidence() -> dict[str, int]:
        live_llm = _nested_int(prompt_counter, "call_count")
        live_embedding = int(getattr(embedder, "api_call_count", 0))
        embedding_texts = int(getattr(embedder, "text_count", 0))
        embedding_hits = int(getattr(embedder, "cache_hit_count", 0))
        unexpected_prompts = len(
            getattr(oracles.prompt, "unexpected_prompt_diagnostics", []) or []
        )
        unexpected_embeddings = len(
            getattr(oracles.embedding, "unexpected_embedding_diagnostics", [])
            or []
        )
        evidence = {
            "live_llm_calls": live_llm,
            "live_embedding_calls": live_embedding,
            "resolved_prompt_count": int(prompt_counter.resolved_prompt_count),
            "resolved_embedding_count": embedding_texts + embedding_hits,
            "unexpected_prompt_count": unexpected_prompts,
            "unexpected_embedding_count": unexpected_embeddings,
            "live_fallback_count": (
                live_llm + live_embedding if selected["mode"] == "replay" else 0
            ),
            "cross_encoder_call_count": int(
                getattr(cross_encoder, "rank_call_count", 0)
            ),
        }
        if candidate_prompt_cache is not None:
            evidence.update(
                {
                    "exact_prompt_hit_count": int(
                        candidate_prompt_cache.exact_prompt_hit_count
                    ),
                    "candidate_remap_hit_count": int(
                        candidate_prompt_cache.candidate_remap_hit_count
                    ),
                    "candidate_remap_node_hit_count": int(
                        candidate_prompt_cache.remap_hit_counts.get(NODE_PROMPT, 0)
                    ),
                    "candidate_remap_edge_hit_count": int(
                        candidate_prompt_cache.remap_hit_counts.get(EDGE_PROMPT, 0)
                    ),
                    "candidate_remap_rejection_count": int(
                        candidate_prompt_cache.candidate_remap_rejection_count
                    ),
                }
            )
        return evidence

    def cache_evidence() -> dict[str, str | None]:
        return {
            "prompt_cache_sha256": _cache_sha(cache_paths.prompt),
            "embedding_cache_sha256": _cache_sha(cache_paths.embedding),
        }

    async def namespace_probe() -> Mapping[str, Any]:
        return await _await(
            components.namespace_state(graph.driver, selected["namespace"])
        )

    async def graph_exporter(
        selected_graph: Any,
        episodes: Sequence[Any],
        namespace: str,
    ) -> Mapping[str, Any]:
        if selected_graph is not graph or namespace != selected["namespace"]:
            raise ValueError("S4 graph export identity drift")
        return await _await(
            components.graph_exporter(selected_graph, episodes, namespace)
        )

    async def cleanup_namespace(namespace: str) -> Any:
        if namespace != selected["namespace"]:
            raise ValueError("S4 cleanup namespace drift")
        return await _await(
            components.clear_data(graph.driver, group_ids=[namespace])
        )

    def episode_kwargs(episode: Any) -> Mapping[str, Any]:
        rebound = replace(episode, group_id=selected["namespace"])
        kwargs = dict(components.episode_kwargs(rebound))
        if kwargs.get("group_id") != selected["namespace"]:
            raise ValueError("S4 episode escaped the isolated namespace")
        return kwargs

    return S4PhaseRuntime(
        graph=graph,
        episode_kwargs=episode_kwargs,
        namespace_probe=namespace_probe,
        graph_exporter=graph_exporter,
        runtime_evidence=runtime_evidence,
        cache_evidence=cache_evidence,
        cleanup_namespace=cleanup_namespace,
        prompt_cache=oracles.prompt,
        embedding_cache=oracles.embedding,
    )
