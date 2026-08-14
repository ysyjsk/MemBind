from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s2_retrieval_probe import (
    ProbeCounters,
    build_episode_bm25_search_config,
    corpus_identity_sha256,
    finalize_episode_surface_probe,
    run_episode_surface_probe,
)


NAMESPACE = "pev3-s1-20260814-001"


@dataclass(frozen=True)
class _Episode:
    name: str
    session_id: str
    body: str


class _Driver:
    def __init__(self, observed: tuple[SimpleNamespace, ...]) -> None:
        self.observed = observed
        self.calls: list[dict[str, object]] = []
        self._init_task = None

    async def execute_query(self, cypher_query_: str, **kwargs: object) -> object:
        self.calls.append({"query": cypher_query_, **kwargs})
        records = [
            {
                "uuid": value.uuid,
                "name": value.name,
                "group_id": value.group_id,
                "content": value.content,
            }
            for value in self.observed
        ]
        return SimpleNamespace(records=records)


class _Graph:
    def __init__(
        self,
        observed: tuple[SimpleNamespace, ...],
        ranked_uuids: tuple[str, ...] = ("u1", "u0"),
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.driver = _Driver(observed)
        self.ranked_uuids = ranked_uuids

    async def search_(self, query: str, **kwargs: object) -> object:
        self.calls.append({"query": query, **kwargs})
        await self.driver.execute_query(
            "CALL db.index.fulltext.queryNodes('episode_content', $query)",
            query=query,
            routing_="r",
        )
        by_uuid = {item.uuid: item for item in self.driver.observed}
        return SimpleNamespace(
            episodes=[by_uuid[uuid] for uuid in self.ranked_uuids if uuid in by_uuid]
        )


def _episodes() -> list[_Episode]:
    return [
        _Episode("q::episode::0000", "s1", "session one"),
        _Episode("q::episode::0001", "s2", "session two"),
    ]


def _observed() -> tuple[SimpleNamespace, ...]:
    return (
        SimpleNamespace(
            uuid="u0",
            name="q::episode::0000",
            group_id=NAMESPACE,
            content="session one",
        ),
        SimpleNamespace(
            uuid="u1",
            name="q::episode::0001",
            group_id=NAMESPACE,
            content="session two",
        ),
    )


def _episode_bm25_config() -> object:
    return SimpleNamespace(
        edge_config=None,
        node_config=None,
        episode_config=SimpleNamespace(
            search_methods=[SimpleNamespace(value="bm25")],
            reranker=SimpleNamespace(value="reciprocal_rank_fusion"),
            sim_min_score=0.5,
            mmr_lambda=0.5,
            bfs_max_depth=3,
        ),
        community_config=None,
        limit=10,
        reranker_min_score=0,
    )


@pytest.mark.asyncio
async def test_episode_surface_probe_is_session_ranked_complete_and_read_only() -> None:
    graph = _Graph(_observed())
    config = _episode_bm25_config()
    episodes = _episodes()
    counters = ProbeCounters()
    result = await run_episode_surface_probe(
        graph=graph,
        query="Where does Ravi work now?",
        namespace=NAMESPACE,
        episodes=episodes,
        expected_frozen_session_ids=("s1", "s2"),
        expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
        answer_session_ids=("s2",),
        edge_attributed_source_session_coverage=0.0,
        top_k=10,
        search_config=config,
        counters=counters,
    )

    assert graph.calls == [
        {
            "query": "Where does Ravi work now?",
            "config": config,
            "group_ids": [NAMESPACE],
        }
    ]
    assert len(graph.driver.calls) == 2
    assert graph.driver.calls[0]["params"] == {"group_id": NAMESPACE}
    assert all(call["routing_"] == "r" for call in graph.driver.calls)
    assert result.corpus_completeness_pass is True
    assert result.observed_session_count == 2
    assert result.retrieved_session_ids == ("s2", "s1")
    assert result.session_recall_any_at_10 == 1.0
    assert result.session_recall_all_at_10 == 1.0
    assert result.session_gold_coverage_fraction_at_10 == 1.0
    assert result.classification == "EDGE_SURFACE_COVERAGE_GAP_CONFIRMED"
    assert result.node_surface_status == "UNTESTED"
    assert result.multi_surface_status == "UNTESTED"
    assert counters.neo4j_read_requests == 2
    assert counters.graphiti_search_calls == 1
    assert counters.database_mutation_attempts == 0


@pytest.mark.asyncio
async def test_episode_surface_probe_fails_closed_on_unmapped_result_uuid() -> None:
    graph = _Graph(_observed(), ranked_uuids=())

    async def bad_search(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(
            episodes=[
                SimpleNamespace(
                    uuid="unknown",
                    name="q::episode::0000",
                    group_id=NAMESPACE,
                )
            ]
        )

    graph.search_ = bad_search  # type: ignore[method-assign]
    episodes = _episodes()
    with pytest.raises(RuntimeError, match="UUID mapping"):
        await run_episode_surface_probe(
            graph=graph,
            query="question",
            namespace=NAMESPACE,
            episodes=episodes,
            expected_frozen_session_ids=("s1", "s2"),
            expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
            answer_session_ids=("s1",),
            edge_attributed_source_session_coverage=0.0,
            search_config=_episode_bm25_config(),
        )


@pytest.mark.asyncio
async def test_episode_surface_probe_rejects_non_bm25_or_multi_scope_config() -> None:
    graph = _Graph(_observed())
    bad_config = _episode_bm25_config()
    bad_config.edge_config = object()
    episodes = _episodes()

    with pytest.raises(ValueError, match="episode-only BM25/RRF"):
        await run_episode_surface_probe(
            graph=graph,
            query="question",
            namespace=NAMESPACE,
            episodes=episodes,
            expected_frozen_session_ids=("s1", "s2"),
            expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
            answer_session_ids=("s1",),
            edge_attributed_source_session_coverage=0.0,
            search_config=bad_config,
        )
    assert graph.calls == []
    assert graph.driver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observed, message",
    [
        (_observed()[:1], "observed episode corpus"),
        (
            (
                _observed()[0],
                SimpleNamespace(**{**vars(_observed()[1]), "content": "content drift"}),
            ),
            "content identity",
        ),
        (
            (
                _observed()[0],
                SimpleNamespace(
                    **{**vars(_observed()[1]), "group_id": "other-namespace"}
                ),
            ),
            "namespace",
        ),
    ],
)
async def test_episode_surface_probe_fails_closed_before_search_on_corpus_drift(
    observed: tuple[SimpleNamespace, ...], message: str
) -> None:
    graph = _Graph(observed)
    episodes = _episodes()

    with pytest.raises(ValueError, match=message):
        await run_episode_surface_probe(
            graph=graph,
            query="question",
            namespace=NAMESPACE,
            episodes=episodes,
            expected_frozen_session_ids=("s1", "s2"),
            expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
            answer_session_ids=("s2",),
            edge_attributed_source_session_coverage=0.0,
            search_config=_episode_bm25_config(),
        )

    assert len(graph.driver.calls) == 1
    assert graph.calls == []


@pytest.mark.asyncio
async def test_episode_surface_probe_rejects_frozen_id_hash_or_gold_drift_before_read() -> None:
    episodes = _episodes()
    cases = (
        (("s1", "drift"), corpus_identity_sha256(episodes), ("s2",), "frozen session"),
        (("s1", "s2"), "a" * 64, ("s2",), "corpus identity"),
        (("s1", "s2"), corpus_identity_sha256(episodes), ("outside",), "gold session"),
    )
    for expected_ids, expected_hash, gold_ids, message in cases:
        graph = _Graph(_observed())
        with pytest.raises(ValueError, match=message):
            await run_episode_surface_probe(
                graph=graph,
                query="question",
                namespace=NAMESPACE,
                episodes=episodes,
                expected_frozen_session_ids=expected_ids,
                expected_corpus_identity_sha256=expected_hash,
                answer_session_ids=gold_ids,
                edge_attributed_source_session_coverage=0.0,
                search_config=_episode_bm25_config(),
            )
        assert graph.driver.calls == []
        assert graph.calls == []


@pytest.mark.asyncio
async def test_episode_surface_probe_rejects_driver_auto_schema_init_before_read() -> None:
    graph = _Graph(_observed())
    graph.driver._init_task = object()
    episodes = _episodes()
    with pytest.raises(ValueError, match="auto schema initialization"):
        await run_episode_surface_probe(
            graph=graph,
            query="question",
            namespace=NAMESPACE,
            episodes=episodes,
            expected_frozen_session_ids=("s1", "s2"),
            expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
            answer_session_ids=("s2",),
            edge_attributed_source_session_coverage=0.0,
            search_config=_episode_bm25_config(),
        )
    assert graph.driver.calls == []
    assert graph.calls == []


@pytest.mark.asyncio
async def test_episode_surface_probe_blocks_mutating_database_query() -> None:
    graph = _Graph(_observed())
    counters = ProbeCounters()

    async def mutating_search(*args: object, **kwargs: object) -> object:
        await graph.driver.execute_query("CREATE (:Forbidden)", routing_="w")
        return SimpleNamespace(episodes=[])

    graph.search_ = mutating_search  # type: ignore[method-assign]
    episodes = _episodes()
    with pytest.raises(RuntimeError, match="read-only database contract"):
        await run_episode_surface_probe(
            graph=graph,
            query="question",
            namespace=NAMESPACE,
            episodes=episodes,
            expected_frozen_session_ids=("s1", "s2"),
            expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
            answer_session_ids=("s2",),
            edge_attributed_source_session_coverage=0.0,
            search_config=_episode_bm25_config(),
            counters=counters,
        )
    assert counters.database_mutation_attempts == 1
    assert counters.database_mutations == 0


def test_episode_bm25_config_builder_returns_fresh_exact_configs() -> None:
    class Method:
        bm25 = SimpleNamespace(value="bm25")

    class Reranker:
        rrf = SimpleNamespace(value="reciprocal_rank_fusion")

    class EpisodeConfig(SimpleNamespace):
        pass

    class SearchConfig(SimpleNamespace):
        pass

    types = (SearchConfig, EpisodeConfig, Method, Reranker)
    first = build_episode_bm25_search_config(top_k=10, config_types=types)
    second = build_episode_bm25_search_config(top_k=10, config_types=types)
    assert first is not second
    assert first.episode_config is not second.episode_config
    assert first.edge_config is None
    assert first.node_config is None
    assert first.community_config is None
    assert [value.value for value in first.episode_config.search_methods] == ["bm25"]
    assert first.episode_config.reranker.value == "reciprocal_rank_fusion"
    assert first.limit == 10
    assert first.reranker_min_score == 0
    first.limit = 3
    assert second.limit == 10


@pytest.mark.asyncio
async def test_episode_surface_probe_artifact_is_consistent_content_free_and_no_overwrite(
    tmp_path: Path,
) -> None:
    episodes = _episodes()
    result = await run_episode_surface_probe(
        graph=_Graph(_observed()),
        query="Where does Ravi work now?",
        namespace=NAMESPACE,
        episodes=episodes,
        expected_frozen_session_ids=("s1", "s2"),
        expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
        answer_session_ids=("s2",),
        edge_attributed_source_session_coverage=0.0,
        search_config=_episode_bm25_config(),
    )
    output = tmp_path / "S2_R0_EPISODE_PROBE.json"
    artifact = finalize_episode_surface_probe(
        output,
        run_id="s2r-test",
        history_id="07741c45",
        namespace=NAMESPACE,
        result=result,
        reference_sanity_sha256="a" * 64,
        authorization_sha256="b" * 64,
        consumption_sha256="9" * 64,
        dataset_sha256="c" * 64,
        frozen_split_sha256="d" * 64,
        source_sha256={"probe": "e" * 64, "graphiti_search": "f" * 64},
        git_commit="deadbeef",
    )

    payload = artifact["payload"]
    assert artifact["payload_sha256"] == payload_sha256(payload)
    assert payload["construction_llm_requests"] == 0
    assert payload["embedding_requests"] == 0
    assert payload["cross_encoder_requests"] == 0
    assert payload["database_mutation_attempts"] == 0
    assert payload["database_mutations"] == 0
    assert payload["neo4j_read_requests"] == 2
    assert payload["reader_requests"] == 0
    assert payload["judge_requests"] == 0
    assert payload["retrieved_session_ids"] == ["s2", "s1"]
    assert payload["gold_session_ids"] == ["s2"]
    assert payload["consumption_sha256"] == "9" * 64
    assert payload["retrieval_unit"] == "EpisodicNode"
    assert payload["top_k_unit"] == "session"
    assert payload["session_recall_any_at_10"] == 1.0
    assert payload["session_recall_all_at_10"] == 1.0
    assert payload["construction_quality_surface"] is False
    assert payload["corpus_completeness_pass"] is True
    assert payload["result_sealed_before_policy_freeze"] is True
    serialized = json.dumps(artifact, sort_keys=True)
    assert "Where does Ravi" not in serialized
    assert "session one" not in serialized

    with pytest.raises(ValueError, match="already exists"):
        finalize_episode_surface_probe(
            output,
            run_id="s2r-test",
            history_id="07741c45",
            namespace=NAMESPACE,
            result=result,
            reference_sanity_sha256="a" * 64,
            authorization_sha256="b" * 64,
            consumption_sha256="9" * 64,
            dataset_sha256="c" * 64,
            frozen_split_sha256="d" * 64,
            source_sha256={"probe": "e" * 64},
            git_commit="deadbeef",
        )


@pytest.mark.asyncio
async def test_episode_surface_probe_finalizer_rejects_metric_drift(tmp_path: Path) -> None:
    episodes = _episodes()
    result = await run_episode_surface_probe(
        graph=_Graph(_observed()),
        query="question",
        namespace=NAMESPACE,
        episodes=episodes,
        expected_frozen_session_ids=("s1", "s2"),
        expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
        answer_session_ids=("s2",),
        edge_attributed_source_session_coverage=0.0,
        search_config=_episode_bm25_config(),
    )
    inconsistent = replace(result, session_recall_all_at_10=0.0)
    output = tmp_path / "result.json"
    with pytest.raises(ValueError, match="metric consistency"):
        finalize_episode_surface_probe(
            output,
            run_id="s2r-test",
            history_id="07741c45",
            namespace=NAMESPACE,
            result=inconsistent,
            reference_sanity_sha256="a" * 64,
            authorization_sha256="b" * 64,
            consumption_sha256="9" * 64,
            dataset_sha256="c" * 64,
            frozen_split_sha256="d" * 64,
            source_sha256={"probe": "e" * 64},
            git_commit="deadbeef",
        )
    assert not output.exists()
