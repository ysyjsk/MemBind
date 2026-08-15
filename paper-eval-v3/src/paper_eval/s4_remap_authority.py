"""Single-use authority for the S4 candidate-remap smoke retry."""

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
from .s4_remap_retry_contract import verify_s4_remap_retry_contract


AUTHORITY_SCHEMA = "membind.paper-eval-v3.s4-remap-smoke-authority.v1"
CONSUMPTION_SCHEMA = "membind.paper-eval-v3.s4-remap-authority-consumption.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAMES = {
    "authority",
    "candidate_oracle",
    "controller",
    "production",
    "runner",
    "test",
}


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _sources(value: Mapping[str, str]) -> dict[str, str]:
    selected = _mapping(value, label="S4 remap authority sources")
    if set(selected) != _SOURCE_NAMES:
        raise ValueError("S4 remap authority source inventory drift")
    return {
        name: _sha(selected[name], field=f"source {name}")
        for name in sorted(selected)
    }


def _reject_private(value: object) -> None:
    forbidden = {
        "answer",
        "api_key",
        "content",
        "messages",
        "password",
        "prompt_parts",
        "question",
        "raw_output",
        "raw_response",
        "secret",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in forbidden:
                raise ValueError("S4 remap authority contains private data")
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


def _expected_scope() -> dict[str, bool]:
    return {
        "single_use": True,
        "s4_remap_smoke_pipeline_authorized": True,
        "d0_replay_requires_capture_pass": True,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def _expected_runs() -> dict[str, dict[str, str]]:
    cache_id = "s4-d0-remap-07741c45-20260815-005"
    return {
        "U0_CAPTURE": {
            "cache_id": cache_id,
            "method": "U0",
            "mode": "capture",
            "namespace": "pev3-s4-u0-capture-20260815-005",
            "run_id": "s4-d0-capture-20260815-005",
        },
        "D0_READ_ONLY_REPLAY": {
            "cache_id": cache_id,
            "method": "D0",
            "mode": "replay",
            "namespace": "pev3-s4-d0-replay-20260815-005",
            "run_id": "s4-d0-replay-20260815-005",
        },
    }


def _expected_private_cache() -> dict[str, Any]:
    cache_id = "s4-d0-remap-07741c45-20260815-005"
    return {
        "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
        "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
        "reportable_contents": False,
    }


def _expected_candidate_oracle() -> dict[str, Any]:
    return {
        "exact_lookup_first": True,
        "persistent_cache_mutation": False,
        "translation_kind": "VERIFIED_CANDIDATE_ID_BIJECTION",
        "supported_prompt_names": [
            "dedupe_edges.resolve_edge",
            "dedupe_nodes.nodes",
        ],
        "node_response_fields": [
            "entity_resolutions[].duplicate_candidate_id"
        ],
        "edge_response_fields": ["contradicted_facts[]", "duplicate_facts[]"],
        "fail_closed_on_membership_or_identity_drift": True,
        "raw_or_parsed_cache_write": False,
    }


def _expected_hard_gates() -> dict[str, Any]:
    return {
        "candidate_oracle_resolution_accounting": (
            "EXACT_PLUS_REMAP_EQUALS_RESOLVED"
        ),
        "candidate_remap_breakdown": "NODE_PLUS_EDGE_EQUALS_TOTAL",
        "candidate_remap_rejection_count": 0,
        "canonical_graph_parity": "EXACT_100_PERCENT",
        "cache_mutation_during_replay": False,
    }


def build_s4_remap_authority(
    *,
    contract: Mapping[str, Any],
    contract_file_sha256: str,
    preflight: Mapping[str, Any],
    preflight_file_sha256: str,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    selected_contract = verify_s4_remap_retry_contract(contract)
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
        raise ValueError("S4 remap authority preflight/contract binding drift")
    body = {
        "schema_version": AUTHORITY_SCHEMA,
        "stage": "S4_REMAP_SMOKE",
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
        "candidate_oracle": deepcopy(selected_contract["candidate_oracle"]),
        "remap_hard_gates": deepcopy(selected_contract["remap_hard_gates"]),
        "source_sha256": _sources(source_sha256),
        "authority": _expected_scope(),
    }
    return verify_s4_remap_authority(
        finalize_envelope(
            payload=body,
            protocol_version=PROTOCOL_VERSION,
            git_commit="UNSEALED",
            run_id="s4-remap-smoke-authority-draft",
        )
    )


def verify_s4_remap_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 remap authority")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 remap authority envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="S4 remap authority payload")
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
        "candidate_oracle",
        "remap_hard_gates",
        "source_sha256",
        "authority",
    }
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload) != expected_fields
        or payload.get("schema_version") != AUTHORITY_SCHEMA
        or payload.get("stage") != "S4_REMAP_SMOKE"
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
        raise ValueError("S4 remap authority identity or hash drift")
    for field in (
        "s4_contract_file_sha256",
        "s4_contract_sha256",
        "s4_preflight_file_sha256",
        "s4_preflight_payload_sha256",
        "common_method_policy_sha256",
    ):
        _sha(payload.get(field), field=field)

    if payload.get("runs") != _expected_runs():
        raise ValueError("S4 remap authority run identity drift")
    if payload.get("private_cache") != _expected_private_cache():
        raise ValueError("S4 remap authority cache identity drift")
    if payload.get("candidate_oracle") != _expected_candidate_oracle():
        raise ValueError("S4 remap authority candidate policy drift")
    if payload.get("remap_hard_gates") != _expected_hard_gates():
        raise ValueError("S4 remap authority hard-gate drift")
    _sources(payload.get("source_sha256", {}))
    if payload.get("authority") != _expected_scope():
        raise ValueError("S4 remap authority scope drift")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def finalize_s4_remap_authority(
    *,
    output_path: Path,
    authority: Mapping[str, Any],
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    payload = _mapping(authority, label="S4 remap authority draft payload")
    artifact = verify_s4_remap_authority(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


def verify_s4_remap_authority_consumption(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 remap authority consumption")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 remap consumption envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="S4 remap consumption payload")
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
            "runs",
            "private_cache",
            "candidate_oracle_sha256",
        }
        or payload.get("schema_version") != CONSUMPTION_SCHEMA
        or payload.get("stage") != "S4_REMAP_SMOKE"
        or payload.get("consumed_action") != "S4_REMAP_SMOKE_PIPELINE"
    ):
        raise ValueError("S4 remap consumption identity or hash drift")
    _sha(payload.get("authority_file_sha256"), field="authority file")
    _sha(payload.get("authority_payload_sha256"), field="authority payload")
    _sha(payload.get("candidate_oracle_sha256"), field="candidate oracle")
    if (
        payload.get("runs") != _expected_runs()
        or payload.get("private_cache") != _expected_private_cache()
    ):
        raise ValueError("S4 remap consumption run/cache drift")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def consume_s4_remap_authority(
    *,
    authority: Mapping[str, Any],
    authority_file_sha256: str,
    output_path: Path,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    selected = verify_s4_remap_authority(authority)
    payload = selected["payload"]
    body = {
        "schema_version": CONSUMPTION_SCHEMA,
        "stage": "S4_REMAP_SMOKE",
        "consumed_action": "S4_REMAP_SMOKE_PIPELINE",
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_payload_sha256": selected["payload_sha256"],
        "runs": deepcopy(payload["runs"]),
        "private_cache": deepcopy(payload["private_cache"]),
        "candidate_oracle_sha256": payload_sha256(payload["candidate_oracle"]),
    }
    artifact = verify_s4_remap_authority_consumption(
        finalize_envelope(
            payload=body,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact
