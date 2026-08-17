"""TDD contracts for the fixed short-answer Quality v1 Reader."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from paper_eval.quality_evaluation_v1_reader import (
    QualityEvaluationV1Reader,
    QualityEvaluationV1ReaderError,
    render_quality_v1_prompt,
)


@dataclass
class _Response:
    content: str = "Less water."
    prompt_tokens: int = 100
    completion_tokens: int = 4
    finish_reason: str = "stop"


class _Transport:
    config_sha256 = "1" * 64

    def __init__(self, response: _Response | None = None):
        self.response = response or _Response()
        self.requests: list[dict] = []

    async def complete(self, request):
        self.requests.append(request)
        return self.response


def test_prompt_is_single_common_contract() -> None:
    prompt = render_quality_v1_prompt(
        context_json='[{"raw_evidence":"five ounces"}]',
        question_date="2023-03-01",
        question="More or less water?",
    )

    assert "Answer the question using only the provided memory evidence." in prompt
    assert "Consider evidence in chronological order." in prompt
    assert "latest effective information" in prompt
    assert "future plans" in prompt
    assert "Return only a concise final answer." in prompt


@pytest.mark.asyncio
async def test_reader_wire_contract_is_short_no_thinking_and_no_system_prompt() -> None:
    transport = _Transport()
    reader = QualityEvaluationV1Reader(
        model="qwen3-32b-fp8", transport=transport
    )
    result = await reader.answer(
        context_json='[{"raw_evidence":"five ounces"}]',
        question_date="2023-03-01",
        question="More or less water?",
    )

    assert result.answer == "Less water."
    assert transport.requests == [
        {
            "model": "qwen3-32b-fp8",
            "messages": [{"role": "user", "content": result.prompt_for_test}],
            "temperature": 0,
            "max_tokens": 256,
            "n": 1,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "content_filter", None])
async def test_reader_rejects_every_non_stop_finish_reason(finish_reason) -> None:
    reader = QualityEvaluationV1Reader(
        model="qwen3-32b-fp8",
        transport=_Transport(_Response(finish_reason=finish_reason)),
    )

    with pytest.raises(QualityEvaluationV1ReaderError, match="finish_reason"):
        await reader.answer(
            context_json='[{"raw_evidence":"five ounces"}]',
            question_date="2023-03-01",
            question="More or less water?",
        )
