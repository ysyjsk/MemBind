"""Seal the read-only root cause for the stopped S2 near-zero sanity run."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope, payload_sha256, sha256_file
from .s2_retrieval_contract import classify_edge_surface_observation


DIAGNOSIS_SCHEMA = "membind.paper-eval-v3.s2-near-zero-diagnosis.v1"
STAGE_STATUS_SCHEMA = "membind.paper-eval-v3.stage-status.v1"
_COMPLETED_CHAIN = ["retrieval", "reader", "judge"]
_METRIC_KEYS = {
    "expected_episode_count",
    "namespace_episode_count",
    "namespace_entity_count",
    "namespace_fact_count",
    "gold_session_count",
    "gold_episode_match_count",
    "gold_episode_source_sequences",
    "gold_episode_mentions",
    "gold_episode_entity_edge_counts",
    "gold_attributed_fact_count",
    "search_surface",
    "model_request_count",
    "database_mutation_count",
}


def _load_envelope(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid S2 evidence: {type(error).__name__}") from None
    if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
        raise ValueError("invalid S2 evidence envelope")
    if value.get("payload_sha256") != payload_sha256(value["payload"]):
        raise ValueError("invalid S2 evidence payload hash")
    return value


def _load_checkpoint(path: Path, *, reference_sha256: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid S2 checkpoint: {type(error).__name__}") from None
    if not isinstance(value, dict):
        raise ValueError("invalid S2 checkpoint shape")
    stored = value.get("payload_sha256")
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise ValueError("invalid S2 checkpoint hash")
    if (
        value.get("status") != "completed"
        or value.get("completed_stages") != _COMPLETED_CHAIN
        or value.get("result_sha256") != reference_sha256
    ):
        raise ValueError("S2 checkpoint does not bind the completed result")
    return value


def _nonnegative_int(metrics: Mapping[str, Any], key: str) -> int:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid diagnostic metric: {key}")
    return value


def _validated_metrics(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _METRIC_KEYS:
        raise ValueError("invalid diagnostic metric fields")
    metrics = dict(value)
    for key in _METRIC_KEYS - {
        "gold_episode_source_sequences",
        "gold_episode_mentions",
        "gold_episode_entity_edge_counts",
        "search_surface",
    }:
        _nonnegative_int(metrics, key)
    for key in (
        "gold_episode_source_sequences",
        "gold_episode_mentions",
        "gold_episode_entity_edge_counts",
    ):
        items = metrics.get(key)
        if not isinstance(items, list) or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in items
        ):
            raise ValueError(f"invalid diagnostic metric: {key}")
    if (
        metrics["expected_episode_count"] != 49
        or metrics["namespace_episode_count"] != 49
        or metrics["gold_session_count"] != 2
        or metrics["gold_episode_match_count"] != 2
        or len(metrics["gold_episode_source_sequences"]) != 2
        or len(set(metrics["gold_episode_source_sequences"])) != 2
        or len(metrics["gold_episode_mentions"]) != 2
        or len(metrics["gold_episode_entity_edge_counts"]) != 2
        or any(metrics["gold_episode_entity_edge_counts"])
        or metrics["gold_attributed_fact_count"] != 0
        or metrics["namespace_fact_count"] == 0
        or metrics["search_surface"] != "EntityEdge"
        or metrics["model_request_count"] != 0
        or metrics["database_mutation_count"] != 0
    ):
        raise ValueError("diagnostic evidence does not support the frozen classification")
    return metrics


def finalize_s2_near_zero_diagnosis(
    *,
    diagnosis_path: Path,
    stage_status_path: Path,
    reference_path: Path,
    checkpoint_path: Path,
    metrics: Mapping[str, Any],
    git_commit: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist a content-free diagnosis and stop S3 authorization."""

    if Path(diagnosis_path).exists() or Path(stage_status_path).exists():
        raise ValueError("S2 diagnosis output already exists")
    if not isinstance(git_commit, str) or not git_commit:
        raise ValueError("git_commit must be nonempty")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be nonempty")
    reference = _load_envelope(Path(reference_path))
    payload = reference["payload"]
    reference_sha256 = sha256_file(Path(reference_path))
    checkpoint = _load_checkpoint(
        Path(checkpoint_path), reference_sha256=reference_sha256
    )
    if (
        reference.get("protocol_version") != PROTOCOL_VERSION
        or payload.get("stage") != "S2"
        or payload.get("method") != "U0"
        or payload.get("status") != "PIPELINE_ANOMALY_NEAR_ZERO"
        or payload.get("near_zero_stop_triggered") is not True
        or payload.get("evidence_recall_at_10") != 0.0
        or payload.get("qa_accuracy") != 0.0
        or payload.get("retrieval_result_count") != 10
        or payload.get("gold_session_count") != 2
        or payload.get("reader_status") != "SUCCESS"
        or payload.get("judge_status") != "SUCCESS"
    ):
        raise ValueError("S2 result does not support near-zero diagnosis")
    safe_metrics = _validated_metrics(metrics)
    observation = classify_edge_surface_observation(
        search_surface=str(safe_metrics["search_surface"]),
        gold_episode_entity_edge_counts=safe_metrics[
            "gold_episode_entity_edge_counts"
        ],
        gold_episode_match_count=safe_metrics["gold_episode_match_count"],
        gold_session_count=safe_metrics["gold_session_count"],
    )
    if (
        checkpoint.get("history_id") != payload.get("history_id")
        or checkpoint.get("namespace") != payload.get("namespace")
    ):
        raise ValueError("S2 diagnosis identity mismatch")

    diagnosis_payload = {
        "schema_version": DIAGNOSIS_SCHEMA,
        "stage": "S2",
        "method": "U0",
        "history_id": payload["history_id"],
        "namespace": payload["namespace"],
        "status": "ROOT_CAUSE_IDENTIFIED",
        **observation,
        "service_failure": False,
        "next_stage_authorized": False,
        "evidence": safe_metrics,
        "reference_sanity_sha256": reference_sha256,
        "checkpoint_sha256": sha256_file(Path(checkpoint_path)),
        "interpretation": (
            "The gold episodes were published and produced entity mentions, but "
            "have no EntityEdge provenance and therefore cannot be attributed "
            "through Graphiti.search's edge-only retrieval surface. This does "
            "not establish node-, episode-, or whole-graph retrieval quality."
        ),
    }
    diagnosis = finalize_envelope(
        payload=diagnosis_payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=run_id,
    )
    atomic_write_json(Path(diagnosis_path), diagnosis)

    ledger_payload = {
        "schema_version": STAGE_STATUS_SCHEMA,
        "current_stage": "S2",
        "status": "STOPPED_ROOT_CAUSE_IDENTIFIED",
        "passed_stages": ["S0", "S1"],
        "terminal_run_id": checkpoint["run_id"],
        "next_authorized_stage": None,
        "diagnosis_sha256": sha256_file(Path(diagnosis_path)),
        "reference_sanity_sha256": reference_sha256,
    }
    ledger = finalize_envelope(
        payload=ledger_payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=run_id,
    )
    atomic_write_json(Path(stage_status_path), ledger)
    return diagnosis, ledger
