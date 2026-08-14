from __future__ import annotations

import asyncio
import copy
import hashlib
from types import SimpleNamespace

import pytest

from paper_eval.s2_session_reader import (
    LONGMEMEVAL_SESSION_READER_COMMIT,
    LONGMEMEVAL_SESSION_READER_FILE_SHA256,
    OfficialSessionReader,
    SessionReaderError,
    materialize_ranked_sessions,
    render_official_session_prompt,
)


class _CaptureTransport:
    def __init__(self, answer: str = "OpenAI") -> None:
        self.answer = answer
        self.requests: list[dict[str, object]] = []

    async def complete(self, request: dict[str, object]) -> object:
        self.requests.append(copy.deepcopy(request))
        return SimpleNamespace(
            content=self.answer,
            prompt_tokens=321,
            completion_tokens=4,
        )


def _record() -> dict[str, object]:
    return {
        "question_id": "history-dev",
        "haystack_session_ids": ["s1", "s2", "s3"],
        "haystack_dates": [
            "2024/02/02 (Fri) 10:00",
            "2024/01/01 (Mon) 09:00",
            "2024/02/02 (Fri) 10:00",
        ],
        "haystack_sessions": [
            [
                {"role": "user", "content": "New question", "has_answer": False},
                {"role": "assistant", "content": "New answer"},
            ],
            [
                {"role": "user", "content": "Old question", "has_answer": True},
                {"role": "assistant", "content": "Old answer"},
            ],
            [
                {"role": "user", "content": "Tie question"},
                {"role": "assistant", "content": "Tie answer"},
            ],
        ],
        "answer_session_ids": ["s1"],
        "answer": "must never enter Reader",
    }


def test_materializes_rank_first_then_chronological_with_stable_ties() -> None:
    sessions = materialize_ranked_sessions(
        record=_record(),
        ranked_session_ids=("s3", "s1", "s2"),
        top_k=3,
    )

    assert [item.session_id for item in sessions] == ["s2", "s3", "s1"]
    assert [item.retrieval_rank for item in sessions] == [3, 1, 2]
    assert sessions[1].session_date == sessions[2].session_date


def test_materialization_removes_labels_from_copy_without_mutating_dataset() -> None:
    record = _record()
    original = copy.deepcopy(record)

    sessions = materialize_ranked_sessions(
        record=record,
        ranked_session_ids=("s1", "s2", "s3"),
        top_k=3,
    )

    assert record == original
    assert "has_answer" in repr(record)
    assert "has_answer" not in repr(sessions)
    assert "must never enter Reader" not in repr(sessions)
    assert "answer_session_ids" not in repr(sessions)


@pytest.mark.parametrize(
    "ranked",
    [
        ("s1", "s1", "s2"),
        ("s1", "s2", "foreign"),
        ("s1", "s2"),
    ],
)
def test_materialization_rejects_duplicate_foreign_or_short_ranked_sessions(
    ranked: tuple[str, ...],
) -> None:
    with pytest.raises(SessionReaderError, match="ranked session"):
        materialize_ranked_sessions(
            record=_record(), ranked_session_ids=ranked, top_k=3
        )


@pytest.mark.parametrize(
    "mutation",
    ["parallel_length", "duplicate_corpus_id", "invalid_turn", "empty_date"],
)
def test_materialization_rejects_invalid_dataset_session_contract(
    mutation: str,
) -> None:
    record = _record()
    if mutation == "parallel_length":
        record["haystack_dates"].pop()
    elif mutation == "duplicate_corpus_id":
        record["haystack_session_ids"][1] = "s1"
    elif mutation == "invalid_turn":
        record["haystack_sessions"][1] = [{"role": "user"}]
    else:
        record["haystack_dates"][1] = ""

    with pytest.raises(SessionReaderError, match="dataset session"):
        materialize_ranked_sessions(
            record=record,
            ranked_session_ids=("s1", "s2", "s3"),
            top_k=3,
        )


def test_prompt_matches_pinned_longmemeval_flat_session_json_semantics() -> None:
    sessions = materialize_ranked_sessions(
        record=_record(),
        ranked_session_ids=("s3", "s1", "s2"),
        top_k=3,
    )

    prompt = render_official_session_prompt(
        sessions,
        question_date="2024/03/01 (Fri) 12:00",
        question="Where is the current answer?",
    )

    assert prompt == (
        "I will give you several history chats between you and a user. "
        "Please answer the question based on the relevant chat history.\n\n\n"
        "History Chats:\n\n"
        "\n### Session 1:\n"
        "Session Date: 2024/01/01 (Mon) 09:00\n"
        "Session Content:\n"
        "\n[{\"role\": \"user\", \"content\": \"Old question\"}, "
        "{\"role\": \"assistant\", \"content\": \"Old answer\"}]\n"
        "\n### Session 2:\n"
        "Session Date: 2024/02/02 (Fri) 10:00\n"
        "Session Content:\n"
        "\n[{\"role\": \"user\", \"content\": \"Tie question\"}, "
        "{\"role\": \"assistant\", \"content\": \"Tie answer\"}]\n"
        "\n### Session 3:\n"
        "Session Date: 2024/02/02 (Fri) 10:00\n"
        "Session Content:\n"
        "\n[{\"role\": \"user\", \"content\": \"New question\"}, "
        "{\"role\": \"assistant\", \"content\": \"New answer\"}]\n"
        "\n\nCurrent Date: 2024/03/01 (Fri) 12:00\n"
        "Question: Where is the current answer?\n"
        "Answer:"
    )
    assert LONGMEMEVAL_SESSION_READER_COMMIT == (
        "9e0b455f4ef0e2ab8f2e582289761153549043fc"
    )
    assert LONGMEMEVAL_SESSION_READER_FILE_SHA256 == (
        "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672"
    )


def test_reader_sends_exact_single_attempt_no_thinking_request() -> None:
    transport = _CaptureTransport()
    reader = OfficialSessionReader(
        model="qwen3-32b-fp8",
        transport=transport,
    )
    sessions = materialize_ranked_sessions(
        record=_record(),
        ranked_session_ids=("s1", "s2", "s3"),
        top_k=3,
    )

    result = asyncio.run(
        reader.answer(
            sessions,
            question_date="2024/03/01 (Fri) 12:00",
            question="Where is the current answer?",
        )
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request == {
        "model": "qwen3-32b-fp8",
        "messages": [{"role": "user", "content": result.prompt_for_test}],
        "temperature": 0,
        "max_tokens": 500,
        "n": 1,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    assert "system" not in repr(request).lower()
    assert reader.public_config["official_flat_session_item_semantics"] is True
    assert reader.public_config["truncation_policy"] == (
        "FAIL_CLOSED_IF_CONTEXT_EXCEEDED"
    )

    artifact = result.to_artifact()
    assert artifact["prompt_sha256"] == hashlib.sha256(
        result.prompt_for_test.encode("utf-8")
    ).hexdigest()
    assert artifact["output_sha256"] == hashlib.sha256(b"OpenAI").hexdigest()
    assert artifact["prompt_tokens"] == 321
    assert artifact["completion_tokens"] == 4
    assert "OpenAI" not in repr(artifact)
    assert "Old question" not in repr(artifact)
    assert "prompt_for_test" not in artifact
    assert "answer" not in artifact


def test_reader_failure_is_sanitized_and_not_retried() -> None:
    class _FailingTransport:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: dict[str, object]) -> object:
            self.calls += 1
            raise ConnectionError("private endpoint and prompt")

    transport = _FailingTransport()
    reader = OfficialSessionReader(model="qwen3-32b-fp8", transport=transport)
    sessions = materialize_ranked_sessions(
        record=_record(),
        ranked_session_ids=("s1", "s2", "s3"),
        top_k=3,
    )

    with pytest.raises(SessionReaderError) as captured:
        asyncio.run(
            reader.answer(
                sessions,
                question_date="date",
                question="question",
            )
        )

    assert transport.calls == 1
    assert str(captured.value) == "Reader request failed: ConnectionError"
    assert "private" not in str(captured.value)
