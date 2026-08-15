"""Additive P* role clarification for real-workload correctness evidence.

The sealed S4 contracts use one hard-zero invariant rule for every method,
while the parent protocol deliberately keeps P* as an unsafe concurrency
baseline.  This module narrows only that method-role conflict: P* must retain
complete accounting and disclose treatment violations, but those observations
do not erase its performance record.  No existing artifact is rewritten and
no live or result-generation authority is granted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .s4_revised_offline_gate import (
    RevisedS4OfflineGateError,
    verify_revised_s4_offline_gate,
)
from .s5_method_qualification_plan import (
    S5MethodQualificationError,
    verify_s5_method_qualification_plan,
)


SCHEMA = "membind.paper-eval-v3.p-star-real-workload-role-amendment.v1"
RUN_ID = "p-star-real-workload-role-amendment-20260815-001"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAMES = {
    "amendment_source",
    "amendment_test",
    "amendment_document",
    "amendment_finalizer",
}
_EVIDENCE_FIELDS = {"junit_file_sha256", "tests", "failures", "errors", "skipped"}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization_header",
    "credential",
    "messages",
    "password",
    "prompt",
    "raw_content",
    "raw_output",
    "raw_response",
    "secret",
}

_SCOPE = {
    "existing_artifacts_rewritten": False,
    "sealed_s4_gate_preserved": True,
    "sealed_real_workload_contract_preserved": True,
    "supersedes_only": [
        "P_STAR_DIRECT_INVARIANT_ZERO_AS_PERFORMANCE_RECORD_MERGE_GATE",
        "P_STAR_TREATMENT_VIOLATION_AS_INFRASTRUCTURE_FAILURE",
    ],
}
_HARD_ZERO = {
    "methods": ["U0", "A0", "M*"],
    "required": {
        "episode_source_coverage": 1.0,
        "lost_episode_or_source_count": 0,
        "duplicate_episode_or_source_count": 0,
        "source_publication_order_violation_count": 0,
        "visibility_publication_violation_count": 0,
        "temporal_provenance_violation_count": 0,
    },
    "violation_effect": "RESULT_NON_MERGEABLE_FOR_METHOD",
}
_P_STAR_ROLE = {
    "method": "P*",
    "role": "NAIVE_WHOLE_UPDATE_PARALLEL_PERFORMANCE_CORRECTNESS_TRADEOFF_BASELINE",
    "all_methods_still_execute_real_graphiti": True,
    "input_accounting_coverage": 1.0,
    "telemetry_coverage": 1.0,
    "all_scheduled_sources_require_terminal_classification": True,
    "required_accounting": [
        "SCHEDULED_SOURCE_COUNT",
        "TERMINAL_SOURCE_CLASSIFICATION",
        "PUBLISHED_SOURCE_COUNT",
        "TREATMENT_FAILURE_COUNT",
        "LOST_SOURCE_COUNT",
        "DUPLICATE_SOURCE_COUNT",
        "WORK_VOLUME",
        "RETRY_AND_TRANSACTION_COUNTS",
        "EVENT_AND_CHECKPOINT_INTEGRITY",
    ],
    "treatment_induced_violation_policy": {
        "performance_record_retained": True,
        "silent_deletion_allowed": False,
        "reclassified_as_infrastructure_failure": False,
        "required_disclosure_metrics": [
            "LOST_SOURCE_COUNT",
            "DUPLICATE_SOURCE_COUNT",
            "SOURCE_PUBLICATION_ORDER_VIOLATION_COUNT",
            "VISIBILITY_PUBLICATION_VIOLATION_COUNT",
            "TEMPORAL_PROVENANCE_VIOLATION_COUNT",
            "SEMANTIC_GRAPH_DIFFERENCE_METRICS",
            "TRANSACTION_AND_METHOD_FAILURE_COUNT",
            "DRAIN_OR_CENSORING_STATUS",
        ],
    },
    "evidence_failure_policy": {
        "incomplete_accounting_or_telemetry": "NON_MERGEABLE_INFRASTRUCTURE_FAILURE",
        "corrupt_or_unverifiable_artifact": "NON_MERGEABLE_INFRASTRUCTURE_FAILURE",
        "treatment_induced_failure_with_complete_accounting": (
            "RETAIN_AS_SCIENTIFIC_OUTCOME"
        ),
    },
    "claim_boundary": {
        "performance_baseline_authorized": True,
        "semantics_preserving_claim_authorized": False,
        "correctness_equivalence_claim_authorized": False,
        "quality_equivalence_claim_authorized": False,
        "quality_non_inferiority_claim_authorized": False,
        "quality_measurements_if_executed": "DESCRIPTIVE_WITH_FULL_DISCLOSURE",
    },
}
_AUTHORITY = {
    "offline_additive_amendment_authorized": True,
    "result_generation_or_inspection_authorized": False,
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}


class PStarRoleAmendmentError(ValueError):
    """The additive role policy, evidence, or identity binding is invalid."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PStarRoleAmendmentError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PStarRoleAmendmentError(f"{label} must be a lowercase SHA256")
    return value


def _count(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PStarRoleAmendmentError(f"{label} must be a nonnegative integer")
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
        raise PStarRoleAmendmentError(f"{label} envelope shape drift")
    payload = _mapping(artifact.get("payload"), label=f"{label} payload")
    if artifact.get("status") != "finalized" or artifact.get(
        "payload_sha256"
    ) != payload_sha256(payload):
        raise PStarRoleAmendmentError(f"{label} is not finalized")
    artifact["payload"] = payload
    return artifact, payload


def _evidence(value: object, *, label: str, expected_red: bool) -> dict[str, Any]:
    evidence = _mapping(value, label=label)
    if set(evidence) != _EVIDENCE_FIELDS:
        raise PStarRoleAmendmentError(f"{label} shape drift")
    _sha(evidence.get("junit_file_sha256"), label=f"{label} JUnit")
    tests = _count(evidence.get("tests"), label=f"{label} tests")
    failures = _count(evidence.get("failures"), label=f"{label} failures")
    errors = _count(evidence.get("errors"), label=f"{label} errors")
    skipped = _count(evidence.get("skipped"), label=f"{label} skipped")
    if skipped:
        raise PStarRoleAmendmentError(f"{label} contains skipped tests")
    if expected_red:
        if tests < 1 or failures + errors < 1:
            raise PStarRoleAmendmentError(f"{label} is not an expected RED run")
    elif tests < 12 or failures or errors:
        raise PStarRoleAmendmentError(f"{label} is not a complete focused GREEN run")
    return evidence


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise PStarRoleAmendmentError(
                    "P* role amendment contains private runtime data"
                )
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _verified_s4(value: object) -> dict[str, Any]:
    try:
        artifact = verify_revised_s4_offline_gate(
            _mapping(value, label="revised S4 gate")
        )
    except (RevisedS4OfflineGateError, ValueError) as exc:
        raise PStarRoleAmendmentError("revised S4 gate is invalid") from exc
    payload = artifact["payload"]
    if (
        payload.get("status") != "OFFLINE_FRAMEWORKS_QUALIFIED_ONLY"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("authority", {}).get("s5_live_execution_authorized")
        is not False
        or payload.get("authority", {}).get("model_call_authorized") is not False
    ):
        raise PStarRoleAmendmentError("revised S4 gate boundary drift")
    return artifact


def _verified_s5(value: object) -> dict[str, Any]:
    try:
        artifact = verify_s5_method_qualification_plan(
            _mapping(value, label="S5 method qualification plan")
        )
    except (S5MethodQualificationError, ValueError) as exc:
        raise PStarRoleAmendmentError("S5 method qualification plan is invalid") from exc
    payload = artifact["payload"]
    if (
        payload.get("status") != "OFFLINE_DESIGN_ONLY"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("method_registry", {}).get("P*", {}).get("concurrency") != 2
        or "P_C2_INFRA_ADAPTER_TELEMETRY_OR_ACCOUNTING_FAILURE_STOPS_S5"
        not in payload.get("stop_rules", [])
        or payload.get("authority", {}).get("s5_live_execution_authorized")
        is not False
    ):
        raise PStarRoleAmendmentError("S5 P* role or authority drift")
    return artifact


def _current(value: object) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact, payload = _sealed(value, label="current stage pointer")
    if (
        payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("pilot_execution_authorized") is not False
        or payload.get("s4_live_execution_authorized") is not False
    ):
        raise PStarRoleAmendmentError("current stage pointer drift")
    return artifact, payload


def _bindings(
    *,
    parent_protocol_file_sha256: str,
    s4_revised_gate: object,
    s4_revised_gate_file_sha256: str,
    s5_method_plan: object,
    s5_method_plan_file_sha256: str,
    current_stage_pointer: object,
    current_stage_pointer_file_sha256: str,
) -> dict[str, Any]:
    parent_sha = _sha(parent_protocol_file_sha256, label="parent protocol")
    s4_file_sha = _sha(s4_revised_gate_file_sha256, label="revised S4 gate file")
    s5_file_sha = _sha(s5_method_plan_file_sha256, label="S5 plan file")
    current_file_sha = _sha(current_stage_pointer_file_sha256, label="current pointer file")
    s4 = _verified_s4(s4_revised_gate)
    s5 = _verified_s5(s5_method_plan)
    current, current_payload = _current(current_stage_pointer)

    s4_payload = s4["payload"]
    s5_payload = s5["payload"]
    s5_bindings = _mapping(s5_payload.get("input_bindings"), label="S5 bindings")
    s4_parent = (
        s4_payload.get("boundary_amendment", {})
        .get("artifact", {})
        .get("payload", {})
        .get("input_bindings", {})
        .get("parent_protocol", {})
    )
    embedded = (
        s4_payload.get("real_workload_correctness", {}).get("contract")
    )
    embedded_artifact, embedded_payload = _sealed(
        embedded, label="embedded real-workload correctness contract"
    )
    if (
        s4_parent.get("file_sha256") != parent_sha
        or s5_bindings.get("parent_protocol", {}).get("file_sha256") != parent_sha
        or s5_bindings.get("s4_revised_offline_gate", {}).get("file_sha256")
        != s4_file_sha
        or s5_bindings.get("s4_revised_offline_gate", {}).get("payload_sha256")
        != s4.get("payload_sha256")
        or s5_bindings.get("current_stage_pointer", {}).get("file_sha256")
        != current_file_sha
        or s5_bindings.get("current_stage_pointer", {}).get("payload_sha256")
        != current.get("payload_sha256")
        or embedded_payload.get("direct_invariants", {}).get(
            "source_publication_order_violation_count"
        )
        != 0
        or embedded_payload.get("methods") != ["U0", "A0", "P*", "M*"]
        or current_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
    ):
        raise PStarRoleAmendmentError("cross-artifact identity binding drift")
    return {
        "parent_protocol": {"file_sha256": parent_sha},
        "s4_revised_offline_gate": {
            "file_sha256": s4_file_sha,
            "payload_sha256": s4["payload_sha256"],
            "run_id": s4["run_id"],
            "status": s4_payload["status"],
        },
        "s5_method_qualification_plan": {
            "file_sha256": s5_file_sha,
            "payload_sha256": s5["payload_sha256"],
            "run_id": s5["run_id"],
            "status": s5_payload["status"],
        },
        "current_stage_pointer": {
            "file_sha256": current_file_sha,
            "payload_sha256": current["payload_sha256"],
            "run_id": current["run_id"],
            "current_stage": current_payload["current_stage"],
        },
        "sealed_real_workload_correctness_contract": {
            "container": "S4_REVISED_OFFLINE_GATE.json",
            "payload_sha256": embedded_artifact["payload_sha256"],
            "run_id": embedded_artifact["run_id"],
            "schema_version": embedded_payload["schema_version"],
            "status": "PRESERVED_WITH_P_STAR_ROLE_CLAUSE_SUPERSEDED_ONLY",
        },
    }


def build_p_star_real_workload_role_amendment(
    *,
    parent_protocol_file_sha256: str,
    s4_revised_gate: Mapping[str, Any],
    s4_revised_gate_file_sha256: str,
    s5_method_plan: Mapping[str, Any],
    s5_method_plan_file_sha256: str,
    current_stage_pointer: Mapping[str, Any],
    current_stage_pointer_file_sha256: str,
    source_file_sha256: Mapping[str, str],
    red_evidence: Mapping[str, Any],
    focused_green_evidence: Mapping[str, Any],
    git_commit: str,
) -> dict[str, Any]:
    """Seal the role clarification without authorizing any experiment."""

    if not isinstance(git_commit, str) or not git_commit:
        raise PStarRoleAmendmentError("git commit is required")
    bindings = _bindings(
        parent_protocol_file_sha256=parent_protocol_file_sha256,
        s4_revised_gate=s4_revised_gate,
        s4_revised_gate_file_sha256=s4_revised_gate_file_sha256,
        s5_method_plan=s5_method_plan,
        s5_method_plan_file_sha256=s5_method_plan_file_sha256,
        current_stage_pointer=current_stage_pointer,
        current_stage_pointer_file_sha256=current_stage_pointer_file_sha256,
    )
    sources = _mapping(source_file_sha256, label="source file inventory")
    if set(sources) != _SOURCE_NAMES:
        raise PStarRoleAmendmentError("source file inventory drift")
    for name, digest in sources.items():
        _sha(digest, label=name)
    payload = {
        "schema_version": SCHEMA,
        "stage": "S5_P_STAR_ROLE_AMENDMENT",
        "status": "ADDITIVE_ROLE_CLARIFICATION_FROZEN",
        "decision": (
            "P_STAR_PERFORMANCE_RECORD_RETENTION_WITH_MANDATORY_VIOLATION_DISCLOSURE"
        ),
        "current_stage": "S3_CONFIGURATION_FROZEN",
        "scope": deepcopy(_SCOPE),
        "hard_zero_merge_gate": deepcopy(_HARD_ZERO),
        "p_star_role": deepcopy(_P_STAR_ROLE),
        "input_bindings": bindings,
        "source_file_sha256": sources,
        "offline_evidence": {
            "red": _evidence(red_evidence, label="RED evidence", expected_red=True),
            "focused_green": _evidence(
                focused_green_evidence,
                label="focused GREEN evidence",
                expected_red=False,
            ),
        },
        "next_action": "S5_ADAPTER_IMPLEMENTATION_AND_OFFLINE_TESTS",
        "authority": deepcopy(_AUTHORITY),
    }
    _reject_private(payload)
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=RUN_ID,
    )
    return verify_p_star_real_workload_role_amendment(artifact)


def verify_p_star_real_workload_role_amendment(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Reject policy inflation, missing evidence, or private-data leakage."""

    artifact, payload = _sealed(value, label="P* role amendment")
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "decision",
        "current_stage",
        "scope",
        "hard_zero_merge_gate",
        "p_star_role",
        "input_bindings",
        "source_file_sha256",
        "offline_evidence",
        "next_action",
        "authority",
    }
    if (
        set(payload) != expected_fields
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("run_id") != RUN_ID
        or payload.get("schema_version") != SCHEMA
        or payload.get("stage") != "S5_P_STAR_ROLE_AMENDMENT"
        or payload.get("status") != "ADDITIVE_ROLE_CLARIFICATION_FROZEN"
        or payload.get("decision")
        != "P_STAR_PERFORMANCE_RECORD_RETENTION_WITH_MANDATORY_VIOLATION_DISCLOSURE"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("scope") != _SCOPE
        or payload.get("hard_zero_merge_gate") != _HARD_ZERO
        or payload.get("p_star_role") != _P_STAR_ROLE
        or payload.get("next_action")
        != "S5_ADAPTER_IMPLEMENTATION_AND_OFFLINE_TESTS"
        or payload.get("authority") != _AUTHORITY
    ):
        raise PStarRoleAmendmentError("P* role amendment policy drift")
    bindings = _mapping(payload.get("input_bindings"), label="input bindings")
    if set(bindings) != {
        "parent_protocol",
        "s4_revised_offline_gate",
        "s5_method_qualification_plan",
        "current_stage_pointer",
        "sealed_real_workload_correctness_contract",
    }:
        raise PStarRoleAmendmentError("input binding inventory drift")
    for name, binding in bindings.items():
        selected = _mapping(binding, label=f"input binding {name}")
        if name != "sealed_real_workload_correctness_contract":
            _sha(selected.get("file_sha256"), label=f"{name} file")
        if name != "parent_protocol":
            _sha(selected.get("payload_sha256"), label=f"{name} payload")
    if (
        bindings["s4_revised_offline_gate"].get("status")
        != "OFFLINE_FRAMEWORKS_QUALIFIED_ONLY"
        or bindings["s5_method_qualification_plan"].get("status")
        != "OFFLINE_DESIGN_ONLY"
        or bindings["current_stage_pointer"].get("current_stage")
        != "S3_CONFIGURATION_FROZEN"
        or bindings["sealed_real_workload_correctness_contract"].get("status")
        != "PRESERVED_WITH_P_STAR_ROLE_CLAUSE_SUPERSEDED_ONLY"
    ):
        raise PStarRoleAmendmentError("input binding semantics drift")
    sources = _mapping(payload.get("source_file_sha256"), label="source inventory")
    if set(sources) != _SOURCE_NAMES:
        raise PStarRoleAmendmentError("source inventory drift")
    for name, digest in sources.items():
        _sha(digest, label=name)
    evidence = _mapping(payload.get("offline_evidence"), label="offline evidence")
    if set(evidence) != {"red", "focused_green"}:
        raise PStarRoleAmendmentError("offline evidence inventory drift")
    _evidence(evidence["red"], label="RED evidence", expected_red=True)
    _evidence(
        evidence["focused_green"],
        label="focused GREEN evidence",
        expected_red=False,
    )
    _reject_private(artifact)
    artifact["payload"] = payload
    return artifact


__all__ = [
    "PStarRoleAmendmentError",
    "build_p_star_real_workload_role_amendment",
    "verify_p_star_real_workload_role_amendment",
]
