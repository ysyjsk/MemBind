from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, finalize_envelope, payload_sha256, sha256_file
from paper_eval.s2_diagnosis import finalize_s2_near_zero_diagnosis


def _inputs(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    reference = tmp_path / "U0_REFERENCE_SANITY.json"
    reference_artifact = finalize_envelope(
        payload={
            "stage": "S2",
            "method": "U0",
            "history_id": "07741c45",
            "namespace": "pev3-s1-20260814-001",
            "status": "PIPELINE_ANOMALY_NEAR_ZERO",
            "near_zero_stop_triggered": True,
            "evidence_recall_at_10": 0.0,
            "qa_accuracy": 0.0,
            "retrieval_result_count": 10,
            "gold_session_count": 2,
            "reader_status": "SUCCESS",
            "judge_status": "SUCCESS",
        },
        protocol_version="paper-eval-v3",
        git_commit="deadbeef",
        run_id="s2-live-test",
    )
    atomic_write_json(reference, reference_artifact)
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint_body = {
        "run_id": "s2-live-test",
        "history_id": "07741c45",
        "namespace": "pev3-s1-20260814-001",
        "status": "completed",
        "completed_stages": ["retrieval", "reader", "judge"],
        "result_sha256": sha256_file(reference),
    }
    checkpoint_body["payload_sha256"] = payload_sha256(checkpoint_body)
    atomic_write_json(checkpoint, checkpoint_body)
    metrics: dict[str, object] = {
        "expected_episode_count": 49,
        "namespace_episode_count": 49,
        "namespace_entity_count": 245,
        "namespace_fact_count": 183,
        "gold_session_count": 2,
        "gold_episode_match_count": 2,
        "gold_episode_source_sequences": [2, 31],
        "gold_episode_mentions": [9, 1],
        "gold_episode_entity_edge_counts": [0, 0],
        "gold_attributed_fact_count": 0,
        "search_surface": "EntityEdge",
        "model_request_count": 0,
        "database_mutation_count": 0,
    }
    return reference, checkpoint, metrics


def test_s2_diagnosis_seals_root_cause_and_terminal_stage_ledger(tmp_path: Path) -> None:
    reference, checkpoint, metrics = _inputs(tmp_path)
    diagnosis_path = tmp_path / "S2_NEAR_ZERO_ROOT_CAUSE.json"
    ledger_path = tmp_path / "STAGE_STATUS.json"

    diagnosis, ledger = finalize_s2_near_zero_diagnosis(
        diagnosis_path=diagnosis_path,
        stage_status_path=ledger_path,
        reference_path=reference,
        checkpoint_path=checkpoint,
        metrics=metrics,
        git_commit="deadbeef",
        run_id="s2-diagnosis-test",
    )

    assert diagnosis["payload"]["classification"] == (
        "GOLD_EPISODES_HAVE_NO_ENTITYEDGE_PROVENANCE"
    )
    assert diagnosis["payload"]["whole_graph_quality_conclusion"] == "NOT_INFERRED"
    assert diagnosis["payload"]["official_session_recall_computed"] is False
    assert diagnosis["payload"]["service_failure"] is False
    assert diagnosis["payload"]["next_stage_authorized"] is False
    assert diagnosis["payload"]["evidence"]["gold_episode_entity_edge_counts"] == [0, 0]
    assert ledger["payload"]["current_stage"] == "S2"
    assert ledger["payload"]["status"] == "STOPPED_ROOT_CAUSE_IDENTIFIED"
    assert ledger["payload"]["next_authorized_stage"] is None
    assert ledger["payload"]["diagnosis_sha256"] == sha256_file(diagnosis_path)
    assert json.loads(diagnosis_path.read_text())["payload_sha256"] == payload_sha256(
        diagnosis["payload"]
    )


@pytest.mark.parametrize(
    ("reference_mutation", "metric_mutation"),
    [
        ({"status": "PASS", "near_zero_stop_triggered": False}, {}),
        ({}, {"gold_episode_match_count": 1}),
        ({}, {"gold_episode_entity_edge_counts": [0, 1]}),
        ({}, {"model_request_count": 1}),
        ({}, {"database_mutation_count": 1}),
    ],
)
def test_s2_diagnosis_fails_closed_on_unsupported_evidence(
    tmp_path: Path,
    reference_mutation: dict[str, object],
    metric_mutation: dict[str, object],
) -> None:
    reference, checkpoint, metrics = _inputs(tmp_path)
    artifact = json.loads(reference.read_text())
    artifact["payload"].update(reference_mutation)
    artifact["payload_sha256"] = payload_sha256(artifact["payload"])
    atomic_write_json(reference, artifact)
    checkpoint_body = json.loads(checkpoint.read_text())
    checkpoint_body["result_sha256"] = sha256_file(reference)
    checkpoint_body.pop("payload_sha256")
    checkpoint_body["payload_sha256"] = payload_sha256(checkpoint_body)
    atomic_write_json(checkpoint, checkpoint_body)
    metrics.update(metric_mutation)

    with pytest.raises(ValueError):
        finalize_s2_near_zero_diagnosis(
            diagnosis_path=tmp_path / "diagnosis.json",
            stage_status_path=tmp_path / "status.json",
            reference_path=reference,
            checkpoint_path=checkpoint,
            metrics=metrics,
            git_commit="deadbeef",
            run_id="s2-diagnosis-test",
        )
    assert not (tmp_path / "diagnosis.json").exists()
    assert not (tmp_path / "status.json").exists()


def test_s2_diagnosis_never_overwrites_historical_outputs(tmp_path: Path) -> None:
    reference, checkpoint, metrics = _inputs(tmp_path)
    diagnosis_path = tmp_path / "diagnosis.json"
    ledger_path = tmp_path / "status.json"
    diagnosis_path.write_text("historical diagnosis\n", encoding="utf-8")
    ledger_path.write_text("historical ledger\n", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        finalize_s2_near_zero_diagnosis(
            diagnosis_path=diagnosis_path,
            stage_status_path=ledger_path,
            reference_path=reference,
            checkpoint_path=checkpoint,
            metrics=metrics,
            git_commit="deadbeef",
            run_id="s2-diagnosis-test",
        )
    assert diagnosis_path.read_text(encoding="utf-8") == "historical diagnosis\n"
    assert ledger_path.read_text(encoding="utf-8") == "historical ledger\n"
