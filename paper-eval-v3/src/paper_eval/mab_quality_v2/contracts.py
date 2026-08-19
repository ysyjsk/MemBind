"""Contracts for the isolated MAB quality lane.

The types in this module intentionally keep benchmark labels separate from the
runtime projection.  A context may be serialized for construction/retrieval
without ever carrying its reference answers or gold session annotations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


FAILURE_TAXONOMY = (
    "DATASET_MAPPING_INVALID",
    "CONSTRUCTION_FAILED",
    "NAMESPACE_NOT_SEALED",
    "RETRIEVAL_FAILED",
    "CONTEXT_PACK_INVALID",
    "READER_FAILED",
    "READER_INVALID_FINISH",
    "JUDGE_FAILED",
    "JUDGE_INVALID",
    "GOLD_LEAK_DETECTED",
    "QA_PHASE_WRITE_VIOLATION",
    "RESUME_IDENTITY_MISMATCH",
    "ARTIFACT_HASH_MISMATCH",
    "UNKNOWN_INFRA_FAILURE",
)

_GOLD_KEYS = frozenset(
    {
        "answer",
        "answers",
        "reference_answer",
        "reference_answers",
        "has_answer",
        "gold_session_ids",
        "gold_sessions",
        "qa_pair_id",
        "question_type",
    }
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list or tuple")
    return tuple(value)


def _turns(value: Any) -> tuple[dict[str, str], ...]:
    values = _sequence(value, "turns")
    if not values:
        raise ValueError("turns must not be empty")
    result: list[dict[str, str]] = []
    for turn in values:
        if not isinstance(turn, Mapping):
            raise ValueError("each turn must be an object")
        role = _text(turn.get("role"), "turn.role").lower()
        content = _text(turn.get("content"), "turn.content")
        if role not in {"user", "assistant", "system"}:
            raise ValueError("turn.role must be user, assistant, or system")
        result.append({"role": role, "content": content})
    return tuple(result)


def _unique_texts(value: Any, field: str) -> tuple[str, ...]:
    values = tuple(_text(item, field) for item in _sequence(value, field))
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must contain unique values")
    return values


@dataclass(frozen=True)
class MABSession:
    session_id: str
    source_sequence: int
    timestamp: str
    turns: tuple[dict[str, str], ...]
    source_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        if isinstance(self.source_sequence, bool) or not isinstance(self.source_sequence, int):
            raise ValueError("source_sequence must be an integer")
        if self.source_sequence < 0:
            raise ValueError("source_sequence must be non-negative")
        object.__setattr__(self, "timestamp", _text(self.timestamp, "timestamp"))
        object.__setattr__(self, "turns", _turns(self.turns))
        source = _text(self.source_sha256, "source_sha256").lower()
        if len(source) != 64 or any(char not in "0123456789abcdef" for char in source):
            raise ValueError("source_sha256 must be a SHA-256 hex digest")
        object.__setattr__(self, "source_sha256", source)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, source_sequence: int | None = None) -> "MABSession":
        if not isinstance(value, Mapping):
            raise ValueError("session must be an object")
        session_id = value.get("session_id", value.get("id"))
        timestamp = value.get("timestamp", value.get("date", value.get("session_date")))
        turns = value.get("turns", value.get("messages", value.get("dialogue")))
        sequence = value.get("source_sequence", value.get("sequence", source_sequence))
        if sequence is None:
            raise ValueError("session source_sequence is required")
        raw = {"session_id": session_id, "source_sequence": sequence, "timestamp": timestamp, "turns": turns}
        digest = value.get("source_sha256") or canonical_sha256(raw)
        return cls(
            session_id=_text(session_id, "session_id"),
            source_sequence=sequence,
            timestamp=_text(timestamp, "timestamp"),
            turns=_turns(turns),
            source_sha256=digest,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_sequence": self.source_sequence,
            "timestamp": self.timestamp,
            "turns": [dict(turn) for turn in self.turns],
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class MABQA:
    qa_pair_id: str
    question_id: str
    question: str
    reference_answers: tuple[str, ...]
    question_date: str
    question_type: str
    gold_session_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "qa_pair_id", _text(self.qa_pair_id, "qa_pair_id"))
        object.__setattr__(self, "question_id", _text(self.question_id, "question_id"))
        object.__setattr__(self, "question", _text(self.question, "question"))
        answers = _unique_texts(self.reference_answers, "reference_answers")
        if not answers:
            raise ValueError("reference_answers must not be empty")
        object.__setattr__(self, "reference_answers", answers)
        object.__setattr__(self, "question_date", _text(self.question_date, "question_date"))
        object.__setattr__(self, "question_type", _text(self.question_type, "question_type"))
        object.__setattr__(self, "gold_session_ids", _unique_texts(self.gold_session_ids, "gold_session_ids"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int = 0) -> "MABQA":
        if not isinstance(value, Mapping):
            raise ValueError("QA item must be an object")
        question_id = value.get("question_id", value.get("id", f"q-{index:04d}"))
        pair_id = value.get("qa_pair_id", value.get("pair_id", question_id))
        answers = value.get("reference_answers", value.get("answers", value.get("answer")))
        if isinstance(answers, str):
            answers = [answers]
        gold = value.get("gold_session_ids", value.get("gold_sessions", value.get("answer_session_ids")))
        question = value.get("question")
        date = value.get("question_date", value.get("date", value.get("timestamp")))
        qtype = value.get("question_type", value.get("type", "unknown"))
        return cls(pair_id, question_id, question, answers, date, qtype, gold)

    def public_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "question_date": self.question_date,
        }

    def private_dict(self) -> dict[str, Any]:
        return {
            "qa_pair_id": self.qa_pair_id,
            "question_id": self.question_id,
            "question": self.question,
            "reference_answers": list(self.reference_answers),
            "question_date": self.question_date,
            "question_type": self.question_type,
            "gold_session_ids": list(self.gold_session_ids),
        }


@dataclass(frozen=True)
class MABContext:
    context_id: str
    sessions: tuple[MABSession, ...]
    qa_items: tuple[MABQA, ...]
    context_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_id", _text(self.context_id, "context_id"))
        sessions = tuple(self.sessions)
        qas = tuple(self.qa_items)
        if not sessions or any(not isinstance(item, MABSession) for item in sessions):
            raise ValueError("sessions must contain MABSession values")
        if not qas or any(not isinstance(item, MABQA) for item in qas):
            raise ValueError("qa_items must contain MABQA values")
        if len({item.session_id for item in sessions}) != len(sessions):
            raise ValueError("session IDs must be unique within a context")
        if len({item.source_sequence for item in sessions}) != len(sessions):
            raise ValueError("source_sequence must be unique within a context")
        if len({item.qa_pair_id for item in qas}) != len(qas):
            raise ValueError("qa_pair_id must be unique within a context")
        known = {item.session_id for item in sessions}
        if any(not set(item.gold_session_ids).issubset(known) for item in qas):
            raise ValueError("QA gold sessions must belong to the context")
        object.__setattr__(self, "sessions", tuple(sorted(sessions, key=lambda item: item.source_sequence)))
        object.__setattr__(self, "qa_items", qas)
        digest = _text(self.context_sha256, "context_sha256").lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("context_sha256 must be a SHA-256 hex digest")
        object.__setattr__(self, "context_sha256", digest)

    @classmethod
    def create(cls, context_id: str, sessions: tuple[MABSession, ...], qa_items: tuple[MABQA, ...]) -> "MABContext":
        body = {
            "context_id": context_id,
            "sessions": [item.public_dict() for item in sessions],
            "qa_items": [item.private_dict() for item in qa_items],
        }
        return cls(context_id, sessions, qa_items, canonical_sha256(body))

    def public_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "context_sha256": self.context_sha256,
            "sessions": [item.public_dict() for item in self.sessions],
            "qa_items": [item.public_dict() for item in self.qa_items],
        }

    def private_labels(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.private_dict() for item in self.qa_items)


@dataclass(frozen=True)
class PublicContext:
    """Explicit runtime-only projection; no gold fields are representable."""

    context_id: str
    context_sha256: str
    sessions: tuple[dict[str, Any], ...]
    qa_items: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "context_sha256": self.context_sha256,
            "sessions": [dict(item) for item in self.sessions],
            "qa_items": [dict(item) for item in self.qa_items],
        }


@dataclass(frozen=True)
class PrivateQALabels:
    qa_pair_id: str
    question_type: str
    reference_answers: tuple[str, ...]
    gold_session_ids: tuple[str, ...]


def assert_gold_blind(value: Any) -> None:
    """Raise if any forbidden benchmark label is present recursively."""

    def visit(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if str(key).casefold() in _GOLD_KEYS:
                    raise ValueError(f"GOLD_LEAK_DETECTED:{path}.{key}")
                visit(child, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    visit(value, "$" )


__all__ = [
    "FAILURE_TAXONOMY",
    "MABContext",
    "MABQA",
    "MABSession",
    "PrivateQALabels",
    "PublicContext",
    "assert_gold_blind",
    "canonical_sha256",
]
