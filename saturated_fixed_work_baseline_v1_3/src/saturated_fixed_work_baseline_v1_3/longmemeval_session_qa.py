"""Read-only episodic/session-value QA adapter for completed graph pairs.

This lane follows LongMemEval's public flat-session Reader contract while
making the evidence source explicit: session text must come from persisted
``EpisodicNode.content`` returned by a read-only Neo4j query.  Benchmark
metadata (session id, source sequence, and display date) is used only to label
and order the retrieved sessions; source conversation bodies and answer labels
are never accepted by the materializer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from paper_eval.s2_session_reader import MaterializedSession


class SessionEvidenceError(ValueError):
    """A persisted-session evidence or provenance contract failed closed."""


_MARKER = re.compile(r"(?m)^\[(USER|ASSISTANT)\][ \t]?")
_NAME = re.compile(r"^(?P<history>[^:]+)::episode::(?P<sequence>[0-9]+)$")
_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SessionEvidenceError(f"SESSION_{field.upper()}_INVALID")
    return value


def parse_episodic_content(content: str) -> tuple[dict[str, str], ...]:
    """Parse the v1.3 persisted ``[USER]``/``[ASSISTANT]`` body format.

    The parser does not normalize or summarize text.  It only removes the
    storage markers and preserves each complete turn for the official Reader.
    Unknown markers, empty turns, and arbitrary free-form content are rejected.
    """

    body = _text(content, field="content")
    matches = list(_MARKER.finditer(body))
    if not matches or matches[0].start() != 0:
        raise SessionEvidenceError("SESSION_CONTENT_MARKER_INVALID")
    turns: list[dict[str, str]] = []
    for index, marker in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        turn_content = body[marker.end() : end].strip()
        if not turn_content:
            raise SessionEvidenceError("SESSION_EMPTY_TURN")
        turns.append(
            {
                "role": marker.group(1).casefold(),
                "content": turn_content,
            }
        )
    if not turns or any(turn["role"] not in {"user", "assistant"} for turn in turns):
        raise SessionEvidenceError("SESSION_ROLE_INVALID")
    return tuple(turns)


def parse_episode_name(name: str, *, history_id: str) -> int:
    """Return the source sequence encoded by the qualified episode name."""

    text = _text(name, field="name")
    expected_history = _text(history_id, field="history_id")
    match = _NAME.fullmatch(text)
    if match is None or match.group("history") != expected_history:
        raise SessionEvidenceError("SESSION_NAME_PROVENANCE_INVALID")
    sequence = int(match.group("sequence"))
    if sequence < 0:
        raise SessionEvidenceError("SESSION_SEQUENCE_INVALID")
    return sequence


def synthetic_session_date(source_sequence: int) -> str:
    """Use the frozen gold-blind monotonic construction clock as a fallback."""

    if isinstance(source_sequence, bool) or not isinstance(source_sequence, int) or source_sequence < 0:
        raise SessionEvidenceError("SESSION_SEQUENCE_INVALID")
    return (
        _EPOCH + timedelta(minutes=source_sequence)
    ).isoformat().replace("+00:00", "Z")


def _metadata_row(value: Any, *, sequence: int) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise SessionEvidenceError("SESSION_METADATA_INVALID")
    session_id = _text(value.get("session_id"), field="session_id")
    date = value.get("session_date")
    if date is None:
        date = synthetic_session_date(sequence)
    date = _text(date, field="session_date")
    return session_id, date


def materialize_retrieved_sessions(
    *,
    history_id: str,
    retrieved_episodes: Sequence[Any],
    episodic_rows: Mapping[str, Mapping[str, Any]],
    public_session_metadata: Mapping[int, Mapping[str, Any]],
    top_k: int = 10,
) -> tuple[MaterializedSession, ...]:
    """Map retrieved persisted episodes to official Reader sessions.

    ``episodic_rows`` is the only source of model-visible text.  The metadata
    map contains identifiers/dates only and cannot provide a body or answer.
    """

    expected_history = _text(history_id, field="history_id")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise SessionEvidenceError("SESSION_TOP_K_INVALID")
    if isinstance(retrieved_episodes, (str, bytes)) or not isinstance(retrieved_episodes, Sequence):
        raise SessionEvidenceError("SESSION_RETRIEVAL_INVALID")
    selected = tuple(retrieved_episodes[:top_k])
    if len(selected) != top_k:
        raise SessionEvidenceError("SESSION_RETRIEVAL_INCOMPLETE")

    sessions: list[MaterializedSession] = []
    seen_uuid: set[str] = set()
    seen_session: set[str] = set()
    seen_sequence: set[int] = set()
    for expected_rank, retrieved in enumerate(selected, start=1):
        rank = getattr(retrieved, "retrieval_rank", None)
        uuid = _text(getattr(retrieved, "episode_uuid", None), field="episode_uuid")
        if rank != expected_rank or uuid in seen_uuid:
            raise SessionEvidenceError("SESSION_RETRIEVAL_RANK_INVALID")
        row = episodic_rows.get(uuid)
        if not isinstance(row, Mapping):
            raise SessionEvidenceError("SESSION_EPISODE_ROW_MISSING")
        name = _text(row.get("name"), field="name")
        sequence = parse_episode_name(name, history_id=expected_history)
        if sequence in seen_sequence:
            raise SessionEvidenceError("SESSION_SEQUENCE_DUPLICATE")
        metadata = public_session_metadata.get(sequence)
        if metadata is None:
            raise SessionEvidenceError("SESSION_METADATA_MISSING")
        session_id, session_date = _metadata_row(metadata, sequence=sequence)
        if session_id in seen_session:
            raise SessionEvidenceError("SESSION_ID_DUPLICATE")
        content = _text(row.get("content"), field="content")
        turns = parse_episodic_content(content)
        sessions.append(
            MaterializedSession(
                session_id=session_id,
                session_date=session_date,
                turns=tuple(turns),
                retrieval_rank=expected_rank,
            )
        )
        seen_uuid.add(uuid)
        seen_sequence.add(sequence)
        seen_session.add(session_id)
    return tuple(sorted(sessions, key=lambda item: (item.session_date, item.retrieval_rank)))


def persisted_episode_identity(
    *, history_id: str, episodic_rows: Mapping[str, Mapping[str, Any]]
) -> str:
    """Hash only persisted episode identity/content for append-only artifacts."""

    rows: list[dict[str, str]] = []
    for uuid, row in sorted(episodic_rows.items()):
        if not isinstance(uuid, str) or not uuid or not isinstance(row, Mapping):
            raise SessionEvidenceError("SESSION_EPISODE_ROW_INVALID")
        name = _text(row.get("name"), field="name")
        parse_episode_name(name, history_id=history_id)
        content = _text(row.get("content"), field="content")
        rows.append(
            {
                "uuid": uuid,
                "name": name,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    if not rows:
        raise SessionEvidenceError("SESSION_EPISODE_CORPUS_EMPTY")
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "SessionEvidenceError",
    "parse_episodic_content",
    "parse_episode_name",
    "synthetic_session_date",
    "materialize_retrieved_sessions",
    "persisted_episode_identity",
]
