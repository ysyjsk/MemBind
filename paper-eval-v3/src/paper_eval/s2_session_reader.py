"""Pinned LongMemEval flat-session Reader with no live dependency at import."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from .artifacts import payload_sha256


LONGMEMEVAL_SESSION_READER_REPOSITORY = "xiaowu0162/LongMemEval"
LONGMEMEVAL_SESSION_READER_COMMIT = "9e0b455f4ef0e2ab8f2e582289761153549043fc"
LONGMEMEVAL_SESSION_READER_SOURCE_PATH = "src/generation/run_generation.py"
LONGMEMEVAL_SESSION_READER_FILE_SHA256 = (
    "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672"
)

_SESSION_TEMPLATE = (
    "I will give you several history chats between you and a user. "
    "Please answer the question based on the relevant chat history.\n\n\n"
    "History Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer:"
)


class SessionReaderError(ValueError):
    """A dataset, prompt, transport, or response contract failed safely."""


@dataclass(frozen=True)
class MaterializedSession:
    session_id: str
    session_date: str
    turns: tuple[dict[str, Any], ...]
    retrieval_rank: int


class SessionReaderTransport(Protocol):
    async def complete(self, request: dict[str, object]) -> object: ...


def _positive(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SessionReaderError(f"{field} must be a positive integer")
    return value


def _remove_has_answer(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _remove_has_answer(child)
            for key, child in value.items()
            if str(key) != "has_answer"
        }
    if isinstance(value, list):
        return [_remove_has_answer(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_remove_has_answer(child) for child in value)
    return deepcopy(value)


def _validated_dataset_sessions(
    record: Mapping[str, Any],
) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    if not isinstance(record, Mapping):
        raise SessionReaderError("dataset session record is invalid")
    ids = record.get("haystack_session_ids")
    dates = record.get("haystack_dates")
    sessions = record.get("haystack_sessions")
    if not all(isinstance(value, list) for value in (ids, dates, sessions)):
        raise SessionReaderError("dataset session arrays are missing")
    if not ids or len(ids) != len(dates) or len(ids) != len(sessions):
        raise SessionReaderError("dataset session arrays have different lengths")
    if any(not isinstance(value, str) or not value for value in ids):
        raise SessionReaderError("dataset session ID is invalid")
    if len(ids) != len(set(ids)):
        raise SessionReaderError("dataset session IDs must be unique")
    if any(not isinstance(value, str) or not value for value in dates):
        raise SessionReaderError("dataset session date is invalid")

    indexed: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for session_id, date, raw_turns in zip(ids, dates, sessions, strict=True):
        if not isinstance(raw_turns, list) or not raw_turns:
            raise SessionReaderError("dataset session turns are invalid")
        turns: list[dict[str, Any]] = []
        for raw_turn in raw_turns:
            if (
                not isinstance(raw_turn, Mapping)
                or not isinstance(raw_turn.get("role"), str)
                or not raw_turn.get("role")
                or not isinstance(raw_turn.get("content"), str)
            ):
                raise SessionReaderError("dataset session turn is invalid")
            cleaned = _remove_has_answer(dict(raw_turn))
            if not isinstance(cleaned, dict):
                raise SessionReaderError("dataset session turn cleaning failed")
            turns.append(cleaned)
        indexed[session_id] = (date, turns)
    return indexed


def materialize_ranked_sessions(
    *,
    record: Mapping[str, Any],
    ranked_session_ids: Sequence[str],
    top_k: int,
) -> tuple[MaterializedSession, ...]:
    """Map ranked IDs to copied corpus values, then stably sort by date."""

    limit = _positive(top_k, field="top_k")
    if isinstance(ranked_session_ids, (str, bytes)) or not isinstance(
        ranked_session_ids, Sequence
    ):
        raise SessionReaderError("ranked session IDs must be a sequence")
    selected = tuple(ranked_session_ids[:limit])
    if len(selected) != limit:
        raise SessionReaderError("ranked session results are incomplete")
    if any(not isinstance(value, str) or not value for value in selected):
        raise SessionReaderError("ranked session ID is invalid")
    if len(selected) != len(set(selected)):
        raise SessionReaderError("ranked session IDs must be unique")

    corpus = _validated_dataset_sessions(record)
    if not set(selected).issubset(corpus):
        raise SessionReaderError("ranked session is foreign to the dataset corpus")
    ranked = [
        MaterializedSession(
            session_id=session_id,
            session_date=corpus[session_id][0],
            turns=tuple(deepcopy(corpus[session_id][1])),
            retrieval_rank=rank,
        )
        for rank, session_id in enumerate(selected, start=1)
    ]
    return tuple(sorted(ranked, key=lambda item: item.session_date))


def render_official_session_prompt(
    sessions: Sequence[MaterializedSession],
    *,
    question_date: str,
    question: str,
) -> str:
    """Render pinned LongMemEval JSON/non-CoT/flat-session semantics."""

    if isinstance(sessions, (str, bytes)) or not isinstance(sessions, Sequence):
        raise SessionReaderError("Reader sessions are invalid")
    materialized = tuple(sessions)
    if not materialized or any(
        not isinstance(item, MaterializedSession) for item in materialized
    ):
        raise SessionReaderError("Reader sessions are invalid")
    ranks = [item.retrieval_rank for item in materialized]
    if len(set(ranks)) != len(ranks) or sorted(ranks) != list(
        range(1, len(ranks) + 1)
    ):
        raise SessionReaderError("Reader retrieval ranks are invalid")
    if not isinstance(question_date, str) or not question_date:
        raise SessionReaderError("Reader question date is invalid")
    if not isinstance(question, str) or not question:
        raise SessionReaderError("Reader question is invalid")

    history = ""
    for index, item in enumerate(materialized, start=1):
        session_json = "\n" + json.dumps(list(item.turns))
        history += (
            f"\n### Session {index}:\n"
            f"Session Date: {item.session_date}\n"
            f"Session Content:\n{session_json}\n"
        )
    return _SESSION_TEMPLATE.format(history, question_date, question)


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SessionReaderError(f"Reader response invalid: {field}")
    return value


@dataclass(frozen=True)
class SessionReaderResult:
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
            "truncation_count": 0,
        }


class OfficialSessionReader:
    """Issue one explicit no-thinking Reader request without retry."""

    def __init__(self, *, model: str, transport: SessionReaderTransport) -> None:
        if not isinstance(model, str) or not model:
            raise SessionReaderError("Reader model is invalid")
        self.model = model
        self._transport = transport
        self.public_config = {
            "implementation": "longmemeval_official_session_reader",
            "upstream_repository": LONGMEMEVAL_SESSION_READER_REPOSITORY,
            "upstream_commit": LONGMEMEVAL_SESSION_READER_COMMIT,
            "upstream_source_path": LONGMEMEVAL_SESSION_READER_SOURCE_PATH,
            "upstream_file_sha256": LONGMEMEVAL_SESSION_READER_FILE_SHA256,
            "prompt_template_sha256": hashlib.sha256(
                _SESSION_TEMPLATE.encode("utf-8")
            ).hexdigest(),
            "input_representation": "longmemeval_flat_session_item",
            "official_flat_session_item_semantics": True,
            "retriever_type": "flat-session",
            "topk_context": 10,
            "history_format": "json",
            "useronly": False,
            "cot": False,
            "con": False,
            "merge_key_expansion_into_value": "none",
            "session_value_source": "frozen_dataset_haystack_sessions",
            "has_answer_label_removed": True,
            "presentation_order": "chronological_after_top_k_rank_stable_ties",
            "messages": ["user"],
            "system_prompt": None,
            "temperature": 0,
            "max_tokens": 500,
            "n": 1,
            "thinking_control": "client_request",
            "effective_enable_thinking": False,
            "thinking_parameter_sent": True,
            "truncation_policy": "FAIL_CLOSED_IF_CONTEXT_EXCEEDED",
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
            "retry_delays_seconds": [],
            "model": model,
        }
        self.config_sha256 = payload_sha256(self.public_config)

    async def answer(
        self,
        sessions: Sequence[MaterializedSession],
        *,
        question_date: str,
        question: str,
    ) -> SessionReaderResult:
        prompt = render_official_session_prompt(
            sessions, question_date=question_date, question=question
        )
        request: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 500,
            "n": 1,
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False}
            },
        }
        try:
            response = await self._transport.complete(request)
        except Exception as error:
            raise SessionReaderError(
                f"Reader request failed: {type(error).__name__}"
            ) from None
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise SessionReaderError("Reader response invalid: content")
        return SessionReaderResult(
            answer=content.strip(),
            prompt_for_test=prompt,
            prompt_tokens=_nonnegative_int(
                getattr(response, "prompt_tokens", None), field="prompt_tokens"
            ),
            completion_tokens=_nonnegative_int(
                getattr(response, "completion_tokens", None),
                field="completion_tokens",
            ),
            model=self.model,
            config_sha256=self.config_sha256,
        )
