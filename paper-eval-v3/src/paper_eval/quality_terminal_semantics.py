"""Auditable Judge terminal states for LongMemEval-style QA aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class QualityTerminalError(RuntimeError):
    """A service failure or internally inconsistent Judge result occurred."""


@dataclass(frozen=True)
class JudgeOutcome:
    """One terminal Judge result with explicit denominator membership."""

    status: str
    included: bool
    correct: bool | None


def classify_judge_artifact(value: Mapping[str, Any]) -> JudgeOutcome:
    """Classify SUCCESS only; INVALID_OUTPUT is never an incorrect answer."""

    if not isinstance(value, Mapping):
        raise QualityTerminalError("Judge artifact is invalid")
    status = value.get("status")
    label = value.get("label")
    parse_status = value.get("parse_status")
    if status == "SERVICE_ERROR":
        raise QualityTerminalError("Judge service error cannot be scored")
    if status == "INVALID_OUTPUT":
        return JudgeOutcome(
            status="INVALID_OUTPUT",
            included=False,
            correct=None,
        )
    if status != "SUCCESS" or type(label) is not bool:
        raise QualityTerminalError("Judge terminal status is invalid")
    expected_parse = "YES" if label else "NO"
    if parse_status != expected_parse:
        raise QualityTerminalError("Judge SUCCESS result is inconsistent")
    if value.get("retry_count") != 0 or value.get("error_class") is not None:
        raise QualityTerminalError("Judge SUCCESS metadata is inconsistent")
    return JudgeOutcome(status="SUCCESS", included=True, correct=label)


def aggregate_judge_outcomes(
    outcomes: Sequence[JudgeOutcome],
) -> dict[str, int | float | bool | None]:
    """Aggregate without silently treating invalid output as a wrong answer."""

    values = tuple(outcomes)
    if any(not isinstance(value, JudgeOutcome) for value in values):
        raise QualityTerminalError("Judge outcomes are invalid")
    valid = [value for value in values if value.included]
    correct = sum(1 for value in valid if value.correct is True)
    incorrect = sum(1 for value in valid if value.correct is False)
    invalid = len(values) - len(valid)
    denominator = len(valid)
    return {
        "total_attempted": len(values),
        "valid_denominator": denominator,
        "correct": correct,
        "incorrect": incorrect,
        "invalid": invalid,
        "qa_accuracy": correct / denominator if denominator else None,
        "headline_eligible": invalid == 0 and denominator > 0,
    }


__all__ = [
    "JudgeOutcome",
    "QualityTerminalError",
    "aggregate_judge_outcomes",
    "classify_judge_artifact",
]

