"""Additive retry identity for S4's candidate-index replay amendment."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256
from .s4_d0_contract import verify_s4_d0_contract
from .s4_retry_contract import verify_s4_retry_contract


SCHEMA = "membind.paper-eval-v3.s4-remap-retry-contract.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAMES = {"candidate_oracle", "contract", "production", "runner", "test"}
_RUN_DATE = "20260815"


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _sources(value: Mapping[str, str]) -> dict[str, str]:
    selected = _mapping(value, label="S4 remap retry sources")
    if set(selected) != _SOURCE_NAMES:
        raise ValueError("S4 remap retry source inventory drift")
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
                raise ValueError("S4 remap retry contract contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _verify_diagnosis(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 replay diagnosis")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 replay diagnosis envelope drift")
    payload = _mapping(artifact.get("payload"), label="S4 diagnosis payload")
    diagnosis = _mapping(payload.get("diagnosis"), label="S4 diagnosis")
    expected_cleanup = {
        "scope": "EXACT_GROUP_ID_ONLY",
        "group_ids": ["pev3-s4-d0-replay-20260814-004"],
        "global_cleanup_used": False,
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
    }
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s4-replay-diagnosis.v1"
        or payload.get("run_id") != "s4-d0-replay-20260814-004"
        or payload.get("namespace") != "pev3-s4-d0-replay-20260814-004"
        or payload.get("status") != "INCOMPLETE_DIAGNOSED_NON_MERGEABLE"
        or payload.get("mergeable") is not False
        or payload.get("qualification_authorized") is not False
        or payload.get("s5_authorized") is not False
        or payload.get("pilot_execution_authorized") is not False
        or payload.get("cleanup") != expected_cleanup
        or diagnosis.get("classification")
        != "ORDER_ONLY_CANDIDATE_RENUMBERING_CONFIRMED"
        or diagnosis.get("prompt_name") != "dedupe_nodes.nodes"
        or diagnosis.get("candidate_set_equal") is not True
        or diagnosis.get("candidate_order_changed") is not True
        or diagnosis.get("stable_sort_exactly_reproduces_replay_hash") is not True
        or diagnosis.get("general_replay_requirement")
        != "CANDIDATE_ID_AWARE_RESPONSE_REMAP_OR_CAPTURE_ORDER_REPLAY"
    ):
        raise ValueError("S4 replay diagnosis is not the sealed order-only failure")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def _candidate_oracle_policy() -> dict[str, Any]:
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


def _hard_gates() -> dict[str, Any]:
    return {
        "candidate_oracle_resolution_accounting": (
            "EXACT_PLUS_REMAP_EQUALS_RESOLVED"
        ),
        "candidate_remap_breakdown": "NODE_PLUS_EDGE_EQUALS_TOTAL",
        "candidate_remap_rejection_count": 0,
        "canonical_graph_parity": "EXACT_100_PERCENT",
        "cache_mutation_during_replay": False,
    }


def build_s4_remap_retry_contract(
    *,
    parent_contract: Mapping[str, Any],
    parent_contract_file_sha256: str,
    prior_retry_contract: Mapping[str, Any],
    prior_retry_contract_file_sha256: str,
    diagnosis: Mapping[str, Any],
    diagnosis_file_sha256: str,
    amendment_file_sha256: str,
    attempt_number: int,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    parent = verify_s4_d0_contract(parent_contract)
    prior = verify_s4_retry_contract(prior_retry_contract)
    diagnosed = _verify_diagnosis(diagnosis)
    if prior.get("attempt_id") != "004":
        raise ValueError("S4 remap retry must follow retry-004")
    if not isinstance(attempt_number, int) or not 5 <= attempt_number <= 999:
        raise ValueError("S4 remap retry attempt must be in [5, 999]")
    attempt = f"{attempt_number:03d}"
    cache_id = f"s4-d0-remap-07741c45-{_RUN_DATE}-{attempt}"
    body = {
        "schema_version": SCHEMA,
        "stage": "S4",
        "status": "REMAP_RETRY_EXECUTION_IDENTITY_FROZEN",
        "attempt_id": attempt,
        "parent_contract_file_sha256": _sha(
            parent_contract_file_sha256, field="parent contract file"
        ),
        "parent_contract_sha256": parent["contract_sha256"],
        "prior_retry_contract_file_sha256": _sha(
            prior_retry_contract_file_sha256, field="prior retry contract file"
        ),
        "prior_retry_contract_sha256": prior["contract_sha256"],
        "diagnosis_file_sha256": _sha(
            diagnosis_file_sha256, field="diagnosis file"
        ),
        "diagnosis_payload_sha256": diagnosed["payload_sha256"],
        "amendment_file_sha256": _sha(
            amendment_file_sha256, field="amendment file"
        ),
        "history": deepcopy(parent["history"]),
        "execution_order": ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"],
        "common_method_policy_sha256": parent["common_method_policy_sha256"],
        "runs": {
            "U0_CAPTURE": {
                "cache_id": cache_id,
                "method": "U0",
                "mode": "capture",
                "namespace": f"pev3-s4-u0-capture-{_RUN_DATE}-{attempt}",
                "run_id": f"s4-d0-capture-{_RUN_DATE}-{attempt}",
            },
            "D0_READ_ONLY_REPLAY": {
                "cache_id": cache_id,
                "method": "D0",
                "mode": "replay",
                "namespace": f"pev3-s4-d0-replay-{_RUN_DATE}-{attempt}",
                "run_id": f"s4-d0-replay-{_RUN_DATE}-{attempt}",
            },
        },
        "private_cache": {
            "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
            "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
            "reportable_contents": False,
        },
        "candidate_oracle": _candidate_oracle_policy(),
        "remap_hard_gates": _hard_gates(),
        "source_sha256": _sources(source_sha256),
        "authority": {
            "preflight_authorized": True,
            "live_execution_authorized": False,
            "s4_four_history_qualification_authorized": False,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        },
    }
    return verify_s4_remap_retry_contract(
        {**body, "contract_sha256": payload_sha256(body)}
    )


def verify_s4_remap_retry_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    selected = _mapping(value, label="S4 remap retry contract")
    stored = selected.pop("contract_sha256", None)
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "attempt_id",
        "parent_contract_file_sha256",
        "parent_contract_sha256",
        "prior_retry_contract_file_sha256",
        "prior_retry_contract_sha256",
        "diagnosis_file_sha256",
        "diagnosis_payload_sha256",
        "amendment_file_sha256",
        "history",
        "execution_order",
        "common_method_policy_sha256",
        "runs",
        "private_cache",
        "candidate_oracle",
        "remap_hard_gates",
        "source_sha256",
        "authority",
    }
    if set(selected) != expected_fields or stored != payload_sha256(selected):
        raise ValueError("S4 remap retry contract shape or hash drift")
    attempt = selected.get("attempt_id")
    if (
        selected.get("schema_version") != SCHEMA
        or selected.get("stage") != "S4"
        or selected.get("status")
        != "REMAP_RETRY_EXECUTION_IDENTITY_FROZEN"
        or not isinstance(attempt, str)
        or re.fullmatch(r"\d{3}", attempt) is None
        or int(attempt) < 5
        or selected.get("history")
        != {
            "data_role": "DEVELOPMENT_EXPOSED",
            "episode_count": 49,
            "history_id": "07741c45",
        }
        or selected.get("execution_order")
        != ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
    ):
        raise ValueError("S4 remap retry identity drift")
    for field in (
        "parent_contract_file_sha256",
        "parent_contract_sha256",
        "prior_retry_contract_file_sha256",
        "prior_retry_contract_sha256",
        "diagnosis_file_sha256",
        "diagnosis_payload_sha256",
        "amendment_file_sha256",
        "common_method_policy_sha256",
    ):
        _sha(selected.get(field), field=field)
    cache_id = f"s4-d0-remap-07741c45-{_RUN_DATE}-{attempt}"
    expected_runs = {
        "U0_CAPTURE": {
            "cache_id": cache_id,
            "method": "U0",
            "mode": "capture",
            "namespace": f"pev3-s4-u0-capture-{_RUN_DATE}-{attempt}",
            "run_id": f"s4-d0-capture-{_RUN_DATE}-{attempt}",
        },
        "D0_READ_ONLY_REPLAY": {
            "cache_id": cache_id,
            "method": "D0",
            "mode": "replay",
            "namespace": f"pev3-s4-d0-replay-{_RUN_DATE}-{attempt}",
            "run_id": f"s4-d0-replay-{_RUN_DATE}-{attempt}",
        },
    }
    if selected.get("runs") != expected_runs:
        raise ValueError("S4 remap retry run identity drift")
    if selected.get("private_cache") != {
        "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
        "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
        "reportable_contents": False,
    }:
        raise ValueError("S4 remap retry cache identity drift")
    if selected.get("candidate_oracle") != _candidate_oracle_policy():
        raise ValueError("S4 candidate oracle policy drift")
    if selected.get("remap_hard_gates") != _hard_gates():
        raise ValueError("S4 candidate remap hard-gate drift")
    _sources(selected.get("source_sha256", {}))
    if selected.get("authority") != {
        "preflight_authorized": True,
        "live_execution_authorized": False,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }:
        raise ValueError("S4 remap retry authority drift")
    _reject_private(selected)
    return {**selected, "contract_sha256": stored}


def finalize_s4_remap_retry_contract(
    *, path: Path, contract: Mapping[str, Any]
) -> dict[str, Any]:
    verified = verify_s4_remap_retry_contract(contract)
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
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return verified
