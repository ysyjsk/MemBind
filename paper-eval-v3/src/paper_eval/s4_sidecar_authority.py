"""Single-use authority for an S4 bilateral-sidecar retry smoke."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .s4_preflight import verify_s4_preflight
from .s4_sidecar_retry_contract import (
    _candidate_oracle,
    _hard_gates,
    _private_cache,
    _runs,
    verify_s4_sidecar_retry_contract,
)


AUTHORITY_SCHEMA = "membind.paper-eval-v3.s4-sidecar-smoke-authority.v1"
CONSUMPTION_SCHEMA = (
    "membind.paper-eval-v3.s4-sidecar-authority-consumption.v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASE_SOURCE_NAMES = {
    "authority",
    "candidate_oracle",
    "candidate_projection",
    "candidate_sidecar",
    "candidate_sidecar_runtime",
    "controller",
    "production",
    "result",
    "runner",
    "test",
}
_EDGE_IDENTITY_SOURCE = "edge_identity"


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _sources(value: object, *, attempt: str = "006") -> dict[str, str]:
    selected = _mapping(value, label="S4 sidecar authority sources")
    expected = set(_BASE_SOURCE_NAMES)
    if int(attempt) >= 7:
        expected.add(_EDGE_IDENTITY_SOURCE)
    if set(selected) != expected:
        raise ValueError("S4 sidecar authority source inventory drift")
    return {
        name: _sha(selected[name], field=f"source {name}")
        for name in sorted(selected)
    }


def _scope() -> dict[str, bool]:
    return {
        "single_use": True,
        "s4_sidecar_smoke_pipeline_authorized": True,
        "d0_replay_requires_capture_pass": True,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def _attempt_from_runs(value: object) -> str:
    runs = _mapping(value, label="S4 sidecar authority runs")
    attempts: set[str] = set()
    for phase, prefix in (
        ("U0_CAPTURE", "s4-d0-capture-20260815-"),
        ("D0_READ_ONLY_REPLAY", "s4-d0-replay-20260815-"),
    ):
        run = runs.get(phase)
        run_id = run.get("run_id") if isinstance(run, Mapping) else None
        if not isinstance(run_id, str) or not run_id.startswith(prefix):
            raise ValueError("S4 sidecar authority run identity drift")
        attempt = run_id.removeprefix(prefix)
        if re.fullmatch(r"\d{3}", attempt) is None or int(attempt) < 6:
            raise ValueError("S4 sidecar authority attempt identity drift")
        attempts.add(attempt)
    if len(attempts) != 1:
        raise ValueError("S4 sidecar authority phase attempts disagree")
    return attempts.pop()


def _reject_private(value: object) -> None:
    forbidden = {
        "answer",
        "api_key",
        "body",
        "content",
        "messages",
        "password",
        "prompt_parts",
        "question",
        "raw_output",
        "raw_response",
        "secret",
        "uuid",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in forbidden:
                raise ValueError("S4 sidecar authority contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def build_s4_sidecar_authority(
    *,
    contract: Mapping[str, Any],
    contract_file_sha256: str,
    preflight: Mapping[str, Any],
    preflight_file_sha256: str,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    selected_contract = verify_s4_sidecar_retry_contract(contract)
    attempt = selected_contract["attempt_id"]
    selected_preflight = verify_s4_preflight(preflight)
    contract_file_sha = _sha(contract_file_sha256, field="contract file")
    preflight_file_sha = _sha(preflight_file_sha256, field="preflight file")
    preflight_payload = selected_preflight["payload"]
    if (
        preflight_payload["s4_contract_file_sha256"] != contract_file_sha
        or preflight_payload["s4_contract_sha256"]
        != selected_contract["contract_sha256"]
        or preflight_payload["authority"]["s4_authority_creation_authorized"]
        is not True
    ):
        raise ValueError("S4 sidecar authority preflight binding drift")
    body = {
        "schema_version": AUTHORITY_SCHEMA,
        "stage": "S4_BILATERAL_SIDECAR_SMOKE",
        "status": "AUTHORIZED_SINGLE_USE",
        "s4_contract_file_sha256": contract_file_sha,
        "s4_contract_sha256": selected_contract["contract_sha256"],
        "s4_preflight_file_sha256": preflight_file_sha,
        "s4_preflight_payload_sha256": selected_preflight["payload_sha256"],
        "common_method_policy_sha256": selected_contract[
            "common_method_policy_sha256"
        ],
        "history": deepcopy(selected_contract["history"]),
        "execution_order": deepcopy(selected_contract["execution_order"]),
        "runs": deepcopy(selected_contract["runs"]),
        "private_cache": deepcopy(selected_contract["private_cache"]),
        "projection_schema_sha256": selected_contract[
            "projection_schema_sha256"
        ],
        "candidate_oracle": deepcopy(selected_contract["candidate_oracle"]),
        "sidecar_hard_gates": deepcopy(selected_contract["sidecar_hard_gates"]),
        "source_sha256": _sources(source_sha256, attempt=attempt),
        "authority": _scope(),
    }
    return verify_s4_sidecar_authority(
        finalize_envelope(
            payload=body,
            protocol_version=PROTOCOL_VERSION,
            git_commit="UNSEALED",
            run_id="s4-sidecar-smoke-authority-draft",
        )
    )


def verify_s4_sidecar_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 sidecar authority")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 sidecar authority envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="S4 sidecar authority payload")
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "s4_contract_file_sha256",
        "s4_contract_sha256",
        "s4_preflight_file_sha256",
        "s4_preflight_payload_sha256",
        "common_method_policy_sha256",
        "history",
        "execution_order",
        "runs",
        "private_cache",
        "projection_schema_sha256",
        "candidate_oracle",
        "sidecar_hard_gates",
        "source_sha256",
        "authority",
    }
    attempt = _attempt_from_runs(payload.get("runs"))
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload) != expected_fields
        or payload.get("schema_version") != AUTHORITY_SCHEMA
        or payload.get("stage") != "S4_BILATERAL_SIDECAR_SMOKE"
        or payload.get("status") != "AUTHORIZED_SINGLE_USE"
        or payload.get("history")
        != {
            "data_role": "DEVELOPMENT_EXPOSED",
            "episode_count": 49,
            "history_id": "07741c45",
        }
        or payload.get("execution_order")
        != ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
        or payload.get("runs") != _runs(attempt)
        or payload.get("private_cache") != _private_cache(attempt)
        or payload.get("candidate_oracle") != _candidate_oracle()
        or payload.get("sidecar_hard_gates") != _hard_gates()
        or payload.get("authority") != _scope()
    ):
        raise ValueError("S4 sidecar authority identity or scope drift")
    for field in (
        "s4_contract_file_sha256",
        "s4_contract_sha256",
        "s4_preflight_file_sha256",
        "s4_preflight_payload_sha256",
        "common_method_policy_sha256",
        "projection_schema_sha256",
    ):
        _sha(payload.get(field), field=field)
    _sources(payload.get("source_sha256"), attempt=attempt)
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def finalize_s4_sidecar_authority(
    *,
    output_path: Path,
    authority: Mapping[str, Any],
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    artifact = verify_s4_sidecar_authority(
        finalize_envelope(
            payload=_mapping(authority, label="S4 sidecar authority payload"),
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    _write_exclusive(output_path, artifact)
    return artifact


def verify_s4_sidecar_authority_consumption(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 sidecar authority consumption")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 sidecar consumption envelope shape drift")
    payload = _mapping(
        artifact.get("payload"), label="S4 sidecar consumption payload"
    )
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload)
        != {
            "schema_version",
            "stage",
            "authority_file_sha256",
            "authority_payload_sha256",
            "consumed_action",
            "runs",
        }
        or payload.get("schema_version") != CONSUMPTION_SCHEMA
        or payload.get("stage") != "S4_BILATERAL_SIDECAR_SMOKE"
        or payload.get("consumed_action")
        != "S4_BILATERAL_SIDECAR_SMOKE_PIPELINE"
    ):
        raise ValueError("S4 sidecar consumption identity or hash drift")
    _sha(payload.get("authority_file_sha256"), field="authority file")
    _sha(payload.get("authority_payload_sha256"), field="authority payload")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def consume_s4_sidecar_authority(
    *,
    authority: Mapping[str, Any],
    authority_file_sha256: str,
    output_path: Path,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    selected = verify_s4_sidecar_authority(authority)
    body = {
        "schema_version": CONSUMPTION_SCHEMA,
        "stage": "S4_BILATERAL_SIDECAR_SMOKE",
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_payload_sha256": selected["payload_sha256"],
        "consumed_action": "S4_BILATERAL_SIDECAR_SMOKE_PIPELINE",
        "runs": deepcopy(selected["payload"]["runs"]),
    }
    artifact = verify_s4_sidecar_authority_consumption(
        finalize_envelope(
            payload=body,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    _write_exclusive(output_path, artifact)
    return artifact
