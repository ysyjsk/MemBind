"""Offline TDD for the pinned Graphiti M* live semantic adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding
from paper_eval.s5_graphiti_mstar_semantics import logical_ns_to_datetime
from paper_eval.s5_mstar_live_semantic_adapter import (
    RELEVANT_SCHEMA_LIMIT,
    S5MStarLiveSemanticAdapter,
    S5MStarLiveSemanticAdapterError,
    materialize_s5_mstar_sources,
)


NAMESPACE = "pev3-s5-mstar-20260816-001"


@dataclass(frozen=True)
class _Episode:
    name: str
    body: str
    reference_time: str
    group_id: str
    source_sequence: int
    source_hash: str


class _Driver:
    def __init__(self, database: str) -> None:
        self._database = database
        self.clones: list[str] = []

    def clone(self, *, database: str):
        self.clones.append(database)
        return _Driver(database)


class _EpisodeNode:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)
        self.uuid = f"episode-{kwargs['name']}"


def _episode(index: int = 0, *, group_id: str = NAMESPACE) -> _Episode:
    return _Episode(
        name=f"07741c45::episode::{index:04d}",
        body="opaque test body",
        reference_time="2025-01-02T03:04:05+00:00",
        group_id=group_id,
        source_sequence=index,
        source_hash=f"{index + 1:064x}",
    )


def _episode_kwargs(episode: _Episode):
    return {
        "name": episode.name,
        "episode_body": episode.body,
        "source_description": "LongMemEval-S haystack session",
        "reference_time": datetime.fromisoformat(episode.reference_time),
        "source": "message",
        "group_id": episode.group_id,
    }


def _binding(observed: dict[str, object]) -> S5GraphitiSemanticBinding:
    async def extract_nodes(_clients, episode, previous, *_args):
        observed["extract_previous"] = list(previous)
        observed["prepared_episode"] = episode
        return ([{"uuid": "extracted"}], {"extracted": [0]})

    async def resolve_nodes(_clients, nodes, _episode, previous, _types):
        observed["resolve_previous"] = list(previous)
        return ([{"uuid": "canonical"}], {"extracted": "canonical"}, [])

    async def extract_edges(*_args):
        return []

    def pointers(edges, _uuid_map):
        return list(edges)

    async def resolve_edges(*_args):
        return ([], [], [])

    async def attributes(_clients, nodes, *_args, **_kwargs):
        return list(nodes)

    async def process(
        _graphiti,
        episode,
        _nodes,
        _edges,
        now,
        group_id,
        *_args,
    ):
        observed["commit_now"] = now
        observed["commit_group_id"] = group_id
        observed["committed_episode"] = episode
        return ([], episode)

    return S5GraphitiSemanticBinding(
        extract_nodes=extract_nodes,
        resolve_extracted_nodes=resolve_nodes,
        extract_attributes_from_nodes=attributes,
        extract_edges=extract_edges,
        resolve_extracted_edges=resolve_edges,
        resolve_edge_pointers=pointers,
        process_episode_data=process,
    )


def test_live_adapter_matches_native_snapshot_and_latest_retrieval_arguments() -> None:
    observed: dict[str, object] = {}
    retrievals: list[dict[str, object]] = []
    driver = _Driver("neo4j")

    class GraphitiDouble:
        def __init__(self) -> None:
            self.driver = driver
            self.clients = SimpleNamespace(driver=driver)

        async def retrieve_episodes(self, reference_time, **kwargs):
            retrievals.append(
                {
                    "reference_time": reference_time,
                    "last_n": kwargs.get("last_n"),
                    "group_ids": kwargs.get("group_ids"),
                    "source": kwargs.get("source"),
                    "database": self.driver._database,
                }
            )
            return ["prepare-snapshot"] if len(retrievals) == 1 else ["bind-latest"]

    graphiti = GraphitiDouble()
    adapter = S5MStarLiveSemanticAdapter(
        graphiti=graphiti,
        semantic_binding=_binding(observed),
        graphiti_episode_kwargs=_episode_kwargs,
        episodic_node_type=_EpisodeNode,
    )
    logical_time_ns = 1_735_787_045_123_456_789

    prepared = asyncio.run(adapter.prepare(_episode(), logical_time_ns))
    result = asyncio.run(adapter.bind(prepared, logical_time_ns, 0, ()))

    assert len(retrievals) == 2
    assert retrievals == [
        {
            "reference_time": datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            "last_n": RELEVANT_SCHEMA_LIMIT,
            "group_ids": [NAMESPACE],
            "source": "message",
            "database": NAMESPACE,
        },
        {
            "reference_time": datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            "last_n": RELEVANT_SCHEMA_LIMIT,
            "group_ids": [NAMESPACE],
            "source": "message",
            "database": NAMESPACE,
        },
    ]
    assert observed["extract_previous"] == ["prepare-snapshot"]
    assert observed["resolve_previous"] == ["bind-latest"]
    assert observed["commit_group_id"] == NAMESPACE
    episode_node = observed["committed_episode"]
    assert episode_node.created_at == observed["commit_now"]
    assert episode_node.created_at == logical_ns_to_datetime(logical_time_ns)
    assert episode_node.valid_at == datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert result.source_sequence == 0


def test_materialized_sources_are_exact_and_use_epoch_not_monotonic_time() -> None:
    episodes = tuple(_episode(index) for index in range(49))
    ticks = iter([1_735_000_000_000_000_000] * 49)

    sources = materialize_s5_mstar_sources(
        episodes,
        namespace=NAMESPACE,
        epoch_clock_ns=lambda: next(ticks),
    )

    assert [source.source_sequence for source in sources] == list(range(49))
    assert [source.source_sha256 for source in sources] == [
        f"{index + 1:064x}" for index in range(49)
    ]
    assert [source.logical_time_ns for source in sources] == [
        1_735_000_000_000_000_000 + index for index in range(49)
    ]
    assert all(source.logical_time_ns > 1_000_000_000_000_000_000 for source in sources)


def test_materialization_rejects_monotonic_clock_as_semantic_epoch() -> None:
    episodes = tuple(_episode(index) for index in range(49))
    with pytest.raises(S5MStarLiveSemanticAdapterError, match="epoch_clock"):
        materialize_s5_mstar_sources(
            episodes,
            namespace=NAMESPACE,
            epoch_clock_ns=lambda: 123_456_789_000_000,
        )


@pytest.mark.parametrize(
    "mutation, code",
    [
        (lambda episodes: episodes[:-1], "source_count"),
        (
            lambda episodes: (*episodes[:1], _episode(1, group_id="wrong"), *episodes[2:]),
            "namespace",
        ),
    ],
)
def test_materialization_fails_closed_on_workload_or_namespace_drift(mutation, code) -> None:
    episodes = tuple(_episode(index) for index in range(49))
    with pytest.raises(S5MStarLiveSemanticAdapterError, match=code):
        materialize_s5_mstar_sources(
            mutation(episodes),
            namespace=NAMESPACE,
            epoch_clock_ns=lambda: 1_735_000_000_000_000_000,
        )


def test_adapter_rejects_non_utc_reference_time_before_model_or_db_work() -> None:
    episode = _Episode(
        **{
            **_episode().__dict__,
            "reference_time": "2025-01-02T03:04:05",
        }
    )
    graphiti = SimpleNamespace(
        driver=_Driver("neo4j"),
        clients=SimpleNamespace(driver=_Driver("neo4j")),
        retrieve_episodes=lambda *_args, **_kwargs: asyncio.sleep(0, result=[]),
    )
    adapter = S5MStarLiveSemanticAdapter(
        graphiti=graphiti,
        semantic_binding=_binding({}),
        graphiti_episode_kwargs=_episode_kwargs,
        episodic_node_type=_EpisodeNode,
    )
    with pytest.raises(S5MStarLiveSemanticAdapterError, match="reference_time"):
        asyncio.run(adapter.prepare(episode, 1_735_000_000_000_000_000))
