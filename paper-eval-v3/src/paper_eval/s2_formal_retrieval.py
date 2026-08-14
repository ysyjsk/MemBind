"""Gold-blind formal Episode retrieval built from pinned, tested R0 guards.

The R0 module is already sealed, so this module reuses its read-only corpus and
query guards without editing that historical source.  Only this orchestration
is new: it deliberately has no gold-label parameter.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .s2_completion_chain import BoundedRetrievalOutcome
from .s2_retrieval_probe import (
    ProbeCounters,
    _expected_corpus_rows,
    _preflight_corpus,
    _read_only_query_guard,
    _validate_episode_bm25_config,
    _validate_success_counters,
    corpus_identity_sha256,
)


async def run_formal_session_retrieval(
    *,
    graph: Any,
    query: str,
    namespace: str,
    episodes: Sequence[Any],
    expected_frozen_session_ids: Sequence[str],
    expected_corpus_identity_sha256: str,
    search_config: Any,
    counters: ProbeCounters | None = None,
) -> BoundedRetrievalOutcome:
    """Return ten unique session IDs without receiving any evaluation label."""

    if not isinstance(query, str) or not query:
        raise ValueError("formal retrieval query is invalid")
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("formal retrieval namespace is invalid")
    _validate_episode_bm25_config(search_config, top_k=10)
    expected_rows = _expected_corpus_rows(episodes)
    frozen_sessions = tuple(expected_frozen_session_ids)
    if (
        len(frozen_sessions) != len(expected_rows)
        or len(set(frozen_sessions)) != len(frozen_sessions)
        or frozen_sessions
        != tuple(str(row["session_id"]) for row in expected_rows)
    ):
        raise ValueError("formal retrieval frozen session mapping drift")
    if corpus_identity_sha256(episodes) != expected_corpus_identity_sha256:
        raise ValueError("formal retrieval corpus identity drift")

    observed = counters if counters is not None else ProbeCounters()
    with _read_only_query_guard(graph.driver, observed):
        corpus = await _preflight_corpus(
            driver=graph.driver,
            namespace=namespace,
            expected_rows=expected_rows,
            expected_frozen_session_ids=frozen_sessions,
        )
        observed.graphiti_search_calls += 1
        search_results = await graph.search_(
            query,
            config=search_config,
            group_ids=[namespace],
        )

    returned = getattr(search_results, "episodes", None)
    if not isinstance(returned, list) or len(returned) != 10:
        raise RuntimeError("formal retrieval must return exactly ten episodes")
    ranked_sessions: list[str] = []
    returned_uuids: set[str] = set()
    for result in returned:
        uuid = str(getattr(result, "uuid", ""))
        if not uuid or uuid in returned_uuids:
            raise RuntimeError("formal retrieval episode UUID mapping failed")
        returned_uuids.add(uuid)
        session_id = corpus.uuid_to_session_id.get(uuid)
        if session_id is None:
            raise RuntimeError("formal retrieval episode UUID mapping failed")
        if str(getattr(result, "group_id", "")) != namespace:
            raise RuntimeError("formal retrieval result escaped the namespace")
        if session_id in ranked_sessions:
            raise RuntimeError("formal retrieval returned a duplicate session")
        ranked_sessions.append(session_id)

    snapshot = observed.snapshot()
    _validate_success_counters(snapshot)
    return BoundedRetrievalOutcome(
        retrieved_session_ids=tuple(ranked_sessions),
        graphiti_search_calls=snapshot.graphiti_search_calls,
        neo4j_read_requests=snapshot.neo4j_read_requests,
        construction_llm_requests=snapshot.construction_llm_requests,
        embedding_requests=snapshot.embedding_requests,
        cross_encoder_requests=snapshot.cross_encoder_requests,
        database_mutation_attempts=snapshot.database_mutation_attempts,
        database_mutations=snapshot.database_mutations,
        cleanup_calls=snapshot.namespace_cleanup_calls,
        retry_count=snapshot.retry_count,
    )
