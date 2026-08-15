"""Offline contract for real-workload correctness evaluation.

The contract is intentionally a design surface, not a result artifact.  It
binds the frozen protocol/configuration identities and specifies the
measurements that future U0/A0/P*/M* real Graphiti runs must provide.  No
model, database, or result inspection is authorized by this module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .s4_validation_boundary_amendment import verify_s4_validation_boundary_amendment


SCHEMA = "membind.paper-eval-v3.real-workload-correctness-contract.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METHODS = ["U0", "A0", "P*", "M*"]
_ROLES = ["DEVELOPMENT_EXPOSED", "PILOT", "FINAL_PAPER_TEST"]
_FORBIDDEN_KEYS = {
    "api_key",
    "password",
    "secret",
    "prompt",
    "messages",
    "raw_output",
    "raw_response",
}


class RealWorkloadCorrectnessError(ValueError):
    """Raised when the offline contract or an identity binding drifts."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RealWorkloadCorrectnessError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RealWorkloadCorrectnessError(f"{label} must be a SHA256")
    return value


def _sealed(value: object, *, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = _mapping(value, label=label)
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise RealWorkloadCorrectnessError(f"{label} envelope shape drift")
    payload = _mapping(artifact["payload"], label=f"{label} payload")
    if artifact["status"] != "finalized" or artifact["payload_sha256"] != payload_sha256(payload):
        raise RealWorkloadCorrectnessError(f"{label} is not finalized")
    artifact["payload"] = payload
    return artifact, payload


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise RealWorkloadCorrectnessError("private data in correctness contract")
            _reject_private(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_private(child)


def _bind_artifact(
    value: Mapping[str, Any],
    file_sha256: str,
    *,
    label: str,
    expected_schema: str | None = None,
    expected_stage: str | None = None,
) -> dict[str, Any]:
    artifact, payload = _sealed(value, label=label)
    file_digest = _sha(file_sha256, label=f"{label} file")
    if expected_schema is not None and payload.get("schema_version") != expected_schema:
        raise RealWorkloadCorrectnessError(f"{label} schema drift")
    if expected_stage is not None and payload.get("stage") != expected_stage:
        raise RealWorkloadCorrectnessError(f"{label} stage drift")
    return {
        "path": label,
        "file_sha256": file_digest,
        "payload_sha256": artifact["payload_sha256"],
        "run_id": artifact["run_id"],
        "status": artifact["status"],
        "schema_version": payload.get("schema_version"),
    }


def _identity_bindings(
    *,
    parent_protocol_file_sha256: str,
    s4_amendment_document_file_sha256: str,
    s4_amendment_artifact: Mapping[str, Any],
    s4_amendment_artifact_file_sha256: str,
    current_stage_pointer: Mapping[str, Any],
    current_stage_pointer_file_sha256: str,
    role_registry: Mapping[str, Any],
    role_registry_file_sha256: str,
    dataset_parity: Mapping[str, Any],
    dataset_parity_file_sha256: str,
    evaluator_parity: Mapping[str, Any],
    evaluator_parity_file_sha256: str,
    native_baseline_freeze: Mapping[str, Any],
    native_baseline_freeze_file_sha256: str,
) -> dict[str, Any]:
    parent_sha = _sha(parent_protocol_file_sha256, label="parent protocol file")
    document_sha = _sha(
        s4_amendment_document_file_sha256, label="S4 amendment document file"
    )
    amendment_artifact, amendment_payload = _sealed(
        s4_amendment_artifact, label="S4 amendment artifact"
    )
    try:
        verify_s4_validation_boundary_amendment(amendment_artifact)
    except Exception as exc:  # normalize cross-lane failures at this boundary
        raise RealWorkloadCorrectnessError("S4 amendment is not valid") from exc
    if amendment_payload.get("authority", {}).get("s5_offline_design_authorized") is not True:
        raise RealWorkloadCorrectnessError("S4 amendment does not authorize offline S5 design")
    amendment_document = amendment_payload.get("input_bindings", {}).get("amendment_document", {})
    if amendment_document.get("file_sha256") != document_sha:
        raise RealWorkloadCorrectnessError("S4 amendment document binding drift")

    freeze_file_sha = _sha(
        native_baseline_freeze_file_sha256, label="native freeze file"
    )
    current_artifact, current_payload = _sealed(
        current_stage_pointer, label="current stage pointer"
    )
    if (
        current_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or current_payload.get("pilot_execution_authorized") is not False
        or current_payload.get("s4_live_execution_authorized") is not False
        or current_payload.get("native_baseline_v2_freeze_payload_sha256")
        != _sealed(native_baseline_freeze, label="native baseline freeze")[0]["payload_sha256"]
        or current_payload.get("native_baseline_v2_freeze_file_sha256")
        != freeze_file_sha
    ):
        raise RealWorkloadCorrectnessError("current stage pointer is not the frozen S3 pointer")
    current = {
        "path": "runtime/CURRENT_STAGE_STATUS.json",
        "file_sha256": _sha(current_stage_pointer_file_sha256, label="current pointer file"),
        "payload_sha256": current_artifact["payload_sha256"],
        "run_id": current_artifact["run_id"],
        "current_stage": current_payload["current_stage"],
    }

    freeze_artifact, freeze_payload = _sealed(
        native_baseline_freeze, label="native baseline freeze"
    )
    if (
        freeze_payload.get("stage") != "S3"
        or freeze_payload.get("status") != "PASS"
        or freeze_payload.get("baseline_id") != "native-graphiti-u0-reader-v2"
        or freeze_payload.get("authority", {}).get("pilot_execution_authorized") is not False
        or freeze_payload.get("authority", {}).get("s4_live_execution_authorized") is not False
    ):
        raise RealWorkloadCorrectnessError("native baseline freeze is not configuration-only")
    input_files = _mapping(freeze_payload.get("input_file_sha256"), label="native freeze inputs")
    if input_files.get("parent_workplan") != parent_sha:
        raise RealWorkloadCorrectnessError("parent protocol does not match native freeze")
    freeze = {
        "path": "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json",
        "file_sha256": freeze_file_sha,
        "payload_sha256": freeze_artifact["payload_sha256"],
        "run_id": freeze_artifact["run_id"],
        "baseline_id": freeze_payload["baseline_id"],
        "stage": freeze_payload["stage"],
    }

    role_artifact, role_payload = _sealed(role_registry, label="role registry")
    roles = role_payload.get("roles")
    if not isinstance(roles, Mapping) or sorted(roles) != sorted(_ROLES):
        raise RealWorkloadCorrectnessError("role registry role inventory drift")
    role = {
        "path": "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json",
        "file_sha256": _sha(role_registry_file_sha256, label="role registry file"),
        "payload_sha256": role_artifact["payload_sha256"],
        "run_id": role_artifact["run_id"],
        "roles": list(_ROLES),
        "development_exposed_history_ids": list(roles["DEVELOPMENT_EXPOSED"]),
    }
    dataset = _bind_artifact(
        dataset_parity,
        dataset_parity_file_sha256,
        label="artifacts/paper_eval/native/DATASET_PARITY.json",
    )
    evaluator = _bind_artifact(
        evaluator_parity,
        evaluator_parity_file_sha256,
        label="artifacts/paper_eval/native/EVALUATOR_PARITY.json",
    )
    common_policy = _mapping(
        freeze_payload.get("common_evaluation_policy"),
        label="native freeze common evaluation policy",
    )
    if (
        input_files.get("role_registry") != role["file_sha256"]
        or input_files.get("dataset_parity") != dataset["file_sha256"]
        or input_files.get("evaluator_parity") != evaluator["file_sha256"]
        or common_policy.get("role_registry_payload_sha256")
        != role["payload_sha256"]
        or common_policy.get("dataset_parity_payload_sha256")
        != dataset["payload_sha256"]
        or common_policy.get("evaluator_parity_payload_sha256")
        != evaluator["payload_sha256"]
    ):
        raise RealWorkloadCorrectnessError(
            "role, dataset, or evaluator identity does not match native freeze"
        )
    return {
        "parent_protocol": {
            "path": "../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md",
            "file_sha256": parent_sha,
        },
        "s4_amendment_document": {
            "path": "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md",
            "file_sha256": document_sha,
        },
        "s4_amendment_artifact": {
            "path": "artifacts/paper_eval/native/S4_VALIDATION_BOUNDARY_AMENDMENT.json",
            "file_sha256": _sha(s4_amendment_artifact_file_sha256, label="S4 amendment artifact file"),
            "payload_sha256": amendment_artifact["payload_sha256"],
            "run_id": amendment_artifact["run_id"],
        },
        "current_stage_pointer": current,
        "role_registry": role,
        "dataset_parity": dataset,
        "evaluator_parity": evaluator,
        "native_baseline_freeze": freeze,
    }


def _fixed_policy() -> dict[str, Any]:
    return {
        "methods": list(_METHODS),
        "execution_contract": {
            "all_methods_execute_real_graphiti": True,
            "synthetic_graphiti_substitution_allowed": False,
            "per_method_per_history_accounting_required": True,
            "result_merge_requires_all_direct_invariants": True,
            "real_workload_results_required_for_headline_performance": True,
        },
        "direct_invariants": {
            "episode_source_coverage": 1.0,
            "lost_episode_or_source_count": 0,
            "duplicate_episode_or_source_count": 0,
            "source_publication_order_violation_count": 0,
            "visibility_publication_violation_count": 0,
            "temporal_provenance_violation_count": 0,
            "scope": "PER_METHOD_PER_HISTORY_AND_AGGREGATE",
            "failure_policy": "FAIL_CLOSED_NON_MERGEABLE",
        },
        "semantic_graph_contract": {
            "matching_oracle_status": "MUST_FREEZE_BEFORE_RESULT_GENERATION_OR_INSPECTION",
            "matching_oracle_identity_required": True,
            "matching_oracle_may_not_be_tuned_after_results": True,
            "required_metrics": [
                "NODE_PRECISION",
                "NODE_RECALL",
                "EDGE_PRECISION",
                "EDGE_RECALL",
                "UNMATCHED_NODE_COUNT",
                "UNMATCHED_EDGE_COUNT",
                "TEMPORAL_DIFFERENCE_COUNT",
            ],
            "required_oracle_freeze_fields": [
                "NODE_CANONICALIZATION",
                "NODE_SIMILARITY",
                "NODE_MATCH_THRESHOLD",
                "NODE_ASSIGNMENT_AND_TIE_BREAK",
                "EDGE_ENDPOINT_AND_TYPE_MATCHING",
                "EDGE_ATTRIBUTE_MATCHING",
                "TEMPORAL_FIELD_COMPARISON",
                "MISSING_AND_EXTRA_ITEM_POLICY",
            ],
            "aggregate_graph_counts_are_descriptive_only": True,
            "aggregate_counts_can_establish_parity": False,
        },
        "quality_contract": {
            "metrics": ["EVIDENCE_RECALL_AT_10", "QA_ACCURACY"],
            "paired_per_history_analysis": True,
            "same_history_question_set_across_methods": True,
            "confidence_intervals_required": True,
            "freeze_timing": "BEFORE_RESULT_GENERATION_OR_INSPECTION",
            "required_preregistered_fields": [
                "ESTIMAND_PER_METRIC",
                "PAIRING_UNIT",
                "CONFIDENCE_LEVEL",
                "CI_METHOD",
                "RESAMPLING_UNIT_IF_APPLICABLE",
                "MULTIPLICITY_POLICY",
                "NON_INFERIORITY_MARGIN_PER_METRIC",
                "EQUIVALENCE_MARGIN_PER_METRIC",
                "MISSING_RESULT_POLICY",
            ],
            "non_inferiority_and_equivalence_are_distinct": True,
            "post_result_margin_selection_allowed": False,
        },
    }


_AUTHORITY = {
    "offline_s5_design_authorized": True,
    "result_generation_or_inspection_authorized": False,
    "model_call_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}

_LEGACY_D0 = {
    "authority_inheritance_allowed": False,
    "authority_reuse_allowed": False,
    "result_merge_allowed": False,
    "historical_evidence_may_be_cited_only_with_original_status": True,
}


def build_real_workload_correctness_contract(
    *,
    parent_protocol_file_sha256: str,
    s4_amendment_document_file_sha256: str,
    s4_amendment_artifact: Mapping[str, Any],
    s4_amendment_artifact_file_sha256: str,
    current_stage_pointer: Mapping[str, Any],
    current_stage_pointer_file_sha256: str,
    role_registry: Mapping[str, Any],
    role_registry_file_sha256: str,
    dataset_parity: Mapping[str, Any],
    dataset_parity_file_sha256: str,
    evaluator_parity: Mapping[str, Any],
    evaluator_parity_file_sha256: str,
    native_baseline_freeze: Mapping[str, Any],
    native_baseline_freeze_file_sha256: str,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    if not run_id or not git_commit:
        raise RealWorkloadCorrectnessError("run_id and git_commit are required")
    payload = {
        "schema_version": SCHEMA,
        "stage": "S4",
        "lane": "REAL_WORKLOAD_CORRECTNESS",
        "status": "OFFLINE_S5_DESIGN_ONLY",
        "methods": list(_METHODS),
        **_fixed_policy(),
        "input_bindings": _identity_bindings(
            parent_protocol_file_sha256=parent_protocol_file_sha256,
            s4_amendment_document_file_sha256=s4_amendment_document_file_sha256,
            s4_amendment_artifact=s4_amendment_artifact,
            s4_amendment_artifact_file_sha256=s4_amendment_artifact_file_sha256,
            current_stage_pointer=current_stage_pointer,
            current_stage_pointer_file_sha256=current_stage_pointer_file_sha256,
            role_registry=role_registry,
            role_registry_file_sha256=role_registry_file_sha256,
            dataset_parity=dataset_parity,
            dataset_parity_file_sha256=dataset_parity_file_sha256,
            evaluator_parity=evaluator_parity,
            evaluator_parity_file_sha256=evaluator_parity_file_sha256,
            native_baseline_freeze=native_baseline_freeze,
            native_baseline_freeze_file_sha256=native_baseline_freeze_file_sha256,
        ),
        "legacy_d0": deepcopy(_LEGACY_D0),
        "authority": deepcopy(_AUTHORITY),
    }
    return verify_real_workload_correctness_contract(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=run_id,
        )
    )


def verify_real_workload_correctness_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact, payload = _sealed(value, label="real-workload correctness contract")
    if artifact.get("protocol_version") != PROTOCOL_VERSION:
        raise RealWorkloadCorrectnessError("protocol version drift")
    expected = {
        "schema_version",
        "stage",
        "lane",
        "status",
        "methods",
        "execution_contract",
        "direct_invariants",
        "semantic_graph_contract",
        "quality_contract",
        "input_bindings",
        "legacy_d0",
        "authority",
    }
    if set(payload) != expected or payload.get("schema_version") != SCHEMA:
        raise RealWorkloadCorrectnessError("correctness contract shape drift")
    if (
        payload.get("stage") != "S4"
        or payload.get("lane") != "REAL_WORKLOAD_CORRECTNESS"
        or payload.get("status") != "OFFLINE_S5_DESIGN_ONLY"
        or payload.get("methods") != _METHODS
        or payload.get("execution_contract") != _fixed_policy()["execution_contract"]
        or payload.get("direct_invariants") != _fixed_policy()["direct_invariants"]
        or payload.get("semantic_graph_contract") != _fixed_policy()["semantic_graph_contract"]
        or payload.get("quality_contract") != _fixed_policy()["quality_contract"]
        or payload.get("legacy_d0") != _LEGACY_D0
        or payload.get("authority") != _AUTHORITY
    ):
        raise RealWorkloadCorrectnessError("correctness policy drift")
    bindings = _mapping(payload.get("input_bindings"), label="input bindings")
    required_bindings = {
        "parent_protocol",
        "s4_amendment_document",
        "s4_amendment_artifact",
        "current_stage_pointer",
        "role_registry",
        "dataset_parity",
        "evaluator_parity",
        "native_baseline_freeze",
    }
    if set(bindings) != required_bindings:
        raise RealWorkloadCorrectnessError("input binding inventory drift")
    for name, binding in bindings.items():
        _mapping(binding, label=f"input binding {name}")
        _sha(binding.get("file_sha256"), label=f"input binding {name} file")
        if name not in {"parent_protocol", "s4_amendment_document"}:
            _sha(binding.get("payload_sha256"), label=f"input binding {name} payload")
    expected_paths = {
        "parent_protocol": "../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md",
        "s4_amendment_document": "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md",
        "s4_amendment_artifact": "artifacts/paper_eval/native/S4_VALIDATION_BOUNDARY_AMENDMENT.json",
        "current_stage_pointer": "runtime/CURRENT_STAGE_STATUS.json",
        "role_registry": "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json",
        "dataset_parity": "artifacts/paper_eval/native/DATASET_PARITY.json",
        "evaluator_parity": "artifacts/paper_eval/native/EVALUATOR_PARITY.json",
        "native_baseline_freeze": "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json",
    }
    if any(bindings[name].get("path") != path for name, path in expected_paths.items()):
        raise RealWorkloadCorrectnessError("input binding path drift")
    if (
        bindings["current_stage_pointer"].get("current_stage")
        != "S3_CONFIGURATION_FROZEN"
        or bindings["native_baseline_freeze"].get("baseline_id")
        != "native-graphiti-u0-reader-v2"
        or bindings["native_baseline_freeze"].get("stage") != "S3"
        or bindings["role_registry"].get("roles") != _ROLES
    ):
        raise RealWorkloadCorrectnessError("input binding semantic identity drift")
    _sha(artifact.get("payload_sha256"), label="contract payload")
    _reject_private(artifact)
    return artifact
