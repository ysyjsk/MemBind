"""Offline production-wiring tests for the isolated S4 capture/replay smoke."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s4_candidate_oracle import CandidateAwareReplayCache
from paper_eval.s4_candidate_projection import PROJECTION_SCHEMA_SHA256
from paper_eval.s4_candidate_sidecar import build_candidate_call_record
from paper_eval.s4_candidate_sidecar_runtime import CandidateSidecarPromptCache
from paper_eval.s4_d0_production import (
    S4CachePaths,
    S4CandidateSidecarConfig,
    NamespaceNormalizedPromptCache,
    S4ProductionFactories,
    build_embedding_namespace,
    build_s4_phase_runtime,
    open_s4_oracles,
)


class FakePromptCache:
    def __init__(self, path: Path, read_only: bool) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self.unexpected_prompt_diagnostics: list[dict] = []
        if self.path.exists():
            return
        if read_only:
            # The production helper must reject this before constructing us.
            return


class FakeEmbeddingCache:
    def __init__(self, path: Path, read_only: bool, namespace: object) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self.namespace = namespace
        self.unexpected_embedding_diagnostics: list[dict] = []
        self.successful_hit_count = 0
        if read_only:
            if not self.path.exists():
                raise FileNotFoundError(self.path)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.open("x", encoding="utf-8").write("embedding-header\n")


@dataclass(frozen=True)
class FakeEmbeddingNamespace:
    served_model_id: str
    identity_kind: str
    identity_value: str
    dimension: int
    dtype: str
    pooling: str
    normalization: str
    instruction_policy: str
    input_transform: str


class FakePromptLLM:
    def __init__(
        self,
        inner: object,
        cache: object,
        model_revision: str,
        decoding_config: dict,
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.model_revision = model_revision
        self.decoding_config = decoding_config

    async def generate_response(self, *args, **kwargs):
        return await self.inner.generate_response(*args, **kwargs)


class FakeCountingEmbedder:
    def __init__(self, inner: object, persistent_cache: object) -> None:
        self.inner = inner
        self.persistent_cache = persistent_cache
        self.api_call_count = 0
        self.text_count = 0
        self.cache_hit_count = 0


class FakeCrossEncoderAudit:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.rank_call_count = 0


class FakeGraph:
    def __init__(self) -> None:
        self.driver = object()
        self.llm_client = SimpleNamespace(call_count=0)
        self.embedder = object()
        self.cross_encoder = object()
        self.clients = SimpleNamespace(
            llm_client=self.llm_client,
            embedder=self.embedder,
            cross_encoder=self.cross_encoder,
        )
        self.nodes = SimpleNamespace(
            entity=SimpleNamespace(_embedder=self.embedder),
            community=SimpleNamespace(_embedder=self.embedder),
        )
        self.edges = SimpleNamespace(
            entity=SimpleNamespace(_embedder=self.embedder),
        )

        async def extract_and_resolve_edges(*args, **kwargs):
            del args, kwargs
            return []

        self._extract_and_resolve_edges = extract_and_resolve_edges


def _paths(tmp_path: Path) -> S4CachePaths:
    return S4CachePaths(
        prompt=tmp_path / "shared.prompt.jsonl",
        embedding=tmp_path / "shared.embedding.jsonl",
    )


def _factories(calls: list[tuple]) -> S4ProductionFactories:
    def build_u0(**kwargs):
        calls.append(("build_u0", set(kwargs)))
        graph = FakeGraph()
        return SimpleNamespace(
            graphiti=graph,
            llm_client=graph.llm_client,
            embedder=graph.embedder,
            reranker=graph.cross_encoder,
            config=SimpleNamespace(
                construction_model_revision="6e2312b85c2ae9a31f629f24493b79d8b02eab1a",
                requested_max_tokens=16384,
            ),
        )

    def build_d0(prompt_cache, embedding_cache, **kwargs):
        calls.append(
            (
                "build_d0",
                prompt_cache.read_only,
                embedding_cache.read_only,
                set(kwargs),
            )
        )
        graph = FakeGraph()
        graph.llm_client = FakePromptLLM(
            graph.llm_client,
            prompt_cache,
            "6e2312b85c2ae9a31f629f24493b79d8b02eab1a",
            {"max_tokens": 16384},
        )
        graph.embedder = FakeCountingEmbedder(graph.embedder, embedding_cache)
        graph.cross_encoder = FakeCrossEncoderAudit(graph.cross_encoder)
        return graph

    async def namespace_state(_driver, namespace: str):
        calls.append(("namespace_state", namespace))
        return {"node_count": 0, "relationship_count": 0, "episode_names": []}

    async def export_graph(_graph, _episodes, namespace: str):
        calls.append(("export", namespace))
        return {"entities": [], "edges": [], "episodes": []}

    async def clear_data(driver, *, group_ids):
        calls.append(("clear_data", driver, group_ids))

    return S4ProductionFactories(
        prompt_cache_type=FakePromptCache,
        prompt_llm_type=FakePromptLLM,
        embedding_namespace_type=FakeEmbeddingNamespace,
        embedding_cache_type=FakeEmbeddingCache,
        caching_embedder_type=FakeCountingEmbedder,
        cross_encoder_audit_type=FakeCrossEncoderAudit,
        build_u0=build_u0,
        build_d0=build_d0,
        namespace_state=namespace_state,
        graph_exporter=export_graph,
        clear_data=clear_data,
        episode_kwargs=lambda episode: {"group_id": episode.group_id},
    )


def _spec(mode: str) -> dict[str, str]:
    return {
        "phase": "U0_CAPTURE" if mode == "capture" else "D0_READ_ONLY_REPLAY",
        "run_id": f"s4-prod-{mode}",
        "history_id": "07741c45",
        "namespace": f"pev3-s4-prod-{mode}",
        "method": "U0" if mode == "capture" else "D0",
        "mode": mode,
        "cache_id": "s4-shared-cache",
    }


@dataclass(frozen=True)
class FakeSourceEpisode:
    source_sequence: int
    source_hash: str
    name: str
    body: str
    group_id: str = "source-group"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sidecar_sources() -> list[FakeSourceEpisode]:
    return [
        FakeSourceEpisode(
            source_sequence=index,
            source_hash=_sha(f"source-{index}"),
            name=f"episode-{index}",
            body=f"synthetic body {index}",
        )
        for index in range(49)
    ]


def _sidecar_config(
    path: Path,
    *,
    namespace: str,
) -> tuple[S4CandidateSidecarConfig, SimpleNamespace]:
    sources = _sidecar_sources()
    manifest = {
        source.name: {
            "body_sha256": _sha(source.body),
            "source_hash": source.source_hash,
            "source_sequence": source.source_sequence,
        }
        for source in sources
    }

    async def resolve_extracted_edge(*args, **kwargs):
        del args, kwargs
        return (None, [], [])

    edge_operations = SimpleNamespace(
        resolve_extracted_edge=resolve_extracted_edge,
    )
    return (
        S4CandidateSidecarConfig(
            path=path,
            identity={
                "attempt_id": "006",
                "cache_id": "s4-d0-sidecar-07741c45-20260815-006",
                "history_id": "07741c45",
                "episode_manifest_sha256": payload_sha256(manifest),
                "projection_schema_sha256": PROJECTION_SCHEMA_SHA256,
            },
            episodes=sources,
            edge_operations_module=edge_operations,
            namespace=namespace,
        ),
        edge_operations,
    )


def test_embedding_namespace_is_the_operator_attested_deployment() -> None:
    namespace = build_embedding_namespace(FakeEmbeddingNamespace)

    assert namespace == FakeEmbeddingNamespace(
        served_model_id="qwen3-embedding-0.6b",
        identity_kind="deployment_fingerprint",
        identity_value=(
            "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"
        ),
        dimension=1024,
        dtype="bfloat16",
        pooling="last_token",
        normalization="l2",
        instruction_policy="none",
        input_transform="utf8_exact_v1",
    )


def test_oracle_open_modes_are_fail_closed_and_capture_resume_is_explicit(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    namespace = build_embedding_namespace(FakeEmbeddingNamespace)

    capture = open_s4_oracles(
        paths=paths,
        mode="capture",
        resume_capture=False,
        namespace=namespace,
        prompt_cache_type=FakePromptCache,
        embedding_cache_type=FakeEmbeddingCache,
    )
    assert capture.prompt.read_only is False
    assert capture.embedding.read_only is False
    assert paths.prompt.is_file()
    assert paths.embedding.is_file()

    with pytest.raises(FileExistsError):
        open_s4_oracles(
            paths=paths,
            mode="capture",
            resume_capture=False,
            namespace=namespace,
            prompt_cache_type=FakePromptCache,
            embedding_cache_type=FakeEmbeddingCache,
        )

    resumed = open_s4_oracles(
        paths=paths,
        mode="capture",
        resume_capture=True,
        namespace=namespace,
        prompt_cache_type=FakePromptCache,
        embedding_cache_type=FakeEmbeddingCache,
    )
    assert resumed.prompt.read_only is False
    assert resumed.embedding.read_only is False

    replay = open_s4_oracles(
        paths=paths,
        mode="replay",
        resume_capture=False,
        namespace=namespace,
        prompt_cache_type=FakePromptCache,
        embedding_cache_type=FakeEmbeddingCache,
    )
    assert replay.prompt.read_only is True
    assert replay.embedding.read_only is True

    with pytest.raises(ValueError, match="resume"):
        open_s4_oracles(
            paths=paths,
            mode="replay",
            resume_capture=True,
            namespace=namespace,
            prompt_cache_type=FakePromptCache,
            embedding_cache_type=FakeEmbeddingCache,
        )


def test_replay_rejects_missing_or_partial_shared_cache(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    namespace = build_embedding_namespace(FakeEmbeddingNamespace)

    with pytest.raises(FileNotFoundError):
        open_s4_oracles(
            paths=paths,
            mode="replay",
            resume_capture=False,
            namespace=namespace,
            prompt_cache_type=FakePromptCache,
            embedding_cache_type=FakeEmbeddingCache,
        )

    paths.prompt.parent.mkdir(parents=True, exist_ok=True)
    paths.prompt.write_text("prompt-only\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        open_s4_oracles(
            paths=paths,
            mode="replay",
            resume_capture=False,
            namespace=namespace,
            prompt_cache_type=FakePromptCache,
            embedding_cache_type=FakeEmbeddingCache,
        )


@pytest.mark.asyncio
async def test_capture_uses_only_u0_factory_and_exact_namespace_cleanup(
    tmp_path: Path,
) -> None:
    calls: list[tuple] = []
    runtime = build_s4_phase_runtime(
        spec=_spec("capture"),
        cache_paths=_paths(tmp_path),
        factories=_factories(calls),
        resume_capture=False,
    )

    assert [call[0] for call in calls] == ["build_u0"]
    assert isinstance(runtime.graph.llm_client.inner, FakePromptLLM)
    assert isinstance(
        runtime.graph.llm_client.inner.cache,
        NamespaceNormalizedPromptCache,
    )
    assert not isinstance(
        runtime.graph.llm_client.inner.cache.inner,
        CandidateAwareReplayCache,
    )
    assert isinstance(runtime.graph.embedder, FakeCountingEmbedder)
    assert isinstance(runtime.graph.cross_encoder, FakeCrossEncoderAudit)
    assert runtime.graph.clients.llm_client is runtime.graph.llm_client
    assert runtime.graph.clients.embedder is runtime.graph.embedder
    assert runtime.graph.clients.cross_encoder is runtime.graph.cross_encoder
    assert runtime.graph.nodes.entity._embedder is runtime.graph.embedder
    assert runtime.graph.nodes.community._embedder is runtime.graph.embedder
    assert runtime.graph.edges.entity._embedder is runtime.graph.embedder

    await runtime.cleanup_namespace("pev3-s4-prod-capture")
    assert calls[-1][0] == "clear_data"
    assert calls[-1][2] == ["pev3-s4-prod-capture"]
    with pytest.raises(ValueError, match="namespace"):
        await runtime.cleanup_namespace("pev3-s4-other")


@pytest.mark.asyncio
async def test_replay_uses_d0_factory_read_only_and_reports_zero_live_calls(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    namespace = build_embedding_namespace(FakeEmbeddingNamespace)
    open_s4_oracles(
        paths=paths,
        mode="capture",
        resume_capture=False,
        namespace=namespace,
        prompt_cache_type=FakePromptCache,
        embedding_cache_type=FakeEmbeddingCache,
    )
    calls: list[tuple] = []
    runtime = build_s4_phase_runtime(
        spec=_spec("replay"),
        cache_paths=paths,
        factories=_factories(calls),
        resume_capture=False,
    )

    assert calls == [("build_d0", True, True, {"authorization_checker"})]
    assert runtime.graph.clients.llm_client is runtime.graph.llm_client
    assert runtime.graph.clients.embedder is runtime.graph.embedder
    assert runtime.graph.clients.cross_encoder is runtime.graph.cross_encoder
    namespace_cache = runtime.graph.llm_client.inner.cache
    assert isinstance(namespace_cache, NamespaceNormalizedPromptCache)
    candidate_cache = namespace_cache.inner
    assert isinstance(candidate_cache, CandidateAwareReplayCache)
    assert candidate_cache.inner.read_only is True
    candidate_cache.exact_prompt_hit_count = 7
    candidate_cache.candidate_remap_hit_count = 2
    candidate_cache.candidate_remap_rejection_count = 0
    candidate_cache.remap_hit_counts = {
        "dedupe_nodes.nodes": 1,
        "dedupe_edges.resolve_edge": 1,
    }
    evidence = runtime.runtime_evidence()
    assert evidence == {
        "live_llm_calls": 0,
        "live_embedding_calls": 0,
        "resolved_prompt_count": 0,
        "resolved_embedding_count": 0,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "cross_encoder_call_count": 0,
        "exact_prompt_hit_count": 7,
        "candidate_remap_hit_count": 2,
        "candidate_remap_node_hit_count": 1,
        "candidate_remap_edge_hit_count": 1,
        "candidate_remap_rejection_count": 0,
    }
    before = runtime.cache_evidence()
    after = runtime.cache_evidence()
    assert before == after


def test_replay_build_uses_frozen_16k_without_mutating_parent_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    namespace = build_embedding_namespace(FakeEmbeddingNamespace)
    open_s4_oracles(
        paths=paths,
        mode="capture",
        resume_capture=False,
        namespace=namespace,
        prompt_cache_type=FakePromptCache,
        embedding_cache_type=FakeEmbeddingCache,
    )
    calls: list[tuple] = []
    factories = _factories(calls)
    original = factories.build_d0

    def assert_frozen(*args, **kwargs):
        import os

        assert os.environ["CONSTRUCTION_MAX_TOKENS"] == "16384"
        return original(*args, **kwargs)

    factories = S4ProductionFactories(
        **{
            **factories.__dict__,
            "build_d0": assert_frozen,
        }
    )
    monkeypatch.setenv("CONSTRUCTION_MAX_TOKENS", "2048")

    build_s4_phase_runtime(
        spec=_spec("replay"),
        cache_paths=paths,
        factories=factories,
        resume_capture=False,
    )

    assert __import__("os").environ["CONSTRUCTION_MAX_TOKENS"] == "2048"


@dataclass(frozen=True)
class FakePromptParts:
    model_revision: str
    decoding_config: dict
    structured_output_schema: dict
    system_prompt: str
    user_prompt: str


def test_prompt_oracle_normalizes_only_cache_key_namespace_metadata() -> None:
    seen: list[tuple[str, FakePromptParts]] = []

    class Cache:
        read_only = False

        def get(self, parts):
            seen.append(("get", parts))
            return None

        def put(self, parts, *args, **kwargs):
            seen.append(("put", parts))
            return "stored"

        def record_unexpected(self, parts):
            seen.append(("unexpected", parts))
            return {}

    proxy = NamespaceNormalizedPromptCache(Cache())
    original = FakePromptParts(
        model_revision="revision",
        decoding_config={
            "group_id": "pev3-s4-u0-capture-20260814-001",
            "max_tokens": 16384,
        },
        structured_output_schema={"type": "object"},
        system_prompt="unchanged-system",
        user_prompt="unchanged-user",
    )

    proxy.get(original)
    proxy.put(original, "raw", {"ok": True})
    proxy.record_unexpected(original)

    assert original.decoding_config["group_id"] == (
        "pev3-s4-u0-capture-20260814-001"
    )
    assert [kind for kind, _ in seen] == ["get", "put", "unexpected"]
    for _, normalized in seen:
        assert normalized.system_prompt == "unchanged-system"
        assert normalized.user_prompt == "unchanged-user"
        assert normalized.structured_output_schema == {"type": "object"}
        assert normalized.decoding_config == {
            "group_id": "__S4_ISOLATED_NAMESPACE__",
            "max_tokens": 16384,
        }


def test_default_runtime_sidecar_surface_is_noop_and_evidence_is_unchanged(
    tmp_path: Path,
) -> None:
    runtime = build_s4_phase_runtime(
        spec=_spec("capture"),
        cache_paths=_paths(tmp_path),
        factories=_factories([]),
        resume_capture=False,
    )
    original_outer = runtime.graph._extract_and_resolve_edges

    with runtime.phase_context():
        assert runtime.graph._extract_and_resolve_edges is original_outer

    assert runtime.restore_sidecar_prefix([]) is None
    assert runtime.pre_finalize_sidecar(runtime.cache_evidence()) is None
    assert runtime.sidecar_counters() == {}
    assert runtime.sidecar_cache_sha256() is None
    assert set(runtime.cache_evidence()) == {
        "prompt_cache_sha256",
        "embedding_cache_sha256",
    }


def test_optional_capture_sidecar_has_exact_wrapper_order_and_phase_hooks(
    tmp_path: Path,
) -> None:
    sidecar, edge_operations = _sidecar_config(
        tmp_path / "candidate-sidecar.jsonl",
        namespace=_spec("capture")["namespace"],
    )
    runtime = build_s4_phase_runtime(
        spec=_spec("capture"),
        cache_paths=_paths(tmp_path),
        factories=_factories([]),
        resume_capture=False,
        sidecar=sidecar,
    )

    namespace_cache = runtime.graph.llm_client.inner.cache
    assert isinstance(namespace_cache, NamespaceNormalizedPromptCache)
    sidecar_cache = namespace_cache.inner
    assert isinstance(sidecar_cache, CandidateSidecarPromptCache)
    assert sidecar_cache.mode == "capture"
    assert sidecar_cache.inner is runtime.prompt_cache
    assert not isinstance(sidecar_cache.inner, CandidateAwareReplayCache)

    original_outer = runtime.graph._extract_and_resolve_edges
    original_inner = edge_operations.resolve_extracted_edge
    with runtime.phase_context():
        assert runtime.graph._extract_and_resolve_edges is not original_outer
        assert edge_operations.resolve_extracted_edge is not original_inner
    assert runtime.graph._extract_and_resolve_edges is original_outer
    assert edge_operations.resolve_extracted_edge is original_inner

    assert runtime.restore_sidecar_prefix([]) is None
    assert runtime.sidecar_counters() == {
        "capture_append_count": 0,
        "capture_reuse_count": 0,
        "replay_binding_count": 0,
        "consumed_call_count": 0,
        "remaining_call_count": 0,
        "resumed_consumed_call_count": 0,
        "record_count": 0,
        "prepared_call_count": 0,
    }
    assert runtime.sidecar_cache_sha256() == _sha(
        (tmp_path / "candidate-sidecar.jsonl").read_text(encoding="ascii")
    )
    assert runtime.runtime_evidence() | {} == {
        **{
            "live_llm_calls": 0,
            "live_embedding_calls": 0,
            "resolved_prompt_count": 0,
            "resolved_embedding_count": 0,
            "unexpected_prompt_count": 0,
            "unexpected_embedding_count": 0,
            "live_fallback_count": 0,
            "cross_encoder_call_count": 0,
        },
        "sidecar_exact_hit_count": 0,
        "sidecar_remap_hit_count": 0,
        "sidecar_rejection_count": 0,
        "sidecar_capture_append_count": 0,
        "sidecar_capture_reuse_count": 0,
        "sidecar_replay_binding_count": 0,
        "sidecar_record_count": 0,
        "sidecar_consumed_count": 0,
        "sidecar_remaining_count": 0,
        "sidecar_resumed_consumed_count": 0,
        "sidecar_prepared_count": 0,
    }
    assert set(runtime.cache_evidence()) == {
        "prompt_cache_sha256",
        "embedding_cache_sha256",
        "candidate_sidecar_sha256",
    }


def test_sidecar_capture_finalize_and_replay_prefix_restore_are_hash_stable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-sidecar.jsonl"
    paths = _paths(tmp_path)
    capture_config, _ = _sidecar_config(
        path,
        namespace=_spec("capture")["namespace"],
    )
    capture = build_s4_phase_runtime(
        spec=_spec("capture"),
        cache_paths=paths,
        factories=_factories([]),
        resume_capture=False,
        sidecar=capture_config,
    )
    capture_wrapper = capture.graph.llm_client.inner.cache.inner
    capture_wrapper.store.append(
        build_candidate_call_record(
            source_sequence=0,
            source_hash=_sha("source-0"),
            logical_call_sha256=_sha("logical-call-0"),
            prompt_sha256=_sha("prompt-0"),
            related=[],
            invalidation=[],
        )
    )
    seal = capture.pre_finalize_sidecar(capture.cache_evidence())
    sealed_sha = capture.sidecar_cache_sha256()

    assert seal["record_count"] == 1
    assert seal["cache_evidence"] == {
        key: value
        for key, value in capture.cache_evidence().items()
        if key != "candidate_sidecar_sha256"
    }
    assert capture.pre_finalize_sidecar(capture.cache_evidence()) == seal
    assert capture.sidecar_cache_sha256() == sealed_sha

    replay_config, _ = _sidecar_config(
        path,
        namespace=_spec("replay")["namespace"],
    )
    replay = build_s4_phase_runtime(
        spec=_spec("replay"),
        cache_paths=paths,
        factories=_factories([]),
        resume_capture=False,
        sidecar=replay_config,
    )

    namespace_cache = replay.graph.llm_client.inner.cache
    assert isinstance(namespace_cache, NamespaceNormalizedPromptCache)
    sidecar_cache = namespace_cache.inner
    assert isinstance(sidecar_cache, CandidateSidecarPromptCache)
    assert sidecar_cache.mode == "replay"
    assert isinstance(sidecar_cache.inner, CandidateAwareReplayCache)
    assert sidecar_cache.inner.inner is replay.prompt_cache
    sidecar_cache.inner.sidecar_exact_hit_count = 1
    sidecar_cache.inner.sidecar_remap_hit_count = 2
    sidecar_cache.inner.sidecar_rejection_count = 0

    replay.restore_sidecar_prefix([0])
    assert replay.sidecar_counters() == {
        "capture_append_count": 0,
        "capture_reuse_count": 0,
        "replay_binding_count": 0,
        "consumed_call_count": 1,
        "remaining_call_count": 0,
        "resumed_consumed_call_count": 1,
        "record_count": 1,
        "prepared_call_count": 0,
    }
    assert replay.pre_finalize_sidecar(replay.cache_evidence()) == seal
    assert replay.sidecar_cache_sha256() == sealed_sha
    evidence = replay.runtime_evidence()
    assert evidence["sidecar_exact_hit_count"] == 1
    assert evidence["sidecar_remap_hit_count"] == 2
    assert evidence["sidecar_rejection_count"] == 0
    assert evidence["sidecar_replay_binding_count"] == 0


def test_sealed_capture_resume_requires_the_complete_checkpoint_prefix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-sidecar.jsonl"
    paths = _paths(tmp_path)
    config, _ = _sidecar_config(
        path,
        namespace=_spec("capture")["namespace"],
    )
    capture = build_s4_phase_runtime(
        spec=_spec("capture"),
        cache_paths=paths,
        factories=_factories([]),
        resume_capture=False,
        sidecar=config,
    )
    sealed = capture.pre_finalize_sidecar(capture.cache_evidence())

    resumed = build_s4_phase_runtime(
        spec=_spec("capture"),
        cache_paths=paths,
        factories=_factories([]),
        resume_capture=True,
        sidecar=config,
    )

    with pytest.raises(ValueError, match="sealed.*prefix"):
        resumed.restore_sidecar_prefix(list(range(48)))
    resumed.restore_sidecar_prefix(list(range(49)))
    assert resumed.pre_finalize_sidecar(resumed.cache_evidence()) == sealed


def test_unsealed_capture_resume_rejects_sidecar_records_beyond_failed_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-sidecar.jsonl"
    paths = _paths(tmp_path)
    config, _ = _sidecar_config(
        path,
        namespace=_spec("capture")["namespace"],
    )
    capture = build_s4_phase_runtime(
        spec=_spec("capture"),
        cache_paths=paths,
        factories=_factories([]),
        resume_capture=False,
        sidecar=config,
    )
    capture.graph.llm_client.inner.cache.inner.store.append(
        build_candidate_call_record(
            source_sequence=2,
            source_hash=_sha("source-2"),
            logical_call_sha256=_sha("future-call"),
            prompt_sha256=_sha("future-prompt"),
            related=[],
            invalidation=[],
        )
    )

    resumed = build_s4_phase_runtime(
        spec=_spec("capture"),
        cache_paths=paths,
        factories=_factories([]),
        resume_capture=True,
        sidecar=config,
    )

    with pytest.raises(ValueError, match="future source"):
        resumed.restore_sidecar_prefix([])
