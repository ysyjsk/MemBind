"""TDD contracts for one gold-blind multi-surface Graphiti query."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from paper_eval.quality_evaluation_v1_retrieval import (
    QualityV1RetrievalError,
    build_quality_v1_search_config,
    retrieve_quality_v1,
)


class _Driver:
    _init_task = None

    async def execute_query(self, _query: str, **_kwargs: object) -> object:
        return []


def _edge(uuid: str, *, group: str = "ns", episode: str = "ep-1"):
    return SimpleNamespace(
        uuid=uuid,
        group_id=group,
        source_node_uuid="user-node",
        target_node_uuid="ratio-node",
        name="HAS_RATIO",
        fact=f"fact-{uuid}",
        episodes=[episode],
        valid_at="2023-01-01T00:00:00+00:00",
        invalid_at=None,
        expired_at=None,
        reference_time="2023-01-01T00:00:00+00:00",
    )


def _episode(uuid: str, *, group: str = "ns"):
    return SimpleNamespace(uuid=uuid, group_id=group)


class _Graph:
    def __init__(self, *, edges, episodes):
        self.driver = _Driver()
        self.edges = edges
        self.episodes = episodes
        self.calls = []

    async def search_(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SimpleNamespace(edges=self.edges, episodes=self.episodes)


def test_retrieval_signature_has_no_gold_or_reference_input() -> None:
    parameters = set(inspect.signature(retrieve_quality_v1).parameters)
    assert not parameters.intersection(
        {"gold_session_ids", "answer_session_ids", "reference_answer", "answer"}
    )


def test_config_is_fresh_native_edge_plus_episode_rrf() -> None:
    first = build_quality_v1_search_config()
    second = build_quality_v1_search_config()

    assert first is not second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.limit == 20
    assert first.edge_config is not None
    assert first.episode_config is not None
    assert first.node_config is None
    assert first.community_config is None


@pytest.mark.asyncio
async def test_one_query_maps_edges_and_ranked_episodes() -> None:
    graph = _Graph(
        edges=[_edge("edge-1", episode="ep-1")],
        episodes=[_episode("ep-2"), _episode("ep-1")],
    )
    result = await retrieve_quality_v1(
        graph=graph,
        query="ratio",
        namespace="ns",
        episode_uuid_to_session_id={"ep-1": "s1", "ep-2": "s2"},
    )

    assert len(graph.calls) == 1
    assert graph.calls[0][1]["group_ids"] == ["ns"]
    assert result.facts[0].source_session_ids == ("s1",)
    assert [value.session_id for value in result.episodes] == ["s2", "s1"]
    assert result.graphiti_search_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edges", "episodes", "mapping", "message"),
    [
        ([_edge("same"), _edge("same")], [], {"ep-1": "s1"}, "duplicate edge"),
        ([_edge("e", group="foreign")], [], {"ep-1": "s1"}, "namespace"),
        ([_edge("e", episode="foreign")], [], {"ep-1": "s1"}, "provenance"),
        ([], [_episode("ep-1", group="foreign")], {"ep-1": "s1"}, "namespace"),
        ([], [_episode("foreign")], {}, "mapping"),
    ],
)
async def test_foreign_or_duplicate_results_fail_closed(
    edges, episodes, mapping, message
) -> None:
    with pytest.raises(QualityV1RetrievalError, match=message):
        await retrieve_quality_v1(
            graph=_Graph(edges=edges, episodes=episodes),
            query="ratio",
            namespace="ns",
            episode_uuid_to_session_id=mapping,
        )
