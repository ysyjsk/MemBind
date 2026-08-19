"""Small immutable contracts and the public/private information boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

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
    """Hash JSON-compatible data with a stable representation."""

    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence")
    return tuple(value)


def _turns(value: Any, field: str = "turns") -> tuple[dict[str, str], ...]:
    values = _sequence(value, field)
    if not values:
        raise ValueError(f"{field} must not be empty")
    result: list[dict[str, str]] = []
    for turn in values:
        if not isinstance(turn, Mapping):
            raise TypeError(f"{field} must contain objects")
        role = _text(turn.get("role"), f"{field}.role").lower()
        content = _text(turn.get("content"), f"{field}.content")
        if role not in {"user", "assistant"}:
            raise ValueError("MAB/Quality-v1 turns must use user or assistant roles")
        result.append({"role": role, "content": content})
    return tuple(result)


def _unique_texts(
    value: Any, field: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    values = tuple(_text(item, field) for item in _sequence(value, field))
    if not allow_empty and not values:
        raise ValueError(f"{field} must not be empty")
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
        if isinstance(self.source_sequence, bool) or not isinstance(
            self.source_sequence, int
        ):
            raise TypeError("source_sequence must be an integer")
        if self.source_sequence < 0:
            raise ValueError("source_sequence must be non-negative")
        object.__setattr__(self, "timestamp", _text(self.timestamp, "timestamp"))
        object.__setattr__(self, "turns", _turns(self.turns))
        digest = _text(self.source_sha256, "source_sha256").lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("source_sha256 must be a SHA-256 digest")
        object.__setattr__(self, "source_sha256", digest)

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
        object.__setattr__(
            self,
            "reference_answers",
            _unique_texts(self.reference_answers, "reference_answers"),
        )
        object.__setattr__(
            self, "question_date", _text(self.question_date, "question_date")
        )
        object.__setattr__(
            self, "question_type", _text(self.question_type, "question_type")
        )
        object.__setattr__(
            self,
            "gold_session_ids",
            _unique_texts(self.gold_session_ids, "gold_session_ids"),
        )

    def public_dict(self) -> dict[str, str]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "question_date": self.question_date,
        }

    def private_labels(self) -> PrivateQALabels:
        return PrivateQALabels(
            qa_pair_id=self.qa_pair_id,
            question_type=self.question_type,
            reference_answers=self.reference_answers,
            gold_session_ids=self.gold_session_ids,
        )


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
        if len({item.question_id for item in qas}) != len(qas):
            raise ValueError("question_id must be unique within a context")
        known = {item.session_id for item in sessions}
        if any(not set(item.gold_session_ids).issubset(known) for item in qas):
            raise ValueError("gold session IDs must belong to this context")
        object.__setattr__(
            self,
            "sessions",
            tuple(sorted(sessions, key=lambda item: item.source_sequence)),
        )
        object.__setattr__(self, "qa_items", qas)
        digest = _text(self.context_sha256, "context_sha256").lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("context_sha256 must be a SHA-256 digest")
        object.__setattr__(self, "context_sha256", digest)

    @classmethod
    def create(
        cls, context_id: str, sessions: Sequence[MABSession], qa_items: Sequence[MABQA]
    ) -> MABContext:
        body = {
            "context_id": context_id,
            "sessions": [item.public_dict() for item in sessions],
            "qa_items": [item.public_dict() for item in qa_items],
        }
        return cls(context_id, tuple(sessions), tuple(qa_items), canonical_sha256(body))

    def public_context(self) -> PublicContext:
        projection = PublicContext(
            context_id=self.context_id,
            context_sha256=self.context_sha256,
            sessions=tuple(item.public_dict() for item in self.sessions),
            qa_items=tuple(item.public_dict() for item in self.qa_items),
        )
        assert_gold_blind(projection.as_dict())
        return projection


@dataclass(frozen=True)
class PublicContext:
    """The only context representation allowed into construction/runtime code."""

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

    def question(self, qa: MABQA) -> dict[str, str]:
        if qa.question_id not in {item["question_id"] for item in self.qa_items}:
            # The check is intentionally conservative; callers should use the
            # positionally aligned public QA object from the context.
            raise ValueError("QA is not part of this public context")
        return qa.public_dict()


@dataclass(frozen=True)
class PrivateQALabels:
    qa_pair_id: str
    question_type: str
    reference_answers: tuple[str, ...]
    gold_session_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "qa_pair_id": self.qa_pair_id,
            "question_type": self.question_type,
            "reference_answers": list(self.reference_answers),
            "gold_session_ids": list(self.gold_session_ids),
        }


def assert_gold_blind(value: Any) -> None:
    """Raise on benchmark labels in any nested mapping/list payload."""

    def visit(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if str(key).casefold() in _GOLD_KEYS:
                    raise ValueError(f"GOLD_LEAK_DETECTED:{path}.{key}")
                visit(child, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    visit(value, "$")


__all__ = [
    "FAILURE_TAXONOMY",
    "MABQA",
    "MABContext",
    "MABSession",
    "PrivateQALabels",
    "PublicContext",
    "assert_gold_blind",
    "canonical_sha256",
]
