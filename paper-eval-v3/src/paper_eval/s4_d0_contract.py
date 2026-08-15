"""Pure S4 contract for one Native capture and deterministic read-only replay."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .s3_native_v2_freeze import (
    NativeBaselineV2FreezeError,
    verify_native_baseline_v2_freeze,
)


S4_D0_CONTRACT_SCHEMA = "membind.paper-eval-v3.s4-d0-contract.v1"
HISTORY_ID = "07741c45"
EPISODE_COUNT = 49
CACHE_ID = "s4-d0-07741c45-20260814-001"
CAPTURE_RUN_ID = "s4-d0-capture-20260814-001"
CAPTURE_NAMESPACE = "pev3-s4-u0-capture-20260814-001"
REPLAY_RUN_ID = "s4-d0-replay-20260814-001"
REPLAY_NAMESPACE = "pev3-s4-d0-replay-20260814-001"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAMES = {
    "canonicalizer",
    "embedding_oracle",
    "graphiti_d0_factory",
    "native_u0_runtime",
    "prompt_oracle",
    "s1_namespace_adapter",
    "s4_contract_source",
    "s4_contract_test",
}
_UNSAFE_KEYS = {
    "answer",
    "api_key",
    "content",
    "messages",
    "password",
    "prompt",
    "question",
    "raw_output",
    "secret",
}


class S4D0ContractError(ValueError):
    """The S4 deterministic-control contract is incomplete or has drifted."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise S4D0ContractError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise S4D0ContractError(f"{field} is not a SHA256")
    return value


def _sources(value: object) -> dict[str, str]:
    selected = _mapping(value, label="source inventory")
    if set(selected) != _SOURCE_NAMES:
        raise S4D0ContractError("source inventory drift")
    return {
        name: _sha(selected[name], field=f"source {name}")
        for name in sorted(selected)
    }


def _sealed_pointer(value: object) -> tuple[dict[str, Any], dict[str, Any]]:
    pointer = _mapping(value, label="current pointer")
    payload = _mapping(pointer.get("payload"), label="current pointer payload")
    if (
        set(pointer)
        != {
            "protocol_version",
            "git_commit",
            "run_id",
            "status",
            "payload",
            "payload_sha256",
        }
        or pointer.get("status") != "finalized"
        or pointer.get("payload_sha256") != payload_sha256(payload)
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("status") != "PASS_CONFIGURATION_FREEZE_ONLY"
        or payload.get("next_authorized_action")
        != "S4_OFFLINE_GATE_DESIGN_AND_TESTS"
        or payload.get("live_preflight_required") is not True
        or payload.get("s4_live_execution_authorized") is not False
        or payload.get("pilot_execution_authorized") is not False
    ):
        raise S4D0ContractError("current pointer is not S4-offline-only")
    pointer["payload"] = payload
    return pointer, payload


def _reject_unsafe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _UNSAFE_KEYS:
                raise S4D0ContractError("S4 contract contains secret or raw content")
            _reject_unsafe(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_unsafe(child)


def build_s4_d0_contract(
    *,
    native_baseline_v2_freeze: Mapping[str, Any],
    native_baseline_v2_freeze_file_sha256: str,
    current_pointer: Mapping[str, Any],
    s4_workplan_sha256: str,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Build the hash-bound offline contract without performing live I/O."""

    try:
        freeze = verify_native_baseline_v2_freeze(native_baseline_v2_freeze)
    except NativeBaselineV2FreezeError as error:
        raise S4D0ContractError(f"Native-v2 freeze: {error}") from error
    freeze_file_sha = _sha(
        native_baseline_v2_freeze_file_sha256,
        field="Native-v2 freeze file",
    )
    _, pointer_payload = _sealed_pointer(current_pointer)
    if (
        pointer_payload.get("native_baseline_v2_freeze_file_sha256")
        != freeze_file_sha
        or pointer_payload.get("native_baseline_v2_freeze_payload_sha256")
        != freeze["payload_sha256"]
    ):
        raise S4D0ContractError("current pointer Native-v2 binding drift")

    freeze_payload = freeze["payload"]
    construction = _mapping(
        freeze_payload.get("native_construction"), label="Native construction"
    )
    roles = _mapping(
        freeze_payload.get("role_registry_snapshot"), label="role snapshot"
    )
    if HISTORY_ID not in roles.get("DEVELOPMENT_EXPOSED", []):
        raise S4D0ContractError("S4 smoke history is not DEVELOPMENT_EXPOSED")
    if construction.get("episode_count") != EPISODE_COUNT:
        raise S4D0ContractError("S4 smoke episode-count drift")
    bindings = _mapping(
        freeze_payload.get("method_policy_bindings"), label="method bindings"
    )
    common_hashes = set(bindings.values())
    if set(bindings) != {"U0", "A0", "P*", "M*"} or len(common_hashes) != 1:
        raise S4D0ContractError("S3 common method-policy binding drift")
    common_policy_sha = next(iter(common_hashes))
    _sha(common_policy_sha, field="common method policy")

    source_hashes = _sources(source_sha256)
    if source_hashes["native_u0_runtime"] != freeze_payload[
        "critical_source_sha256"
    ]["u0_runtime"]:
        raise S4D0ContractError("Native U0 runtime source drift")

    parent_workplan_sha = _sha(
        freeze_payload["input_file_sha256"].get("parent_workplan"),
        field="parent workplan",
    )
    workplan_sha = _sha(s4_workplan_sha256, field="S4 workplan")
    body = {
        "schema_version": S4_D0_CONTRACT_SCHEMA,
        "parent_workplan_sha256": parent_workplan_sha,
        "s4_workplan_sha256": workplan_sha,
        "native_baseline_v2_freeze_file_sha256": freeze_file_sha,
        "native_baseline_v2_freeze_payload_sha256": freeze["payload_sha256"],
        "current_pointer_payload_sha256": current_pointer["payload_sha256"],
        "common_method_policy_sha256": common_policy_sha,
        "history": {
            "data_role": "DEVELOPMENT_EXPOSED",
            "episode_count": EPISODE_COUNT,
            "history_id": HISTORY_ID,
        },
        "execution_order": ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"],
        "runs": {
            "U0_CAPTURE": {
                "cache_id": CACHE_ID,
                "method": "U0",
                "mode": "capture",
                "namespace": CAPTURE_NAMESPACE,
                "run_id": CAPTURE_RUN_ID,
            },
            "D0_READ_ONLY_REPLAY": {
                "cache_id": CACHE_ID,
                "method": "D0",
                "mode": "replay",
                "namespace": REPLAY_NAMESPACE,
                "run_id": REPLAY_RUN_ID,
            },
        },
        "u0_capture": {
            "candidate_order_stabilization": False,
            "embedding_oracle_mode": "capture_empty_new",
            "live_embedding_calls_required": True,
            "live_llm_calls_required": True,
            "prior_cache_allowed": False,
            "prompt_oracle_mode": "capture_empty_new",
            "serial_add_episode": True,
        },
        "d0_replay": {
            "candidate_order_stabilizers": [
                "edge_search",
                "node_resolution",
                "edge_query",
                "node_query",
            ],
            "embedding_oracle_mode": "read_only",
            "live_embedding_fallback": False,
            "live_llm_fallback": False,
            "prompt_oracle_mode": "read_only",
            "serial_add_episode": True,
        },
        "hard_gates": {
            "capture_episode_coverage": "49/49_exactly_once_source_order",
            "capture_live_embedding_calls": "GREATER_THAN_ZERO",
            "capture_live_llm_calls": "GREATER_THAN_ZERO",
            "cache_mutation_during_replay": False,
            "canonical_graph_parity": "EXACT_100_PERCENT",
            "replay_cross_encoder_call_count": 0,
            "replay_episode_coverage": "49/49_exactly_once_source_order",
            "replay_live_embedding_calls": 0,
            "replay_live_fallback_count": 0,
            "replay_live_llm_calls": 0,
            "replay_oracle_miss_count": 0,
            "resolved_embedding_count": "CAPTURE_EQUALS_REPLAY",
            "resolved_prompt_count": "CAPTURE_EQUALS_REPLAY",
        },
        "canonical_comparison": {
            "artifact_only": True,
            "entity_group_id_projection": "__S4_ISOLATED_NAMESPACE__",
            "other_fields_normalized_beyond_existing_canonicalizer": False,
        },
        "namespace_policy": {
            "capture_and_replay_distinct": True,
            "cleanup_scope": "EXACT_GROUP_ID_ONLY",
            "fresh_before_first_mutation": True,
            "global_database_cleanup_allowed": False,
            "historical_s1_namespace_read_only": True,
        },
        "durability": {
            "checkpoint": "ATOMIC_REPLACE_FSYNC_AFTER_EACH_PUBLICATION",
            "event_log": "APPEND_ONLY_FLUSH_FSYNC",
            "failed_attempt_mergeable": False,
            "resume": "CONTIGUOUS_DURABLE_PREFIX_ONLY",
        },
        "preflight": {
            "construction_model": "qwen3-32b-fp8",
            "construction_revision_conflict_disclosed": True,
            "embedding_model": "qwen3-embedding-0.6b",
            "max_model_len_minimum": 65536,
            "neo4j_connectivity_required": True,
            "required_before_live_authority": True,
            "vllm_version": "0.26.0",
        },
        "authority": {
            "pilot_execution_authorized": False,
            "s4_live_execution_authorized": False,
            "s4_preflight_authorized": True,
        },
        "source_sha256": source_hashes,
    }
    _reject_unsafe(body)
    return verify_s4_d0_contract(
        {**body, "contract_sha256": payload_sha256(body)}
    )


def verify_s4_d0_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the exact serialized contract and its non-authorizing semantics."""

    contract = _mapping(value, label="S4 D0 contract")
    stored_hash = contract.pop("contract_sha256", None)
    expected_fields = {
        "schema_version",
        "parent_workplan_sha256",
        "s4_workplan_sha256",
        "native_baseline_v2_freeze_file_sha256",
        "native_baseline_v2_freeze_payload_sha256",
        "current_pointer_payload_sha256",
        "common_method_policy_sha256",
        "history",
        "execution_order",
        "runs",
        "u0_capture",
        "d0_replay",
        "hard_gates",
        "canonical_comparison",
        "namespace_policy",
        "durability",
        "preflight",
        "authority",
        "source_sha256",
    }
    if set(contract) != expected_fields:
        raise S4D0ContractError("S4 D0 contract shape drift")
    if (
        contract.get("schema_version") != S4_D0_CONTRACT_SCHEMA
        or not isinstance(stored_hash, str)
        or stored_hash != payload_sha256(contract)
    ):
        raise S4D0ContractError("S4 D0 contract hash or schema drift")
    for field in (
        "parent_workplan_sha256",
        "s4_workplan_sha256",
        "native_baseline_v2_freeze_file_sha256",
        "native_baseline_v2_freeze_payload_sha256",
        "current_pointer_payload_sha256",
        "common_method_policy_sha256",
    ):
        _sha(contract.get(field), field=field)

    expected_history = {
        "data_role": "DEVELOPMENT_EXPOSED",
        "episode_count": EPISODE_COUNT,
        "history_id": HISTORY_ID,
    }
    expected_runs = {
        "U0_CAPTURE": {
            "cache_id": CACHE_ID,
            "method": "U0",
            "mode": "capture",
            "namespace": CAPTURE_NAMESPACE,
            "run_id": CAPTURE_RUN_ID,
        },
        "D0_READ_ONLY_REPLAY": {
            "cache_id": CACHE_ID,
            "method": "D0",
            "mode": "replay",
            "namespace": REPLAY_NAMESPACE,
            "run_id": REPLAY_RUN_ID,
        },
    }
    expected_u0 = {
        "candidate_order_stabilization": False,
        "embedding_oracle_mode": "capture_empty_new",
        "live_embedding_calls_required": True,
        "live_llm_calls_required": True,
        "prior_cache_allowed": False,
        "prompt_oracle_mode": "capture_empty_new",
        "serial_add_episode": True,
    }
    expected_d0 = {
        "candidate_order_stabilizers": [
            "edge_search",
            "node_resolution",
            "edge_query",
            "node_query",
        ],
        "embedding_oracle_mode": "read_only",
        "live_embedding_fallback": False,
        "live_llm_fallback": False,
        "prompt_oracle_mode": "read_only",
        "serial_add_episode": True,
    }
    if (
        contract.get("history") != expected_history
        or contract.get("execution_order")
        != ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
        or contract.get("runs") != expected_runs
        or contract.get("u0_capture") != expected_u0
        or contract.get("d0_replay") != expected_d0
    ):
        raise S4D0ContractError("S4 D0 execution identity drift")

    expected_gates = {
        "capture_episode_coverage": "49/49_exactly_once_source_order",
        "capture_live_embedding_calls": "GREATER_THAN_ZERO",
        "capture_live_llm_calls": "GREATER_THAN_ZERO",
        "cache_mutation_during_replay": False,
        "canonical_graph_parity": "EXACT_100_PERCENT",
        "replay_cross_encoder_call_count": 0,
        "replay_episode_coverage": "49/49_exactly_once_source_order",
        "replay_live_embedding_calls": 0,
        "replay_live_fallback_count": 0,
        "replay_live_llm_calls": 0,
        "replay_oracle_miss_count": 0,
        "resolved_embedding_count": "CAPTURE_EQUALS_REPLAY",
        "resolved_prompt_count": "CAPTURE_EQUALS_REPLAY",
    }
    expected_namespace = {
        "capture_and_replay_distinct": True,
        "cleanup_scope": "EXACT_GROUP_ID_ONLY",
        "fresh_before_first_mutation": True,
        "global_database_cleanup_allowed": False,
        "historical_s1_namespace_read_only": True,
    }
    expected_canonical_comparison = {
        "artifact_only": True,
        "entity_group_id_projection": "__S4_ISOLATED_NAMESPACE__",
        "other_fields_normalized_beyond_existing_canonicalizer": False,
    }
    expected_durability = {
        "checkpoint": "ATOMIC_REPLACE_FSYNC_AFTER_EACH_PUBLICATION",
        "event_log": "APPEND_ONLY_FLUSH_FSYNC",
        "failed_attempt_mergeable": False,
        "resume": "CONTIGUOUS_DURABLE_PREFIX_ONLY",
    }
    expected_preflight = {
        "construction_model": "qwen3-32b-fp8",
        "construction_revision_conflict_disclosed": True,
        "embedding_model": "qwen3-embedding-0.6b",
        "max_model_len_minimum": 65536,
        "neo4j_connectivity_required": True,
        "required_before_live_authority": True,
        "vllm_version": "0.26.0",
    }
    expected_authority = {
        "pilot_execution_authorized": False,
        "s4_live_execution_authorized": False,
        "s4_preflight_authorized": True,
    }
    if (
        contract.get("hard_gates") != expected_gates
        or contract.get("canonical_comparison")
        != expected_canonical_comparison
        or contract.get("namespace_policy") != expected_namespace
        or contract.get("durability") != expected_durability
        or contract.get("preflight") != expected_preflight
        or contract.get("authority") != expected_authority
    ):
        raise S4D0ContractError("S4 D0 gate or authority drift")
    contract["source_sha256"] = _sources(contract.get("source_sha256"))
    _reject_unsafe(contract)
    return {**contract, "contract_sha256": stored_hash}


def finalize_s4_d0_contract(
    *, path: Path, contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Write the verified contract once without overwriting prior evidence."""

    verified = verify_s4_d0_contract(contract)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(verified, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise S4D0ContractError("S4 D0 contract already exists") from None
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
