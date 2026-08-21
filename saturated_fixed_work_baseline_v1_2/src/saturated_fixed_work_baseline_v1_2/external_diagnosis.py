"""Non-success terminal artifact for repeated external L0 access failures."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class ExternalDiagnosisError(ValueError):
    """Recovery evidence is incomplete or a diagnosis would be overwritten."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def build_stop_diagnosis(
    recovery_rounds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rounds = [dict(row) for row in recovery_rounds]
    if len(rounds) < 3:
        raise ExternalDiagnosisError("RECOVERY_ROUNDS_INCOMPLETE")
    ordinals = [row.get("round") for row in rounds]
    timestamps = [row.get("observed_at") for row in rounds]
    if (
        len(set(ordinals)) != len(rounds)
        or len(set(timestamps)) != len(rounds)
        or any(not isinstance(row.get("commands"), list) for row in rounds)
    ):
        raise ExternalDiagnosisError("RECOVERY_ROUNDS_NOT_DISTINCT")
    body = {
        "schema_version": "membind.saturated-fixed-work.external-diagnosis.v1",
        "status": "BLOCKED_EXTERNAL_PROVIDER_RESOURCE_IDENTITY",
        "completed": False,
        "resume_from_gate": "L0_RESOURCE_IDENTITY",
        "formal_blocks_started": 0,
        "qa_rows_created": 0,
        "main_tables_created": False,
        "final_seal_created": False,
        "completion_marker_created": False,
        "missing_evidence": [
            "provider_hostname",
            "provider_gpu_uuid",
            "provider_8000_pid_argv_cuda_to_gpu_uuid",
            "provider_8001_pid_argv_cuda_to_gpu_uuid",
            "historical_provider_gpu_uuid_and_process_mapping",
        ],
        "recovery_paths_attempted": [
            "target_host_identity_and_process_probe",
            "existing_tmux_or_service_status_probe",
            "historical_startup_log_and_command_recovery",
        ],
        "recovery_rounds": rounds,
        "next_action": (
            "Grant read-only remote execution for hostname, ps/proc, nvidia-smi, "
            "system inventory, and service/tmux status on the host serving 8000/8001; "
            "then resume at L0 without starting L1-L5 first."
        ),
    }
    body["payload_sha256"] = _hash(body)
    return body


def write_stop_diagnosis(run_root: Path, diagnosis: Mapping[str, Any]) -> Path:
    root = run_root.resolve()
    body = dict(diagnosis)
    observed = body.pop("payload_sha256", None)
    if observed != _hash(body) or body.get("status") != (
        "BLOCKED_EXTERNAL_PROVIDER_RESOURCE_IDENTITY"
    ):
        raise ExternalDiagnosisError("STOP_DIAGNOSIS_INVALID")
    body["payload_sha256"] = observed
    root.mkdir(parents=True, exist_ok=True)
    path = root / "STOP_WITH_EXTERNAL_DIAGNOSIS.json"
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ExternalDiagnosisError("STOP_DIAGNOSIS_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


__all__ = [
    "ExternalDiagnosisError",
    "build_stop_diagnosis",
    "write_stop_diagnosis",
]
