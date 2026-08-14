from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from paper_eval.s2_formal_retrieval import run_formal_session_retrieval
from paper_eval.s2_retrieval_probe import ProbeCounters, corpus_identity_sha256


@dataclass(frozen=True)
class _Episode:
    name: str
    session_id: str
    body: str


def _episodes() -> list[_Episode]:
    return [
        _Episode(f"episode-{index}", f"session-{index}", f"body-{index}")
        for index in range(12)
    ]


def _config() -> object:
    return SimpleNamespace(
        edge_config=None,
        node_config=None,
        episode_config=SimpleNamespace(
            search_methods=[SimpleNamespace(value="bm25")],
            reranker=SimpleNamespace(value="reciprocal_rank_fusion"),
        ),
        community_config=None,
        limit=10,
        reranker_min_score=0,
    )


class _Driver:
    _init_task = None

    def __init__(self, episodes: list[_Episode]) -> None:
        self.episodes = list(episodes)
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute_query(self, query: str, **kwargs: object) -> object:
        self.calls.append((query, kwargs))
        return [
            {
                "uuid": f"uuid-{index}",
                "name": episode.name,
                "group_id": "pev3-s1-test",
                "content": episode.body,
            }
            for index, episode in enumerate(self.episodes)
        ]


class _Graph:
    def __init__(self, episodes: list[_Episode]) -> None:
        self.driver = _Driver(episodes)
        self.search_calls: list[dict[str, object]] = []

    async def search_(self, query: str, **kwargs: object) -> object:
        self.search_calls.append({"query": query, **kwargs})
        returned = [
            SimpleNamespace(
                uuid=f"uuid-{index}",
                group_id="pev3-s1-test",
            )
            for index in range(9, -1, -1)
        ]
        return SimpleNamespace(episodes=returned)


def test_formal_retrieval_has_no_gold_or_answer_parameter() -> None:
    parameters = inspect.signature(run_formal_session_retrieval).parameters
    assert "gold_session_ids" not in parameters
    assert "answer_session_ids" not in parameters
    assert "reference_answer" not in parameters


def test_formal_retrieval_runs_one_read_only_search_and_maps_exactly_ten_sessions() -> None:
    episodes = _episodes()
    graph = _Graph(episodes)

    outcome = asyncio.run(
        run_formal_session_retrieval(
            graph=graph,
            query="exact benchmark question",
            namespace="pev3-s1-test",
            episodes=episodes,
            expected_frozen_session_ids=tuple(
                episode.session_id for episode in episodes
            ),
            expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
            search_config=_config(),
            counters=ProbeCounters(),
        )
    )

    assert outcome.retrieved_session_ids == tuple(
        f"session-{index}" for index in range(9, -1, -1)
    )
    assert outcome.graphiti_search_calls == 1
    assert outcome.neo4j_read_requests == 1
    assert outcome.construction_llm_requests == 0
    assert outcome.embedding_requests == 0
    assert outcome.cross_encoder_requests == 0
    assert outcome.database_mutation_attempts == 0
    assert outcome.database_mutations == 0
    assert outcome.cleanup_calls == 0
    assert outcome.retry_count == 0
    assert len(graph.driver.calls) == 1
    assert graph.driver.calls[0][1]["routing_"] == "r"
    assert graph.search_calls == [
        {
            "query": "exact benchmark question",
            "config": _config(),
            "group_ids": ["pev3-s1-test"],
        }
    ]


@pytest.mark.parametrize("mutation", ["short_result", "foreign_uuid", "wrong_group"])
def test_formal_retrieval_rejects_incomplete_or_foreign_result(mutation: str) -> None:
    episodes = _episodes()
    graph = _Graph(episodes)
    original = graph.search_

    async def changed(query: str, **kwargs: object) -> object:
        result = await original(query, **kwargs)
        if mutation == "short_result":
            result.episodes.pop()
        elif mutation == "foreign_uuid":
            result.episodes[0].uuid = "foreign"
        else:
            result.episodes[0].group_id = "other"
        return result

    graph.search_ = changed

    with pytest.raises(RuntimeError, match="ten|mapping|namespace"):
        asyncio.run(
            run_formal_session_retrieval(
                graph=graph,
                query="exact benchmark question",
                namespace="pev3-s1-test",
                episodes=episodes,
                expected_frozen_session_ids=tuple(
                    episode.session_id for episode in episodes
                ),
                expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
                search_config=_config(),
                counters=ProbeCounters(),
            )
        )


def test_formal_retrieval_rejects_content_drift_before_search() -> None:
    episodes = _episodes()
    graph = _Graph(episodes)
    graph.driver.episodes[0] = _Episode(
        graph.driver.episodes[0].name,
        graph.driver.episodes[0].session_id,
        "changed-content",
    )

    with pytest.raises(ValueError, match="content identity"):
        asyncio.run(
            run_formal_session_retrieval(
                graph=graph,
                query="exact benchmark question",
                namespace="pev3-s1-test",
                episodes=episodes,
                expected_frozen_session_ids=tuple(
                    episode.session_id for episode in episodes
                ),
                expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
                search_config=_config(),
                counters=ProbeCounters(),
            )
        )
    assert graph.search_calls == []
