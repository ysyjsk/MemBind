"""Offline-only S5 method registry and qualification plan.

The plan deliberately distinguishes reusable production fragments from a
qualified S5 entry point.  In particular, the historical M2 prototype is not
promoted to M* merely by hashing it; production journaling, an oracle-isolated
FX0 adapter, exact parity, and method smoke remain required.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .s3_native_v2_freeze import (
    NativeBaselineV2FreezeError,
    verify_native_baseline_v2_freeze,
)
from .s4_revised_offline_gate import (
    RevisedS4OfflineGateError,
    verify_revised_s4_offline_gate,
)


SCHEMA = "membind.paper-eval-v3.s5-method-qualification-plan.v1"
RUN_ID = "s5-method-qualification-plan-20260815-001"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METHODS = ["A0", "P*", "M*"]
_SOURCE_NAMES = {
    "common_runtime",
    "native_entrypoint",
    "a0_scheduler",
    "a0_live_adapter",
    "a0_durable_store",
    "p_scheduler",
    "p_live_adapter",
    "p_invariant_checker",
    "m_candidate_core",
    "m_ordered_binder",
    "m_semantic_compile",
    "fx0_harness",
    "s5_plan_source",
    "s5_plan_test",
    "s5_plan_finalizer",
    "s5_workplan",
}
_EVIDENCE_KEYS = {"red", "focused_green", "full_green"}
_JUNIT_KEYS = {"junit_file_sha256", "tests", "failures", "errors", "skipped"}
_HISTORY = {
    "data_role": "DEVELOPMENT_EXPOSED",
    "history_id": "07741c45",
    "episode_count": 49,
    "selection_rule": "PREDECLARED_DEVELOPMENT_EXPOSED_SMOKE_HISTORY",
    "quality_claim_authorized": False,
}
_COMMON_RUNTIME = {
    "same_native_graphiti_construction_base": True,
    "synthetic_graphiti_substitution_allowed": False,
    "u0_builder": "native_characterization_runtime.build_u0_graphiti_from_env",
    "native_add_episode_entrypoint": "graphiti_native.add_episode",
    "graphiti_version": "0.29.3",
    "graphiti_repository_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
    "forbidden_runtime_changes": [
        "PROMPT_OR_RESPONSE_REPLAY",
        "CANDIDATE_STABILIZER",
        "SEMANTIC_CACHE_MODIFICATION",
        "RESOLUTION_OR_INVALIDATION_MODIFICATION",
        "RETRIEVAL_READER_OR_JUDGE_CHANGE",
    ],
}
_METHOD_REGISTRY = {
    "A0": {
        "method_id": "A0",
        "candidate_status": "REUSABLE_COMPONENTS_ENTRYPOINT_NOT_FROZEN",
        "production_identity_status": "NONE_FROZEN",
        "concurrency": 1,
        "scheduler": "FIFO_DURABLE_ENQUEUE_SINGLE_NATIVE_WORKER",
        "construction_path": "SAME_U0_GRAPHITI_ADD_EPISODE",
        "reusable_source_roles": [
            "common_runtime",
            "native_entrypoint",
            "a0_scheduler",
            "a0_live_adapter",
            "a0_durable_store",
        ],
        "required_checks": [
            "FIFO",
            "SINGLE_WORKER",
            "DURABLE_ACK_BEFORE_CALLER_RETURN",
            "SOURCE_ORDER_PUBLICATION",
            "CALLER_RETURN_AND_PUBLICATION_TIMESTAMPS_DISTINCT",
            "ZERO_LOST_DUPLICATE_OR_DIRECT_INVARIANT_VIOLATION",
        ],
    },
    "P*": {
        "method_id": "P*",
        "candidate_status": "C2_SMOKE_ENTRYPOINT_NOT_FROZEN",
        "production_identity_status": "NONE_FROZEN",
        "concurrency": 2,
        "scheduler": "NAIVE_WHOLE_UPDATE_PARALLEL",
        "construction_path": "CONCURRENT_COMPLETE_U0_GRAPHITI_ADD_EPISODE",
        "direct_invariant_violation_interpretation": (
            "SCIENTIFIC_OUTCOME_NOT_ADAPTER_FAILURE"
        ),
        "reusable_source_roles": [
            "common_runtime",
            "native_entrypoint",
            "p_scheduler",
            "p_live_adapter",
            "p_invariant_checker",
        ],
        "required_checks": [
            "TWO_WHOLE_UPDATE_INTERVALS_OVERLAP",
            "INTENT_AND_PUBLICATION_ACCOUNTING",
            "TELEMETRY_COMPLETE",
            "VIOLATION_CHECKER_ACTIVE",
            "NO_SILENT_DELETION_OF_VIOLATING_RESULT",
        ],
    },
    "M*": {
        "method_id": "M*",
        "candidate_status": "EXPLORATORY_CORE_NOT_PRODUCTION",
        "production_identity_status": "NONE_FROZEN",
        "concurrency": 2,
        "scheduler": "PARALLEL_PREPARE_LATEST_STATE_BIND_ORDERED_PUBLICATION",
        "construction_path": "CANDIDATE_GRAPHITI_MEMBIND_CORE",
        "reusable_source_roles": [
            "common_runtime",
            "native_entrypoint",
            "m_candidate_core",
            "m_ordered_binder",
            "m_semantic_compile",
            "fx0_harness",
        ],
        "fx0_exact_parity_required": True,
        "durable_publication_journal_required": True,
        "same_core_for_live_and_fx0_required": True,
        "graphiti_private_api_signature_binding_required": True,
        "required_checks": [
            "COMPLETE_CONSTRUCTION",
            "ZERO_DIRECT_INVARIANT_VIOLATION",
            "CORRECT_FRESHNESS_TIMESTAMPS",
            "ZERO_HIDDEN_FALLBACK",
            "FX0_PRODUCTION_PATH_EXACT_PARITY",
            "RETRY_IDEMPOTENCE_AND_PARTIAL_PUBLICATION_DETECTION",
        ],
    },
}
_TDD_SEQUENCE = [
    "RED_METHOD_REGISTRY",
    "FOCUSED_GREEN_METHOD_REGISTRY",
    "FULL_OFFLINE_GREEN",
    "FX0_PRODUCTION_IDENTITY_AND_ADAPTER_GREEN",
    "ONLY_THEN_METHOD_LIVE_AUTHORITY",
]
_STOP_RULES = [
    "A0_SMOKE_FAILURE_STOPS_S5",
    "P_C2_INFRA_ADAPTER_TELEMETRY_OR_ACCOUNTING_FAILURE_STOPS_S5",
    "M_STAR_FX0_OR_DIRECT_INVARIANT_FAILURE_STOPS_S5",
    "NO_C_SWEEP_BEFORE_M_STAR_SMOKE_PASS",
    "NO_LIVE_ACTION_FROM_THIS_OFFLINE_PLAN",
]
_AUTHORITY = {
    "s5_offline_design_authorized": True,
    "s5_adapter_implementation_authorized": True,
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_LEGACY = {
    "legacy_d0_authority_inheritance_allowed": False,
    "legacy_d0_result_merge_allowed": False,
    "legacy_s4_namespace_reuse_allowed": False,
    "retry_008_resume_allowed": False,
    "retry_009_allowed": False,
}
_FORBIDDEN_KEYS = {
    "api_key",
    "credential",
    "messages",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "secret",
}


class S5MethodQualificationError(ValueError):
    """The S5 registry, evidence, identity, or authority boundary is invalid."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise S5MethodQualificationError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise S5MethodQualificationError(f"{label} must be a lowercase SHA256")
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
        raise S5MethodQualificationError(f"{label} envelope shape drift")
    payload = _mapping(artifact.get("payload"), label=f"{label} payload")
    if (
        artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise S5MethodQualificationError(f"{label} is not finalized")
    artifact["payload"] = payload
    return artifact, payload


def _junit(value: object, *, label: str, expected_red: bool) -> dict[str, Any]:
    evidence = _mapping(value, label=label)
    if set(evidence) != _JUNIT_KEYS:
        raise S5MethodQualificationError(f"{label} JUnit shape drift")
    _sha(evidence.get("junit_file_sha256"), label=f"{label} JUnit file")
    counts: dict[str, int] = {}
    for name in ("tests", "failures", "errors", "skipped"):
        count = evidence.get(name)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise S5MethodQualificationError(f"{label} {name} is invalid")
        counts[name] = count
    if counts["tests"] < 1 or counts["skipped"]:
        raise S5MethodQualificationError(f"{label} is incomplete")
    if expected_red:
        if counts["failures"] + counts["errors"] < 1:
            raise S5MethodQualificationError(f"{label} is not RED")
    elif counts["failures"] or counts["errors"]:
        raise S5MethodQualificationError(f"{label} is not GREEN")
    return evidence


def _evidence(value: object) -> dict[str, Any]:
    evidence = _mapping(value, label="S5 offline evidence")
    if set(evidence) != _EVIDENCE_KEYS:
        raise S5MethodQualificationError("S5 offline evidence inventory drift")
    red = _junit(evidence["red"], label="S5 RED", expected_red=True)
    focused = _junit(
        evidence["focused_green"], label="S5 focused GREEN", expected_red=False
    )
    full = _junit(
        evidence["full_green"], label="S5 full GREEN", expected_red=False
    )
    if focused["tests"] < 12 or full["tests"] < 900:
        raise S5MethodQualificationError("S5 GREEN evidence is too small")
    return {"red": red, "focused_green": focused, "full_green": full}


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise S5MethodQualificationError("S5 plan contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _verify_gate(value: object) -> dict[str, Any]:
    try:
        return verify_revised_s4_offline_gate(
            _mapping(value, label="revised S4 gate")
        )
    except (RevisedS4OfflineGateError, ValueError) as exc:
        raise S5MethodQualificationError("revised S4 gate is invalid") from exc


def _verify_freeze(value: object) -> dict[str, Any]:
    try:
        return verify_native_baseline_v2_freeze(
            _mapping(value, label="Native baseline freeze")
        )
    except (NativeBaselineV2FreezeError, ValueError) as exc:
        raise S5MethodQualificationError("Native baseline freeze is invalid") from exc


def _input_bindings(
    *,
    parent_protocol_file_sha256: str,
    s4_gate: Mapping[str, Any],
    s4_gate_file_sha256: str,
    current_stage_pointer: Mapping[str, Any],
    current_stage_pointer_file_sha256: str,
    native_baseline_freeze: Mapping[str, Any],
    native_baseline_freeze_file_sha256: str,
    role_registry: Mapping[str, Any],
    role_registry_file_sha256: str,
) -> dict[str, Any]:
    current, current_payload = _sealed(
        current_stage_pointer, label="current stage pointer"
    )
    roles, roles_payload = _sealed(role_registry, label="role registry")
    gate = _verify_gate(s4_gate)
    freeze = _verify_freeze(native_baseline_freeze)
    gate_payload = gate["payload"]
    freeze_payload = freeze["payload"]
    role_map = roles_payload.get("roles")
    if (
        current_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or gate_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or gate_payload.get("authority", {}).get("s5_offline_design_authorized")
        is not True
        or gate_payload.get("authority", {}).get("s5_live_execution_authorized")
        is not False
        or freeze_payload.get("baseline_id") != "native-graphiti-u0-reader-v2"
        or freeze_payload.get("stage") != "S3"
        or not isinstance(role_map, Mapping)
        or _HISTORY["history_id"] not in role_map.get("DEVELOPMENT_EXPOSED", [])
    ):
        raise S5MethodQualificationError("S5 input authority or role drift")

    contract_bindings = gate_payload["real_workload_correctness"]["contract"][
        "payload"
    ]["input_bindings"]
    parent_sha = _sha(parent_protocol_file_sha256, label="parent protocol file")
    current_file_sha = _sha(
        current_stage_pointer_file_sha256, label="current pointer file"
    )
    freeze_file_sha = _sha(
        native_baseline_freeze_file_sha256, label="Native freeze file"
    )
    roles_file_sha = _sha(role_registry_file_sha256, label="role registry file")
    if (
        contract_bindings["parent_protocol"]["file_sha256"] != parent_sha
        or contract_bindings["current_stage_pointer"]["file_sha256"]
        != current_file_sha
        or contract_bindings["native_baseline_freeze"]["file_sha256"]
        != freeze_file_sha
        or contract_bindings["native_baseline_freeze"]["payload_sha256"]
        != freeze["payload_sha256"]
        or contract_bindings["role_registry"]["file_sha256"] != roles_file_sha
        or contract_bindings["role_registry"]["payload_sha256"]
        != roles["payload_sha256"]
    ):
        raise S5MethodQualificationError("S5 inputs do not match revised S4 gate")

    return {
        "parent_protocol": {"file_sha256": parent_sha},
        "s4_revised_offline_gate": {
            "file_sha256": _sha(s4_gate_file_sha256, label="revised S4 gate file"),
            "payload_sha256": gate["payload_sha256"],
            "run_id": gate["run_id"],
            "status": gate_payload["status"],
        },
        "current_stage_pointer": {
            "file_sha256": current_file_sha,
            "payload_sha256": current["payload_sha256"],
            "run_id": current["run_id"],
            "current_stage": current_payload["current_stage"],
        },
        "native_baseline_freeze": {
            "file_sha256": freeze_file_sha,
            "payload_sha256": freeze["payload_sha256"],
            "run_id": freeze["run_id"],
            "baseline_id": freeze_payload["baseline_id"],
        },
        "role_registry": {
            "file_sha256": roles_file_sha,
            "payload_sha256": roles["payload_sha256"],
            "run_id": roles["run_id"],
            "selected_history_role": "DEVELOPMENT_EXPOSED",
        },
    }


def build_s5_method_qualification_plan(
    *,
    parent_protocol_file_sha256: str,
    s4_gate_artifact: Mapping[str, Any],
    s4_gate_file_sha256: str,
    current_stage_pointer: Mapping[str, Any],
    current_stage_pointer_file_sha256: str,
    native_baseline_freeze: Mapping[str, Any],
    native_baseline_freeze_file_sha256: str,
    role_registry: Mapping[str, Any],
    role_registry_file_sha256: str,
    source_file_sha256: Mapping[str, str],
    offline_evidence: Mapping[str, Mapping[str, Any]],
    git_commit: str,
) -> dict[str, Any]:
    """Build the offline plan without promoting any method or granting live I/O."""

    if not isinstance(git_commit, str) or not git_commit:
        raise S5MethodQualificationError("git commit is required")
    sources = _mapping(source_file_sha256, label="S5 source inventory")
    if set(sources) != _SOURCE_NAMES:
        raise S5MethodQualificationError("S5 source inventory drift")
    sources = {name: _sha(sources[name], label=name) for name in sorted(sources)}
    payload = {
        "schema_version": SCHEMA,
        "stage": "S5_METHOD_QUALIFICATION",
        "status": "OFFLINE_DESIGN_ONLY",
        "current_stage": "S3_CONFIGURATION_FROZEN",
        "methods": list(_METHODS),
        "execution_order": list(_METHODS),
        "method_id_aliases_forbidden": ["M0", "M1", "M2"],
        "history": deepcopy(_HISTORY),
        "common_runtime": deepcopy(_COMMON_RUNTIME),
        "method_registry": deepcopy(_METHOD_REGISTRY),
        "required_production_work": [
            "S5_A0_THIN_ENTRYPOINT_AND_DURABLE_STORE",
            "S5_P_C2_THIN_ENTRYPOINT_AND_INVARIANT_ACCOUNTING",
            "M_STAR_DURABLE_INTENT_COMMIT_PUBLICATION_JOURNAL",
            "M_STAR_FAILURE_POISON_AND_IDEMPOTENT_RETRY",
            "M_STAR_SHARED_LIVE_AND_FX0_PRODUCTION_CORE",
            "M_STAR_LOGICAL_TIME_AND_EPISODE_CREATION_PARITY",
            "M_STAR_GROUP_DATABASE_SEMANTICS_PARITY",
            "M_STAR_GRAPHITI_PRIVATE_API_SIGNATURE_BINDING",
            "S5_FX0_PRODUCTION_PARITY_ARTIFACT",
            "METHOD_SPECIFIC_SINGLE_USE_LIVE_AUTHORITIES",
        ],
        "tdd_sequence": list(_TDD_SEQUENCE),
        "stop_rules": list(_STOP_RULES),
        "input_bindings": _input_bindings(
            parent_protocol_file_sha256=parent_protocol_file_sha256,
            s4_gate=s4_gate_artifact,
            s4_gate_file_sha256=s4_gate_file_sha256,
            current_stage_pointer=current_stage_pointer,
            current_stage_pointer_file_sha256=current_stage_pointer_file_sha256,
            native_baseline_freeze=native_baseline_freeze,
            native_baseline_freeze_file_sha256=native_baseline_freeze_file_sha256,
            role_registry=role_registry,
            role_registry_file_sha256=role_registry_file_sha256,
        ),
        "source_file_sha256": sources,
        "offline_evidence": _evidence(offline_evidence),
        "legacy_boundary": deepcopy(_LEGACY),
        "next_action": "S5_ADAPTER_IMPLEMENTATION_AND_OFFLINE_TESTS",
        "authority": deepcopy(_AUTHORITY),
    }
    _reject_private(payload)
    return verify_s5_method_qualification_plan(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=RUN_ID,
        )
    )


def verify_s5_method_qualification_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Reject method promotion, incomplete evidence, or live authority drift."""

    artifact, payload = _sealed(value, label="S5 method qualification plan")
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "current_stage",
        "methods",
        "execution_order",
        "method_id_aliases_forbidden",
        "history",
        "common_runtime",
        "method_registry",
        "required_production_work",
        "tdd_sequence",
        "stop_rules",
        "input_bindings",
        "source_file_sha256",
        "offline_evidence",
        "legacy_boundary",
        "next_action",
        "authority",
    }
    if (
        set(payload) != expected_fields
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("run_id") != RUN_ID
        or payload.get("schema_version") != SCHEMA
        or payload.get("stage") != "S5_METHOD_QUALIFICATION"
        or payload.get("status") != "OFFLINE_DESIGN_ONLY"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("methods") != _METHODS
        or payload.get("execution_order") != _METHODS
        or payload.get("method_id_aliases_forbidden") != ["M0", "M1", "M2"]
        or payload.get("history") != _HISTORY
        or payload.get("common_runtime") != _COMMON_RUNTIME
        or payload.get("method_registry") != _METHOD_REGISTRY
        or payload.get("tdd_sequence") != _TDD_SEQUENCE
        or payload.get("stop_rules") != _STOP_RULES
        or payload.get("legacy_boundary") != _LEGACY
        or payload.get("next_action")
        != "S5_ADAPTER_IMPLEMENTATION_AND_OFFLINE_TESTS"
        or payload.get("authority") != _AUTHORITY
    ):
        raise S5MethodQualificationError("S5 plan identity or policy drift")
    required_work = payload.get("required_production_work")
    if not isinstance(required_work, list) or set(required_work) != {
        "S5_A0_THIN_ENTRYPOINT_AND_DURABLE_STORE",
        "S5_P_C2_THIN_ENTRYPOINT_AND_INVARIANT_ACCOUNTING",
        "M_STAR_DURABLE_INTENT_COMMIT_PUBLICATION_JOURNAL",
        "M_STAR_FAILURE_POISON_AND_IDEMPOTENT_RETRY",
        "M_STAR_SHARED_LIVE_AND_FX0_PRODUCTION_CORE",
        "M_STAR_LOGICAL_TIME_AND_EPISODE_CREATION_PARITY",
        "M_STAR_GROUP_DATABASE_SEMANTICS_PARITY",
        "M_STAR_GRAPHITI_PRIVATE_API_SIGNATURE_BINDING",
        "S5_FX0_PRODUCTION_PARITY_ARTIFACT",
        "METHOD_SPECIFIC_SINGLE_USE_LIVE_AUTHORITIES",
    }:
        raise S5MethodQualificationError("S5 required production work drift")
    bindings = _mapping(payload.get("input_bindings"), label="S5 input bindings")
    if set(bindings) != {
        "parent_protocol",
        "s4_revised_offline_gate",
        "current_stage_pointer",
        "native_baseline_freeze",
        "role_registry",
    }:
        raise S5MethodQualificationError("S5 input binding inventory drift")
    for name, binding in bindings.items():
        selected = _mapping(binding, label=f"S5 input binding {name}")
        _sha(selected.get("file_sha256"), label=f"S5 input binding {name}")
        if name != "parent_protocol":
            _sha(
                selected.get("payload_sha256"),
                label=f"S5 input binding {name} payload",
            )
    if (
        bindings["s4_revised_offline_gate"].get("status")
        != "OFFLINE_FRAMEWORKS_QUALIFIED_ONLY"
        or bindings["current_stage_pointer"].get("current_stage")
        != "S3_CONFIGURATION_FROZEN"
        or bindings["native_baseline_freeze"].get("baseline_id")
        != "native-graphiti-u0-reader-v2"
        or bindings["role_registry"].get("selected_history_role")
        != "DEVELOPMENT_EXPOSED"
    ):
        raise S5MethodQualificationError("S5 input binding semantics drift")
    sources = _mapping(payload.get("source_file_sha256"), label="S5 source inventory")
    if set(sources) != _SOURCE_NAMES:
        raise S5MethodQualificationError("S5 source inventory drift")
    for name, digest in sources.items():
        _sha(digest, label=name)
    _evidence(payload.get("offline_evidence"))
    _reject_private(artifact)
    artifact["payload"] = payload
    return artifact
