"""Pure policy for a future, explicitly authorized S2-R0 episode probe.

This module does not construct a Graphiti runtime or expose a command-line
entry point.  A caller must supply an already-bound graph and the pinned
episode-only BM25/RRF config.  Persisted evidence contains metrics and hashes,
never the query or retrieved content.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope, payload_sha256
from .s2_retrieval_contract import (
    SESSION_SURFACE_CONTRACT,
    RetrievalContractError,
    classify_surface_comparison,
)


@dataclass(frozen=True)
class EpisodeSurfaceProbeResult:
    retrieved_session_ids: tuple[str, ...]
    session_recall_any_at_10: float
    session_recall_all_at_10: float
    session_gold_coverage_fraction_at_10: float
    edge_attributed_source_session_coverage: float
    classification: str


def _nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RetrievalContractError(f"{field} must be nonempty")
    return value


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _validate_episode_bm25_config(search_config: Any, *, top_k: int) -> None:
    episode_config = getattr(search_config, "episode_config", None)
    methods = getattr(episode_config, "search_methods", None)
    reranker = getattr(episode_config, "reranker", None)
    if (
        getattr(search_config, "edge_config", None) is not None
        or getattr(search_config, "node_config", None) is not None
        or getattr(search_config, "community_config", None) is not None
        or getattr(search_config, "limit", None) != top_k
        or getattr(search_config, "reranker_min_score", 0) != 0
        or not isinstance(methods, list)
        or [_enum_value(value) for value in methods] != ["bm25"]
        or _enum_value(reranker) != "reciprocal_rank_fusion"
    ):
        raise RetrievalContractError(
            "probe requires the pinned episode-only BM25/RRF search config"
        )


async def run_episode_surface_probe(
    *,
    graph: Any,
    query: str,
    namespace: str,
    episodes: Sequence[Any],
    answer_session_ids: Sequence[str],
    edge_attributed_source_session_coverage: float,
    search_config: Any,
    top_k: int = 10,
) -> EpisodeSurfaceProbeResult:
    """Run one session-ranked episode search without Reader or Judge calls."""

    _nonempty(query, field="query")
    _nonempty(namespace, field="namespace")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise RetrievalContractError("top_k must be a positive integer")
    if not episodes or not answer_session_ids:
        raise RetrievalContractError("episode probe inputs are incomplete")
    _validate_episode_bm25_config(search_config, top_k=top_k)

    by_name: dict[str, str] = {}
    input_sessions: set[str] = set()
    for episode in episodes:
        name = _nonempty(getattr(episode, "name", None), field="episode name")
        session_id = _nonempty(
            getattr(episode, "session_id", None), field="episode session ID"
        )
        if name in by_name or session_id in input_sessions:
            raise RetrievalContractError(
                "episode probe requires one EpisodicNode per session"
            )
        by_name[name] = session_id
        input_sessions.add(session_id)
    gold = tuple(_nonempty(value, field="gold session ID") for value in answer_session_ids)
    if len(set(gold)) != len(gold):
        raise RetrievalContractError("gold session IDs must be unique")

    search_results = await graph.search_(
        query,
        config=search_config,
        group_ids=[namespace],
    )
    returned = getattr(search_results, "episodes", None)
    if not isinstance(returned, list):
        raise RuntimeError("episode surface returned an invalid result shape")
    ranked_sessions: list[str] = []
    for result in returned[:top_k]:
        name = str(getattr(result, "name", ""))
        session_id = by_name.get(name)
        if session_id is None:
            raise RuntimeError("episode surface result mapping failed")
        if session_id in ranked_sessions:
            raise RuntimeError("episode surface returned duplicate session ranks")
        ranked_sessions.append(session_id)

    retrieved = set(ranked_sessions)
    gold_set = set(gold)
    covered = len(retrieved.intersection(gold_set))
    coverage_fraction = covered / len(gold_set)
    recall_any = 1.0 if covered > 0 else 0.0
    recall_all = 1.0 if gold_set.issubset(retrieved) else 0.0
    comparison = classify_surface_comparison(
        edge_attributed_source_session_coverage=(
            edge_attributed_source_session_coverage
        ),
        episode_session_recall=coverage_fraction,
    )
    return EpisodeSurfaceProbeResult(
        retrieved_session_ids=tuple(ranked_sessions),
        session_recall_any_at_10=recall_any,
        session_recall_all_at_10=recall_all,
        session_gold_coverage_fraction_at_10=coverage_fraction,
        edge_attributed_source_session_coverage=(
            edge_attributed_source_session_coverage
        ),
        classification=str(comparison["classification"]),
    )


def finalize_episode_surface_probe(
    output_path: Path,
    *,
    run_id: str,
    history_id: str,
    namespace: str,
    retrieved_session_ids: Sequence[str],
    gold_session_count: int,
    session_recall_any_at_10: float,
    session_recall_all_at_10: float,
    session_gold_coverage_fraction_at_10: float,
    edge_attributed_source_session_coverage: float,
    reference_sanity_sha256: str,
    git_commit: str,
) -> dict[str, Any]:
    """Persist content-free evidence from an authorized read-only probe."""

    for value, field in (
        (run_id, "run_id"),
        (history_id, "history_id"),
        (namespace, "namespace"),
        (git_commit, "git_commit"),
    ):
        _nonempty(value, field=field)
    if not isinstance(reference_sanity_sha256, str) or len(reference_sanity_sha256) != 64:
        raise RetrievalContractError("reference sanity hash is invalid")
    if (
        isinstance(gold_session_count, bool)
        or not isinstance(gold_session_count, int)
        or gold_session_count < 1
    ):
        raise RetrievalContractError("gold session count is invalid")
    sessions = tuple(
        _nonempty(value, field="retrieved session ID")
        for value in retrieved_session_ids
    )
    if len(set(sessions)) != len(sessions):
        raise RetrievalContractError("retrieved session IDs must be unique")
    comparison = classify_surface_comparison(
        edge_attributed_source_session_coverage=(
            edge_attributed_source_session_coverage
        ),
        episode_session_recall=session_gold_coverage_fraction_at_10,
    )
    for value in (session_recall_any_at_10, session_recall_all_at_10):
        if value not in {0.0, 1.0}:
            raise RetrievalContractError("session recall labels must be binary")

    payload = {
        "schema_version": "membind.paper-eval-v3.s2-r0-episode-probe.v1",
        "stage": "S2-R0",
        "status": "READ_ONLY_RETRIEVAL_SURFACE_DIAGNOSTIC",
        "history_id": history_id,
        "namespace": namespace,
        **SESSION_SURFACE_CONTRACT.to_identity(),
        "top_k": 10,
        "retrieved_session_count": len(sessions),
        "retrieved_session_ids_sha256": payload_sha256(list(sessions)),
        "gold_session_count": gold_session_count,
        "session_recall_any_at_10": session_recall_any_at_10,
        "session_recall_all_at_10": session_recall_all_at_10,
        "session_gold_coverage_fraction_at_10": (
            session_gold_coverage_fraction_at_10
        ),
        "edge_attributed_source_session_coverage": (
            edge_attributed_source_session_coverage
        ),
        "classification": comparison["classification"],
        "retrieval_policy_selected": False,
        "s3_authorized": False,
        "model_request_count": 0,
        "database_mutation_count": 0,
        "reader_call_count": 0,
        "judge_call_count": 0,
        "reference_sanity_sha256": reference_sanity_sha256,
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=run_id,
    )
    atomic_write_json(Path(output_path), artifact)
    return artifact
