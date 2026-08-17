from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.baseline_suite_u0_reuse import (
    REQUIRED_U0_FILES,
    U0ReuseError,
    build_verified_u0_reuse_artifact,
    verify_u0_reuse_artifact,
)
from paper_eval.native_baseline_runner import (
    DEVELOPMENT_HISTORIES,
    build_native_baseline_plan,
    make_checkpoint,
    seal_history_result,
)


RUN_ID = "nb-20260816-099"
QUALITY_IDENTITY = {
    "baseline_id": "native-graphiti-u0-reader-v2",
    "reader_config_sha256": "a" * 64,
    "judge_config_sha256": "b" * 64,
}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _completed_run(tmp_path: Path, *, run_id: str = RUN_ID) -> Path:
    runs_root = tmp_path / "runs"
    plan = build_native_baseline_plan(run_id)
    for history in plan.histories:
        history_root = runs_root / run_id / history.history_id
        history_root.mkdir(parents=True)
        expected = [0, 1]
        checkpoint = make_checkpoint(
            run_id=run_id,
            history_id=history.history_id,
            namespace=history.namespace,
            expected_sequences=expected,
            completed_sequences=expected,
            status="completed",
        )
        result = seal_history_result(
            {
                "schema_version": "membind.paper-eval-v3.native-baseline-history.v1",
                "run_id": run_id,
                "history_id": history.history_id,
                "namespace": history.namespace,
                "method": "U0",
                "repeat_id": 0,
                "status": "completed",
                "quality_identity": QUALITY_IDENTITY,
                "quality": {
                    "status": "SUCCESS",
                    "reader": {"config_sha256": "a" * 64},
                },
                "aggregate": {
                    "episode_count": len(expected),
                    "metrics": {
                        "qa_accuracy": 0.5,
                        "evidence_recall_at_10": 1.0,
                    },
                },
                "final_namespace_observation": {
                    "episode_count": len(expected),
                    "episode_names_match_expected": True,
                },
            }
        )
        for filename in REQUIRED_U0_FILES:
            target = history_root / filename
            if filename == "checkpoint.json":
                _write_json(target, checkpoint)
            elif filename == "history_result.json":
                _write_json(target, result)
            else:
                target.write_text(
                    json.dumps(
                        {
                            "history_id": history.history_id,
                            "stream": filename.removesuffix(".jsonl"),
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
    return runs_root


def _resign_json(path: Path, **updates: object) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(updates)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = payload_sha256(value)
    _write_json(path, value)


def test_complete_fixed_four_run_builds_sanitized_hash_bound_reuse_artifact(
    tmp_path: Path,
) -> None:
    runs_root = _completed_run(tmp_path)

    artifact = build_verified_u0_reuse_artifact(
        native_runs_root=runs_root,
        native_run_id=RUN_ID,
    )

    assert artifact["status"] == "VERIFIED_RESULT_ARTIFACTS_ONLY"
    assert artifact["source_method"] == "U0"
    assert artifact["source_run_id"] == RUN_ID
    assert artifact["source_history_order"] == list(DEVELOPMENT_HISTORIES)
    assert artifact["namespace_reuse"] is False
    assert artifact["target_must_use_fresh_namespaces"] is True
    assert artifact["quality_identity"] == QUALITY_IDENTITY
    assert [row["history_id"] for row in artifact["histories"]] == list(
        DEVELOPMENT_HISTORIES
    )
    assert artifact["payload_sha256"] == payload_sha256(
        {key: value for key, value in artifact.items() if key != "payload_sha256"}
    )
    first = artifact["histories"][0]
    assert first["quality_identity"] == QUALITY_IDENTITY
    assert first["quality_metrics"] == {
        "qa_accuracy": 0.5,
        "evidence_recall_at_10": 1.0,
    }
    assert set(first["file_sha256"]) == set(REQUIRED_U0_FILES)
    assert first["file_sha256"]["spans.jsonl"] == sha256_file(
        runs_root / RUN_ID / DEVELOPMENT_HISTORIES[0] / "spans.jsonl"
    )
    serialized = json.dumps(artifact, sort_keys=True)
    for history in build_native_baseline_plan(RUN_ID).histories:
        assert history.namespace not in serialized
    assert verify_u0_reuse_artifact(
        artifact,
        native_runs_root=runs_root,
    ) == artifact


def test_partial_checkpoint_is_not_reusable(tmp_path: Path) -> None:
    runs_root = _completed_run(tmp_path)
    checkpoint_path = (
        runs_root / RUN_ID / DEVELOPMENT_HISTORIES[1] / "checkpoint.json"
    )
    _resign_json(
        checkpoint_path,
        status="running",
        completed_sequences=[0],
    )

    with pytest.raises(U0ReuseError, match="completed full prefix"):
        build_verified_u0_reuse_artifact(
            native_runs_root=runs_root,
            native_run_id=RUN_ID,
        )


def test_missing_required_level_zero_file_fails_closed(tmp_path: Path) -> None:
    runs_root = _completed_run(tmp_path)
    missing = runs_root / RUN_ID / DEVELOPMENT_HISTORIES[2] / "queue.jsonl"
    missing.unlink()

    with pytest.raises(U0ReuseError, match="required U0 artifact is missing"):
        build_verified_u0_reuse_artifact(
            native_runs_root=runs_root,
            native_run_id=RUN_ID,
        )


def test_checkpoint_hash_or_plan_identity_drift_is_rejected(tmp_path: Path) -> None:
    runs_root = _completed_run(tmp_path)
    checkpoint_path = (
        runs_root / RUN_ID / DEVELOPMENT_HISTORIES[0] / "checkpoint.json"
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["completed_sequences"] = [0]
    _write_json(checkpoint_path, checkpoint)

    with pytest.raises(U0ReuseError, match="checkpoint invalid"):
        build_verified_u0_reuse_artifact(
            native_runs_root=runs_root,
            native_run_id=RUN_ID,
        )


def test_result_episode_count_must_match_completed_prefix(tmp_path: Path) -> None:
    runs_root = _completed_run(tmp_path)
    result_path = (
        runs_root / RUN_ID / DEVELOPMENT_HISTORIES[3] / "history_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["aggregate"] = {"episode_count": 1}
    result.pop("payload_sha256")
    result["payload_sha256"] = payload_sha256(result)
    _write_json(result_path, result)

    with pytest.raises(U0ReuseError, match="episode count"):
        build_verified_u0_reuse_artifact(
            native_runs_root=runs_root,
            native_run_id=RUN_ID,
        )


def test_previously_sealed_reuse_artifact_rejects_source_file_hash_drift(
    tmp_path: Path,
) -> None:
    runs_root = _completed_run(tmp_path)
    artifact = build_verified_u0_reuse_artifact(
        native_runs_root=runs_root,
        native_run_id=RUN_ID,
    )
    spans = runs_root / RUN_ID / DEVELOPMENT_HISTORIES[0] / "spans.jsonl"
    spans.write_text(spans.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    with pytest.raises(U0ReuseError, match="source artifact hash drift"):
        verify_u0_reuse_artifact(artifact, native_runs_root=runs_root)


def test_reuse_artifact_payload_tampering_is_rejected_before_source_scan(
    tmp_path: Path,
) -> None:
    runs_root = _completed_run(tmp_path)
    artifact = build_verified_u0_reuse_artifact(
        native_runs_root=runs_root,
        native_run_id=RUN_ID,
    )
    artifact["namespace_reuse"] = True

    with pytest.raises(U0ReuseError, match="payload hash"):
        verify_u0_reuse_artifact(artifact, native_runs_root=runs_root)


def test_cross_history_quality_identity_drift_is_rejected(tmp_path: Path) -> None:
    runs_root = _completed_run(tmp_path)
    result_path = (
        runs_root / RUN_ID / DEVELOPMENT_HISTORIES[2] / "history_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["quality_identity"]["reader_config_sha256"] = "c" * 64
    result["quality"]["reader"]["config_sha256"] = "c" * 64
    result.pop("payload_sha256")
    result["payload_sha256"] = payload_sha256(result)
    _write_json(result_path, result)

    with pytest.raises(U0ReuseError, match="quality identity drift"):
        build_verified_u0_reuse_artifact(
            native_runs_root=runs_root,
            native_run_id=RUN_ID,
        )
