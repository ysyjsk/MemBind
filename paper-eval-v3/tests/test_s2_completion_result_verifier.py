from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s2_completion_result_verifier import (
    CompletionResultPaths,
    CompletionResultVerificationError,
    verify_s2_completion_result,
)


ROOT = Path(__file__).parents[1]
NATIVE = ROOT / "artifacts/paper_eval/native"
RUN_ID = "s2-completion-20260814-001"


def _paths(root: Path = NATIVE) -> CompletionResultPaths:
    run_dir = root / "runs" / RUN_ID
    return CompletionResultPaths(
        authorization=root / "S2_COMPLETION_AUTHORIZATION.json",
        consumption=run_dir / "S2_COMPLETION_AUTHORIZATION_CONSUMPTION.json",
        events=run_dir / "events.jsonl",
        checkpoint=run_dir / "checkpoint.json",
        result=run_dir / "S2_COMPLETION_RESULT.json",
        failure=run_dir / "S2_COMPLETION_FAILURE.json",
    )


def _copy(tmp_path: Path) -> CompletionResultPaths:
    target = tmp_path / "native"
    paths = _paths(target)
    paths.result.parent.mkdir(parents=True)
    source = _paths()
    for old, new in (
        (source.authorization, paths.authorization),
        (source.consumption, paths.consumption),
        (source.events, paths.events),
        (source.checkpoint, paths.checkpoint),
        (source.result, paths.result),
    ):
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old, new)
    return paths


def test_verifies_real_review_required_result_chain() -> None:
    outcome = verify_s2_completion_result(_paths())

    assert outcome.run_id == RUN_ID
    assert outcome.status == "REVIEW_REQUIRED"
    assert outcome.evidence_recall_at_10 == 1.0
    assert outcome.qa_accuracy == 0.0
    assert outcome.gold_ranks == (2, 1)
    assert outcome.reader_prompt_tokens == 27814
    assert outcome.reader_truncation_count == 0
    assert outcome.judge_parse_status == "NO"
    assert outcome.result_mergeable is False
    assert outcome.s3_authorized is False


def test_rejects_unsealed_result_tamper(tmp_path: Path) -> None:
    paths = _copy(tmp_path)
    value = json.loads(paths.result.read_text())
    value["payload"]["result"]["qa_accuracy"] = 1.0
    paths.result.write_text(json.dumps(value) + "\n")

    with pytest.raises(CompletionResultVerificationError, match="seal"):
        verify_s2_completion_result(paths)


def test_rejects_resealed_metric_or_s3_drift(tmp_path: Path) -> None:
    paths = _copy(tmp_path)
    value = json.loads(paths.result.read_text())
    value["payload"]["result"]["qa_accuracy"] = 1.0
    value["payload_sha256"] = payload_sha256(value["payload"])
    paths.result.write_text(json.dumps(value) + "\n")
    with pytest.raises(CompletionResultVerificationError, match="metric|status"):
        verify_s2_completion_result(paths)

    paths = _copy(tmp_path / "second")
    value = json.loads(paths.result.read_text())
    value["payload"]["s3_authorized"] = True
    value["payload_sha256"] = payload_sha256(value["payload"])
    paths.result.write_text(json.dumps(value) + "\n")
    with pytest.raises(CompletionResultVerificationError, match="S3"):
        verify_s2_completion_result(paths)


def test_rejects_event_or_checkpoint_tamper(tmp_path: Path) -> None:
    paths = _copy(tmp_path)
    lines = paths.events.read_text().splitlines()
    event = json.loads(lines[1])
    event["evidence"]["retrieved_session_count"] = 9
    lines[1] = json.dumps(event)
    paths.events.write_text("\n".join(lines) + "\n")
    with pytest.raises(CompletionResultVerificationError, match="event|hash"):
        verify_s2_completion_result(paths)

    paths = _copy(tmp_path / "second")
    checkpoint = json.loads(paths.checkpoint.read_text())
    checkpoint["status"] = "running"
    checkpoint["payload_sha256"] = payload_sha256(
        {key: value for key, value in checkpoint.items() if key != "payload_sha256"}
    )
    paths.checkpoint.write_text(json.dumps(checkpoint) + "\n")
    with pytest.raises(CompletionResultVerificationError, match="checkpoint|hash"):
        verify_s2_completion_result(paths)


def test_rejects_result_and_failure_coexistence(tmp_path: Path) -> None:
    paths = _copy(tmp_path)
    shutil.copyfile(paths.result, paths.failure)

    with pytest.raises(CompletionResultVerificationError, match="exactly one terminal"):
        verify_s2_completion_result(paths)
