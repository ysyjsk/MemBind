"""Offline production-wiring tests for the one-item Reader-v2 canary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.native_reader_v2 import OfficialConSessionReader
from paper_eval.native_reader_v2_production import (
    ReaderV2ProductionFactories,
    build_reader_v2_live_executor,
)
from paper_eval.s2_completion_chain import BoundedRetrievalOutcome


HISTORY_ID = "b6019101"
NAMESPACE = "nc-e1e2-1deef863d4241064"


@dataclass(frozen=True)
class _Episode:
    session_id: str


class _Closeable:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _record(count: int = 49) -> dict[str, object]:
    return {
        "question_id": HISTORY_ID,
        "question_type": "knowledge-update",
        "question": "What is current?",
        "question_date": "2026/08/14",
        "answer": "new state",
        "answer_session_ids": [f"s{count - 2}", f"s{count - 1}"],
        "haystack_session_ids": [f"s{index}" for index in range(count)],
        "haystack_dates": [f"2026/01/{index + 1:02d}" for index in range(count)],
        "haystack_sessions": [
            [{"role": "user", "content": f"turn-{index}"}]
            for index in range(count)
        ],
    }


def _env() -> dict[str, str]:
    return {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "private",
        "CONSTRUCTION_LLM_BASE_URL": "http://10.87.5.247:8000/v1/",
        "CONSTRUCTION_LLM_API_KEY": "not-required",
        "CONSTRUCTION_LLM_MODEL": "qwen3-32b-fp8",
    }


def test_wires_gold_blind_canary_with_official_con_reader_and_closes() -> None:
    record = _record()
    episodes = [_Episode(str(value)) for value in record["haystack_session_ids"]]
    graph = SimpleNamespace(close=lambda: None)
    runtime = SimpleNamespace(graphiti=graph, counters=object())
    transport = _Closeable()
    judge = _Closeable()
    calls: dict[str, object] = {}

    async def retrieval(**kwargs: object) -> BoundedRetrievalOutcome:
        calls["retrieval_keys"] = set(kwargs)
        calls["namespace"] = kwargs["namespace"]
        return BoundedRetrievalOutcome(
            retrieved_session_ids=tuple(f"s{index}" for index in range(10)),
            graphiti_search_calls=1,
            neo4j_read_requests=2,
            construction_llm_requests=0,
            embedding_requests=0,
            cross_encoder_requests=0,
            database_mutation_attempts=0,
            database_mutations=0,
            cleanup_calls=0,
            retry_count=0,
        )

    async def chain(**kwargs: object) -> object:
        inputs = kwargs["inputs"]
        calls["inputs"] = inputs
        calls["reader"] = kwargs["reader"]
        calls["judge"] = kwargs["judge"]
        await kwargs["retrieve"](
            question=inputs.question,
            namespace=inputs.namespace,
        )
        return "bounded-result"

    factories = ReaderV2ProductionFactories(
        load_history=lambda _path: record,
        build_episodes=lambda _record: episodes,
        build_runtime=lambda _env: runtime,
        build_search_config=lambda: "search-config",
        build_transport=lambda **_kwargs: transport,
        build_reader=lambda **kwargs: OfficialConSessionReader(**kwargs),
        build_judge=lambda **_kwargs: judge,
        run_retrieval=retrieval,
        execute_chain=chain,
        corpus_identity=lambda _episodes: "f" * 64,
    )
    live = build_reader_v2_live_executor(
        env=_env(),
        dataset_path=Path("unused.json"),
        factories=factories,
        run_id="native-reader-v2-canary-test-001",
    )

    result = asyncio.run(live.execute(lambda _stage, _evidence: None))
    asyncio.run(live.close())

    assert result == "bounded-result"
    assert calls["inputs"].history_id == HISTORY_ID
    assert calls["inputs"].namespace == NAMESPACE
    assert calls["namespace"] == NAMESPACE
    assert isinstance(calls["reader"], OfficialConSessionReader)
    assert calls["reader"].public_config["reading_method"] == "con"
    assert calls["reader"].public_config["cot"] is True
    assert calls["reader"].public_config["con"] is False
    assert calls["retrieval_keys"] == {
        "graph",
        "query",
        "namespace",
        "episodes",
        "expected_frozen_session_ids",
        "expected_corpus_identity_sha256",
        "search_config",
        "counters",
    }
    assert "answer_session_ids" not in calls["retrieval_keys"]
    assert transport.closed is True
    assert judge.closed is True


@pytest.mark.parametrize(
    "mutation",
    ["history", "question_type", "session_count", "episode_order"],
)
def test_rejects_canary_or_corpus_drift_before_building_clients(
    mutation: str,
) -> None:
    record = _record()
    episodes = [_Episode(str(value)) for value in record["haystack_session_ids"]]
    if mutation == "history":
        record["question_id"] = "other"
    elif mutation == "question_type":
        record["question_type"] = "single-session-user"
    elif mutation == "session_count":
        record = _record(48)
        episodes = [_Episode(str(value)) for value in record["haystack_session_ids"]]
    else:
        episodes[0], episodes[1] = episodes[1], episodes[0]
    built = False

    def build_runtime(_env: object) -> object:
        nonlocal built
        built = True
        raise AssertionError

    factories = ReaderV2ProductionFactories(
        load_history=lambda _path: record,
        build_episodes=lambda _record: episodes,
        build_runtime=build_runtime,
        build_search_config=lambda: object(),
        build_transport=lambda **_kwargs: object(),
        build_reader=lambda **_kwargs: object(),
        build_judge=lambda **_kwargs: object(),
        run_retrieval=lambda **_kwargs: None,
        execute_chain=lambda **_kwargs: None,
        corpus_identity=lambda _episodes: "f" * 64,
    )

    with pytest.raises(ValueError, match="canary|session|episode"):
        build_reader_v2_live_executor(
            env=_env(),
            dataset_path=Path("unused.json"),
            factories=factories,
        )
    assert built is False
