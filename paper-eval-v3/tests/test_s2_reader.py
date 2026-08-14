from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from paper_eval.s2_retrieval_contract import (
    EDGE_SURFACE_CONTRACT,
    validate_retrieval_identity,
)
from paper_eval.s2_reader import (
    LONGMEMEVAL_READER_COMMIT,
    LONGMEMEVAL_READER_FILE_SHA256,
    OfficialFactsReader,
    ReaderServiceError,
    RetrievedFact,
    render_official_facts_prompt,
)


class _CaptureTransport:
    def __init__(self, *, answer: str = "The current answer.") -> None:
        self.answer = answer
        self.requests: list[dict[str, object]] = []

    async def complete(self, request: dict[str, object]) -> object:
        self.requests.append(request)
        return SimpleNamespace(
            content=self.answer,
            prompt_tokens=123,
            completion_tokens=7,
        )


def _facts() -> list[RetrievedFact]:
    return [
        RetrievedFact(
            rank=2,
            fact="Ravi now works at OpenAI.",
            reference_time="2024/02/02 (Fri) 10:00",
            source_session_ids=("s2",),
        ),
        RetrievedFact(
            rank=1,
            fact="Ravi previously worked at Google.",
            reference_time="2024/01/01 (Mon) 09:00",
            source_session_ids=("s1",),
        ),
    ]


def test_official_facts_prompt_matches_pinned_replace_direct_reader() -> None:
    prompt = render_official_facts_prompt(
        _facts(),
        question_date="2024/03/01 (Fri) 12:00",
        question="Where does Ravi work now?",
    )

    assert prompt == (
        "I will give you several facts extracted from history chats between you and a user. "
        "Please answer the question based on the relevant facts.\n\n\n"
        "History Chats:\n\n"
        "\n### Session 1:\n"
        "Session Date: 2024/01/01 (Mon) 09:00\n"
        "Session Content:\n"
        "\n\"Ravi previously worked at Google.\"\n"
        "\n### Session 2:\n"
        "Session Date: 2024/02/02 (Fri) 10:00\n"
        "Session Content:\n"
        "\n\"Ravi now works at OpenAI.\"\n"
        "\n\nCurrent Date: 2024/03/01 (Fri) 12:00\n"
        "Question: Where does Ravi work now?\n"
        "Answer:"
    )
    assert LONGMEMEVAL_READER_COMMIT == "9e0b455f4ef0e2ab8f2e582289761153549043fc"
    assert LONGMEMEVAL_READER_FILE_SHA256 == (
        "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672"
    )


def test_official_facts_prompt_fails_closed_on_empty_or_duplicate_rank() -> None:
    with pytest.raises(ValueError, match="at least one"):
        render_official_facts_prompt([], question_date="date", question="question")
    duplicate = [*_facts(), _facts()[0]]
    with pytest.raises(ValueError, match="rank"):
        render_official_facts_prompt(
            duplicate, question_date="date", question="question"
        )


def test_reader_identity_declares_edge_fact_input_not_flat_session() -> None:
    reader = OfficialFactsReader(
        model="qwen3-32b-fp8",
        transport=_CaptureTransport(),
    )
    assert reader.public_config["input_representation"] == "EntityEdge.fact"
    assert reader.public_config["official_flat_session_item_semantics"] is False
    assert "retriever_type" not in reader.public_config
    identity = {
        **EDGE_SURFACE_CONTRACT.to_identity(),
        "retriever_type": "graphiti-basic-edge",
    }
    assert validate_retrieval_identity(identity) == identity


def test_reader_sends_exact_official_chat_contract_and_sanitizes_artifact() -> None:
    transport = _CaptureTransport(answer="OpenAI")
    reader = OfficialFactsReader(
        model="qwen3-32b-fp8",
        transport=transport,
    )

    result = asyncio.run(
        reader.answer(
            _facts(),
            question_date="2024/03/01 (Fri) 12:00",
            question="Where does Ravi work now?",
        )
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request["model"] == "qwen3-32b-fp8"
    assert request["messages"] == [
        {"role": "user", "content": result.prompt_for_test}
    ]
    assert request["temperature"] == 0
    assert request["max_tokens"] == 500
    assert request["n"] == 1
    assert "response_format" not in request
    assert "system" not in repr(request).lower()
    assert "extra_body" not in request

    assert result.answer == "OpenAI"
    safe = result.to_artifact()
    assert safe["status"] == "SUCCESS"
    assert safe["prompt_sha256"] == hashlib.sha256(
        result.prompt_for_test.encode("utf-8")
    ).hexdigest()
    assert safe["output_sha256"] == hashlib.sha256(b"OpenAI").hexdigest()
    assert safe["prompt_tokens"] == 123
    assert safe["completion_tokens"] == 7
    assert "OpenAI" not in repr(safe)
    assert "Ravi" not in repr(safe)
    assert "prompt_for_test" not in safe
    assert "answer" not in safe


def test_reader_service_failure_is_sanitized_and_not_retried() -> None:
    class _FailingTransport:
        def __init__(self) -> None:
            self.call_count = 0

        async def complete(self, request: dict[str, object]) -> object:
            self.call_count += 1
            raise ConnectionError("private endpoint and prompt must not escape")

    transport = _FailingTransport()
    reader = OfficialFactsReader(model="qwen3-32b-fp8", transport=transport)

    with pytest.raises(ReaderServiceError) as captured:
        asyncio.run(
            reader.answer(
                _facts(),
                question_date="date",
                question="question",
            )
        )

    assert transport.call_count == 1
    assert str(captured.value) == "reader request failed: ConnectionError"
    assert "private" not in str(captured.value)
