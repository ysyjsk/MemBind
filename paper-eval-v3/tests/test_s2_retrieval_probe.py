from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s2_retrieval_probe import (
    finalize_episode_surface_probe,
    run_episode_surface_probe,
)


@dataclass(frozen=True)
class _Episode:
    name: str
    session_id: str


class _Graph:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search_(self, query: str, **kwargs: object) -> object:
        self.calls.append({"query": query, **kwargs})
        return SimpleNamespace(
            episodes=[
                SimpleNamespace(name="q::episode::0001"),
                SimpleNamespace(name="q::episode::0000"),
            ]
        )


def _episode_bm25_config() -> object:
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


@pytest.mark.asyncio
async def test_episode_surface_probe_is_session_ranked_and_read_only() -> None:
    graph = _Graph()
    config = _episode_bm25_config()
    result = await run_episode_surface_probe(
        graph=graph,
        query="Where does Ravi work now?",
        namespace="pev3-s1-namespace",
        episodes=[
            _Episode("q::episode::0000", "s1"),
            _Episode("q::episode::0001", "s2"),
        ],
        answer_session_ids=("s2",),
        edge_attributed_source_session_coverage=0.0,
        top_k=10,
        search_config=config,
    )

    assert graph.calls == [
        {
            "query": "Where does Ravi work now?",
            "config": config,
            "group_ids": ["pev3-s1-namespace"],
        }
    ]
    assert result.retrieved_session_ids == ("s2", "s1")
    assert result.session_recall_any_at_10 == 1.0
    assert result.session_recall_all_at_10 == 1.0
    assert result.session_gold_coverage_fraction_at_10 == 1.0
    assert result.classification == "EDGE_SURFACE_COVERAGE_GAP_CONFIRMED"


@pytest.mark.asyncio
async def test_episode_surface_probe_fails_closed_on_unmapped_result() -> None:
    graph = _Graph()

    async def bad_search(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(episodes=[SimpleNamespace(name="unknown")])

    graph.search_ = bad_search  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="mapping"):
        await run_episode_surface_probe(
            graph=graph,
            query="question",
            namespace="pev3-s1-namespace",
            episodes=[_Episode("q::episode::0000", "s1")],
            answer_session_ids=("s1",),
            edge_attributed_source_session_coverage=0.0,
            search_config=_episode_bm25_config(),
        )


@pytest.mark.asyncio
async def test_episode_surface_probe_rejects_non_bm25_or_multi_scope_config() -> None:
    graph = _Graph()
    bad_config = _episode_bm25_config()
    bad_config.edge_config = object()

    with pytest.raises(ValueError, match="episode-only BM25/RRF"):
        await run_episode_surface_probe(
            graph=graph,
            query="question",
            namespace="pev3-s1-namespace",
            episodes=[_Episode("q::episode::0000", "s1")],
            answer_session_ids=("s1",),
            edge_attributed_source_session_coverage=0.0,
            search_config=bad_config,
        )
    assert graph.calls == []


def test_episode_surface_probe_artifact_contains_no_raw_query(tmp_path: Path) -> None:
    output = tmp_path / "S2_RETRIEVAL_SURFACE_PROBE.json"
    artifact = finalize_episode_surface_probe(
        output,
        run_id="s2r-test",
        history_id="07741c45",
        namespace="pev3-s1-namespace",
        retrieved_session_ids=("s2", "s1"),
        gold_session_count=1,
        session_recall_any_at_10=1.0,
        session_recall_all_at_10=1.0,
        session_gold_coverage_fraction_at_10=1.0,
        edge_attributed_source_session_coverage=0.0,
        reference_sanity_sha256="a" * 64,
        git_commit="deadbeef",
    )

    assert artifact["payload_sha256"] == payload_sha256(artifact["payload"])
    assert artifact["payload"]["model_request_count"] == 0
    assert artifact["payload"]["database_mutation_count"] == 0
    assert artifact["payload"]["reader_call_count"] == 0
    assert artifact["payload"]["judge_call_count"] == 0
    assert artifact["payload"]["retrieval_unit"] == "EpisodicNode"
    assert artifact["payload"]["top_k_unit"] == "session"
    assert artifact["payload"]["session_recall_any_at_10"] == 1.0
    assert artifact["payload"]["session_recall_all_at_10"] == 1.0
    assert artifact["payload"]["construction_quality_surface"] is False
    serialized = json.dumps(artifact, sort_keys=True)
    assert "Where does Ravi" not in serialized
