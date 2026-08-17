"""TDD contracts for the isolated read-only QA decomposition overlay."""

from __future__ import annotations

from copy import deepcopy

import pytest

from paper_eval.qa_decomposition import (
    build_final_artifacts,
    classify_reader_judge,
    make_stage,
    select_variant_sessions,
    summarize_results,
    verify_stage,
)


def _record() -> dict:
    return {
        "question_id": "07741c45",
        "question": "Where is the item now?",
        "question_date": "2023-06-01",
        "question_type": "knowledge-update",
        "answer": "in the closet",
        "answer_session_ids": ["s3", "s1"],
        "haystack_session_ids": ["s1", "s2", "s3", "s4"],
        "haystack_dates": [
            "2023-01-01",
            "2023-02-01",
            "2023-03-01",
            "2023-04-01",
        ],
        "haystack_sessions": [
            [{"role": "user", "content": "old", "has_answer": True}],
            [{"role": "assistant", "content": "noise"}],
            [{"role": "user", "content": "new", "has_answer": True}],
            [{"role": "assistant", "content": "other"}],
        ],
    }


def _binding(variant: str = "top10") -> dict:
    return {
        "overlay_run_id": "qd-dev-20260817-001",
        "source_run_id": "nb-20260816-001",
        "history_id": "07741c45",
        "namespace_sha256": "1" * 64,
        "construction_result_sha256": "2" * 64,
        "variant": variant,
        "selected_session_ids_sha256": "3" * 64,
        "reader_config_sha256": "4" * 64,
        "judge_config_sha256": "5" * 64,
    }


def test_variant_selection_keeps_top10_and_gold_only_distinct() -> None:
    record = _record()
    top = select_variant_sessions(
        record=record,
        variant="top10",
        retrieved_session_ids=("s4", "s2", "s3"),
        top_k=3,
    )
    oracle = select_variant_sessions(
        record=record,
        variant="gold_only",
        retrieved_session_ids=("s4", "s2", "s3"),
        top_k=3,
    )

    assert top.selected_session_ids == ("s4", "s2", "s3")
    assert oracle.selected_session_ids == ("s3", "s1")
    assert [value.session_id for value in top.sessions] == ["s2", "s3", "s4"]
    assert [value.session_id for value in oracle.sessions] == ["s1", "s3"]
    assert all("has_answer" not in turn for item in oracle.sessions for turn in item.turns)


@pytest.mark.parametrize("variant", ["unknown", "TOP10", ""])
def test_variant_selection_rejects_unfrozen_variant(variant: str) -> None:
    with pytest.raises(ValueError, match="variant"):
        select_variant_sessions(
            record=_record(),
            variant=variant,
            retrieved_session_ids=("s1", "s2", "s3"),
            top_k=3,
        )


def test_stage_round_trip_rejects_tampering_and_binding_drift() -> None:
    stage = make_stage(
        stage="reader",
        binding=_binding(),
        result={"answer": "closet", "prompt": "private prompt"},
    )
    assert verify_stage(stage, stage="reader", binding=_binding())["result"] == {
        "answer": "closet",
        "prompt": "private prompt",
    }

    tampered = deepcopy(stage)
    tampered["result"]["answer"] = "garage"
    with pytest.raises(ValueError, match="hash"):
        verify_stage(tampered, stage="reader", binding=_binding())

    drifted = _binding("gold_only")
    with pytest.raises(ValueError, match="binding"):
        verify_stage(stage, stage="reader", binding=drifted)


def test_final_artifacts_keep_raw_material_private() -> None:
    private, public = build_final_artifacts(
        binding=_binding(),
        question="Where is it?",
        reference_answer="closet",
        selected_session_ids=("s1", "s3"),
        reader_result={
            "prompt": "raw prompt",
            "answer": "It is in the closet.",
            "prompt_tokens": 20,
            "completion_tokens": 7,
        },
        judge_result={"label": True, "raw_output": "yes", "status": "SUCCESS"},
    )

    assert private["question"] == "Where is it?"
    assert private["reader_result"]["answer"] == "It is in the closet."
    serialized_public = repr(public)
    for secret in ("Where is it?", "closet", "raw prompt", "yes", "s1", "s3"):
        assert secret not in serialized_public
    assert public["qa_accuracy"] == 1.0
    assert public["private_payload_sha256"] == private["payload_sha256"]


@pytest.mark.parametrize(
    ("human_correct", "judge_label", "expected"),
    [
        (True, True, "READER_CORRECT_JUDGE_YES"),
        (True, False, "READER_CORRECT_JUDGE_FALSE_NEGATIVE"),
        (False, False, "READER_WRONG_JUDGE_NO"),
        (False, True, "READER_WRONG_JUDGE_FALSE_POSITIVE"),
    ],
)
def test_reader_judge_classification(
    human_correct: bool, judge_label: bool, expected: str
) -> None:
    assert classify_reader_judge(human_correct, judge_label) == expected


def test_summary_separates_top10_from_gold_only() -> None:
    rows = []
    for history_id, top, oracle in (
        ("07741c45", 0.0, 1.0),
        ("b6019101", 0.0, 1.0),
        ("6071bd76", 1.0, 1.0),
        ("a2f3aa27", 0.0, 0.0),
    ):
        for variant, score in (("top10", top), ("gold_only", oracle)):
            rows.append(
                {
                    "history_id": history_id,
                    "variant": variant,
                    "qa_accuracy": score,
                    "reader_prompt_tokens": 100,
                    "payload_sha256": "a" * 64,
                }
            )

    summary = summarize_results(rows)

    assert summary["top10"]["qa_accuracy_macro"] == 0.25
    assert summary["gold_only"]["qa_accuracy_macro"] == 0.75
    assert summary["gold_only"]["reader_prompt_tokens_total"] == 400
    assert summary["oracle_gain"] == 0.5
