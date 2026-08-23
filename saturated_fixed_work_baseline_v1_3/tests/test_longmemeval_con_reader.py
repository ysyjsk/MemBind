from __future__ import annotations

from types import SimpleNamespace

import pytest

from paper_eval.s2_session_reader import MaterializedSession
from saturated_fixed_work_baseline_v1_3.longmemeval_con_reader import (
    ChainOfNoteReader,
    ChainOfNoteReaderError,
    render_answer_prompt,
    render_note_prompt,
)


class FakeTransport:
    config_sha256 = "a" * 64

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def complete(self, request: dict[str, object]) -> object:
        self.requests.append(request)
        index = len(self.requests)
        return SimpleNamespace(
            content=f"note-or-answer-{index}",
            prompt_tokens=10,
            completion_tokens=3,
            finish_reason="stop",
        )


def _sessions() -> tuple[MaterializedSession, ...]:
    return (
        MaterializedSession(
            session_id="s0",
            session_date="2023/01/01 (Sun) 00:00",
            turns=({"role": "user", "content": "old fact"},),
            retrieval_rank=1,
        ),
        MaterializedSession(
            session_id="s1",
            session_date="2023/01/02 (Mon) 00:00",
            turns=({"role": "assistant", "content": "new fact"},),
            retrieval_rank=2,
        ),
    )


def test_note_and_answer_prompts_are_fixed_json_con_contracts() -> None:
    note = render_note_prompt(_sessions()[0], question_date="2023/01/03", question="What changed?")
    assert "Extracted note" in note
    assert '"role": "user"' in note
    answer = render_answer_prompt(
        (("2023/01/01", "old note"), ("2023/01/02", "new note")),
        question_date="2023/01/03",
        question="What changed?",
    )
    assert '"session_summary": "old note"' in answer
    assert "step by step" in answer


@pytest.mark.asyncio
async def test_con_reader_uses_one_note_call_per_session_then_one_answer_call() -> None:
    transport = FakeTransport()
    reader = ChainOfNoteReader(model="qwen3-32b-fp8", transport=transport)
    result = await reader.answer(_sessions(), question_date="2023/01/03", question="What changed?")
    assert result.note_calls == 2
    assert len(transport.requests) == 3
    assert transport.requests[0]["max_tokens"] == 500
    assert transport.requests[-1]["max_tokens"] == 800
    assert transport.requests[-1]["temperature"] == 0
    assert result.answer == "note-or-answer-3"
    artifact = result.to_artifact()
    assert artifact["note_calls"] == 2
    assert len(artifact["note_prompt_sha256"]) == 2


@pytest.mark.asyncio
async def test_con_reader_prompt_does_not_include_session_ids_or_gold() -> None:
    transport = FakeTransport()
    reader = ChainOfNoteReader(model="qwen3-32b-fp8", transport=transport)
    result = await reader.answer(_sessions(), question_date="2023/01/03", question="What changed?")
    assert "s0" not in result.prompt_for_test
    assert "s1" not in result.prompt_for_test
    assert "gold-answer" not in result.prompt_for_test


@pytest.mark.asyncio
async def test_con_reader_fails_closed_on_truncated_note_or_answer() -> None:
    class Truncated(FakeTransport):
        async def complete(self, request: dict[str, object]) -> object:
            self.requests.append(request)
            return SimpleNamespace(content="note", prompt_tokens=1, completion_tokens=1, finish_reason="length")

    reader = ChainOfNoteReader(model="qwen3-32b-fp8", transport=Truncated())
    with pytest.raises(ChainOfNoteReaderError, match="NOT_STOP"):
        await reader.answer(_sessions(), question_date="2023/01/03", question="What changed?")
