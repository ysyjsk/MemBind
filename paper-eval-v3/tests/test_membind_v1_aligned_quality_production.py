"""TDD contracts for the production-safe aligned quality adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from paper_eval.membind_v1.aligned_quality_live import (
    NamespaceCorrectnessObservation,
)
from paper_eval.membind_v1.aligned_quality_production import (
    AlignedQualityProductionError,
    build_aligned_quality_hooks,
    build_session_retrieval_case,
)


@dataclass(frozen=True)
class _Episode:
    source_sequence: int
    source_hash: str
    session_id: str
    body: str

    @property
    def name(self) -> str:
        return f"history::episode::{self.source_sequence:04d}"


def _episodes() -> tuple[_Episode, ...]:
    return tuple(
        _Episode(
            source_sequence=index,
            source_hash=f"{index + 1:064x}",
            session_id=f"session-{index}",
            body=f"private source body {index}",
        )
        for index in range(10)
    )


def _record() -> dict[str, object]:
    return {
        "question": "private benchmark question",
        "answer_session_ids": ["session-2", "session-7"],
        "haystack_session_ids": [f"session-{index}" for index in range(10)],
    }


def test_production_hooks_keep_gold_labels_out_of_retrieval_and_map_observed_episode_names() -> None:
    calls: list[tuple[str, object]] = []
    episodes = _episodes()

    class _Driver:
        async def execute_query(self, query: str, *, params: dict[str, str], routing_: str):
            calls.append(("query", (query, params, routing_)))
            return SimpleNamespace(
                records=[
                    {
                        "episode_names": [episode.name for episode in episodes],
                        "namespace_escape_count": 3,
                    }
                ]
            )

    async def formal_retriever(**kwargs: object):
        calls.append(("retrieve", kwargs))
        return SimpleNamespace(
            retrieved_session_ids=tuple(f"session-{index}" for index in range(10))
        )

    graph = SimpleNamespace(driver=_Driver())
    case = build_session_retrieval_case(record=_record())
    hooks = build_aligned_quality_hooks(
        graph=graph,
        record=_record(),
        episodes=episodes,
        formal_retriever=formal_retriever,
        search_config_factory=lambda: SimpleNamespace(),
    )

    async def scenario() -> tuple[tuple[str, ...], NamespaceCorrectnessObservation]:
        sessions = await hooks.retrieve_sessions(
            namespace="fresh-namespace", request=case.request()
        )
        observation = await hooks.observe_namespace_correctness(
            namespace="fresh-namespace",
            expected_source_sha256s=tuple(episode.source_hash for episode in episodes),
        )
        return sessions, observation

    sessions, observation = asyncio.run(scenario())

    assert sessions == tuple(f"session-{index}" for index in range(10))
    assert observation == NamespaceCorrectnessObservation(
        observed_source_sha256s=tuple(episode.source_hash for episode in episodes),
        namespace_escape_count=3,
    )
    retrieval = next(value for kind, value in calls if kind == "retrieve")
    assert "answer_session_ids" not in retrieval
    assert retrieval["query"] == "private benchmark question"
    query = next(value for kind, value in calls if kind == "query")[0]
    assert "CREATE" not in query.upper()
    assert "DELETE" not in query.upper()


def test_case_and_correctness_adapter_fail_closed_on_contract_drift() -> None:
    episodes = _episodes()
    with pytest.raises(AlignedQualityProductionError, match="record"):
        build_session_retrieval_case(record={"question": "x"})

    class _Driver:
        async def execute_query(self, *_args: object, **_kwargs: object):
            return SimpleNamespace(
                records=[
                    {
                        "episode_names": [episodes[0].name, "foreign"],
                        "namespace_escape_count": -1,
                    }
                ]
            )

    hooks = build_aligned_quality_hooks(
        graph=SimpleNamespace(driver=_Driver()),
        record=_record(),
        episodes=episodes,
        formal_retriever=lambda **_kwargs: None,
        search_config_factory=lambda: SimpleNamespace(),
    )
    with pytest.raises(AlignedQualityProductionError, match="namespace"):
        asyncio.run(
            hooks.observe_namespace_correctness(
                namespace="fresh-namespace",
                expected_source_sha256s=tuple(episode.source_hash for episode in episodes),
            )
        )
