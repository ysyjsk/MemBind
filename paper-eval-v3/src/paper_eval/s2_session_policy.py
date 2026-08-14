"""Pure Episode-to-session mapping and LongMemEval retrieval metrics for S2."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SessionPolicyError(ValueError):
    """The formal session retrieval contract was violated."""


@dataclass(frozen=True)
class FrozenEpisodeSession:
    episode_name: str
    source_sequence: int
    session_id: str
    content_sha256: str


@dataclass(frozen=True)
class RankedEpisodeObservation:
    episode_uuid: str
    episode_name: str
    content_sha256: str


@dataclass(frozen=True)
class SessionRetrievalMetrics:
    retrieved_session_count: int
    gold_session_count: int
    covered_gold_session_count: int
    session_recall_any_at_10: float
    session_recall_all_at_10: float
    session_gold_coverage_fraction_at_10: float
    evidence_recall_at_10: float
    gold_ranks: tuple[int | None, ...]
    coverage_fraction_is_official: bool = False


def _ids(value: object, *, label: str, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SessionPolicyError(f"{label} IDs must be a sequence")
    result = tuple(value)
    if (not allow_empty and not result) or any(
        not isinstance(item, str) or not item for item in result
    ):
        raise SessionPolicyError(f"{label} IDs are invalid")
    if len(result) != len(set(result)):
        raise SessionPolicyError(f"{label} IDs must be unique")
    return result


def _positive_top_k(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SessionPolicyError("top_k must be a positive integer")
    return value


def _validate_frozen_mapping(
    value: object,
) -> tuple[FrozenEpisodeSession, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise SessionPolicyError("frozen mapping is incomplete")
    rows = tuple(value)
    if any(not isinstance(row, FrozenEpisodeSession) for row in rows):
        raise SessionPolicyError("frozen mapping has an invalid row")
    if any(
        not row.episode_name
        or isinstance(row.source_sequence, bool)
        or not isinstance(row.source_sequence, int)
        or row.source_sequence < 0
        or not row.session_id
        or _SHA256.fullmatch(row.content_sha256) is None
        for row in rows
    ):
        raise SessionPolicyError("frozen mapping has an invalid identity")
    if [row.source_sequence for row in rows] != list(range(len(rows))):
        raise SessionPolicyError("frozen mapping source sequence is not contiguous")
    if len({row.episode_name for row in rows}) != len(rows):
        raise SessionPolicyError("frozen mapping episode names must be unique")
    if len({row.session_id for row in rows}) != len(rows):
        raise SessionPolicyError("frozen mapping session IDs must be unique")
    return rows


def map_ranked_episodes_to_sessions(
    *,
    ranked_episodes: Sequence[RankedEpisodeObservation],
    frozen_mapping: Sequence[FrozenEpisodeSession],
    top_k: int,
) -> tuple[str, ...]:
    """Map native Episode results without consulting gold labels or deduplicating."""

    limit = _positive_top_k(top_k)
    frozen = _validate_frozen_mapping(frozen_mapping)
    if (
        isinstance(ranked_episodes, (str, bytes))
        or not isinstance(ranked_episodes, Sequence)
        or len(ranked_episodes) < limit
    ):
        raise SessionPolicyError("ranked episode results are incomplete")
    selected = tuple(ranked_episodes[:limit])
    if any(not isinstance(row, RankedEpisodeObservation) for row in selected):
        raise SessionPolicyError("ranked episode result has an invalid row")
    if any(
        not row.episode_uuid
        or not row.episode_name
        or _SHA256.fullmatch(row.content_sha256) is None
        for row in selected
    ):
        raise SessionPolicyError("ranked episode identity is invalid")
    if len({row.episode_uuid for row in selected}) != limit:
        raise SessionPolicyError("ranked episode UUIDs must be unique")
    if len({row.episode_name for row in selected}) != limit:
        raise SessionPolicyError("ranked episode names must be unique")

    by_name = {row.episode_name: row for row in frozen}
    sessions: list[str] = []
    for result in selected:
        expected = by_name.get(result.episode_name)
        if expected is None:
            raise SessionPolicyError("ranked episode is foreign to the frozen mapping")
        if result.content_sha256 != expected.content_sha256:
            raise SessionPolicyError("ranked episode content hash mismatch")
        sessions.append(expected.session_id)
    if len(sessions) != len(set(sessions)):
        raise SessionPolicyError("ranked episode mapping produced duplicate sessions")
    return tuple(sessions)


def evaluate_session_retrieval(
    *,
    retrieved_session_ids: Sequence[str],
    gold_session_ids: Sequence[str],
    top_k: int,
    allowed_session_ids: Sequence[str] | None = None,
) -> SessionRetrievalMetrics:
    """Compute official binary any/all semantics plus a labeled diagnostic."""

    limit = _positive_top_k(top_k)
    retrieved = _ids(retrieved_session_ids, label="retrieved session")[:limit]
    gold = _ids(gold_session_ids, label="gold session")
    if allowed_session_ids is not None:
        allowed = set(_ids(allowed_session_ids, label="allowed session"))
        if not set(retrieved).issubset(allowed):
            raise SessionPolicyError("retrieved session IDs fall outside the corpus")
        if not set(gold).issubset(allowed):
            raise SessionPolicyError("gold session IDs fall outside the corpus")

    ranks_by_session = {
        session_id: rank for rank, session_id in enumerate(retrieved, start=1)
    }
    ranks = tuple(ranks_by_session.get(session_id) for session_id in gold)
    covered = sum(rank is not None for rank in ranks)
    recall_any = 1.0 if covered else 0.0
    recall_all = 1.0 if covered == len(gold) else 0.0
    return SessionRetrievalMetrics(
        retrieved_session_count=len(retrieved),
        gold_session_count=len(gold),
        covered_gold_session_count=covered,
        session_recall_any_at_10=recall_any,
        session_recall_all_at_10=recall_all,
        session_gold_coverage_fraction_at_10=covered / len(gold),
        evidence_recall_at_10=recall_all,
        gold_ranks=ranks,
    )
