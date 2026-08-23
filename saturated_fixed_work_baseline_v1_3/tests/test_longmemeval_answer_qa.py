from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.longmemeval_answer_qa import (
    LongMemEvalAnswerQAError,
    answer_surface_diagnostic,
    evaluate_official_answer,
    paired_answer_outcome,
)


def test_surface_diagnostic_is_not_semantic_authority() -> None:
    result = answer_surface_diagnostic(
        "in a shoe rack in my closet", "They are in a shoe rack in my closet."
    )
    assert result["status"] == "LEXICAL_MATCH"
    assert result["semantic_authority"] == "NONE"
    assert result["official_judge_required"] is True


def test_numeric_surface_diagnostic_uses_token_boundaries() -> None:
    result = answer_surface_diagnostic(5, "The graph mentions 50 layers.")
    assert result["status"] == "LEXICAL_NO_MATCH"


def test_missing_judge_is_unscored_not_incorrect() -> None:
    result = evaluate_official_answer(
        expected_answer="1300",
        reader_answer="The current count is 1300.",
        judge=None,
    )
    assert result["status"] == "UNSCORED_NO_OFFICIAL_JUDGE"
    assert result["correct"] is None
    assert result["semantic_authority"] == "NONE"


def test_official_judge_controls_answer_score() -> None:
    result = evaluate_official_answer(
        expected_answer="in a shoe rack in my closet",
        reader_answer="The sneakers are stored in the closet's shoe rack.",
        judge={"status": "SUCCESS", "label": True},
    )
    assert result["status"] == "PASS"
    assert result["semantic_authority"] == "OFFICIAL_LONGMEMEVAL_JUDGE"


def test_invalid_or_service_judge_is_explicitly_unscored() -> None:
    for status in ("INVALID_OUTPUT", "SERVICE_ERROR"):
        result = evaluate_official_answer(
            expected_answer="5", reader_answer="5", judge={"status": status}
        )
        assert result["status"].startswith("UNSCORED_")
        assert result["correct"] is None


def test_paired_gate_requires_b0_pass_and_keeps_state_diagnostic_separate() -> None:
    result = paired_answer_outcome(
        {"status": "PASS", "semantic_authority": "OFFICIAL_LONGMEMEVAL_JUDGE"},
        {"status": "FAIL", "semantic_authority": "OFFICIAL_LONGMEMEVAL_JUDGE"},
    )
    assert result["concrete_b1_answer_failure"] is True
    assert result["answer_decision_authority"] == "OFFICIAL_LONGMEMEVAL_JUDGE"
    assert result["state_diagnostic_is_separate"] is True


def test_state_failure_does_not_override_official_answer_pass() -> None:
    answer = evaluate_official_answer(
        expected_answer="5",
        reader_answer="I watched five MCU films.",
        judge={"status": "SUCCESS", "label": True},
    )
    state = {"status": "FAIL"}
    assert answer["status"] == "PASS"
    assert state["status"] == "FAIL"


def test_invalid_reader_answer_fails_closed() -> None:
    with pytest.raises(LongMemEvalAnswerQAError, match="READER_ANSWER_INVALID"):
        answer_surface_diagnostic("5", "")
