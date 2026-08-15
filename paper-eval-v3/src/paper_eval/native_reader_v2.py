"""Pinned single-call LongMemEval CoN Reader for the common evaluation layer."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Protocol

from .artifacts import payload_sha256
from .s2_session_reader import MaterializedSession, SessionReaderResult


LONGMEMEVAL_READER_V2_REPOSITORY = "xiaowu0162/LongMemEval"
LONGMEMEVAL_READER_V2_COMMIT = "9e0b455f4ef0e2ab8f2e582289761153549043fc"
LONGMEMEVAL_READER_V2_SOURCE_PATH = "src/generation/run_generation.py"
LONGMEMEVAL_READER_V2_SOURCE_SHA256 = (
    "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672"
)
LONGMEMEVAL_READER_V2_RUNNER_PATH = "src/generation/run_generation.sh"
LONGMEMEVAL_READER_V2_RUNNER_SHA256 = (
    "6602147b866eca4a80acdf5e6689389586086216c9198fce7b8380b7495c5422"
)
LONGMEMEVAL_READER_V2_README_SHA256 = (
    "c4ff45676683d9e2f7cf7d9099d26426f14635ec110dbb1da818d1019a142573"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CON_SESSION_TEMPLATE = (
    "I will give you several history chats between you and a user. "
    "Please answer the question based on the relevant chat history. "
    "Answer the question step by step: first extract all the relevant "
    "information, and then reason over the information to get the answer.\n\n\n"
    "History Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\n"
    "Answer (step by step):"
)


class ReaderV2Error(ValueError):
    """The versioned Reader contract failed without exposing private content."""


class ReaderV2Transport(Protocol):
    async def complete(self, request: dict[str, object]) -> object: ...


def resolve_official_reading_method(method: str) -> dict[str, object]:
    """Resolve the public upstream name without confusing it with `--con`."""

    if method != "con":
        raise ReaderV2Error("reading_method must be exactly con")
    return {
        "reading_method": "con",
        "cot": True,
        "con": False,
        "separate_note_extraction": False,
        "reader_requests_per_question": 1,
        "max_tokens": 800,
    }


def _validate_prompt_inputs(
    sessions: Sequence[MaterializedSession],
    *,
    question_date: str,
    question: str,
) -> tuple[MaterializedSession, ...]:
    if isinstance(sessions, (str, bytes)) or not isinstance(sessions, Sequence):
        raise ReaderV2Error("Reader-v2 sessions are invalid")
    materialized = tuple(sessions)
    if not materialized or any(
        not isinstance(item, MaterializedSession) for item in materialized
    ):
        raise ReaderV2Error("Reader-v2 sessions are invalid")
    ranks = [item.retrieval_rank for item in materialized]
    if len(set(ranks)) != len(ranks) or sorted(ranks) != list(
        range(1, len(ranks) + 1)
    ):
        raise ReaderV2Error("Reader-v2 retrieval ranks are invalid")
    if [item.session_date for item in materialized] != sorted(
        item.session_date for item in materialized
    ):
        raise ReaderV2Error("Reader-v2 sessions are not chronological")
    if not isinstance(question_date, str) or not question_date:
        raise ReaderV2Error("Reader-v2 question date is invalid")
    if not isinstance(question, str) or not question:
        raise ReaderV2Error("Reader-v2 question is invalid")
    return materialized


def render_official_con_session_prompt(
    sessions: Sequence[MaterializedSession],
    *,
    question_date: str,
    question: str,
) -> str:
    """Render the pinned `READING_METHOD=con` single-completion prompt."""

    materialized = _validate_prompt_inputs(
        sessions,
        question_date=question_date,
        question=question,
    )
    history = ""
    for index, item in enumerate(materialized, start=1):
        session_json = "\n" + json.dumps(list(item.turns))
        history += (
            f"\n### Session {index}:\n"
            f"Session Date: {item.session_date}\n"
            f"Session Content:\n{session_json}\n"
        )
    return _CON_SESSION_TEMPLATE.format(history, question_date, question)


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReaderV2Error(f"Reader-v2 response invalid: {field}")
    return value


def common_method_reader_bindings(config_sha256: str) -> dict[str, str]:
    """Bind every compared execution policy to the same Reader identity."""

    if not isinstance(config_sha256, str) or _SHA256.fullmatch(config_sha256) is None:
        raise ReaderV2Error("Reader-v2 config SHA256 is invalid")
    return {method: config_sha256 for method in ("U0", "A0", "P*", "M*")}


class OfficialConSessionReader:
    """Issue one pinned CoN-style answer request with no retry or note calls."""

    def __init__(self, *, model: str, transport: ReaderV2Transport) -> None:
        if not isinstance(model, str) or not model:
            raise ReaderV2Error("Reader-v2 model is invalid")
        method = resolve_official_reading_method("con")
        self.model = model
        self._transport = transport
        self.public_config = {
            "implementation": "longmemeval_official_con_session_reader_v2",
            "upstream_repository": LONGMEMEVAL_READER_V2_REPOSITORY,
            "upstream_commit": LONGMEMEVAL_READER_V2_COMMIT,
            "upstream_source_path": LONGMEMEVAL_READER_V2_SOURCE_PATH,
            "upstream_source_sha256": LONGMEMEVAL_READER_V2_SOURCE_SHA256,
            "upstream_runner_path": LONGMEMEVAL_READER_V2_RUNNER_PATH,
            "upstream_runner_sha256": LONGMEMEVAL_READER_V2_RUNNER_SHA256,
            "upstream_readme_sha256": LONGMEMEVAL_READER_V2_README_SHA256,
            "prompt_template_sha256": hashlib.sha256(
                _CON_SESSION_TEMPLATE.encode("utf-8")
            ).hexdigest(),
            "input_representation": "longmemeval_flat_session_item",
            "retriever_type": "flat-session",
            "topk_context": 10,
            "history_format": "json",
            "useronly": False,
            **method,
            "merge_key_expansion_into_value": "none",
            "session_value_source": "frozen_dataset_haystack_sessions",
            "has_answer_label_removed": True,
            "presentation_order": "chronological_after_top_k_rank_stable_ties",
            "messages": ["user"],
            "system_prompt": None,
            "temperature": 0,
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
        prompt = render_official_con_session_prompt(
            sessions,
            question_date=question_date,
            question=question,
        )
        request: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 800,
            "n": 1,
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False}
            },
        }
        try:
            response = await self._transport.complete(request)
        except Exception as error:
            raise ReaderV2Error(
                f"Reader-v2 request failed: {type(error).__name__}"
            ) from None
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ReaderV2Error("Reader-v2 response invalid: content")
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
