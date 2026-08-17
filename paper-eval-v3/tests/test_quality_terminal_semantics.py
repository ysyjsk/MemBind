"""RED-first aggregation rules for Reader and Judge terminal states."""

from __future__ import annotations

import pytest

from paper_eval.quality_terminal_semantics import (
    QualityTerminalError,
    aggregate_judge_outcomes,
    classify_judge_artifact,
)


def _judge(status: str, label: bool, parse_status: str) -> dict[str, object]:
    return {
        "status": status,
        "label": label,
        "parse_status": parse_status,
        "retry_count": 0,
        "error_class": None,
    }


def test_success_yes_and_no_are_the_only_accuracy_denominator_members() -> None:
    yes = classify_judge_artifact(_judge("SUCCESS", True, "YES"))
    no = classify_judge_artifact(_judge("SUCCESS", False, "NO"))
    invalid = classify_judge_artifact(
        _judge("INVALID_OUTPUT", False, "INVALID_OUTPUT")
    )

    summary = aggregate_judge_outcomes((yes, no, invalid))
    assert summary == {
        "total_attempted": 3,
        "valid_denominator": 2,
        "correct": 1,
        "incorrect": 1,
        "invalid": 1,
        "qa_accuracy": 0.5,
        "headline_eligible": False,
    }


def test_service_error_and_inconsistent_success_fail_closed() -> None:
    with pytest.raises(QualityTerminalError, match="service"):
        classify_judge_artifact(
            {
                "status": "SERVICE_ERROR",
                "label": False,
                "parse_status": "SERVICE_ERROR",
                "retry_count": 0,
                "error_class": "TimeoutError",
            }
        )
    with pytest.raises(QualityTerminalError, match="inconsistent"):
        classify_judge_artifact(_judge("SUCCESS", False, "YES"))

