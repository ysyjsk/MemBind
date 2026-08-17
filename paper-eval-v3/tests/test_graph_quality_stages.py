"""RED-first recovery contracts for private graph-quality model stages."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from paper_eval.graph_quality_stages import (
    GraphQualityStageError,
    GraphQualityStageStore,
)
from paper_eval.temporal_fact_reader import TemporalFactReaderResult


def _binding() -> dict[str, object]:
    return {
        "overlay_run_id": "gq-dev-001",
        "method": "U0",
        "history_id": "07741c45",
        "namespace_sha256": "a" * 64,
        "construction_result_sha256": "b" * 64,
        "runtime_identity_sha256": "c" * 64,
        "retrieval_config_sha256": "d" * 64,
        "evidence_sha256": "e" * 64,
        "reader_config_sha256": "f" * 64,
        "question_sha256": hashlib.sha256(b"PRIVATE_QUESTION").hexdigest(),
        "question_date_sha256": hashlib.sha256(b"2025-03-01").hexdigest(),
        "reader_prompt_sha256": hashlib.sha256(
            b"PRIVATE_READER_PROMPT"
        ).hexdigest(),
    }


def _reader_result() -> TemporalFactReaderResult:
    return TemporalFactReaderResult(
        answer="PRIVATE_READER_ANSWER",
        prompt_for_test="PRIVATE_READER_PROMPT",
        prompt_tokens=120,
        completion_tokens=12,
        finish_reason="stop",
        model="qwen3-32b-fp8",
        config_sha256="f" * 64,
    )


def test_reader_stage_is_reused_by_the_next_attempt_without_resampling(
    tmp_path: Path,
) -> None:
    first = GraphQualityStageStore(tmp_path / "attempt-001")
    stage_sha = first.persist_reader(_binding(), _reader_result())

    second = GraphQualityStageStore(tmp_path / "attempt-002")
    restored = second.load_reader(_binding())

    assert restored is not None
    result, restored_sha = restored
    assert result == _reader_result()
    assert restored_sha == stage_sha
    assert (
        tmp_path
        / "attempt-001"
        / "runtime"
        / "private"
        / "reader_stage.json"
    ).is_file()


def test_reader_stage_identity_drift_fails_closed_instead_of_resampling(
    tmp_path: Path,
) -> None:
    first = GraphQualityStageStore(tmp_path / "attempt-001")
    first.persist_reader(_binding(), _reader_result())
    drifted = {**_binding(), "runtime_identity_sha256": "9" * 64}

    with pytest.raises(GraphQualityStageError, match="identity drift"):
        GraphQualityStageStore(tmp_path / "attempt-002").load_reader(drifted)


@pytest.mark.parametrize(
    "field",
    ("question_sha256", "question_date_sha256", "reader_prompt_sha256"),
)
def test_reader_stage_semantic_input_or_exact_prompt_drift_fails_closed(
    tmp_path: Path, field: str
) -> None:
    first = GraphQualityStageStore(tmp_path / "attempt-001")
    first.persist_reader(_binding(), _reader_result())

    with pytest.raises(GraphQualityStageError, match="identity drift"):
        GraphQualityStageStore(tmp_path / "attempt-002").load_reader(
            {**_binding(), field: "9" * 64}
        )


def test_reader_stage_rejects_binding_that_does_not_hash_the_exact_prompt(
    tmp_path: Path,
) -> None:
    drifted = {**_binding(), "reader_prompt_sha256": "9" * 64}

    with pytest.raises(GraphQualityStageError, match="Reader.*prompt"):
        GraphQualityStageStore(tmp_path / "attempt-001").persist_reader(
            drifted, _reader_result()
        )


def test_judge_stage_binds_the_reader_stage_and_retains_raw_output_privately(
    tmp_path: Path,
) -> None:
    store = GraphQualityStageStore(tmp_path / "attempt-001")
    reader_sha = store.persist_reader(_binding(), _reader_result())
    judge_binding = {
        **_binding(),
        "reader_stage_sha256": reader_sha,
        "judge_config_sha256": "1" * 64,
        "question_type_sha256": hashlib.sha256(
            b"knowledge-update"
        ).hexdigest(),
        "reference_answer_sha256": hashlib.sha256(
            b"PRIVATE_REFERENCE"
        ).hexdigest(),
        "reader_answer_sha256": hashlib.sha256(
            b"PRIVATE_READER_ANSWER"
        ).hexdigest(),
        "judge_prompt_sha256": "2" * 64,
    }
    judge_result = {
        "status": "SUCCESS",
        "label": True,
        "parse_status": "YES",
        "output_sha256": hashlib.sha256(
            b"PRIVATE_JUDGE_OUTPUT"
        ).hexdigest(),
        "raw_output": "PRIVATE_JUDGE_OUTPUT",
        "retry_count": 0,
        "error_class": None,
        "prompt_sha256": "2" * 64,
    }
    judge_sha = store.persist_judge(judge_binding, judge_result)

    restored = GraphQualityStageStore(tmp_path / "attempt-002").load_judge(
        judge_binding
    )

    assert restored is not None
    observed, observed_sha = restored
    assert observed == judge_result
    assert observed_sha == judge_sha


@pytest.mark.parametrize(
    "field",
    (
        "question_sha256",
        "question_type_sha256",
        "reference_answer_sha256",
        "reader_answer_sha256",
        "judge_prompt_sha256",
    ),
)
def test_judge_stage_semantic_input_or_exact_prompt_drift_fails_closed(
    tmp_path: Path, field: str
) -> None:
    store = GraphQualityStageStore(tmp_path / "attempt-001")
    reader_sha = store.persist_reader(_binding(), _reader_result())
    judge_binding = {
        **_binding(),
        "reader_stage_sha256": reader_sha,
        "judge_config_sha256": "1" * 64,
        "question_type_sha256": hashlib.sha256(
            b"knowledge-update"
        ).hexdigest(),
        "reference_answer_sha256": hashlib.sha256(
            b"PRIVATE_REFERENCE"
        ).hexdigest(),
        "reader_answer_sha256": hashlib.sha256(
            b"PRIVATE_READER_ANSWER"
        ).hexdigest(),
        "judge_prompt_sha256": "2" * 64,
    }
    judge_result = {
        "status": "SUCCESS",
        "label": True,
        "parse_status": "YES",
        "output_sha256": hashlib.sha256(
            b"PRIVATE_JUDGE_OUTPUT"
        ).hexdigest(),
        "raw_output": "PRIVATE_JUDGE_OUTPUT",
        "retry_count": 0,
        "error_class": None,
        "prompt_sha256": "2" * 64,
    }
    store.persist_judge(judge_binding, judge_result)

    with pytest.raises(GraphQualityStageError, match="identity drift"):
        GraphQualityStageStore(tmp_path / "attempt-002").load_judge(
            {**judge_binding, field: "9" * 64}
        )


def test_judge_stage_rejects_binding_that_does_not_hash_the_exact_prompt(
    tmp_path: Path,
) -> None:
    store = GraphQualityStageStore(tmp_path / "attempt-001")
    reader_sha = store.persist_reader(_binding(), _reader_result())
    binding = {
        **_binding(),
        "reader_stage_sha256": reader_sha,
        "judge_config_sha256": "1" * 64,
        "question_type_sha256": hashlib.sha256(
            b"knowledge-update"
        ).hexdigest(),
        "reference_answer_sha256": hashlib.sha256(
            b"PRIVATE_REFERENCE"
        ).hexdigest(),
        "reader_answer_sha256": hashlib.sha256(
            b"PRIVATE_READER_ANSWER"
        ).hexdigest(),
        "judge_prompt_sha256": "9" * 64,
    }
    result = {
        "status": "SUCCESS",
        "label": True,
        "parse_status": "YES",
        "output_sha256": hashlib.sha256(
            b"PRIVATE_JUDGE_OUTPUT"
        ).hexdigest(),
        "raw_output": "PRIVATE_JUDGE_OUTPUT",
        "retry_count": 0,
        "error_class": None,
        "prompt_sha256": "2" * 64,
    }

    with pytest.raises(GraphQualityStageError, match="Judge.*prompt"):
        store.persist_judge(binding, result)


def test_private_stage_and_atomic_bundle_temps_are_git_ignored() -> None:
    project = Path(__file__).resolve().parents[1]
    rules = (project / ".gitignore").read_text(encoding="utf-8")

    assert (
        "artifacts/paper_eval/graph_quality_overlay/runs/**/runtime/private/"
        in rules
    )
    assert (
        "artifacts/paper_eval/graph_quality_overlay/runs/**/"
        ".private_bundle.json.*.tmp"
        in rules
    )
