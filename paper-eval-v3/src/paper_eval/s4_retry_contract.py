"""Additive execution-identity retry contract for an invalidated S4 attempt."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .s4_d0_contract import verify_s4_d0_contract


SCHEMA = "membind.paper-eval-v3.s4-d0-retry-contract.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _verify_invalidation(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 invalidation")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 invalidation envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="S4 invalidation payload")
    if (
        artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or payload.get("status") != "INCOMPLETE_INVALID_NON_MERGEABLE"
        or payload.get("reason")
        != "CAPTURE_ORACLES_BYPASSED_BY_RETAINED_GRAPHITI_CLIENT_BUNDLE"
        or payload.get("mergeable") is not False
        or payload.get("reuse_authorized") is not False
        or payload.get("cleanup")
        != {
            "scope": "EXACT_GROUP_ID_ONLY",
            "group_ids": ["pev3-s4-u0-capture-20260814-001"],
            "global_cleanup_used": False,
            "post_cleanup_node_count": 0,
            "post_cleanup_relationship_count": 0,
        }
    ):
        raise ValueError("S4 invalidation is not a cleaned nonmergeable attempt")
    artifact["payload"] = payload
    return artifact


def build_s4_retry_contract(
    *,
    parent_contract: Mapping[str, Any],
    parent_contract_file_sha256: str,
    invalidation: Mapping[str, Any],
    invalidation_file_sha256: str,
    attempt_number: int,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    parent = verify_s4_d0_contract(parent_contract)
    invalid = _verify_invalidation(invalidation)
    if not isinstance(attempt_number, int) or not 4 <= attempt_number <= 999:
        raise ValueError("S4 retry attempt must be in [4, 999]")
    attempt = f"{attempt_number:03d}"
    sources = _mapping(source_sha256, label="S4 retry sources")
    if set(sources) != {"retry_contract", "test"}:
        raise ValueError("S4 retry source inventory drift")
    sources = {
        name: _sha(value, field=f"source {name}")
        for name, value in sorted(sources.items())
    }
    cache_id = f"s4-d0-07741c45-20260814-{attempt}"
    inherited = {
        key: deepcopy(parent[key])
        for key in (
            "u0_capture",
            "d0_replay",
            "hard_gates",
            "canonical_comparison",
            "namespace_policy",
            "durability",
            "preflight",
        )
    }
    body = {
        "schema_version": SCHEMA,
        "stage": "S4",
        "status": "RETRY_EXECUTION_IDENTITY_FROZEN",
        "attempt_id": attempt,
        "parent_contract_file_sha256": _sha(
            parent_contract_file_sha256, field="parent contract file"
        ),
        "parent_contract_sha256": parent["contract_sha256"],
        "invalidation_file_sha256": _sha(
            invalidation_file_sha256, field="invalidation file"
        ),
        "invalidation_payload_sha256": invalid["payload_sha256"],
        "history": deepcopy(parent["history"]),
        "execution_order": deepcopy(parent["execution_order"]),
        "common_method_policy_sha256": parent["common_method_policy_sha256"],
        "inherited_gate_projection_sha256": payload_sha256(inherited),
        "runs": {
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
        },
        "private_cache": {
            "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
            "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
            "reportable_contents": False,
        },
        "source_sha256": sources,
        "authority": {
            "preflight_authorized": True,
            "live_execution_authorized": False,
            "pilot_execution_authorized": False,
        },
    }
    return verify_s4_retry_contract(
        {**body, "contract_sha256": payload_sha256(body)}
    )


def verify_s4_retry_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    contract = _mapping(value, label="S4 retry contract")
    stored = contract.pop("contract_sha256", None)
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "attempt_id",
        "parent_contract_file_sha256",
        "parent_contract_sha256",
        "invalidation_file_sha256",
        "invalidation_payload_sha256",
        "history",
        "execution_order",
        "common_method_policy_sha256",
        "inherited_gate_projection_sha256",
        "runs",
        "private_cache",
        "source_sha256",
        "authority",
    }
    if set(contract) != expected_fields or stored != payload_sha256(contract):
        raise ValueError("S4 retry contract shape or hash drift")
    if (
        contract.get("schema_version") != SCHEMA
        or contract.get("stage") != "S4"
        or contract.get("status") != "RETRY_EXECUTION_IDENTITY_FROZEN"
        or contract.get("history")
        != {
            "data_role": "DEVELOPMENT_EXPOSED",
            "episode_count": 49,
            "history_id": "07741c45",
        }
        or contract.get("execution_order")
        != ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
    ):
        raise ValueError("S4 retry contract identity drift")
    attempt = contract.get("attempt_id")
    if not isinstance(attempt, str) or not re.fullmatch(r"\d{3}", attempt):
        raise ValueError("S4 retry attempt identity drift")
    number = int(attempt)
    if number < 4:
        raise ValueError("S4 retry attempt predates invalidation recovery")
    for field in (
        "parent_contract_file_sha256",
        "parent_contract_sha256",
        "invalidation_file_sha256",
        "invalidation_payload_sha256",
        "common_method_policy_sha256",
        "inherited_gate_projection_sha256",
    ):
        _sha(contract.get(field), field=field)
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
    if contract.get("runs") != expected_runs:
        raise ValueError("S4 retry run identity drift")
    if contract.get("private_cache") != {
        "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
        "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
        "reportable_contents": False,
    }:
        raise ValueError("S4 retry private-cache identity drift")
    sources = _mapping(contract.get("source_sha256"), label="S4 retry sources")
    if set(sources) != {"retry_contract", "test"}:
        raise ValueError("S4 retry source inventory drift")
    for name, source_sha in sources.items():
        _sha(source_sha, field=f"source {name}")
    if contract.get("authority") != {
        "preflight_authorized": True,
        "live_execution_authorized": False,
        "pilot_execution_authorized": False,
    }:
        raise ValueError("S4 retry authority drift")
    return {**contract, "contract_sha256": stored}


def finalize_s4_retry_contract(
    *, path: Path, contract: Mapping[str, Any]
) -> dict[str, Any]:
    verified = verify_s4_retry_contract(contract)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(verified, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return verified

