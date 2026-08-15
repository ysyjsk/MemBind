"""Fail-closed predecessor evidence for the retry-008 adapter fix."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256


_CHECKPOINT_FIELDS = {
    "cache_id",
    "canonical_graph_sha256",
    "completed_source_sequences",
    "error_class",
    "history_id",
    "method",
    "mode",
    "namespace",
    "namespace_state",
    "payload_sha256",
    "phase",
    "run_id",
    "runtime_evidence_cumulative",
    "schema_version",
    "status",
}


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _checkpoint(value: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint = _mapping(value, label="retry-007 checkpoint")
    stored = checkpoint.pop("payload_sha256", None)
    if set(checkpoint) | {"payload_sha256"} != _CHECKPOINT_FIELDS:
        raise ValueError("retry-007 checkpoint shape drift")
    if stored != payload_sha256(checkpoint):
        raise ValueError("retry-007 checkpoint hash drift")
    return {**checkpoint, "payload_sha256": stored}


def _phase_result(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, label="retry-007 phase result")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("retry-007 phase-result envelope drift")
    payload = _mapping(artifact.get("payload"), label="retry-007 phase payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ValueError("retry-007 phase-result hash drift")
    artifact["payload"] = payload
    return artifact


def verify_retry_007_duplicate_uuid_failure(
    *,
    checkpoint: Mapping[str, Any],
    phase_result: Mapping[str, Any],
    execution_log: str,
    replay_checkpoint_exists: bool,
    replay_phase_result_exists: bool,
    smoke_result_exists: bool,
) -> dict[str, Any]:
    """Prove retry-007 stopped only at the over-strong duplicate UUID gate."""

    selected_checkpoint = _checkpoint(checkpoint)
    selected_result = _phase_result(phase_result)
    payload = selected_result["payload"]
    complete = list(range(12))
    if (
        selected_checkpoint.get("schema_version")
        != "membind.paper-eval-v3.s4-phase-checkpoint.v1"
        or selected_checkpoint.get("run_id")
        != "s4-d0-capture-20260815-007"
        or selected_checkpoint.get("phase") != "U0_CAPTURE"
        or selected_checkpoint.get("method") != "U0"
        or selected_checkpoint.get("mode") != "capture"
        or selected_checkpoint.get("status") != "incomplete"
        or selected_checkpoint.get("completed_source_sequences") != complete
        or selected_checkpoint.get("error_class")
        != "CandidateSidecarRuntimeError"
        or selected_result.get("run_id") != selected_checkpoint.get("run_id")
        or payload.get("run_id") != selected_checkpoint.get("run_id")
        or payload.get("phase") != "U0_CAPTURE"
        or payload.get("status") != "INCOMPLETE"
        or payload.get("mergeable") is not False
        or payload.get("expected_episode_count") != 49
        or payload.get("completed_source_sequences") != complete
        or payload.get("episode_coverage") != 12 / 49
        or payload.get("error_class") != "CandidateSidecarRuntimeError"
        or payload.get("canonical_graph_sha256") is not None
        or payload.get("cleanup") is not None
    ):
        raise ValueError("retry-007 duplicate-UUID predecessor evidence drift")
    if (
        not isinstance(execution_log, str)
        or "source_sequence\": 12" not in execution_log
        or "failure_stage\": \"add_episode\"" not in execution_log
        or "resolution entity UUID is duplicated" not in execution_log
        or "CandidateSidecarRuntimeError" not in execution_log
    ):
        raise ValueError("retry-007 duplicate-UUID failure trace is missing")
    if (
        replay_checkpoint_exists is not False
        or replay_phase_result_exists is not False
        or smoke_result_exists is not False
    ):
        raise ValueError("retry-007 unexpectedly produced downstream evidence")
    return {
        "attempt_id": "007",
        "failure_stage": "U0_CAPTURE/add_episode/source_sequence=12",
        "error_class": "CandidateSidecarRuntimeError",
        "error_code": "DUPLICATE_UUID_CONFLICT_POLICY_TOO_STRICT",
        "completed_episode_count": 12,
        "expected_episode_count": 49,
        "mergeable": False,
        "replay_started": False,
        "smoke_result_exists": False,
    }
