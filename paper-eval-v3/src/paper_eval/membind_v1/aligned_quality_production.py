"""Production adapters for MemBind-v1's read-only aligned quality layer.

The generic quality contract intentionally knows nothing about Graphiti.  This
module is the small live bridge: it reuses the qualified formal session
retrieval path and performs a bounded read-only namespace observation.  It
does not create a graph, mutate a namespace, or call a Reader/Judge model.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_quality_live import (
    AlignedQualityLiveHooks,
    NamespaceCorrectnessObservation,
    SessionRetrievalCase,
)
from paper_eval.s2_retrieval_probe import corpus_identity_sha256


_CORRECTNESS_QUERY = """
CALL {
  MATCH (episode:Episodic)
  WHERE episode.group_id = $group_id
  RETURN collect(episode.name) AS episode_names
}
CALL {
  MATCH (left:Entity)-[relation:RELATES_TO]->(right:Entity)
  WHERE relation.group_id = $group_id
     OR left.group_id = $group_id
     OR right.group_id = $group_id
  RETURN
    coalesce(sum(CASE WHEN relation.group_id <> $group_id THEN 1 ELSE 0 END), 0)
    + coalesce(sum(CASE WHEN left.group_id <> $group_id THEN 1 ELSE 0 END), 0)
    + coalesce(sum(CASE WHEN right.group_id <> $group_id THEN 1 ELSE 0 END), 0)
    AS namespace_escape_count
}
RETURN episode_names, namespace_escape_count
"""


class AlignedQualityProductionError(RuntimeError):
    """The read-only production adapter has drifted or returned bad data."""


def _fail(code: str) -> AlignedQualityProductionError:
    return AlignedQualityProductionError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    return value


def _ids(value: object, code: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    selected = tuple(value)
    if not selected or any(not isinstance(item, str) or not item for item in selected):
        raise _fail(code)
    if len(set(selected)) != len(selected):
        raise _fail(code)
    return selected


def _episodes(value: object) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("episode inventory invalid")
    selected = tuple(value)
    if not selected:
        raise _fail("episode inventory invalid")
    for sequence, episode in enumerate(selected):
        if getattr(episode, "source_sequence", None) != sequence:
            raise _fail("episode source sequence invalid")
        _text(getattr(episode, "name", None), "episode name invalid")
        _text(getattr(episode, "source_hash", None), "episode source identity invalid")
        _text(getattr(episode, "session_id", None), "episode session identity invalid")
    if len({str(getattr(item, "name")) for item in selected}) != len(selected):
        raise _fail("episode name inventory invalid")
    if len({str(getattr(item, "source_hash")) for item in selected}) != len(selected):
        raise _fail("episode source inventory invalid")
    return selected


def build_session_retrieval_case(*, record: Mapping[str, object]) -> SessionRetrievalCase:
    """Keep the frozen labels local while creating one gold-blind request."""

    if not isinstance(record, Mapping):
        raise _fail("retrieval record invalid")
    query = _text(record.get("question"), "retrieval record invalid")
    gold = _ids(record.get("answer_session_ids"), "retrieval record invalid")
    allowed = _ids(record.get("haystack_session_ids"), "retrieval record invalid")
    if not set(gold).issubset(allowed):
        raise _fail("retrieval record invalid")
    return SessionRetrievalCase(
        question_sha256=payload_sha256({"question": query}),
        query=query,
        gold_session_ids=gold,
        allowed_session_ids=allowed,
    )


FormalRetriever = Callable[..., Awaitable[object]]
SearchConfigFactory = Callable[[], object]


def _production_formal_retriever() -> FormalRetriever:
    from paper_eval.s2_formal_retrieval import run_formal_session_retrieval

    return run_formal_session_retrieval


def _production_search_config() -> object:
    from paper_eval.s2_retrieval_probe import build_episode_bm25_search_config

    return build_episode_bm25_search_config()


def _records(value: object) -> list[Mapping[str, object]]:
    rows = getattr(value, "records", None)
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or len(rows) != 1:
        raise _fail("namespace observation result invalid")
    row = rows[0]
    if isinstance(row, Mapping):
        return [row]
    getter = getattr(row, "get", None)
    if not callable(getter):
        raise _fail("namespace observation result invalid")
    return [
        {
            "episode_names": getter("episode_names"),
            "namespace_escape_count": getter("namespace_escape_count"),
        }
    ]


def build_aligned_quality_hooks(
    *,
    graph: object,
    record: Mapping[str, object],
    episodes: Sequence[object],
    formal_retriever: FormalRetriever | None = None,
    search_config_factory: SearchConfigFactory | None = None,
) -> AlignedQualityLiveHooks:
    """Build only the two read-only callbacks admitted by the quality layer."""

    driver = getattr(graph, "driver", None)
    if graph is None or driver is None or not callable(getattr(driver, "execute_query", None)):
        raise _fail("read-only Graphiti runtime invalid")
    source_episodes = _episodes(episodes)
    case = build_session_retrieval_case(record=record)
    retriever = _production_formal_retriever() if formal_retriever is None else formal_retriever
    config_factory = _production_search_config if search_config_factory is None else search_config_factory
    if not callable(retriever) or not callable(config_factory):
        raise _fail("read-only retrieval adapter invalid")
    names_to_sources = {
        str(getattr(episode, "name")): str(getattr(episode, "source_hash"))
        for episode in source_episodes
    }
    source_hashes = tuple(str(getattr(episode, "source_hash")) for episode in source_episodes)
    session_ids = tuple(str(getattr(episode, "session_id")) for episode in source_episodes)
    if session_ids != case.allowed_session_ids:
        raise _fail("frozen retrieval session mapping drift")

    async def retrieve_sessions(*, namespace: str, request: object) -> tuple[str, ...]:
        if not isinstance(namespace, str) or not namespace:
            raise _fail("namespace invalid")
        if request != case.request():
            raise _fail("retrieval request drift")
        try:
            result = retriever(
                graph=graph,
                query=case.query,
                namespace=namespace,
                episodes=source_episodes,
                expected_frozen_session_ids=session_ids,
                expected_corpus_identity_sha256=corpus_identity_sha256(source_episodes),
                search_config=config_factory(),
            )
            if not inspect.isawaitable(result):
                raise _fail("formal retrieval must be async")
            outcome = await result
        except AlignedQualityProductionError:
            raise
        except Exception:
            raise _fail("formal retrieval failed") from None
        raw = getattr(outcome, "retrieved_session_ids", None)
        return _ids(raw, "formal retrieval result invalid")

    async def observe_namespace_correctness(
        *, namespace: str, expected_source_sha256s: Sequence[str]
    ) -> NamespaceCorrectnessObservation:
        if not isinstance(namespace, str) or not namespace:
            raise _fail("namespace invalid")
        expected = tuple(expected_source_sha256s)
        if expected != source_hashes:
            raise _fail("namespace source inventory drift")
        try:
            result = await driver.execute_query(
                _CORRECTNESS_QUERY,
                params={"group_id": namespace},
                routing_="r",
            )
        except AlignedQualityProductionError:
            raise
        except Exception:
            raise _fail("namespace correctness query failed") from None
        row = _records(result)[0]
        names = row.get("episode_names")
        if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
            raise _fail("namespace correctness result invalid")
        observed: list[str] = []
        for value in names:
            name = _text(value, "namespace correctness result invalid")
            source = names_to_sources.get(name)
            if source is None:
                source = payload_sha256({"unrecognized_episode_name": name})
                while source in source_hashes:
                    source = payload_sha256({"unrecognized_episode_name": source})
            observed.append(source)
        escape = row.get("namespace_escape_count")
        if isinstance(escape, bool) or not isinstance(escape, int) or escape < 0:
            raise _fail("namespace correctness result invalid")
        return NamespaceCorrectnessObservation(
            observed_source_sha256s=tuple(observed),
            namespace_escape_count=escape,
        )

    return AlignedQualityLiveHooks(
        retrieve_sessions=retrieve_sessions,
        observe_namespace_correctness=observe_namespace_correctness,
    )


def build_read_only_aligned_quality_runtime(*, env: Mapping[str, str]) -> object:
    """Construct the prequalified read-only Graphiti runtime outside a loop."""

    from paper_eval.s2_r0_live import build_read_only_graphiti

    return build_read_only_graphiti(env=env)


async def close_read_only_aligned_quality_runtime(runtime: object) -> None:
    """Close only the isolated read-only quality graph after the command ends."""

    graph = getattr(runtime, "graphiti", None)
    close = getattr(graph, "close", None)
    if close is None:
        return
    if not callable(close):
        raise _fail("read-only Graphiti close invalid")
    value = close()
    if not inspect.isawaitable(value):
        raise _fail("read-only Graphiti close invalid")
    await value


__all__ = [
    "AlignedQualityProductionError",
    "build_aligned_quality_hooks",
    "build_read_only_aligned_quality_runtime",
    "build_session_retrieval_case",
    "close_read_only_aligned_quality_runtime",
]
