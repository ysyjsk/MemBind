"""Single-use authority for the one-history S4 capture/replay pipeline."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .s4_d0_contract import verify_s4_d0_contract
from .s4_preflight import verify_s4_preflight
from .s4_retry_contract import SCHEMA as RETRY_CONTRACT_SCHEMA
from .s4_retry_contract import verify_s4_retry_contract


AUTHORITY_SCHEMA = "membind.paper-eval-v3.s4-smoke-authority.v1"
CONSUMPTION_SCHEMA = "membind.paper-eval-v3.s4-authority-consumption.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAMES = {"authority", "controller", "production", "runner", "test"}


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _sources(value: Mapping[str, str]) -> dict[str, str]:
    selected = _mapping(value, label="S4 authority source inventory")
    if set(selected) != _SOURCE_NAMES:
        raise ValueError("S4 authority source inventory drift")
    return {
        name: _sha(selected[name], field=f"source {name}")
        for name in sorted(selected)
    }


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def build_s4_smoke_authority(
    *,
    contract: Mapping[str, Any],
    contract_file_sha256: str,
    preflight: Mapping[str, Any],
    preflight_file_sha256: str,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Build an offline authority draft from the sealed contract and preflight."""

    if contract.get("schema_version") == RETRY_CONTRACT_SCHEMA:
        selected_contract = verify_s4_retry_contract(contract)
    else:
        selected_contract = verify_s4_d0_contract(contract)
    selected_preflight = verify_s4_preflight(preflight)
    contract_file_sha = _sha(contract_file_sha256, field="S4 contract file")
    preflight_file_sha = _sha(preflight_file_sha256, field="S4 preflight file")
    preflight_payload = selected_preflight["payload"]
    if (
        preflight_payload["s4_contract_file_sha256"] != contract_file_sha
        or preflight_payload["s4_contract_sha256"]
        != selected_contract["contract_sha256"]
        or preflight_payload["authority"]["s4_authority_creation_authorized"]
        is not True
    ):
        raise ValueError("S4 authority preflight/contract binding drift")

    body = {
        "schema_version": AUTHORITY_SCHEMA,
        "stage": "S4_SMOKE",
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
        "private_cache": deepcopy(
            selected_contract.get("private_cache")
            or {
                "embedding_relpath": (
                    "runtime/private/s4-d0-07741c45-20260814-001/embedding.jsonl"
                ),
                "prompt_relpath": (
                    "runtime/private/s4-d0-07741c45-20260814-001/prompt.jsonl"
                ),
                "reportable_contents": False,
            }
        ),
        "source_sha256": _sources(source_sha256),
        "authority": {
            "single_use": True,
            "s4_smoke_pipeline_authorized": True,
            "d0_replay_requires_capture_pass": True,
            "s4_four_history_qualification_authorized": False,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        },
    }
    return verify_s4_smoke_authority(
        finalize_envelope(
            payload=body,
            protocol_version=PROTOCOL_VERSION,
            git_commit="UNSEALED",
            run_id="s4-smoke-authority-draft",
        )
    )


def verify_s4_smoke_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 smoke authority")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 smoke authority envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="S4 authority payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ValueError("S4 smoke authority hash or envelope drift")
    if set(payload) != {
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
        "source_sha256",
        "authority",
    }:
        raise ValueError("S4 smoke authority payload shape drift")
    if (
        payload.get("schema_version") != AUTHORITY_SCHEMA
        or payload.get("stage") != "S4_SMOKE"
        or payload.get("status") != "AUTHORIZED_SINGLE_USE"
        or payload.get("history")
        != {
            "data_role": "DEVELOPMENT_EXPOSED",
            "episode_count": 49,
            "history_id": "07741c45",
        }
        or payload.get("execution_order")
        != ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
    ):
        raise ValueError("S4 smoke authority identity drift")
    for field in (
        "s4_contract_file_sha256",
        "s4_contract_sha256",
        "s4_preflight_file_sha256",
        "s4_preflight_payload_sha256",
        "common_method_policy_sha256",
    ):
        _sha(payload.get(field), field=field)
    runs = _mapping(payload.get("runs"), label="S4 authority runs")
    if set(runs) != {"U0_CAPTURE", "D0_READ_ONLY_REPLAY"}:
        raise ValueError("S4 authority run inventory drift")
    capture = _mapping(runs.get("U0_CAPTURE"), label="S4 capture run")
    match = re.fullmatch(
        r"s4-d0-07741c45-20260814-(?P<attempt>\d{3})",
        str(capture.get("cache_id", "")),
    )
    if match is None:
        raise ValueError("S4 authority cache attempt identity drift")
    attempt = match["attempt"]
    if attempt != "001" and int(attempt) < 4:
        raise ValueError("S4 authority refers to an unsealed retry attempt")
    cache_id = f"s4-d0-07741c45-20260814-{attempt}"
    expected_runs = {
        "U0_CAPTURE": {
            "cache_id": cache_id,
            "method": "U0",
            "mode": "capture",
            "namespace": f"pev3-s4-u0-capture-20260814-{attempt}",
            "run_id": f"s4-d0-capture-20260814-{attempt}",
        },
        "D0_READ_ONLY_REPLAY": {
            "cache_id": cache_id,
            "method": "D0",
            "mode": "replay",
            "namespace": f"pev3-s4-d0-replay-20260814-{attempt}",
            "run_id": f"s4-d0-replay-20260814-{attempt}",
        },
    }
    if runs != expected_runs:
        raise ValueError("S4 authority run identity drift")
    if payload.get("private_cache") != {
        "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
        "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
        "reportable_contents": False,
    }:
        raise ValueError("S4 authority private-cache identity drift")
    _sources(payload.get("source_sha256", {}))
    if payload.get("authority") != {
        "single_use": True,
        "s4_smoke_pipeline_authorized": True,
        "d0_replay_requires_capture_pass": True,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }:
        raise ValueError("S4 smoke authority scope drift")
    artifact["payload"] = payload
    return artifact


def finalize_s4_smoke_authority(
    *,
    output_path: Path,
    authority: Mapping[str, Any],
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    payload = _mapping(authority, label="S4 authority draft payload")
    artifact = verify_s4_smoke_authority(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


def verify_s4_authority_consumption(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 authority consumption")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 authority consumption envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="S4 consumption payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload)
        != {
            "schema_version",
            "stage",
            "consumed_action",
            "authority_file_sha256",
            "authority_payload_sha256",
            "execution_order",
            "runs",
            "further_live_authority",
        }
    ):
        raise ValueError("S4 authority consumption hash or shape drift")
    if (
        payload.get("schema_version") != CONSUMPTION_SCHEMA
        or payload.get("stage") != "S4_SMOKE"
        or payload.get("consumed_action") != "S4_SMOKE_PIPELINE"
        or payload.get("execution_order")
        != ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
        or payload.get("further_live_authority") is not False
    ):
        raise ValueError("S4 authority consumption scope drift")
    _sha(payload.get("authority_file_sha256"), field="authority file")
    _sha(payload.get("authority_payload_sha256"), field="authority payload")
    if set(_mapping(payload.get("runs"), label="consumed runs")) != {
        "U0_CAPTURE",
        "D0_READ_ONLY_REPLAY",
    }:
        raise ValueError("S4 authority consumption runs drift")
    artifact["payload"] = payload
    return artifact


def consume_s4_smoke_authority(
    *,
    authority: Mapping[str, Any],
    authority_file_sha256: str,
    output_path: Path,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    selected = verify_s4_smoke_authority(authority)
    payload = {
        "schema_version": CONSUMPTION_SCHEMA,
        "stage": "S4_SMOKE",
        "consumed_action": "S4_SMOKE_PIPELINE",
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_payload_sha256": selected["payload_sha256"],
        "execution_order": deepcopy(selected["payload"]["execution_order"]),
        "runs": deepcopy(selected["payload"]["runs"]),
        "further_live_authority": False,
    }
    artifact = verify_s4_authority_consumption(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact
