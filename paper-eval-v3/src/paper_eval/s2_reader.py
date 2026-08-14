"""Pinned LongMemEval facts-template Reader with an injectable transport.

The prompt text mirrors LongMemEval's ``replace``/JSON/non-CoT facts template,
but the current caller supplies Graphiti EntityEdge facts rather than one
ranked item per LongMemEval session. Raw prompts and answers remain in memory;
only hashes, lengths, token counts, and public configuration are serializable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .artifacts import payload_sha256


LONGMEMEVAL_READER_REPOSITORY = "xiaowu0162/LongMemEval"
LONGMEMEVAL_READER_COMMIT = "9e0b455f4ef0e2ab8f2e582289761153549043fc"
LONGMEMEVAL_READER_SOURCE_PATH = "src/generation/run_generation.py"
LONGMEMEVAL_READER_GIT_BLOB = "8e9e0f25b804d3d0afbadc9619264b0c7a275dc0"
LONGMEMEVAL_READER_FILE_SHA256 = (
    "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672"
)

_FACTS_TEMPLATE = (
    "I will give you several facts extracted from history chats between you and a user. "
    "Please answer the question based on the relevant facts.\n\n\n"
    "History Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer:"
)


@dataclass(frozen=True)
class RetrievedFact:
    rank: int
    fact: str
    reference_time: str
    source_session_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("fact rank must be a positive integer")
        if not isinstance(self.fact, str) or not self.fact.strip():
            raise ValueError("fact text must be nonempty")
        if not isinstance(self.reference_time, str) or not self.reference_time.strip():
            raise ValueError("fact reference time must be nonempty")
        if not isinstance(self.source_session_ids, tuple) or any(
            not isinstance(value, str) or not value for value in self.source_session_ids
        ):
            raise ValueError("fact source session IDs are invalid")


class ReaderTransport(Protocol):
    async def complete(self, request: dict[str, object]) -> object: ...


class ReaderServiceError(RuntimeError):
    """A sanitized transport or response failure."""


def render_official_facts_prompt(
    facts: Sequence[RetrievedFact],
    *,
    question_date: str,
    question: str,
) -> str:
    """Render the pinned ``replace``/JSON/non-CoT facts prompt exactly."""

    materialized = list(facts)
    if not materialized:
        raise ValueError("Reader requires at least one retrieved fact")
    if not isinstance(question_date, str) or not question_date:
        raise ValueError("question date must be nonempty")
    if not isinstance(question, str) or not question:
        raise ValueError("question must be nonempty")
    ranks = [item.rank for item in materialized]
    if len(set(ranks)) != len(ranks) or sorted(ranks) != list(
        range(1, len(ranks) + 1)
    ):
        raise ValueError("fact ranks must be unique and contiguous")

    # The official implementation restores chronological order before
    # assigning the displayed Session indices.
    ordered = sorted(materialized, key=lambda item: item.reference_time)
    history = ""
    for index, item in enumerate(ordered, start=1):
        encoded_fact = "\n" + json.dumps(item.fact)
        history += (
            f"\n### Session {index}:\n"
            f"Session Date: {item.reference_time}\n"
            f"Session Content:\n{encoded_fact}\n"
        )
    return _FACTS_TEMPLATE.format(history, question_date, question)


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReaderServiceError(f"reader response invalid: {field}")
    return value


@dataclass(frozen=True)
class ReaderResult:
    answer: str
    prompt_for_test: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    config_sha256: str

    def to_artifact(self) -> dict[str, object]:
        prompt_bytes = self.prompt_for_test.encode("utf-8")
        output_bytes = self.answer.encode("utf-8")
        return {
            "status": "SUCCESS",
            "model": self.model,
            "config_sha256": self.config_sha256,
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "prompt_character_count": len(self.prompt_for_test),
            "prompt_byte_count": len(prompt_bytes),
            "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
            "output_character_count": len(self.answer),
            "output_byte_count": len(output_bytes),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


class OfficialFactsReader:
    """Issue one official Reader request without hidden prompt additions."""

    def __init__(self, *, model: str, transport: ReaderTransport) -> None:
        if not isinstance(model, str) or not model:
            raise ValueError("Reader model must be nonempty")
        self.model = model
        self._transport = transport
        self.public_config = {
            "implementation": "longmemeval_official_facts_reader",
            "upstream_repository": LONGMEMEVAL_READER_REPOSITORY,
            "upstream_commit": LONGMEMEVAL_READER_COMMIT,
            "upstream_source_path": LONGMEMEVAL_READER_SOURCE_PATH,
            "upstream_git_blob": LONGMEMEVAL_READER_GIT_BLOB,
            "upstream_file_sha256": LONGMEMEVAL_READER_FILE_SHA256,
            "prompt_template_alignment": "longmemeval_replace_json_non_cot",
            "input_representation": "EntityEdge.fact",
            "official_flat_session_item_semantics": False,
            "merge_key_expansion_into_value": "replace",
            "history_format": "json",
            "useronly": False,
            "cot": False,
            "messages": ["user"],
            "temperature": 0,
            "max_tokens": 500,
            "n": 1,
            "system_prompt": None,
            "response_format": None,
            "model": model,
        }
        self.config_sha256 = payload_sha256(self.public_config)

    async def answer(
        self,
        facts: Sequence[RetrievedFact],
        *,
        question_date: str,
        question: str,
    ) -> ReaderResult:
        prompt = render_official_facts_prompt(
            facts,
            question_date=question_date,
            question=question,
        )
        request: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 500,
            "n": 1,
        }
        try:
            response = await self._transport.complete(request)
        except Exception as error:
            raise ReaderServiceError(
                f"reader request failed: {type(error).__name__}"
            ) from None
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ReaderServiceError("reader response invalid: content")
        return ReaderResult(
            answer=content.strip(),
            prompt_for_test=prompt,
            prompt_tokens=_nonnegative_int(
                getattr(response, "prompt_tokens", None), "prompt_tokens"
            ),
            completion_tokens=_nonnegative_int(
                getattr(response, "completion_tokens", None), "completion_tokens"
            ),
            model=self.model,
            config_sha256=self.config_sha256,
        )
