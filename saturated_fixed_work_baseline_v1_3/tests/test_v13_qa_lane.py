from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.qa_lane import (
    QALaneError,
    run_mab_qa_on_sealed_namespace,
)


def _seal() -> dict:
    return {
        "status": "CONSTRUCTION_SEALED",
        "context_id": "ctx0",
        "method": "B0",
        "repeat": 0,
        "namespace": "fresh-b0-ctx0-r0",
        "workload_hash": "w" * 64,
        "expected_episode_count": 2,
    }


def _manifest() -> list[dict]:
    return [
        {"context_id": "ctx0", "qa_pair_id": "q0", "question_id": "id0", "question_type": "multi-session", "qa_identity_sha256": "a" * 64},
        {"context_id": "ctx0", "qa_pair_id": "q1", "question_id": "id1", "question_type": "temporal-reasoning", "qa_identity_sha256": "b" * 64},
    ]


def test_qa_requires_construction_seal_and_preserves_state(tmp_path: Path) -> None:
    state = {"nodes": 2, "writes": 0}
    result = run_mab_qa_on_sealed_namespace(
        construction_seal=_seal(),
        qa_manifest=_manifest(),
        output_root=tmp_path / "qa",
        state_reader=lambda: dict(state),
        answer_fn=lambda row: {"judge_valid": True, "correct": row["qa_pair_id"] == "q0"},
    )
    assert result["quality_status"] == "PASS"
    assert result["completed_count"] == 2
    assert result["graph_state_before"] == result["graph_state_after"]
    assert (tmp_path / "qa" / "qa_results.jsonl").is_file()

    bad = _seal()
    bad["status"] = "CONSTRUCTION_COMPLETE"
    with pytest.raises(QALaneError, match="sealed"):
        run_mab_qa_on_sealed_namespace(
            construction_seal=bad,
            qa_manifest=_manifest(),
            output_root=tmp_path / "bad",
            state_reader=lambda: dict(state),
            answer_fn=lambda row: {},
        )


def test_qa_state_mutation_invalidates_only_quality(tmp_path: Path) -> None:
    state = {"nodes": 2}

    def mutating_answer(row: dict) -> dict:
        state["nodes"] += 1
        return {"judge_valid": True, "correct": True}

    result = run_mab_qa_on_sealed_namespace(
        construction_seal=_seal(),
        qa_manifest=_manifest()[:1],
        output_root=tmp_path / "mutated",
        state_reader=lambda: dict(state),
        answer_fn=mutating_answer,
    )
    assert result["quality_status"] == "INVALID"
    assert result["invalid_reason"] == "QA_PHASE_WRITE_VIOLATION"


def test_qa_resume_does_not_overwrite_existing_result_and_invalid_is_null(tmp_path: Path) -> None:
    output = tmp_path / "resume"
    output.mkdir()
    existing = {
        "context_id": "ctx0", "qa_pair_id": "q0", "question_id": "id0",
        "qa_identity_sha256": "a" * 64, "status": "COMPLETE", "judge_valid": True,
        "correct": True,
    }
    (output / "qa_results.jsonl").write_text(json.dumps(existing) + "\n", encoding="utf-8")
    calls: list[str] = []
    result = run_mab_qa_on_sealed_namespace(
        construction_seal=_seal(),
        qa_manifest=_manifest(),
        output_root=output,
        state_reader=lambda: {"nodes": 2},
        answer_fn=lambda row: calls.append(row["qa_pair_id"]) or {"judge_valid": False, "correct": None, "failure_class": "JUDGE_FAILED"},
    )
    assert calls == ["q1"]
    assert result["invalid_count"] == 1
    rows = [json.loads(line) for line in (output / "qa_results.jsonl").read_text().splitlines()]
    assert rows[0]["correct"] is True
    assert rows[1]["correct"] is None
