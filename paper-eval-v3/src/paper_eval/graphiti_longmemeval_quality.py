"""Gold-blind read-only retrieval for Graphiti-native LongMemEval QA.

The retrieval function has no dataset record, gold session IDs, reference
answer, or raw-session fallback.  It builds a fresh edge+node RRF configuration
for every query so Graphiti's mutable global search recipes are never reused.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .artifacts import payload_sha256
from .s2_retrieval_probe import ProbeCounters, _read_only_query_guard
from .temporal_fact_reader import GraphEntityEvidence, TemporalFactEvidence


TOP_K_EDGES = 20
TOP_K_NODES = 20


class GraphQualityError(RuntimeError):
    """Graph-native retrieval violated its namespace or evidence contract."""


@dataclass(frozen=True)
class _OfflineLayerConfig:
    search_methods: tuple[str, ...]
    reranker: str

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        del mode
        return {
            "search_methods": list(self.search_methods),
            "reranker": self.reranker,
        }


@dataclass(frozen=True)
class _OfflineSearchConfig:
    edge_config: _OfflineLayerConfig
    node_config: _OfflineLayerConfig
    episode_config: None = None
    community_config: None = None
    limit: int = TOP_K_EDGES
    reranker_min_score: float = 0.0

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        del mode
        return {
            "edge_config": self.edge_config.model_dump(),
            "node_config": self.node_config.model_dump(),
            "episode_config": None,
            "community_config": None,
            "limit": self.limit,
            "reranker_min_score": self.reranker_min_score,
        }


def build_fresh_graph_quality_search_config() -> Any:
    """Return a fresh edge+node BM25/cosine RRF config on every call."""

    try:
        module = importlib.import_module("graphiti_core.search.search_config")
    except ModuleNotFoundError:
        # The paper-eval test environment intentionally does not install the
        # live Graphiti dependency.  This shape is identity-equivalent and is
        # never used by the production process, which runs in the live venv.
        layer = _OfflineLayerConfig(
            search_methods=("bm25", "cosine_similarity"),
            reranker="rrf",
        )
        return _OfflineSearchConfig(edge_config=layer, node_config=layer)

    return module.SearchConfig(
        edge_config=module.EdgeSearchConfig(
            search_methods=[
                module.EdgeSearchMethod.bm25,
                module.EdgeSearchMethod.cosine_similarity,
            ],
            reranker=module.EdgeReranker.rrf,
        ),
        node_config=module.NodeSearchConfig(
            search_methods=[
                module.NodeSearchMethod.bm25,
                module.NodeSearchMethod.cosine_similarity,
            ],
            reranker=module.NodeReranker.rrf,
        ),
        limit=TOP_K_EDGES,
    )


def _text(value: object, *, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise GraphQualityError(f"graph result has invalid {field}")
    return value.strip()


def _time(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "iso_format"):
        return str(value.iso_format())
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _items(result: object, field: str) -> list[Any]:
    value = getattr(result, field, None)
    if not isinstance(value, list):
        raise GraphQualityError(f"graph search returned invalid {field}")
    return value


def _mapping(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise GraphQualityError("episode provenance mapping is invalid")
    mapped = {str(key): str(item) for key, item in value.items()}
    if any(not key or not item for key, item in mapped.items()):
        raise GraphQualityError("episode provenance mapping is invalid")
    return mapped


@dataclass(frozen=True)
class GraphQualityEvidence:
    """Ranked graph evidence and safe retrieval identity."""

    facts: tuple[TemporalFactEvidence, ...]
    entities: tuple[GraphEntityEvidence, ...]
    search_config_sha256: str
    graphiti_search_calls: int
    neo4j_read_requests: int


async def retrieve_graph_quality_evidence(
    *,
    graph: Any,
    query: str,
    namespace: str,
    episode_uuid_to_session_id: Mapping[str, str],
    search_config_factory: Callable[[], Any] = build_fresh_graph_quality_search_config,
) -> GraphQualityEvidence:
    """Retrieve graph facts/entities without receiving evaluation labels."""

    question = _text(query, field="query")
    group_id = _text(namespace, field="namespace")
    provenance = _mapping(episode_uuid_to_session_id)
    config = search_config_factory()
    if config is None or getattr(config, "limit", None) != TOP_K_EDGES:
        raise GraphQualityError("graph quality search configuration is invalid")
    if (
        getattr(config, "edge_config", None) is None
        or getattr(config, "node_config", None) is None
        or getattr(config, "episode_config", None) is not None
        or getattr(config, "community_config", None) is not None
    ):
        raise GraphQualityError("graph quality search surface drift")

    counters = ProbeCounters()
    with _read_only_query_guard(graph.driver, counters):
        counters.graphiti_search_calls += 1
        result = await graph.search_(
            question,
            config=config,
            group_ids=[group_id],
        )

    edges = _items(result, "edges")
    nodes = _items(result, "nodes")
    if len(edges) > TOP_K_EDGES or len(nodes) > TOP_K_NODES:
        raise GraphQualityError("graph search exceeded the frozen result limit")
    facts: list[TemporalFactEvidence] = []
    edge_uuids: set[str] = set()
    for rank, edge in enumerate(edges, start=1):
        uuid = _text(getattr(edge, "uuid", None), field="edge UUID")
        if uuid in edge_uuids:
            raise GraphQualityError("graph search returned a duplicate edge UUID")
        edge_uuids.add(uuid)
        if _text(getattr(edge, "group_id", None), field="edge namespace") != group_id:
            raise GraphQualityError("graph edge escaped the namespace")
        episode_uuids = getattr(edge, "episodes", None)
        if not isinstance(episode_uuids, list) or not episode_uuids:
            raise GraphQualityError("graph edge provenance is missing")
        source_sessions: list[str] = []
        for episode_uuid in episode_uuids:
            session_id = provenance.get(str(episode_uuid))
            if session_id is None:
                raise GraphQualityError("graph edge provenance is foreign")
            if session_id not in source_sessions:
                source_sessions.append(session_id)
        facts.append(
            TemporalFactEvidence(
                retrieval_rank=rank,
                edge_uuid=uuid,
                fact=_text(getattr(edge, "fact", None), field="edge fact"),
                source_session_ids=tuple(source_sessions),
                valid_at=_time(getattr(edge, "valid_at", None)),
                invalid_at=_time(getattr(edge, "invalid_at", None)),
                expired_at=_time(getattr(edge, "expired_at", None)),
                reference_time=_time(getattr(edge, "reference_time", None)),
            )
        )

    entities: list[GraphEntityEvidence] = []
    node_uuids: set[str] = set()
    for rank, node in enumerate(nodes, start=1):
        uuid = _text(getattr(node, "uuid", None), field="node UUID")
        if uuid in node_uuids:
            raise GraphQualityError("graph search returned a duplicate node UUID")
        node_uuids.add(uuid)
        if _text(getattr(node, "group_id", None), field="node namespace") != group_id:
            raise GraphQualityError("graph node escaped the namespace")
        entities.append(
            GraphEntityEvidence(
                retrieval_rank=rank,
                node_uuid=uuid,
                name=_text(getattr(node, "name", None), field="node name"),
                summary=_text(
                    getattr(node, "summary", None),
                    field="node summary",
                    allow_empty=True,
                ),
            )
        )

    if not facts:
        raise GraphQualityError("graph search returned no temporal facts")

    model_dump = getattr(config, "model_dump", None)
    if not callable(model_dump):
        raise GraphQualityError("graph quality config identity is unavailable")
    config_identity = model_dump(mode="json")
    if not isinstance(config_identity, dict):
        raise GraphQualityError("graph quality config identity is invalid")
    return GraphQualityEvidence(
        facts=tuple(facts),
        entities=tuple(entities),
        search_config_sha256=payload_sha256(config_identity),
        graphiti_search_calls=counters.graphiti_search_calls,
        neo4j_read_requests=counters.neo4j_read_requests,
    )


__all__ = [
    "GraphQualityError",
    "GraphQualityEvidence",
    "TOP_K_EDGES",
    "TOP_K_NODES",
    "build_fresh_graph_quality_search_config",
    "retrieve_graph_quality_evidence",
]
