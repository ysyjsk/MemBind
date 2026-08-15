"""Additive S3 configuration freeze for Native Graphiti plus Reader-v2.

This module never calls a model, database, or network service.  It preserves
the historical Gate-C and failed direct-Reader artifacts, then projects only
the sealed configuration identities required by later Native/MemBind runs.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .native_reader_v2_freeze import (
    ReaderV2FreezeError,
    verify_reader_v2_freeze,
)
from .native_reader_v2_qualification import (
    ReaderV2QualificationError,
    verify_reader_v2_contract,
)
from .s2_completion_contract import (
    S2CompletionContractError,
    validate_s2_completion_contract,
)
from .s2_completion_authority import (
    CompletionAuthorityError,
    verify_completion_policy_freeze,
)
from .s2_completion_identity import (
    CompletionIdentityError,
    validate_s2_completion_adapter_identity,
)


NATIVE_BASELINE_V2_FREEZE_SCHEMA = (
    "membind.paper-eval-v3.native-baseline-v2-freeze.v1"
)
_METHODS = {"U0", "A0", "P*", "M*"}
_ROLE_NAMES = {"DEVELOPMENT_EXPOSED", "PILOT", "FINAL_PAPER_TEST"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INPUT_FILE_NAMES = {
    "parent_workplan",
    "reader_v2_workplan",
    "s0_current_state",
    "s1_u0_smoke",
    "u0_qualification",
    "dataset_parity",
    "evaluator_parity",
    "direct_add_episode_contract",
    "completion_adapter_identity",
    "retrieval_contract",
    "retrieval_policy_freeze",
    "role_registry",
    "reader_v2_contract",
    "reader_v2_freeze",
}
_SOURCE_NAMES = {
    "finalize_script",
    "focused_green_preseal",
    "freeze_source",
    "freeze_test",
    "full_offline_green_preseal",
}
_PAYLOAD_BINDING_NAMES = {
    "s0_current_state",
    "s1_u0_smoke",
    "u0_qualification",
    "dataset_parity",
    "evaluator_parity",
    "direct_add_episode_contract",
    "completion_adapter_identity",
    "retrieval_contract",
    "retrieval_policy_freeze",
    "role_registry",
    "reader_v2_contract",
    "reader_v2_freeze",
}
_CRITICAL_SOURCE_NAMES = {
    "u0_runtime",
    "instrumentation",
    "dataset_builder",
    "direct_add_episode",
    "retrieval_adapter",
    "reader_v2",
    "judge",
    "official_evaluator",
    "s3_native_v2_freeze",
}
_UNSAFE_OUTPUT_KEYS = {
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


def _serialized_file_sha256(value: Mapping[str, Any]) -> str:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class NativeBaselineV2FreezeError(ValueError):
    """The Native-v2 S3 configuration cannot be frozen safely."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeBaselineV2FreezeError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise NativeBaselineV2FreezeError(f"{field} is not a SHA256")
    return value


def _hash_inventory(
    value: object, *, names: set[str], label: str
) -> dict[str, str]:
    selected = _mapping(value, label=label)
    if set(selected) != names:
        raise NativeBaselineV2FreezeError(f"{label} inventory drift")
    return {
        name: _sha(selected[name], field=f"{label} {name}")
        for name in sorted(selected)
    }


def _sealed(value: object, *, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = _mapping(value, label=label)
    payload = _mapping(artifact.get("payload"), label=f"{label} payload")
    if (
        set(artifact)
        != {
            "protocol_version",
            "git_commit",
            "run_id",
            "status",
            "payload",
            "payload_sha256",
        }
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise NativeBaselineV2FreezeError(f"{label} envelope is invalid")
    artifact["payload"] = payload
    return artifact, payload


def _validate_direct_contract(value: object) -> dict[str, Any]:
    contract = _mapping(value, label="direct add-episode contract")
    if set(contract) != {
        "contract_sha256",
        "namespace_field",
        "operation",
        "source",
        "source_sha256",
    }:
        raise NativeBaselineV2FreezeError("direct add-episode contract shape drift")
    stored = _sha(contract.pop("contract_sha256"), field="direct contract")
    if stored != payload_sha256(contract):
        raise NativeBaselineV2FreezeError("direct add-episode contract hash mismatch")
    if (
        contract.get("operation") != "graphiti.add_episode"
        or contract.get("namespace_field") != "group_id"
    ):
        raise NativeBaselineV2FreezeError("direct add-episode contract drift")
    _sha(contract.get("source_sha256"), field="direct add-episode source")
    return {**contract, "contract_sha256": stored}


def _validate_roles(
    value: object, *, expected_registry: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    artifact, payload = _sealed(value, label="role registry")
    registry = _mapping(payload.get("roles"), label="role registry roles")
    if set(registry) != _ROLE_NAMES:
        raise NativeBaselineV2FreezeError("role registry is incomplete")
    normalized: dict[str, list[str]] = {}
    all_ids: set[str] = set()
    for role in sorted(_ROLE_NAMES):
        identifiers = registry.get(role)
        if isinstance(identifiers, (str, bytes)) or not isinstance(
            identifiers, Sequence
        ):
            raise NativeBaselineV2FreezeError("role registry is invalid")
        selected = list(identifiers)
        if (
            any(not isinstance(item, str) or not item for item in selected)
            or len(selected) != len(set(selected))
            or all_ids.intersection(selected)
        ):
            raise NativeBaselineV2FreezeError("role registry overlap or duplicate")
        normalized[role] = selected
        all_ids.update(selected)
    if normalized != dict(expected_registry):
        raise NativeBaselineV2FreezeError("role binding drift")
    return artifact, normalized


def _validate_s1(
    *,
    smoke_value: object,
    qualification_value: object,
    input_files: Mapping[str, str],
    s0_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    smoke, smoke_payload = _sealed(smoke_value, label="S1 U0 smoke")
    coverage = _mapping(smoke_payload.get("coverage"), label="S1 U0 coverage")
    expected = smoke_payload.get("add_episode_call_count")
    integrity = _mapping(smoke_payload.get("integrity"), label="S1 U0 integrity")
    if (
        smoke_payload.get("stage") != "S1"
        or smoke_payload.get("method") != "U0"
        or smoke_payload.get("verdict") != "PASS"
        or smoke_payload.get("failure_count") != 0
        or smoke_payload.get("serial_source_order") is not True
    ):
        raise NativeBaselineV2FreezeError("S1 U0 smoke is not PASS")
    if (
        not isinstance(expected, int)
        or expected <= 0
        or any(coverage.get(field) != expected for field in ("expected", "intents", "published"))
        or coverage.get("lost") != []
        or coverage.get("duplicates") != []
        or any(value is False for value in integrity.values())
        or any(isinstance(value, int) and value != 0 for value in integrity.values() if not isinstance(value, bool))
    ):
        raise NativeBaselineV2FreezeError("S1 U0 coverage or integrity is incomplete")

    qualification, qualification_payload = _sealed(
        qualification_value, label="U0 qualification"
    )
    expected_bindings = {
        "s0_current_state_sha256": input_files["s0_current_state"],
        "s1_artifact_sha256": input_files["s1_u0_smoke"],
        "dataset_parity_sha256": input_files["dataset_parity"],
        "evaluator_parity_sha256": input_files["evaluator_parity"],
        "direct_add_episode_contract_sha256": input_files[
            "direct_add_episode_contract"
        ],
    }
    if (
        qualification_payload.get("stage") != "S2"
        or qualification_payload.get("method") != "U0"
        or qualification_payload.get("verdict") != "PASS"
        or qualification_payload.get("authorization")
        != "AUTHORIZE_S2_U0_1_HISTORY"
        or any(
            qualification_payload.get(field) != expected_value
            for field, expected_value in expected_bindings.items()
        )
        or qualification_payload.get("s1_run_id") != smoke.get("run_id")
        or qualification_payload.get("history_id") != smoke_payload.get("history_id")
        or qualification_payload.get("namespace") != smoke_payload.get("namespace")
        or qualification_payload.get("episode_count") != expected
        or qualification_payload.get("s1_checkpoint_sha256")
        != smoke_payload.get("checkpoint_sha256")
        or qualification_payload.get("s1_events_sha256")
        != smoke_payload.get("events_sha256")
        or any(
            value is not True
            for value in _mapping(
                qualification_payload.get("checks"),
                label="U0 qualification checks",
            ).values()
        )
        or qualification_payload.get("runtime_identity_sha256")
        != payload_sha256(s0_payload.get("runtime_identities"))
    ):
        raise NativeBaselineV2FreezeError("U0 qualification binding drift")
    return smoke, smoke_payload, qualification, qualification_payload


def _reject_unsafe_output(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _UNSAFE_OUTPUT_KEYS:
                raise NativeBaselineV2FreezeError(
                    "Native-v2 freeze contains secret or raw content"
                )
            _reject_unsafe_output(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_unsafe_output(child)


def build_native_baseline_v2_freeze(
    *,
    s0_current_state: Mapping[str, Any],
    s1_u0_smoke: Mapping[str, Any],
    u0_qualification: Mapping[str, Any],
    dataset_parity: Mapping[str, Any],
    evaluator_parity: Mapping[str, Any],
    direct_add_episode_contract: Mapping[str, Any],
    completion_adapter_identity: Mapping[str, Any],
    retrieval_contract: Mapping[str, Any],
    retrieval_policy_freeze: Mapping[str, Any],
    role_registry: Mapping[str, Any],
    reader_v2_contract: Mapping[str, Any],
    reader_v2_freeze: Mapping[str, Any],
    input_file_sha256: Mapping[str, str],
    source_sha256: Mapping[str, str],
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    """Build the configuration-only S3 freeze from already sealed evidence."""

    input_files = _hash_inventory(
        input_file_sha256, names=_INPUT_FILE_NAMES, label="input file hashes"
    )
    sources = _hash_inventory(
        source_sha256, names=_SOURCE_NAMES, label="source hashes"
    )
    s0, s0_payload = _sealed(s0_current_state, label="S0 current state")
    if s0_payload.get("stage") != "S0":
        raise NativeBaselineV2FreezeError("S0 current state stage drift")
    runtime = _mapping(
        s0_payload.get("runtime_identities"), label="runtime identities"
    )
    required_runtime = {"graphiti", "construction", "embedding", "neo4j"}
    if not required_runtime.issubset(runtime):
        raise NativeBaselineV2FreezeError("runtime identities are incomplete")
    source_state = _mapping(s0_payload.get("source_hashes"), label="S0 sources")
    if input_files["parent_workplan"] != source_state.get("protocol"):
        raise NativeBaselineV2FreezeError("parent workplan binding drift")

    smoke, smoke_payload, qualification, qualification_payload = _validate_s1(
        smoke_value=s1_u0_smoke,
        qualification_value=u0_qualification,
        input_files=input_files,
        s0_payload=s0_payload,
    )
    dataset_artifact, dataset_payload = _sealed(
        dataset_parity, label="dataset parity"
    )
    evaluator_artifact, evaluator_payload = _sealed(
        evaluator_parity, label="evaluator parity"
    )
    if (
        dataset_payload.get("verdict") != "PASS"
        or dataset_payload.get("mismatch_count") != 0
        or dataset_payload.get("source_sha256") != source_state.get("dataset")
    ):
        raise NativeBaselineV2FreezeError("dataset parity is not PASS")
    if (
        evaluator_payload.get("verdict") != "PASS"
        or evaluator_payload.get("mismatch_count") != 0
    ):
        raise NativeBaselineV2FreezeError("evaluator parity is not PASS")

    direct_contract = _validate_direct_contract(direct_add_episode_contract)
    if (
        qualification_payload.get("direct_add_episode_contract_sha256")
        != input_files["direct_add_episode_contract"]
    ):
        raise NativeBaselineV2FreezeError("direct add-episode binding drift")
    try:
        adapter_identity = validate_s2_completion_adapter_identity(
            completion_adapter_identity
        )
    except CompletionIdentityError as error:
        raise NativeBaselineV2FreezeError(
            f"completion adapter identity: {error}"
        ) from error
    try:
        completion_contract = validate_s2_completion_contract(retrieval_contract)
    except S2CompletionContractError as error:
        raise NativeBaselineV2FreezeError(f"retrieval policy: {error}") from error
    retrieval = completion_contract["retrieval_policy"]

    try:
        policy_artifact = verify_completion_policy_freeze(retrieval_policy_freeze)
    except CompletionAuthorityError as error:
        raise NativeBaselineV2FreezeError(
            f"retrieval policy freeze: {error}"
        ) from error
    policy_payload = policy_artifact["payload"]
    if (
        policy_payload.get("status") != "FROZEN"
        or policy_payload.get("retrieval_policy_selected") is not True
        or policy_payload.get("r0_numeric_score_used_for_policy_choice") is not False
        or policy_payload.get("candidate_score_search_performed") is not False
        or policy_payload.get("diagnostic_only") is not False
        or policy_payload.get("s3_authorized") is not False
        or policy_payload.get("contract_file_sha256")
        != input_files["retrieval_contract"]
        or policy_payload.get("contract_sha256")
        != completion_contract["contract_sha256"]
        or policy_payload.get("adapter_identity_file_sha256")
        != input_files["completion_adapter_identity"]
        or policy_payload.get("adapter_identity_sha256")
        != adapter_identity["identity_sha256"]
    ):
        raise NativeBaselineV2FreezeError("retrieval policy freeze binding drift")

    expected_registry = completion_contract["role_binding"]["registry"]
    role_artifact, roles = _validate_roles(
        role_registry, expected_registry=expected_registry
    )
    if (
        completion_contract["role_binding"]["role_artifact_sha256"]
        != input_files["role_registry"]
        or completion_contract["role_binding"]["role_payload_sha256"]
        != role_artifact["payload_sha256"]
    ):
        raise NativeBaselineV2FreezeError("role binding drift")

    try:
        reader_contract = verify_reader_v2_contract(reader_v2_contract)
    except ReaderV2QualificationError as error:
        raise NativeBaselineV2FreezeError(
            f"Reader-v2 contract: {error}"
        ) from error
    try:
        reader_freeze = verify_reader_v2_freeze(reader_v2_freeze)
    except ReaderV2FreezeError as error:
        raise NativeBaselineV2FreezeError(f"Reader-v2 freeze: {error}") from error
    reader_payload = reader_freeze["payload"]
    if (
        reader_contract.get("retrieval_policy_file_sha256")
        != input_files["retrieval_policy_freeze"]
    ):
        raise NativeBaselineV2FreezeError(
            "Reader-v2 contract retrieval policy binding drift"
        )
    if (
        reader_contract.get("judge_identity_sha256")
        != input_files["completion_adapter_identity"]
        or reader_contract.get("reader_config_sha256")
        != reader_payload.get("reader_config_sha256")
        or reader_contract.get("contract_sha256")
        != reader_payload.get("contract_sha256")
        or reader_payload.get("judge_identity_sha256")
        != input_files["completion_adapter_identity"]
        or reader_payload.get("s3_configuration_update_authorized") is not True
        or reader_payload.get("quality_gate_used") is not False
        or reader_payload.get("qualification_scope")
        != "ADAPTER_COMPATIBILITY_ONLY"
        or reader_payload.get("source_sha256", {}).get("contract_file")
        != input_files["reader_v2_contract"]
    ):
        raise NativeBaselineV2FreezeError("Reader-v2 freeze binding drift")
    if (
        input_files["reader_v2_workplan"]
        != reader_payload.get("source_sha256", {}).get("workplan")
        or reader_contract.get("source_sha256", {}).get("workplan")
        != input_files["reader_v2_workplan"]
    ):
        raise NativeBaselineV2FreezeError("Reader-v2 workplan binding drift")
    if (
        reader_contract.get("source_sha256", {}).get("parent_workplan")
        != input_files["parent_workplan"]
    ):
        raise NativeBaselineV2FreezeError("parent workplan binding drift")

    serialized_inputs = {
        "s0_current_state": s0_current_state,
        "s1_u0_smoke": s1_u0_smoke,
        "u0_qualification": u0_qualification,
        "dataset_parity": dataset_parity,
        "evaluator_parity": evaluator_parity,
        "direct_add_episode_contract": direct_add_episode_contract,
        "completion_adapter_identity": completion_adapter_identity,
        "retrieval_contract": retrieval_contract,
        "retrieval_policy_freeze": retrieval_policy_freeze,
        "role_registry": role_registry,
        "reader_v2_contract": reader_v2_contract,
        "reader_v2_freeze": reader_v2_freeze,
    }
    for name, value in serialized_inputs.items():
        if _serialized_file_sha256(value) != input_files[name]:
            raise NativeBaselineV2FreezeError(
                f"input file hash mismatch: {name}"
            )

    runtime_projection = {
        name: deepcopy(runtime[name]) for name in sorted(required_runtime)
    }
    native_construction = {
        **runtime_projection,
        "runtime_identity_sha256": payload_sha256(runtime),
        "runtime_identity_evidence_scope": (
            "DECLARED_EXPECTED_CONFIGURATION_NOT_CURRENT_LIVE_ATTESTATION"
        ),
        "construction_revision_evidence": "CONFLICT_DISCLOSED",
        "declared_construction_repository_revision": runtime[
            "construction"
        ].get("repository_revision"),
        "bound_runtime_source_revision": runtime.get("c2_identity_note", {}).get(
            "c2_model_revision"
        ),
        "s4_live_preflight_required": True,
        "direct_add_episode_contract_sha256": direct_contract["contract_sha256"],
        "s1_run_id": smoke.get("run_id"),
        "history_id": smoke_payload.get("history_id"),
        "namespace": smoke_payload.get("namespace"),
        "episode_count": smoke_payload.get("add_episode_call_count"),
        "serial_source_order": True,
        "s1_checkpoint_sha256": smoke_payload.get("checkpoint_sha256"),
        "s1_events_sha256": smoke_payload.get("events_sha256"),
    }
    common_policy = {
        "dataset_sha256": dataset_payload["source_sha256"],
        "dataset_parity_payload_sha256": dataset_artifact["payload_sha256"],
        "evaluator_parity_payload_sha256": evaluator_artifact["payload_sha256"],
        "retrieval_policy_name": retrieval["policy_name"],
        "retrieval_surface": retrieval["retrieval_surface"],
        "retrieval_method": retrieval["retrieval_method"],
        "retrieval_search_recipe": retrieval["search_recipe"],
        "retrieval_top_k": retrieval["top_k"],
        "retrieval_top_k_unit": retrieval["top_k_unit"],
        "retrieval_candidate_limit": retrieval["candidate_limit"],
        "retrieval_config_sha256": retrieval["configuration_sha256"],
        "retrieval_implementation_source_sha256": retrieval[
            "implementation_source_sha256"
        ],
        "retrieval_contract_sha256": completion_contract["contract_sha256"],
        "retrieval_policy_freeze_payload_sha256": policy_artifact[
            "payload_sha256"
        ],
        "reader_config_sha256": reader_payload["reader_config_sha256"],
        "reader_contract_sha256": reader_contract["contract_sha256"],
        "judge_identity_sha256": reader_payload["judge_identity_sha256"],
        "judge_component_config_sha256": adapter_identity["judge_config_sha256"],
        "judge_transport_config_sha256": reader_payload["judge_config_sha256"],
        "role_registry_payload_sha256": role_artifact["payload_sha256"],
    }
    common_policy_sha = payload_sha256(common_policy)
    critical_sources = {
        "u0_runtime": _sha(source_state.get("u0_runtime_source"), field="U0 runtime source"),
        "instrumentation": _sha(source_state.get("instrumentation_source"), field="instrumentation source"),
        "dataset_builder": _sha(dataset_payload.get("episode_builder_source_sha256"), field="dataset builder source"),
        "direct_add_episode": direct_contract["source_sha256"],
        "retrieval_adapter": retrieval["implementation_source_sha256"],
        "reader_v2": reader_payload["source_sha256"]["reader_source"],
        "judge": completion_contract["judge_identity"]["implementation_source_sha256"],
        "official_evaluator": _sha(source_state.get("official_evaluator_vendor"), field="official evaluator source"),
        "s3_native_v2_freeze": sources["freeze_source"],
    }
    payload_bindings = {
        "s0_current_state": s0["payload_sha256"],
        "s1_u0_smoke": smoke["payload_sha256"],
        "u0_qualification": qualification["payload_sha256"],
        "dataset_parity": dataset_artifact["payload_sha256"],
        "evaluator_parity": evaluator_artifact["payload_sha256"],
        "direct_add_episode_contract": direct_contract["contract_sha256"],
        "completion_adapter_identity": adapter_identity["identity_sha256"],
        "retrieval_contract": completion_contract["contract_sha256"],
        "retrieval_policy_freeze": policy_artifact["payload_sha256"],
        "role_registry": role_artifact["payload_sha256"],
        "reader_v2_contract": reader_contract["contract_sha256"],
        "reader_v2_freeze": reader_freeze["payload_sha256"],
    }
    payload = {
        "schema_version": NATIVE_BASELINE_V2_FREEZE_SCHEMA,
        "stage": "S3",
        "status": "PASS",
        "baseline_id": "native-graphiti-u0-reader-v2",
        "configuration_change_scope": (
            "READER_ONLY_RELATIVE_TO_S2_COMPLETION_POLICY_FREEZE"
        ),
        "historical_gate_c_implementation_untouched": True,
        "configuration_freeze_only": True,
        "s2_quality_pass_claimed": False,
        "quality_estimate_status": "NOT_ESTIMATED",
        "native_construction": native_construction,
        "common_evaluation_policy": common_policy,
        "method_policy_bindings": {
            method: common_policy_sha for method in sorted(_METHODS)
        },
        "role_registry_snapshot": roles,
        "critical_source_sha256": dict(sorted(critical_sources.items())),
        "methodology": {
            "prior_direct_failure_preserved": True,
            "reader_v2_selection_not_blinded": True,
            "reader_change_motivated_by_observed_failure": True,
            "retrieval_or_top_k_candidate_search": False,
            "retrieval_numeric_score_used_for_selection": False,
            "canary_qa_used_as_selection_gate": False,
            "compatibility_only_evidence": True,
        },
        "authority": {
            "native_configuration_frozen": True,
            "next_offline_stage": "S4",
            "pilot_execution_authorized": False,
            "s4_live_execution_authorized": False,
        },
        "input_file_sha256": input_files,
        "input_payload_sha256": dict(sorted(payload_bindings.items())),
        "source_sha256": sources,
    }
    _reject_unsafe_output(payload)
    return verify_native_baseline_v2_freeze(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )


def verify_native_baseline_v2_freeze(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed on any drift in a serialized Native-v2 S3 freeze."""

    artifact, body = _sealed(value, label="Native-v2 freeze")
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "baseline_id",
        "configuration_change_scope",
        "historical_gate_c_implementation_untouched",
        "configuration_freeze_only",
        "s2_quality_pass_claimed",
        "quality_estimate_status",
        "native_construction",
        "common_evaluation_policy",
        "method_policy_bindings",
        "role_registry_snapshot",
        "critical_source_sha256",
        "methodology",
        "authority",
        "input_file_sha256",
        "input_payload_sha256",
        "source_sha256",
    }
    if set(body) != expected_fields:
        raise NativeBaselineV2FreezeError("Native-v2 freeze payload shape drift")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or body.get("schema_version") != NATIVE_BASELINE_V2_FREEZE_SCHEMA
        or body.get("stage") != "S3"
        or body.get("status") != "PASS"
        or body.get("baseline_id") != "native-graphiti-u0-reader-v2"
        or body.get("configuration_change_scope")
        != "READER_ONLY_RELATIVE_TO_S2_COMPLETION_POLICY_FREEZE"
        or body.get("historical_gate_c_implementation_untouched") is not True
        or body.get("configuration_freeze_only") is not True
        or body.get("s2_quality_pass_claimed") is not False
        or body.get("quality_estimate_status") != "NOT_ESTIMATED"
    ):
        raise NativeBaselineV2FreezeError("Native-v2 freeze semantics drift")

    common_policy = _mapping(
        body.get("common_evaluation_policy"), label="common evaluation policy"
    )
    required_common_fields = {
        "dataset_sha256",
        "dataset_parity_payload_sha256",
        "evaluator_parity_payload_sha256",
        "retrieval_policy_name",
        "retrieval_surface",
        "retrieval_method",
        "retrieval_search_recipe",
        "retrieval_top_k",
        "retrieval_top_k_unit",
        "retrieval_candidate_limit",
        "retrieval_config_sha256",
        "retrieval_implementation_source_sha256",
        "retrieval_contract_sha256",
        "retrieval_policy_freeze_payload_sha256",
        "reader_config_sha256",
        "reader_contract_sha256",
        "judge_identity_sha256",
        "judge_component_config_sha256",
        "judge_transport_config_sha256",
        "role_registry_payload_sha256",
    }
    if set(common_policy) != required_common_fields:
        raise NativeBaselineV2FreezeError("common evaluation policy shape drift")
    for name, item in common_policy.items():
        if name.endswith("sha256"):
            _sha(item, field=f"common policy {name}")
    if (
        common_policy.get("retrieval_policy_name")
        != "graphiti-0.29.3-episode-bm25-session-v1"
        or common_policy.get("retrieval_surface") != "graphiti_episode_bm25"
        or common_policy.get("retrieval_method") != "Graphiti.search_"
        or common_policy.get("retrieval_search_recipe") != "EPISODE_BM25_RRF"
        or common_policy.get("retrieval_top_k") != 10
        or common_policy.get("retrieval_top_k_unit") != "unique_session"
        or common_policy.get("retrieval_candidate_limit") != 20
    ):
        raise NativeBaselineV2FreezeError("common retrieval policy drift")
    bindings = _mapping(body.get("method_policy_bindings"), label="method policy")
    common_sha = payload_sha256(common_policy)
    if set(bindings) != _METHODS or any(
        item != common_sha for item in bindings.values()
    ):
        raise NativeBaselineV2FreezeError("method policy binding drift")

    construction = _mapping(
        body.get("native_construction"), label="native construction"
    )
    runtime_names = {"graphiti", "construction", "embedding", "neo4j"}
    expected_construction_fields = runtime_names | {
        "runtime_identity_sha256",
        "runtime_identity_evidence_scope",
        "construction_revision_evidence",
        "declared_construction_repository_revision",
        "bound_runtime_source_revision",
        "s4_live_preflight_required",
        "direct_add_episode_contract_sha256",
        "s1_run_id",
        "history_id",
        "namespace",
        "episode_count",
        "serial_source_order",
        "s1_checkpoint_sha256",
        "s1_events_sha256",
    }
    if set(construction) != expected_construction_fields:
        raise NativeBaselineV2FreezeError("native construction shape drift")
    runtime_projection = {name: construction[name] for name in sorted(runtime_names)}
    # The S0 identity also contains diagnostic fields, so its stored digest is
    # bound as evidence rather than recomputed from this intentionally smaller projection.
    _sha(construction.get("runtime_identity_sha256"), field="runtime identity")
    _sha(
        construction.get("direct_add_episode_contract_sha256"),
        field="direct add-episode contract",
    )
    _sha(construction.get("s1_checkpoint_sha256"), field="S1 checkpoint")
    _sha(construction.get("s1_events_sha256"), field="S1 events")
    if (
        not all(isinstance(runtime_projection[name], Mapping) for name in runtime_names)
        or construction.get("serial_source_order") is not True
        or construction.get("runtime_identity_evidence_scope")
        != "DECLARED_EXPECTED_CONFIGURATION_NOT_CURRENT_LIVE_ATTESTATION"
        or construction.get("construction_revision_evidence")
        != "CONFLICT_DISCLOSED"
        or construction.get("s4_live_preflight_required") is not True
        or not isinstance(
            construction.get("declared_construction_repository_revision"), str
        )
        or not isinstance(construction.get("bound_runtime_source_revision"), str)
        or construction.get("declared_construction_repository_revision")
        == construction.get("bound_runtime_source_revision")
        or not isinstance(construction.get("episode_count"), int)
        or construction.get("episode_count", 0) <= 0
    ):
        raise NativeBaselineV2FreezeError("native construction semantics drift")

    roles = _mapping(
        body.get("role_registry_snapshot"), label="role registry snapshot"
    )
    if set(roles) != _ROLE_NAMES:
        raise NativeBaselineV2FreezeError("role registry shape drift")
    observed: set[str] = set()
    for role in sorted(_ROLE_NAMES):
        identifiers = roles[role]
        if (
            isinstance(identifiers, (str, bytes))
            or not isinstance(identifiers, Sequence)
            or any(not isinstance(item, str) or not item for item in identifiers)
            or len(identifiers) != len(set(identifiers))
            or observed.intersection(identifiers)
        ):
            raise NativeBaselineV2FreezeError("role registry semantics drift")
        observed.update(identifiers)

    critical_sources = _hash_inventory(
        body.get("critical_source_sha256"),
        names=_CRITICAL_SOURCE_NAMES,
        label="critical source hashes",
    )
    input_files = _hash_inventory(
        body.get("input_file_sha256"),
        names=_INPUT_FILE_NAMES,
        label="input file hashes",
    )
    payload_bindings = _hash_inventory(
        body.get("input_payload_sha256"),
        names=_PAYLOAD_BINDING_NAMES,
        label="input payload hashes",
    )
    sources = _hash_inventory(
        body.get("source_sha256"), names=_SOURCE_NAMES, label="source hashes"
    )
    if critical_sources["s3_native_v2_freeze"] != sources["freeze_source"]:
        raise NativeBaselineV2FreezeError("S3 source binding drift")
    if input_files["parent_workplan"] == input_files["reader_v2_workplan"]:
        raise NativeBaselineV2FreezeError("workplan identities collapsed")
    if payload_bindings["reader_v2_contract"] != common_policy[
        "reader_contract_sha256"
    ]:
        raise NativeBaselineV2FreezeError("Reader-v2 contract binding drift")

    methodology = _mapping(body.get("methodology"), label="methodology")
    expected_methodology = {
        "prior_direct_failure_preserved": True,
        "reader_v2_selection_not_blinded": True,
        "reader_change_motivated_by_observed_failure": True,
        "retrieval_or_top_k_candidate_search": False,
        "retrieval_numeric_score_used_for_selection": False,
        "canary_qa_used_as_selection_gate": False,
        "compatibility_only_evidence": True,
    }
    authority = _mapping(body.get("authority"), label="authority")
    expected_authority = {
        "native_configuration_frozen": True,
        "next_offline_stage": "S4",
        "pilot_execution_authorized": False,
        "s4_live_execution_authorized": False,
    }
    if methodology != expected_methodology or authority != expected_authority:
        raise NativeBaselineV2FreezeError("Native-v2 methodology or authority drift")
    _reject_unsafe_output(body)
    artifact["payload"] = body
    return artifact


def finalize_native_baseline_v2_freeze(
    *, path: Path, artifact: Mapping[str, Any]
) -> dict[str, Any]:
    """Persist one verified freeze with exclusive create and directory fsync."""

    verified = verify_native_baseline_v2_freeze(artifact)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(verified, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise NativeBaselineV2FreezeError(
            "Native-v2 freeze already exists"
        ) from None
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
