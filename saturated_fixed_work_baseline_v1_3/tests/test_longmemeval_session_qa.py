from __future__ import annotations

from types import SimpleNamespace

import pytest

from paper_eval.s2_session_reader import render_official_session_prompt
from saturated_fixed_work_baseline_v1_3.longmemeval_session_qa import (
    SessionEvidenceError,
    materialize_retrieved_sessions,
    parse_episodic_content,
    parse_episode_name,
    persisted_episode_identity,
    synthetic_session_date,
)


def _retrieved(rank: int, uuid: str) -> SimpleNamespace:
    return SimpleNamespace(retrieval_rank=rank, episode_uuid=uuid)


def _rows(count: int = 10) -> tuple[list[SimpleNamespace], dict[str, dict[str, str]]]:
    retrieved = []
    rows: dict[str, dict[str, str]] = {}
    for index in range(count):
        uuid = f"ep-{index}"
        retrieved.append(_retrieved(index + 1, uuid))
        rows[uuid] = {
            "name": f"hist::episode::{index:04d}",
            "content": f"[USER] question {index}\n[ASSISTANT] answer {index}",
        }
    return retrieved, rows


def test_persisted_content_parser_preserves_turn_order_and_roles() -> None:
    turns = parse_episodic_content("[USER] first\n[ASSISTANT] second\n[USER] third")
    assert turns == (
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    )


def test_parser_rejects_unmarked_or_empty_content() -> None:
    with pytest.raises(SessionEvidenceError, match="MARKER"):
        parse_episodic_content("raw conversation")
    with pytest.raises(SessionEvidenceError, match="EMPTY_TURN"):
        parse_episodic_content("[USER] \n[ASSISTANT] answer")


def test_episode_name_binds_history_and_source_sequence() -> None:
    assert parse_episode_name("hist::episode::0007", history_id="hist") == 7
    with pytest.raises(SessionEvidenceError, match="PROVENANCE"):
        parse_episode_name("other::episode::0007", history_id="hist")


def test_materializer_uses_only_persisted_content_and_public_metadata() -> None:
    retrieved, rows = _rows()
    sessions = materialize_retrieved_sessions(
        history_id="hist",
        retrieved_episodes=retrieved,
        episodic_rows=rows,
        public_session_metadata={
            index: {"session_id": f"session-{index}", "session_date": synthetic_session_date(index)}
            for index in range(10)
        },
    )
    assert len(sessions) == 10
    assert sessions[0].session_id == "session-0"
    assert sessions[-1].session_id == "session-9"
    assert sessions[3].turns[1]["content"] == "answer 3"
    prompt = render_official_session_prompt(
        sessions,
        question_date="2000-01-01T00:20:00Z",
        question="question 3",
    )
    assert "answer 3" in prompt
    assert "gold_answer" not in prompt
    assert "session-3" not in prompt


def test_materializer_fails_closed_when_retrieval_is_short_or_row_foreign() -> None:
    retrieved, rows = _rows()
    with pytest.raises(SessionEvidenceError, match="INCOMPLETE"):
        materialize_retrieved_sessions(
            history_id="hist",
            retrieved_episodes=retrieved[:9],
            episodic_rows=rows,
            public_session_metadata={i: {"session_id": str(i)} for i in range(10)},
        )
    with pytest.raises(SessionEvidenceError, match="MISSING"):
        materialize_retrieved_sessions(
            history_id="hist",
            retrieved_episodes=retrieved,
            episodic_rows={key: value for key, value in rows.items() if key != "ep-2"},
            public_session_metadata={i: {"session_id": str(i)} for i in range(10)},
        )


def test_identity_is_content_bound_and_history_scoped() -> None:
    _, rows = _rows(2)
    first = persisted_episode_identity(history_id="hist", episodic_rows=rows)
    changed = dict(rows)
    changed["ep-0"] = {**rows["ep-0"], "content": "[USER] changed\n[ASSISTANT] answer"}
    assert first != persisted_episode_identity(history_id="hist", episodic_rows=changed)
    with pytest.raises(SessionEvidenceError):
        persisted_episode_identity(history_id="other", episodic_rows=rows)
