"""LongMemEval's JSON + Chain-of-Note Reader contract.

The contract is copied from the pinned LongMemEval generation source
(``history_format=json``, ``cot=true``, ``con=true``).  It is a QA-only
adapter: each retrieved persisted session is converted into a reading note,
then one final Reader call answers from the ordered JSON notes.  No graph
construction or source-dataset body is involved here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from paper_eval.artifacts import payload_sha256
from paper_eval.s2_session_reader import MaterializedSession


class ChainOfNoteReaderError(ValueError):
    """The fixed CoN Reader contract or provider response is invalid."""


class ChainOfNoteTransport(Protocol):
    config_sha256: str

    async def complete(self, request: dict[str, object]) -> object: ...


_NOTE_TEMPLATE = (
    "I will give you a chat history between you and a user, as well as a "
    "question from the user. Write reading notes to extract all the relevant "
    "user information relevant to answering the answer. If no relevant "
    "information is found, just output \"empty\". \n\n\n"
    "Chat History:\nSession Date: {}\nSession Content:\n{}\n\n"
    "Question Date: {}\nQuestion: {}\n"
    "Extracted note (information relevant to answering the question):"
)
_ANSWER_TEMPLATE = (
    "I will give you several history chats between you and a user. Please "
    "answer the question based on the relevant chat history. Answer the "
    "question step by step: first extract all the relevant information, and "
    "then reason over the information to get the answer.\n\n\n"
    "History Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\n"
    "Answer (step by step):"
)


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChainOfNoteReaderError(f"CON_{field.upper()}_INVALID")
    return value.strip()


def render_note_prompt(session: MaterializedSession, *, question_date: str, question: str) -> str:
    if not isinstance(session, MaterializedSession):
        raise ChainOfNoteReaderError("CON_SESSION_INVALID")
    date = _text(session.session_date, field="session_date")
    turns = list(session.turns)
    if not turns:
        raise ChainOfNoteReaderError("CON_SESSION_TURNS_EMPTY")
    for turn in turns:
        if not isinstance(turn, dict) or turn.get("role") not in {"user", "assistant"} or not isinstance(turn.get("content"), str) or not turn["content"].strip():
            raise ChainOfNoteReaderError("CON_SESSION_TURN_INVALID")
    return _NOTE_TEMPLATE.format(date, json.dumps(turns), _text(question_date, field="question_date"), _text(question, field="question"))


def render_answer_prompt(notes: Sequence[tuple[str, str]], *, question_date: str, question: str) -> str:
    if isinstance(notes, (str, bytes)) or not isinstance(notes, Sequence) or not notes:
        raise ChainOfNoteReaderError("CON_NOTES_INVALID")
    values: list[dict[str, str]] = []
    for date, note in notes:
        values.append({"session_summary": _text(note, field="note")})
        _text(date, field="session_date")
    history = ""
    for value in values:
        history += "\n" + json.dumps(value)
    return _ANSWER_TEMPLATE.format(history, _text(question_date, field="question_date"), _text(question, field="question"))


def _nonnegative(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ChainOfNoteReaderError(f"CON_{field.upper()}_INVALID")
    return value


@dataclass(frozen=True)
class ChainOfNoteReaderResult:
    answer: str
    prompt_for_test: str
    prompt_tokens: int
    completion_tokens: int
    note_calls: int
    note_prompt_sha256: tuple[str, ...]
    note_output_sha256: tuple[str, ...]
    model: str
    config_sha256: str

    def to_artifact(self) -> dict[str, Any]:
        answer_bytes = self.answer.encode("utf-8")
        prompt_bytes = self.prompt_for_test.encode("utf-8")
        return {
            "status": "SUCCESS",
            "model": self.model,
            "config_sha256": self.config_sha256,
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "prompt_character_count": len(self.prompt_for_test),
            "prompt_byte_count": len(prompt_bytes),
            "output_sha256": hashlib.sha256(answer_bytes).hexdigest(),
            "output_character_count": len(self.answer),
            "output_byte_count": len(answer_bytes),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "note_calls": self.note_calls,
            "note_prompt_sha256": list(self.note_prompt_sha256),
            "note_output_sha256": list(self.note_output_sha256),
            "truncation_count": 0,
        }


class ChainOfNoteReader:
    """One fixed LongMemEval CoN pipeline with hidden retries disabled upstream."""

    def __init__(self, *, model: str, transport: ChainOfNoteTransport) -> None:
        self.model = _text(model, field="model")
        config_hash = getattr(transport, "config_sha256", None)
        if not isinstance(config_hash, str) or len(config_hash) != 64:
            raise ChainOfNoteReaderError("CON_TRANSPORT_CONFIG_INVALID")
        self._transport = transport
        self.public_config = {
            "implementation": "longmemeval_json_chain_of_note_reader",
            "upstream_repository": "xiaowu0162/LongMemEval",
            "upstream_commit": "9e0b455f4ef0e2ab8f2e582289761153549043fc",
            "upstream_source_path": "src/generation/run_generation.py",
            "upstream_source_sha256": "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672",
            "retriever_type": "flat-session",
            "history_format": "json",
            "topk_context": 10,
            "cot": True,
            "con": True,
            "note_max_tokens": 500,
            "answer_max_tokens": 800,
            "temperature": 0,
            "n": 1,
            "thinking_control": "client_request",
            "effective_enable_thinking": False,
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
            "note_prompt_sha256": hashlib.sha256(_NOTE_TEMPLATE.encode("utf-8")).hexdigest(),
            "answer_prompt_sha256": hashlib.sha256(_ANSWER_TEMPLATE.encode("utf-8")).hexdigest(),
            "transport_config_sha256": config_hash,
            "source_text": "READ_ONLY_NEO4J_EPISODIC_CONTENT_ONLY",
        }
        self.config_sha256 = payload_sha256(self.public_config)

    async def _complete(self, request: dict[str, object]) -> object:
        try:
            return await self._transport.complete(request)
        except Exception as error:
            raise ChainOfNoteReaderError(f"CON_REQUEST_FAILED:{type(error).__name__}") from None

    @staticmethod
    def _response(response: Any) -> tuple[str, int, int]:
        content = getattr(response, "content", None)
        finish_reason = getattr(response, "finish_reason", None)
        if not isinstance(content, str) or not content.strip():
            raise ChainOfNoteReaderError("CON_RESPONSE_CONTENT_INVALID")
        if finish_reason != "stop":
            raise ChainOfNoteReaderError("CON_RESPONSE_NOT_STOP")
        return content.strip(), _nonnegative(getattr(response, "prompt_tokens", None), field="prompt_tokens"), _nonnegative(getattr(response, "completion_tokens", None), field="completion_tokens")

    async def answer(self, sessions: Sequence[MaterializedSession], *, question_date: str, question: str) -> ChainOfNoteReaderResult:
        if isinstance(sessions, (str, bytes)) or not isinstance(sessions, Sequence) or not sessions:
            raise ChainOfNoteReaderError("CON_SESSIONS_INVALID")
        values = tuple(sessions)
        if any(not isinstance(value, MaterializedSession) for value in values):
            raise ChainOfNoteReaderError("CON_SESSIONS_INVALID")
        ranks = [value.retrieval_rank for value in values]
        if sorted(ranks) != list(range(1, len(values) + 1)):
            raise ChainOfNoteReaderError("CON_SESSION_RANKS_INVALID")
        note_prompts: list[str] = []
        note_outputs: list[str] = []
        notes: list[tuple[str, str]] = []
        prompt_tokens = 0
        completion_tokens = 0
        for session in values:
            prompt = render_note_prompt(session, question_date=question_date, question=question)
            response = await self._complete({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 500,
                "n": 1,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            })
            note, in_tokens, out_tokens = self._response(response)
            note_prompts.append(hashlib.sha256(prompt.encode("utf-8")).hexdigest())
            note_outputs.append(hashlib.sha256(note.encode("utf-8")).hexdigest())
            notes.append((session.session_date, note))
            prompt_tokens += in_tokens
            completion_tokens += out_tokens
        answer_prompt = render_answer_prompt(notes, question_date=question_date, question=question)
        answer_response = await self._complete({
            "model": self.model,
            "messages": [{"role": "user", "content": answer_prompt}],
            "temperature": 0,
            "max_tokens": 800,
            "n": 1,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        })
        answer, final_in, final_out = self._response(answer_response)
        return ChainOfNoteReaderResult(
            answer=answer,
            prompt_for_test=answer_prompt,
            prompt_tokens=prompt_tokens + final_in,
            completion_tokens=completion_tokens + final_out,
            note_calls=len(values),
            note_prompt_sha256=tuple(note_prompts),
            note_output_sha256=tuple(note_outputs),
            model=self.model,
            config_sha256=self.config_sha256,
        )


__all__ = [
    "ChainOfNoteReader",
    "ChainOfNoteReaderError",
    "ChainOfNoteReaderResult",
    "render_answer_prompt",
    "render_note_prompt",
]
