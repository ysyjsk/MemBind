"""One gold-blind Graphiti edge+episode query for Quality Evaluation v1."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .artifacts import payload_sha256
from .quality_evaluation_v1 import RetrievedEpisode, RetrievedFact
from .s2_retrieval_probe import ProbeCounters, _read_only_query_guard


TOP_K = 20


class QualityV1RetrievalError(ValueError):
    """Graphiti returned evidence outside the frozen read-only contract."""


@dataclass(frozen=True)
class _OfflineLayer:
    search_methods: tuple[str, ...]
    reranker: str

    def model_dump(self, **_kwargs: object) -> dict[str, Any]:
        return {
            "search_methods": list(self.search_methods),
            "reranker": self.reranker,
            "sim_min_score": 0.6,
            "mmr_lambda": 0.5,
            "bfs_max_depth": 3,
        }


@dataclass(frozen=True)
class _OfflineConfig:
    edge_config: _OfflineLayer
    episode_config: _OfflineLayer
    node_config: None = None
    community_config: None = None
    limit: int = TOP_K
    reranker_min_score: int = 0

    def model_dump(self, **_kwargs: object) -> dict[str, Any]:
        return {
            "edge_config": self.edge_config.model_dump(),
            "node_config": None,
            "episode_config": self.episode_config.model_dump(),
            "community_config": None,
            "limit": self.limit,
            "reranker_min_score": self.reranker_min_score,
        }


def build_quality_v1_search_config() -> Any:
    """Use Graphiti's native edge hybrid RRF and episode BM25 RRF surfaces."""

    try:
        module = importlib.import_module("graphiti_core.search.search_config")
    except ModuleNotFoundError:
        return _OfflineConfig(
            edge_config=_OfflineLayer(("bm25", "cosine_similarity"), "rrf"),
            episode_config=_OfflineLayer(("bm25",), "rrf"),
        )
    return module.SearchConfig(
        edge_config=module.EdgeSearchConfig(
            search_methods=[
                module.EdgeSearchMethod.bm25,
                module.EdgeSearchMethod.cosine_similarity,
            ],
            reranker=module.EdgeReranker.rrf,
        ),
        episode_config=module.EpisodeSearchConfig(
            search_methods=[module.EpisodeSearchMethod.bm25],
            reranker=module.EpisodeReranker.rrf,
        ),
        limit=TOP_K,
    )


@dataclass(frozen=True)
class QualityV1RetrievalBundle:
    facts: tuple[RetrievedFact, ...]
    episodes: tuple[RetrievedEpisode, ...]
    search_config_sha256: str
    graphiti_search_calls: int
    neo4j_read_requests: int


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualityV1RetrievalError(f"Quality v1 graph {field} is invalid")
    return value.strip()


def _time(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    method = getattr(value, "isoformat", None)
    if callable(method):
        return str(method())
    return str(value)


def _mapping(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise QualityV1RetrievalError("Quality v1 episode mapping is invalid")
    result = {str(key): str(child) for key, child in value.items()}
    if not result or any(not key or not child for key, child in result.items()):
        raise QualityV1RetrievalError("Quality v1 episode mapping is invalid")
    return result


async def retrieve_quality_v1(
    *,
    graph: Any,
    query: str,
    namespace: str,
    episode_uuid_to_session_id: Mapping[str, str],
    search_config_factory: Callable[[], Any] = build_quality_v1_search_config,
) -> QualityV1RetrievalBundle:
    """Return ranked edges and episodes without receiving evaluation labels."""

    question = _text(query, field="query")
    group_id = _text(namespace, field="namespace")
    provenance = _mapping(episode_uuid_to_session_id)
    config = search_config_factory()
    if (
        config is None
        or getattr(config, "limit", None) != TOP_K
        or getattr(config, "edge_config", None) is None
        or getattr(config, "episode_config", None) is None
        or getattr(config, "node_config", None) is not None
        or getattr(config, "community_config", None) is not None
    ):
        raise QualityV1RetrievalError("Quality v1 search config is invalid")

    counters = ProbeCounters()
    with _read_only_query_guard(graph.driver, counters):
        counters.graphiti_search_calls += 1
        result = await graph.search_(
            question,
            config=config,
            group_ids=[group_id],
        )
    edges = getattr(result, "edges", None)
    episodes = getattr(result, "episodes", None)
    if not isinstance(edges, list) or not isinstance(episodes, list):
        raise QualityV1RetrievalError("Quality v1 graph result shape is invalid")
    if len(edges) > TOP_K or len(episodes) > TOP_K:
        raise QualityV1RetrievalError("Quality v1 graph result exceeded limit")

    facts: list[RetrievedFact] = []
    edge_uuids: set[str] = set()
    for rank, edge in enumerate(edges, start=1):
        uuid = _text(getattr(edge, "uuid", None), field="edge UUID")
        if uuid in edge_uuids:
            raise QualityV1RetrievalError("Quality v1 graph returned duplicate edge")
        edge_uuids.add(uuid)
        if _text(getattr(edge, "group_id", None), field="edge namespace") != group_id:
            raise QualityV1RetrievalError("Quality v1 edge escaped namespace")
        raw_episodes = getattr(edge, "episodes", None)
        if not isinstance(raw_episodes, list) or not raw_episodes:
            raise QualityV1RetrievalError("Quality v1 edge provenance is missing")
        source_sessions: list[str] = []
        for episode_uuid in raw_episodes:
            session = provenance.get(str(episode_uuid))
            if session is None:
                raise QualityV1RetrievalError("Quality v1 edge provenance is foreign")
            if session not in source_sessions:
                source_sessions.append(session)
        facts.append(
            RetrievedFact(
                retrieval_rank=rank,
                edge_uuid=uuid,
                source_node_uuid=_text(
                    getattr(edge, "source_node_uuid", None), field="source node"
                ),
                target_node_uuid=_text(
                    getattr(edge, "target_node_uuid", None), field="target node"
                ),
                relation_name=_text(getattr(edge, "name", None), field="relation"),
                fact=_text(getattr(edge, "fact", None), field="fact"),
                source_session_ids=tuple(source_sessions),
                valid_at=_time(getattr(edge, "valid_at", None)),
                invalid_at=_time(getattr(edge, "invalid_at", None)),
                expired_at=_time(getattr(edge, "expired_at", None)),
                reference_time=_time(getattr(edge, "reference_time", None)),
            )
        )

    ranked_episodes: list[RetrievedEpisode] = []
    seen_episode_uuids: set[str] = set()
    seen_sessions: set[str] = set()
    for rank, episode in enumerate(episodes, start=1):
        uuid = _text(getattr(episode, "uuid", None), field="episode UUID")
        if uuid in seen_episode_uuids:
            raise QualityV1RetrievalError("Quality v1 graph returned duplicate episode")
        seen_episode_uuids.add(uuid)
        if _text(getattr(episode, "group_id", None), field="episode namespace") != group_id:
            raise QualityV1RetrievalError("Quality v1 episode escaped namespace")
        session = provenance.get(uuid)
        if session is None:
            raise QualityV1RetrievalError("Quality v1 episode mapping is foreign")
        if session in seen_sessions:
            raise QualityV1RetrievalError("Quality v1 episode mapped to duplicate session")
        seen_sessions.add(session)
        ranked_episodes.append(RetrievedEpisode(rank, uuid, session))

    model_dump = getattr(config, "model_dump", None)
    if not callable(model_dump):
        raise QualityV1RetrievalError("Quality v1 config identity is unavailable")
    identity = model_dump(mode="json")
    if not isinstance(identity, dict):
        raise QualityV1RetrievalError("Quality v1 config identity is invalid")
    return QualityV1RetrievalBundle(
        facts=tuple(facts),
        episodes=tuple(ranked_episodes),
        search_config_sha256=payload_sha256(identity),
        graphiti_search_calls=counters.graphiti_search_calls,
        neo4j_read_requests=counters.neo4j_read_requests,
    )


__all__ = [
    "QualityV1RetrievalBundle",
    "QualityV1RetrievalError",
    "TOP_K",
    "build_quality_v1_search_config",
    "retrieve_quality_v1",
]
