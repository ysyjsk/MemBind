"""RED-first contract for gold-blind, graph-native quality retrieval."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from paper_eval.graphiti_longmemeval_quality import (
    GraphQualityError,
    build_fresh_graph_quality_search_config,
    retrieve_graph_quality_evidence,
)


def _edge(
    uuid: str,
    *,
    group_id: str = "namespace-a",
    episodes: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        uuid=uuid,
        group_id=group_id,
        fact=f"fact-{uuid}",
        episodes=episodes or ["episode-1"],
        valid_at="2025-01-01T00:00:00+00:00",
        invalid_at=None,
        expired_at=None,
        reference_time="2025-01-01T00:00:00+00:00",
    )


def _node(uuid: str, *, group_id: str = "namespace-a") -> SimpleNamespace:
    return SimpleNamespace(
        uuid=uuid,
        group_id=group_id,
        name=f"name-{uuid}",
        summary=f"summary-{uuid}",
    )


class _Driver:
    _init_task = None

    async def execute_query(self, _query: str, **_kwargs: object) -> object:
        return []


class _Graph:
    def __init__(self, *, edges: list[object], nodes: list[object]) -> None:
        self.driver = _Driver()
        self.edges = edges
        self.nodes = nodes
        self.calls: list[dict[str, object]] = []

    async def search_(self, query: str, **kwargs: object) -> object:
        self.calls.append({"query": query, **kwargs})
        return SimpleNamespace(edges=self.edges, nodes=self.nodes)


def test_retrieval_signature_cannot_receive_gold_reference_or_dataset_values() -> None:
    parameters = set(inspect.signature(retrieve_graph_quality_evidence).parameters)
    assert not parameters.intersection(
        {
            "gold_session_ids",
            "answer_session_ids",
            "reference_answer",
            "answer",
            "dataset_record",
            "haystack_sessions",
        }
    )


def test_each_query_receives_a_fresh_non_global_search_config() -> None:
    first = build_fresh_graph_quality_search_config()
    second = build_fresh_graph_quality_search_config()
    assert first is not second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.limit == 20
    assert first.edge_config is not None
    assert first.node_config is not None
    assert first.episode_config is None
    assert first.community_config is None


@pytest.mark.asyncio
async def test_retrieval_is_namespace_scoped_and_uses_only_edge_provenance() -> None:
    graph = _Graph(edges=[_edge("edge-1")], nodes=[_node("node-1")])
    result = await retrieve_graph_quality_evidence(
        graph=graph,
        query="Where are the shoes?",
        namespace="namespace-a",
        episode_uuid_to_session_id={"episode-1": "session-1"},
    )

    assert len(graph.calls) == 1
    assert graph.calls[0]["group_ids"] == ["namespace-a"]
    assert result.facts[0].source_session_ids == ("session-1",)
    assert result.facts[0].edge_uuid == "edge-1"
    assert result.entities[0].node_uuid == "node-1"
    assert not hasattr(result, "raw_sessions")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edges", "nodes", "mapping", "message"),
    [
        ([_edge("same"), _edge("same")], [], {"episode-1": "s1"}, "duplicate edge"),
        ([_edge("edge-1", group_id="foreign")], [], {"episode-1": "s1"}, "namespace"),
        ([_edge("edge-1", episodes=["foreign-episode"])], [], {}, "provenance"),
        ([], [_node("node-1", group_id="foreign")], {}, "namespace"),
    ],
)
async def test_foreign_or_duplicate_graph_results_fail_closed(
    edges: list[object],
    nodes: list[object],
    mapping: dict[str, str],
    message: str,
) -> None:
    graph = _Graph(edges=edges, nodes=nodes)
    with pytest.raises(GraphQualityError, match=message):
        await retrieve_graph_quality_evidence(
            graph=graph,
            query="question",
            namespace="namespace-a",
            episode_uuid_to_session_id=mapping,
        )

