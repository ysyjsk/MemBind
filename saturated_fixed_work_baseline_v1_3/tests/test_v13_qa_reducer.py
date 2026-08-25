from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.qa_reducer import (
    QAReductionError,
    reduce_qa_rows,
)


def _rows() -> list[dict]:
    rows: list[dict] = []
    types = ["multi-session", "temporal-reasoning", "knowledge-update", "single-session-user", "single-session-assistant", "single-session-preference"]
    for context in ("ctx0", "ctx1"):
        for index in range(6):
            rows.append({
                "method": "V6",
                "context_id": context,
                "repeat": 0,
                "scope": "FULL",
                "qa_pair_id": f"{context}-q{index}",
                "question_type": types[index],
                "qa_identity_sha256": f"{index + 1:064x}",
                "status": "COMPLETE",
                "judge_valid": True,
                "correct": index % 2 == 0,
                "gold_mapping_status": "PARTIAL_GOLD_MAPPING" if index == 0 and context == "ctx1" else "COMPLETE",
                "evidence_recall": 0.7,
            })
    return rows


def test_qa_reducer_reports_pooled_and_equal_context_macro_and_nulls_partial_evidence() -> None:
    rows = _rows()
    rows[-1].update({"status": "INVALID", "judge_valid": False, "correct": None, "failure_class": "JUDGE_FAILED"})
    result = reduce_qa_rows(rows, expected_context_count=2, expected_qa_per_context=6, bootstrap_samples=100)
    assert result["qa_count"] == 12
    assert result["valid_judge_count"] == 11
    assert result["invalid_judge_count"] == 1
    assert result["equal_context_macro_accuracy"] is not None
    assert result["by_context"]["ctx1"]["partial_gold_evidence_rows"] == 1
    assert result["by_context"]["ctx1"]["evidence_recall"] is None
    assert result["uncertainty"]["cluster_count"] == 2


def test_qa_reducer_rejects_smoke_duplicates_and_wrong_inventory() -> None:
    rows = _rows()
    rows[0]["scope"] = "SMOKE"
    with pytest.raises(QAReductionError, match="SMOKE"):
        reduce_qa_rows(rows, expected_context_count=2, expected_qa_per_context=6)
    rows = _rows()
    rows.append(dict(rows[0]))
    with pytest.raises(QAReductionError, match="duplicate"):
        reduce_qa_rows(rows, expected_context_count=2, expected_qa_per_context=6)
    with pytest.raises(QAReductionError, match="inventory"):
        reduce_qa_rows(rows[:-2], expected_context_count=2, expected_qa_per_context=6)
