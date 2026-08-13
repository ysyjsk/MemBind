"""Shared immutable schemas for benchmark-native evaluation adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


_HEX_DIGITS = frozenset("0123456789abcdef")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


class EvaluationStatus(str, Enum):
    """Terminal evaluator outcomes that must remain distinct in analysis."""

    SUCCESS = "SUCCESS"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    SERVICE_ERROR = "SERVICE_ERROR"


@dataclass(frozen=True)
class EvaluationItem:
    """One frozen system output presented to a benchmark-owned evaluator."""

    item_id: str
    benchmark: str
    question_id: str
    question_type: str
    question: str
    reference_answer: str
    hypothesis: str
    abstention: bool = False

    def __post_init__(self) -> None:
        for name in (
            "item_id",
            "benchmark",
            "question_id",
            "question_type",
            "question",
            "reference_answer",
            "hypothesis",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.abstention, bool):
            raise TypeError("abstention must be boolean")


@dataclass(frozen=True)
class EvaluationResult:
    """Auditable result without collapsing invalid/service states into false."""

    item_id: str
    benchmark: str
    scorer: str
    judge_model: str
    label: bool | None
    status: EvaluationStatus
    raw_output: str
    normalized_output: str
    parse_status: str
    retry_count: int
    error_class: str | None
    prompt_hash: str
    config_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, EvaluationStatus):
            raise TypeError("status must be EvaluationStatus")
        for name in ("item_id", "benchmark", "scorer", "judge_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("raw_output", "normalized_output", "parse_status"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a string")
        if not _is_sha256(self.prompt_hash) or not _is_sha256(self.config_hash):
            raise ValueError("prompt_hash and config_hash must be lowercase SHA256")
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dictionary")
        if (
            isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or self.retry_count < 0
        ):
            raise ValueError("retry_count must be a non-negative integer")
        if self.status is EvaluationStatus.SUCCESS:
            if not isinstance(self.label, bool):
                raise ValueError("successful results require a boolean label")
            if self.parse_status not in {"YES", "NO"} or self.error_class is not None:
                raise ValueError("successful result parse/error state is inconsistent")
        elif self.status is EvaluationStatus.INVALID_OUTPUT:
            # The bool remains the official LongMemEval headline parser output.
            # Downstream aggregation must select SUCCESS before reading it.
            if not isinstance(self.label, bool):
                raise ValueError("invalid outputs retain the official boolean label")
            if self.parse_status != "INVALID" or self.error_class is not None:
                raise ValueError("invalid-output parse/error state is inconsistent")
        else:
            if self.label is not None:
                raise ValueError("service-error results cannot carry a label")
            if self.parse_status != "NOT_RUN":
                raise ValueError("service-error parser must not run")
            if not isinstance(self.error_class, str) or not self.error_class:
                raise ValueError("service-error results require an error class")


@dataclass(frozen=True)
class JudgeQualificationRecord:
    """Future per-item Qwen/human comparison; no qualification is run here."""

    question_id: str
    candidate_answer_id: str
    qwen_label: bool
    human_label: bool
    agreement: bool

    def __post_init__(self) -> None:
        for name in ("question_id", "candidate_answer_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("qwen_label", "human_label", "agreement"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be boolean")
        if self.agreement is not (self.qwen_label is self.human_label):
            raise ValueError("agreement must match qwen/human label equality")
