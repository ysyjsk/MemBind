"""RED contracts for the paper-aligned LongMemEval user-only Reader overlay."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from paper_eval.native_reader_v2 import OfficialConSessionReader, ReaderV2Error
from paper_eval.s2_session_reader import materialize_ranked_sessions


class CaptureTransport:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def complete(self, request: dict[str, object]) -> object:
        self.requests.append(copy.deepcopy(request))
        return SimpleNamespace(
            content="The current value is 5.",
            prompt_tokens=123,
            completion_tokens=8,
        )


def _sessions():
    record = {
        "haystack_session_ids": ["new", "old"],
        "haystack_dates": ["2024/02/01", "2024/01/01"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "The new value is 5."},
                {
                    "role": "assistant",
                    "content": "Very long assistant distractor NEW_PRIVATE.",
                },
            ],
            [
                {"role": "user", "content": "The old value is 4."},
                {
                    "role": "assistant",
                    "content": "Very long assistant distractor OLD_PRIVATE.",
                },
            ],
        ],
    }
    return materialize_ranked_sessions(
        record=record,
        ranked_session_ids=("new", "old"),
        top_k=2,
    )


def test_useronly_reader_removes_assistant_turns_but_preserves_time_order() -> None:
    transport = CaptureTransport()
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=transport,
        useronly=True,
    )

    result = asyncio.run(
        reader.answer(
            _sessions(),
            question_date="2024/03/01",
            question="What is current?",
        )
    )

    prompt = result.prompt_for_test
    assert "The old value is 4." in prompt
    assert "The new value is 5." in prompt
    assert prompt.index("The old value is 4.") < prompt.index("The new value is 5.")
    assert "assistant" not in prompt
    assert "NEW_PRIVATE" not in prompt
    assert "OLD_PRIVATE" not in prompt
    assert reader.public_config["useronly"] is True
    assert reader.public_config["useronly_basis"] == (
        "LongMemEval_ICLR2025_section_5.1_session_values_keep_user_utterances"
    )


def test_legacy_default_remains_byte_semantic_compatible() -> None:
    transport = CaptureTransport()
    legacy = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=transport,
    )
    explicit = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=CaptureTransport(),
        useronly=False,
    )

    assert legacy.config_sha256 == explicit.config_sha256
    assert legacy.public_config["useronly"] is False
    assert "useronly_basis" not in legacy.public_config


def test_useronly_identity_differs_and_is_shared_across_methods() -> None:
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=CaptureTransport(),
        useronly=True,
    )
    legacy = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=CaptureTransport(),
    )

    assert reader.config_sha256 != legacy.config_sha256
    assert reader.common_method_bindings() == {
        "U0": reader.config_sha256,
        "A0": reader.config_sha256,
        "P(C=2)": reader.config_sha256,
    }


def test_useronly_fails_closed_if_a_session_has_no_user_turn() -> None:
    sessions = list(_sessions())
    sessions[0] = type(sessions[0])(
        session_id=sessions[0].session_id,
        session_date=sessions[0].session_date,
        turns=({"role": "assistant", "content": "only assistant"},),
        retrieval_rank=sessions[0].retrieval_rank,
    )
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=CaptureTransport(),
        useronly=True,
    )

    with pytest.raises(ReaderV2Error, match="user turn"):
        asyncio.run(
            reader.answer(
                sessions,
                question_date="2024/03/01",
                question="What is current?",
            )
        )

