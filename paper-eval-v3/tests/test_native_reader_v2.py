"""Contracts for the versioned LongMemEval-recommended Reader path."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from paper_eval.native_reader_v2 import (
    LONGMEMEVAL_READER_V2_README_SHA256,
    LONGMEMEVAL_READER_V2_RUNNER_SHA256,
    OfficialConSessionReader,
    ReaderV2Error,
    common_method_reader_bindings,
    render_official_con_session_prompt,
    resolve_official_reading_method,
)
from paper_eval.s2_session_reader import materialize_ranked_sessions


class _CaptureTransport:
    def __init__(self, answer: str = "Step 1: note. Step 2: current value.") -> None:
        self.answer = answer
        self.requests: list[dict[str, object]] = []

    async def complete(self, request: dict[str, object]) -> object:
        self.requests.append(copy.deepcopy(request))
        return SimpleNamespace(
            content=self.answer,
            prompt_tokens=456,
            completion_tokens=17,
        )


def _record() -> dict[str, object]:
    return {
        "question_id": "history-dev",
        "haystack_session_ids": ["new", "old", "tie"],
        "haystack_dates": [
            "2024/02/02 (Fri) 10:00",
            "2024/01/01 (Mon) 09:00",
            "2024/02/02 (Fri) 10:00",
        ],
        "haystack_sessions": [
            [
                {"role": "user", "content": "New question", "has_answer": True},
                {"role": "assistant", "content": "New answer"},
            ],
            [
                {"role": "user", "content": "Old question", "has_answer": False},
                {"role": "assistant", "content": "Old answer"},
            ],
            [
                {"role": "user", "content": "Tie question"},
                {"role": "assistant", "content": "Tie answer"},
            ],
        ],
        "answer_session_ids": ["new"],
        "answer": "must never enter the Reader",
    }


def _sessions():
    return materialize_ranked_sessions(
        record=_record(),
        ranked_session_ids=("tie", "new", "old"),
        top_k=3,
    )


def test_public_con_resolves_to_single_call_cot_not_python_con_flag() -> None:
    assert resolve_official_reading_method("con") == {
        "reading_method": "con",
        "cot": True,
        "con": False,
        "separate_note_extraction": False,
        "reader_requests_per_question": 1,
        "max_tokens": 800,
    }


@pytest.mark.parametrize("method", ["direct", "con-separate", "", "CON"])
def test_reader_v2_rejects_any_other_reading_method(method: str) -> None:
    with pytest.raises(ReaderV2Error, match="reading_method"):
        resolve_official_reading_method(method)


def test_prompt_matches_pinned_single_call_con_golden_fixture() -> None:
    prompt = render_official_con_session_prompt(
        _sessions(),
        question_date="2024/03/01 (Fri) 12:00",
        question="What is current?",
    )

    assert prompt == (
        "I will give you several history chats between you and a user. "
        "Please answer the question based on the relevant chat history. "
        "Answer the question step by step: first extract all the relevant "
        "information, and then reason over the information to get the answer.\n\n\n"
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
        "Question: What is current?\n"
        "Answer (step by step):"
    )
    assert "has_answer" not in prompt
    assert "must never enter the Reader" not in prompt


def test_reader_issues_exactly_one_800_token_no_thinking_request() -> None:
    transport = _CaptureTransport()
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=transport,
    )

    result = asyncio.run(
        reader.answer(
            _sessions(),
            question_date="2024/03/01 (Fri) 12:00",
            question="What is current?",
        )
    )

    assert len(transport.requests) == 1
    assert transport.requests[0] == {
        "model": "qwen3-32b-fp8",
        "messages": [{"role": "user", "content": result.prompt_for_test}],
        "temperature": 0,
        "max_tokens": 800,
        "n": 1,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    assert "system" not in repr(transport.requests[0]).lower()
    assert reader.public_config["reading_method"] == "con"
    assert reader.public_config["cot"] is True
    assert reader.public_config["con"] is False
    assert reader.public_config["separate_note_extraction"] is False
    assert reader.public_config["max_attempts"] == 1
    assert reader.public_config["sdk_hidden_retries"] == 0
    assert reader.public_config["max_tokens"] == 800


def test_reader_v2_artifact_is_hash_only_and_binds_upstream_entrypoint() -> None:
    transport = _CaptureTransport(answer="private raw model output")
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=transport,
    )
    result = asyncio.run(
        reader.answer(
            _sessions(),
            question_date="2024/03/01",
            question="private raw question",
        )
    )
    artifact = result.to_artifact()
    encoded = json.dumps(artifact, sort_keys=True)

    assert artifact["prompt_sha256"] == hashlib.sha256(
        result.prompt_for_test.encode("utf-8")
    ).hexdigest()
    assert artifact["output_sha256"] == hashlib.sha256(
        b"private raw model output"
    ).hexdigest()
    assert artifact["prompt_tokens"] == 456
    assert artifact["completion_tokens"] == 17
    assert artifact["truncation_count"] == 0
    assert "private raw" not in encoded
    assert "Old question" not in encoded
    assert "prompt_for_test" not in artifact
    assert LONGMEMEVAL_READER_V2_RUNNER_SHA256 == (
        "6602147b866eca4a80acdf5e6689389586086216c9198fce7b8380b7495c5422"
    )
    assert LONGMEMEVAL_READER_V2_README_SHA256 == (
        "c4ff45676683d9e2f7cf7d9099d26426f14635ec110dbb1da818d1019a142573"
    )


def test_common_reader_identity_is_exactly_shared_by_every_method() -> None:
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=_CaptureTransport(),
    )

    bindings = common_method_reader_bindings(reader.config_sha256)

    assert bindings == {
        "U0": reader.config_sha256,
        "A0": reader.config_sha256,
        "P*": reader.config_sha256,
        "M*": reader.config_sha256,
    }


def test_reader_failure_is_sanitized_and_never_retried() -> None:
    class _FailingTransport:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: dict[str, object]) -> object:
            self.calls += 1
            raise ConnectionError("private endpoint and prompt")

    transport = _FailingTransport()
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=transport,
    )

    with pytest.raises(ReaderV2Error) as captured:
        asyncio.run(
            reader.answer(
                _sessions(),
                question_date="date",
                question="question",
            )
        )

    assert transport.calls == 1
    assert str(captured.value) == "Reader-v2 request failed: ConnectionError"
    assert "private" not in str(captured.value)
