"""Seal the additive S4 validation-boundary amendment.

This module is deliberately offline-only.  It preserves the exact retry-008
outcome, retires candidate-level cross-run replay as a qualification boundary,
and opens only design/test work for the replacement validation lanes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256


SCHEMA = "membind.paper-eval-v3.s4-validation-boundary-amendment.v1"
AMENDMENT_RUN_ID = "s4-validation-boundary-amendment-20260815-001"

PARENT_PROTOCOL_SHA256 = (
    "4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e"
)
LEGACY_WORKPLAN_SHA256 = (
    "bab29baec9d83dcb2ce4310e9694774d9efc0278f23a069a0a2716dea26d5c62"
)
CURRENT_STAGE_POINTER_SHA256 = (
    "3cb7edad4bab3ac6fe961a3d9e8768cbb962cf61cf946cb7e0015d74c0edc26d"
)

CAPTURE_RESULT_SHA256 = (
    "a11cf71a2e0086fc0783b319d34a59471bc15b103e89525b1fbd6fdd281e8da5"
)
CAPTURE_CHECKPOINT_SHA256 = (
    "5ad6bf25fb5f018484d2d6703ae3cc765303bc558913bb586d23713546c3246b"
)
CAPTURE_EVENTS_SHA256 = (
    "4ff12870ae5b2591123aff7fae056518ad394b8198c6821680961f58c868447c"
)
REPLAY_RESULT_SHA256 = (
    "246fd8224ea27b1855d34f2c25f9963d844abe63cfc87fee62069a16f739ee45"
)
REPLAY_CHECKPOINT_SHA256 = (
    "f28113181d8179b94b9a4de778be10ef3e8ab7eb1bf6173a4b53eb7e804e8287"
)
REPLAY_EVENTS_SHA256 = (
    "0f7835bbf9636623bdb6d858dc7dfc5c63638934c2bfc517e06cc4f409839b68"
)
EXECUTION_LOG_SHA256 = (
    "16764523fa62b59c0fd304ce8c6afa1a36191b5289d647701abf305839d71d53"
)
CANONICAL_GRAPH_SHA256 = (
    "ab076234fabef2b94bbd6d8a1815aa4aa8f97f0509086f53675689fe16c24e09"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAMES = {"amendment_source", "amendment_test", "finalizer"}
_OFFLINE_EVIDENCE_NAMES = {
    "red_junit_sha256",
    "red_failure_or_error_count",
    "focused_green_junit_sha256",
    "focused_green_pass_count",
    "full_green_junit_sha256",
    "full_green_pass_count",
}
_FORBIDDEN_KEYS = {
    "answer",
    "api_key",
    "content",
    "messages",
    "password",
    "prompt",
    "question",
    "raw_output",
    "raw_response",
    "secret",
}


class S4ValidationBoundaryError(ValueError):
    """The revised S4 boundary or its historical evidence is invalid."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise S4ValidationBoundaryError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise S4ValidationBoundaryError(f"{field} is not a SHA256")
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
        raise S4ValidationBoundaryError(f"{label} envelope shape drift")
    payload = _mapping(artifact.get("payload"), label=f"{label} payload")
    if (
        artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise S4ValidationBoundaryError(f"{label} envelope is not finalized")
    artifact["payload"] = payload
    return artifact, payload


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise S4ValidationBoundaryError(
                    "S4 boundary artifact contains private data"
                )
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _capture_evidence(
    *,
    result: Mapping[str, Any],
    result_file_sha256: str,
    checkpoint: Mapping[str, Any],
    checkpoint_file_sha256: str,
    events_file_sha256: str,
) -> dict[str, Any]:
    artifact, payload = _sealed(result, label="retry-008 capture result")
    durable = _mapping(checkpoint, label="retry-008 capture checkpoint")
    runtime = _mapping(
        payload.get("runtime_evidence"), label="retry-008 capture runtime"
    )
    cleanup = _mapping(payload.get("cleanup"), label="retry-008 capture cleanup")
    completed = list(range(49))
    if (
        artifact.get("run_id") != "s4-d0-capture-20260815-008"
        or payload.get("run_id") != artifact.get("run_id")
        or payload.get("phase") != "U0_CAPTURE"
        or payload.get("method") != "U0"
        or payload.get("mode") != "capture"
        or payload.get("history_id") != "07741c45"
        or payload.get("namespace") != "pev3-s4-u0-capture-20260815-008"
        or payload.get("status") != "PASS"
        or payload.get("mergeable") is not True
        or payload.get("expected_episode_count") != 49
        or payload.get("completed_source_sequences") != completed
        or payload.get("episode_coverage") != 1.0
        or payload.get("error_class") is not None
        or payload.get("canonical_graph_sha256") != CANONICAL_GRAPH_SHA256
    ):
        raise S4ValidationBoundaryError("retry-008 capture outcome drift")
    expected_runtime = {
        "live_llm_calls": 532,
        "live_embedding_calls": 67,
        "resolved_prompt_count": 531,
        "resolved_embedding_count": 1242,
        "sidecar_capture_append_count": 178,
        "sidecar_record_count": 178,
        "sidecar_rejection_count": 0,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "cross_encoder_call_count": 0,
    }
    if any(runtime.get(key) != value for key, value in expected_runtime.items()):
        raise S4ValidationBoundaryError("retry-008 capture runtime drift")
    if cleanup != {
        "global_cleanup_used": False,
        "namespace": "pev3-s4-u0-capture-20260815-008",
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
        "scope": "EXACT_GROUP_ID_ONLY",
    }:
        raise S4ValidationBoundaryError("retry-008 capture cleanup drift")
    checkpoint_runtime = _mapping(
        durable.get("runtime_evidence_cumulative"),
        label="retry-008 capture checkpoint runtime",
    )
    namespace_state = _mapping(
        durable.get("namespace_state"),
        label="retry-008 capture checkpoint namespace state",
    )
    if (
        durable.get("status") != "completed"
        or durable.get("run_id") != artifact.get("run_id")
        or durable.get("completed_source_sequences") != completed
        or durable.get("error_class") is not None
        or durable.get("canonical_graph_sha256") != CANONICAL_GRAPH_SHA256
        or checkpoint_runtime != runtime
        or namespace_state.get("node_count") != 0
        or namespace_state.get("relationship_count") != 0
    ):
        raise S4ValidationBoundaryError("retry-008 capture checkpoint drift")
    if (
        _sha(result_file_sha256, field="capture result file")
        != CAPTURE_RESULT_SHA256
        or _sha(checkpoint_file_sha256, field="capture checkpoint file")
        != CAPTURE_CHECKPOINT_SHA256
        or _sha(events_file_sha256, field="capture events file")
        != CAPTURE_EVENTS_SHA256
    ):
        raise S4ValidationBoundaryError("retry-008 capture file binding drift")
    return {
        "run_id": artifact["run_id"],
        "status": "PASS",
        "mergeable": True,
        "completed_episode_count": 49,
        "expected_episode_count": 49,
        "live_llm_calls": 532,
        "live_embedding_calls": 67,
        "candidate_sidecar_append_count": 178,
        "candidate_sidecar_record_count": 178,
        "candidate_sidecar_rejection_count": 0,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "canonical_graph_sha256": CANONICAL_GRAPH_SHA256,
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
        "phase_result_file_sha256": CAPTURE_RESULT_SHA256,
        "checkpoint_file_sha256": CAPTURE_CHECKPOINT_SHA256,
        "events_file_sha256": CAPTURE_EVENTS_SHA256,
    }


def _replay_evidence(
    *,
    result: Mapping[str, Any],
    result_file_sha256: str,
    checkpoint: Mapping[str, Any],
    checkpoint_file_sha256: str,
    events_file_sha256: str,
    execution_log: str,
    execution_log_file_sha256: str,
) -> dict[str, Any]:
    artifact, payload = _sealed(result, label="retry-008 replay result")
    durable = _mapping(checkpoint, label="retry-008 replay checkpoint")
    runtime = _mapping(
        payload.get("runtime_evidence"), label="retry-008 replay runtime"
    )
    namespace_state = _mapping(
        durable.get("namespace_state"),
        label="retry-008 replay namespace state",
    )
    completed = list(range(7))
    if (
        artifact.get("run_id") != "s4-d0-replay-20260815-008"
        or payload.get("run_id") != artifact.get("run_id")
        or payload.get("phase") != "D0_READ_ONLY_REPLAY"
        or payload.get("method") != "D0"
        or payload.get("mode") != "replay"
        or payload.get("history_id") != "07741c45"
        or payload.get("namespace") != "pev3-s4-d0-replay-20260815-008"
        or payload.get("status") != "INCOMPLETE"
        or payload.get("mergeable") is not False
        or payload.get("expected_episode_count") != 49
        or payload.get("completed_source_sequences") != completed
        or payload.get("episode_coverage") != 7 / 49
        or payload.get("error_class") != "CandidateSidecarError"
        or payload.get("canonical_graph_sha256") is not None
        or payload.get("cleanup") is not None
    ):
        raise S4ValidationBoundaryError("retry-008 replay outcome drift")
    if (
        runtime.get("live_llm_calls") != 0
        or runtime.get("live_embedding_calls") != 0
        or runtime.get("live_fallback_count") != 0
        or runtime.get("unexpected_prompt_count") != 0
        or runtime.get("unexpected_embedding_count") != 0
        or runtime.get("cross_encoder_call_count") != 0
        or runtime.get("sidecar_record_count") != 178
        or runtime.get("sidecar_consumed_count") != 20
        or runtime.get("sidecar_remaining_count") != 158
        or runtime.get("sidecar_rejection_count") != 0
    ):
        raise S4ValidationBoundaryError("retry-008 replay runtime drift")
    checkpoint_runtime = _mapping(
        durable.get("runtime_evidence_cumulative"),
        label="retry-008 replay checkpoint runtime",
    )
    episode_names = namespace_state.get("episode_names")
    if (
        durable.get("status") != "incomplete"
        or durable.get("run_id") != artifact.get("run_id")
        or durable.get("namespace") != payload.get("namespace")
        or durable.get("completed_source_sequences") != completed
        or durable.get("error_class") != "CandidateSidecarError"
        or durable.get("canonical_graph_sha256") is not None
        or checkpoint_runtime != runtime
        or namespace_state.get("node_count") != 32
        or namespace_state.get("relationship_count") != 48
        or not isinstance(episode_names, list)
        or len(episode_names) != 7
    ):
        raise S4ValidationBoundaryError("retry-008 replay checkpoint drift")
    if not isinstance(execution_log, str) or (
        "source_sequence\": 7" not in execution_log
        or "SIDECAR_CALL_CORRELATION_MISSING" not in execution_log
        or "CandidateSidecarError" not in execution_log
    ):
        raise S4ValidationBoundaryError("retry-008 replay failure trace drift")
    if (
        _sha(result_file_sha256, field="replay result file")
        != REPLAY_RESULT_SHA256
        or _sha(checkpoint_file_sha256, field="replay checkpoint file")
        != REPLAY_CHECKPOINT_SHA256
        or _sha(events_file_sha256, field="replay events file")
        != REPLAY_EVENTS_SHA256
        or _sha(execution_log_file_sha256, field="replay execution log")
        != EXECUTION_LOG_SHA256
    ):
        raise S4ValidationBoundaryError("retry-008 replay file binding drift")
    return {
        "run_id": artifact["run_id"],
        "status": "INCOMPLETE",
        "mergeable": False,
        "completed_episode_count": 7,
        "expected_episode_count": 49,
        "failed_source_sequence": 7,
        "error_class": "CandidateSidecarError",
        "error_code": "SIDECAR_CALL_CORRELATION_MISSING",
        "live_llm_calls": 0,
        "live_embedding_calls": 0,
        "candidate_sidecar_consumed_count": 20,
        "candidate_sidecar_remaining_count": 158,
        "namespace_cleanup_recorded": False,
        "persisted_namespace_state_declared": True,
        "live_namespace_state_attested": False,
        "preservation_policy": "DO_NOT_CLEAN_OR_RESUME",
        "preserved_node_count": 32,
        "preserved_relationship_count": 48,
        "phase_result_file_sha256": REPLAY_RESULT_SHA256,
        "checkpoint_file_sha256": REPLAY_CHECKPOINT_SHA256,
        "events_file_sha256": REPLAY_EVENTS_SHA256,
        "execution_log_file_sha256": EXECUTION_LOG_SHA256,
    }


_DISPOSITION = {
    "resume_authorized": False,
    "cleanup_authorized": False,
    "rewrite_authorized": False,
    "retry_009_authorized": False,
    "candidate_level_replay_is_main_path": False,
    "legacy_infrastructure_status": "PRESERVED_NON_MAIN_PATH_NON_AUTHORIZING",
}

_VALIDATION_LANES: dict[str, Any] = {
    "RX0_NATIVE_REAL_EXECUTION": {
        "real_system_execution": True,
        "headline_performance_source": True,
        "retry_008_role": "ONE_HISTORY_OPERATIONAL_CANARY_ONLY",
        "formal_results_require_fresh_unmodified_method_runs": True,
        "passive_instrumentation_only": True,
    },
    "TR0_SCHEDULING_TRACE_REPLAY": {
        "supporting_control_only": True,
        "headline_performance_source": False,
        "semantic_correctness_oracle": False,
        "fixed_wall_clock_demand_is_counterfactual_only": True,
        "endogenous_latency_effects_excluded": [
            "VLLM_BATCHING_AND_QUEUEING",
            "KV_CACHE_CONTENTION",
            "CONCURRENCY_DEPENDENT_SERVICE_TIME",
            "STATE_DEPENDENT_WORK_GENERATION",
            "DB_CONTENTION",
        ],
        "requires_real_system_calibration": True,
        "calibration_policies": ["NATIVE", "CHANGED_POLICY"],
        "calibration_load_regions": ["LOW", "NEAR_SATURATION"],
        "calibration_metrics": [
            "MAKESPAN",
            "P50_FRESHNESS",
            "P95_FRESHNESS",
            "PEAK_BACKLOG",
            "BACKLOG_AUC",
            "GOODPUT",
            "RESOURCE_OCCUPANCY",
        ],
        "calibration_threshold_status": "MUST_PREREGISTER_BEFORE_RESULTS",
    },
    "FX0_DETERMINISTIC_MECHANISM_FIXTURE": {
        "production_mechanism_path_required": True,
        "only_controlled_nondeterminism_may_be_stubbed": True,
        "fixture_count_policy": "TRANSITION_COVERAGE_NOT_FIXED_COUNT",
        "exact_canonical_state_parity_required": True,
        "exact_publication_history_parity_required": True,
        "required_transitions": [
            "ENTITY_ALIAS_CANONICAL_MERGE",
            "COMPATIBLE_DUPLICATE_UUID_COALESCING",
            "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED",
            "RELATION_RESOLUTION",
            "TEMPORAL_INVALIDATION_UPDATE",
            "PREPARE_TO_BIND_STATE_CHANGE",
            "SOURCE_ORDERED_PUBLICATION",
            "RETRY_IDEMPOTENCE",
            "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
        ],
        "performance_claims_authorized": False,
        "m_method_qualification_gate": True,
    },
    "REAL_WORKLOAD_CORRECTNESS": {
        "all_methods_execute_real_graphiti": True,
        "episode_source_coverage": 1.0,
        "lost": 0,
        "duplicate": 0,
        "source_publication_order_violations": 0,
        "visibility_publication_violations": 0,
        "temporal_provenance_violations": 0,
        "semantic_graph_metrics_require_preregistered_matching_oracle": True,
        "aggregate_graph_counts_are_descriptive_only": True,
        "quality_metrics": ["EVIDENCE_RECALL_AT_10", "QA_ACCURACY"],
        "paired_per_history_analysis": True,
        "confidence_intervals_required": True,
        "quality_margins_status": "MUST_PREREGISTER_BEFORE_RESULTS",
    },
}

_AUTHORITY = {
    "revised_s4_offline_design_authorized": True,
    "tr0_offline_design_authorized": True,
    "fx0_offline_design_authorized": True,
    "s5_offline_design_authorized": True,
    "model_call_authorized": False,
    "neo4j_mutation_authorized": False,
    "tr0_live_execution_authorized": False,
    "fx0_live_execution_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}

_LEGACY_MODULES = [
    "s4_authority",
    "s4_candidate_oracle",
    "s4_candidate_projection",
    "s4_candidate_sidecar",
    "s4_candidate_sidecar_runtime",
    "s4_controller",
    "s4_d0_contract",
    "s4_d0_production",
    "s4_d0_runner",
    "s4_edge_identity_diagnosis",
    "s4_edge_identity_diagnosis_authority",
    "s4_edge_identity_diagnosis_controller",
    "s4_edge_identity_diagnosis_production",
    "s4_edge_identity_dry_run",
    "s4_preflight",
    "s4_preflight_production",
    "s4_qualification_activation",
    "s4_qualification_plan",
    "s4_remap_authority",
    "s4_remap_controller",
    "s4_remap_result",
    "s4_remap_retry_contract",
    "s4_retry_008_compatibility",
    "s4_retry_contract",
    "s4_sidecar_authority",
    "s4_sidecar_controller",
    "s4_sidecar_qualification_activation",
    "s4_sidecar_qualification_authority",
    "s4_sidecar_qualification_controller",
    "s4_sidecar_qualification_data",
    "s4_sidecar_qualification_result",
    "s4_sidecar_result",
    "s4_sidecar_retry_contract",
    "s4_sidecar_smoke_result_verifier",
    "s4_smoke_result",
]

_LEGACY_ARTIFACT_CLASSES = [
    "SMOKE_AUTHORITIES",
    "REMAP_AUTHORITIES",
    "SIDECAR_AUTHORITIES",
    "FIXED_THREE_AUTHORITIES",
    "AUTHORITY_CONSUMPTIONS",
    "SMOKE_AND_FIXED_THREE_RESULTS",
    "QUALIFICATION_ACTIVATIONS",
]

_REUSABLE_MECHANISMS = [
    "ARTIFACT_ENVELOPE_AND_HASHING",
    "EXCLUSIVE_SINGLE_USE_CONSUMPTION",
    "CONTIGUOUS_PREFIX_CHECKPOINT",
    "SANITIZED_NON_MERGEABLE_FAILURE",
    "PATH_CONFINEMENT_AND_DEPENDENCY_INJECTION",
    "EXTERNAL_EVIDENCE_RECOMPUTATION",
    "CANONICAL_NAMESPACE_PROJECTION",
]


def build_s4_validation_boundary_amendment(
    *,
    capture_result: Mapping[str, Any],
    capture_result_file_sha256: str,
    capture_checkpoint: Mapping[str, Any],
    capture_checkpoint_file_sha256: str,
    capture_events_file_sha256: str,
    replay_result: Mapping[str, Any],
    replay_result_file_sha256: str,
    replay_checkpoint: Mapping[str, Any],
    replay_checkpoint_file_sha256: str,
    replay_events_file_sha256: str,
    execution_log: str,
    execution_log_file_sha256: str,
    final_smoke_result_exists: bool,
    activation_v3_exists: bool,
    parent_protocol_sha256: str,
    legacy_workplan_sha256: str,
    current_stage_pointer_sha256: str,
    amendment_document_sha256: str,
    offline_evidence: Mapping[str, Any],
    source_sha256: Mapping[str, str],
    git_commit: str,
) -> dict[str, Any]:
    """Build one immutable, non-live-authorizing S4 boundary artifact."""

    if parent_protocol_sha256 != PARENT_PROTOCOL_SHA256:
        raise S4ValidationBoundaryError("parent protocol binding drift")
    if legacy_workplan_sha256 != LEGACY_WORKPLAN_SHA256:
        raise S4ValidationBoundaryError("legacy S4 workplan binding drift")
    if current_stage_pointer_sha256 != CURRENT_STAGE_POINTER_SHA256:
        raise S4ValidationBoundaryError("current stage pointer drift")
    document_sha = _sha(
        amendment_document_sha256, field="S4 boundary amendment document"
    )
    sources = _mapping(source_sha256, label="S4 boundary source hashes")
    if set(sources) != _SOURCE_NAMES:
        raise S4ValidationBoundaryError("S4 boundary source inventory drift")
    sources = {
        name: _sha(value, field=f"S4 boundary source {name}")
        for name, value in sorted(sources.items())
    }
    tests = _mapping(offline_evidence, label="S4 boundary offline evidence")
    if set(tests) != _OFFLINE_EVIDENCE_NAMES:
        raise S4ValidationBoundaryError("S4 boundary offline evidence shape drift")
    for name in (
        "red_junit_sha256",
        "focused_green_junit_sha256",
        "full_green_junit_sha256",
    ):
        _sha(tests.get(name), field=f"S4 boundary {name}")
    for name in (
        "red_failure_or_error_count",
        "focused_green_pass_count",
        "full_green_pass_count",
    ):
        count = tests.get(name)
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise S4ValidationBoundaryError(f"S4 boundary {name} is invalid")
    if final_smoke_result_exists is not False or activation_v3_exists is not False:
        raise S4ValidationBoundaryError(
            "retry-008 downstream artifact unexpectedly exists"
        )
    capture = _capture_evidence(
        result=capture_result,
        result_file_sha256=capture_result_file_sha256,
        checkpoint=capture_checkpoint,
        checkpoint_file_sha256=capture_checkpoint_file_sha256,
        events_file_sha256=capture_events_file_sha256,
    )
    replay = _replay_evidence(
        result=replay_result,
        result_file_sha256=replay_result_file_sha256,
        checkpoint=replay_checkpoint,
        checkpoint_file_sha256=replay_checkpoint_file_sha256,
        events_file_sha256=replay_events_file_sha256,
        execution_log=execution_log,
        execution_log_file_sha256=execution_log_file_sha256,
    )
    payload = {
        "schema_version": SCHEMA,
        "stage": "S4",
        "amendment_version": "2.0",
        "decision": (
            "FULL_INTERNAL_D0_REPLAY_RETIRED_AS_QUALIFICATION_BOUNDARY"
        ),
        "decision_timing": "BEFORE_REVISED_S4_OR_METHOD_RESULTS",
        "historical_d0_identity_preserved": True,
        "historical_retry_008": {
            "capture": capture,
            "replay": replay,
            "downstream": {
                "final_smoke_result_exists": False,
                "activation_v3_exists": False,
                "retry_008_qualified_d0": False,
            },
            "disposition": deepcopy(_DISPOSITION),
        },
        "validation_lanes": deepcopy(_VALIDATION_LANES),
        "legacy_d0_replay_infrastructure": {
            "status": "LEGACY_EVIDENCE_ONLY_NON_AUTHORIZING",
            "inheritance_allowed": False,
            "authority_reuse_allowed": False,
            "modules": list(_LEGACY_MODULES),
            "evidence_only_artifact_classes": list(_LEGACY_ARTIFACT_CLASSES),
            "mechanisms_requiring_new_lane_qualification": list(
                _REUSABLE_MECHANISMS
            ),
        },
        "input_bindings": {
            "parent_protocol": {
                "path": (
                    "../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_"
                    "v3_SMALL_FIRST_FINAL.md"
                ),
                "file_sha256": PARENT_PROTOCOL_SHA256,
            },
            "legacy_s4_workplan": {
                "path": "S4_D0_EXECUTION_WORKPLAN_v1.0.md",
                "file_sha256": LEGACY_WORKPLAN_SHA256,
                "status": "SUPERSEDED_ONLY_FOR_S4_D0_BOUNDARY_CLAUSES",
            },
            "current_stage_pointer": {
                "path": "runtime/CURRENT_STAGE_STATUS.json",
                "file_sha256": CURRENT_STAGE_POINTER_SHA256,
                "current_stage": "S3_CONFIGURATION_FROZEN",
            },
            "amendment_document": {
                "path": "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md",
                "file_sha256": document_sha,
            },
        },
        "offline_evidence": tests,
        "source_sha256": sources,
        "authority": deepcopy(_AUTHORITY),
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=AMENDMENT_RUN_ID,
    )
    return verify_s4_validation_boundary_amendment(artifact)


def verify_s4_validation_boundary_amendment(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed on any semantic or authority drift in the amendment."""

    artifact, payload = _sealed(value, label="S4 validation boundary amendment")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("run_id") != AMENDMENT_RUN_ID
        or set(payload)
        != {
            "schema_version",
            "stage",
            "amendment_version",
            "decision",
            "decision_timing",
            "historical_d0_identity_preserved",
            "historical_retry_008",
            "validation_lanes",
            "legacy_d0_replay_infrastructure",
            "input_bindings",
            "offline_evidence",
            "source_sha256",
            "authority",
        }
        or payload.get("schema_version") != SCHEMA
        or payload.get("stage") != "S4"
        or payload.get("amendment_version") != "2.0"
        or payload.get("decision")
        != "FULL_INTERNAL_D0_REPLAY_RETIRED_AS_QUALIFICATION_BOUNDARY"
        or payload.get("decision_timing") != "BEFORE_REVISED_S4_OR_METHOD_RESULTS"
        or payload.get("historical_d0_identity_preserved") is not True
    ):
        raise S4ValidationBoundaryError("S4 amendment identity drift")
    historical = _mapping(
        payload.get("historical_retry_008"), label="historical retry-008"
    )
    if set(historical) != {"capture", "replay", "downstream", "disposition"}:
        raise S4ValidationBoundaryError("retry-008 evidence shape drift")
    if historical.get("capture") != {
        "run_id": "s4-d0-capture-20260815-008",
        "status": "PASS",
        "mergeable": True,
        "completed_episode_count": 49,
        "expected_episode_count": 49,
        "live_llm_calls": 532,
        "live_embedding_calls": 67,
        "candidate_sidecar_append_count": 178,
        "candidate_sidecar_record_count": 178,
        "candidate_sidecar_rejection_count": 0,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "canonical_graph_sha256": CANONICAL_GRAPH_SHA256,
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
        "phase_result_file_sha256": CAPTURE_RESULT_SHA256,
        "checkpoint_file_sha256": CAPTURE_CHECKPOINT_SHA256,
        "events_file_sha256": CAPTURE_EVENTS_SHA256,
    }:
        raise S4ValidationBoundaryError("retry-008 capture evidence drift")
    if historical.get("replay") != {
        "run_id": "s4-d0-replay-20260815-008",
        "status": "INCOMPLETE",
        "mergeable": False,
        "completed_episode_count": 7,
        "expected_episode_count": 49,
        "failed_source_sequence": 7,
        "error_class": "CandidateSidecarError",
        "error_code": "SIDECAR_CALL_CORRELATION_MISSING",
        "live_llm_calls": 0,
        "live_embedding_calls": 0,
        "candidate_sidecar_consumed_count": 20,
        "candidate_sidecar_remaining_count": 158,
        "namespace_cleanup_recorded": False,
        "persisted_namespace_state_declared": True,
        "live_namespace_state_attested": False,
        "preservation_policy": "DO_NOT_CLEAN_OR_RESUME",
        "preserved_node_count": 32,
        "preserved_relationship_count": 48,
        "phase_result_file_sha256": REPLAY_RESULT_SHA256,
        "checkpoint_file_sha256": REPLAY_CHECKPOINT_SHA256,
        "events_file_sha256": REPLAY_EVENTS_SHA256,
        "execution_log_file_sha256": EXECUTION_LOG_SHA256,
    }:
        raise S4ValidationBoundaryError("retry-008 replay evidence drift")
    if historical.get("downstream") != {
        "final_smoke_result_exists": False,
        "activation_v3_exists": False,
        "retry_008_qualified_d0": False,
    }:
        raise S4ValidationBoundaryError("retry-008 downstream status drift")
    if historical.get("disposition") != _DISPOSITION:
        raise S4ValidationBoundaryError("retry-008 disposition drift")
    if payload.get("validation_lanes") != _VALIDATION_LANES:
        raise S4ValidationBoundaryError("replacement validation lane drift")
    if payload.get("legacy_d0_replay_infrastructure") != {
        "status": "LEGACY_EVIDENCE_ONLY_NON_AUTHORIZING",
        "inheritance_allowed": False,
        "authority_reuse_allowed": False,
        "modules": _LEGACY_MODULES,
        "evidence_only_artifact_classes": _LEGACY_ARTIFACT_CLASSES,
        "mechanisms_requiring_new_lane_qualification": _REUSABLE_MECHANISMS,
    }:
        raise S4ValidationBoundaryError("legacy module disposition drift")
    bindings = _mapping(payload.get("input_bindings"), label="input bindings")
    if set(bindings) != {
        "parent_protocol",
        "legacy_s4_workplan",
        "current_stage_pointer",
        "amendment_document",
    }:
        raise S4ValidationBoundaryError("S4 amendment binding inventory drift")
    if bindings.get("parent_protocol") != {
        "path": "../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md",
        "file_sha256": PARENT_PROTOCOL_SHA256,
    } or bindings.get("legacy_s4_workplan") != {
        "path": "S4_D0_EXECUTION_WORKPLAN_v1.0.md",
        "file_sha256": LEGACY_WORKPLAN_SHA256,
        "status": "SUPERSEDED_ONLY_FOR_S4_D0_BOUNDARY_CLAUSES",
    } or bindings.get("current_stage_pointer") != {
        "path": "runtime/CURRENT_STAGE_STATUS.json",
        "file_sha256": CURRENT_STAGE_POINTER_SHA256,
        "current_stage": "S3_CONFIGURATION_FROZEN",
    }:
        raise S4ValidationBoundaryError("S4 amendment fixed binding drift")
    document = _mapping(
        bindings.get("amendment_document"), label="amendment document binding"
    )
    if document.get("path") != "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md":
        raise S4ValidationBoundaryError("S4 amendment document path drift")
    _sha(document.get("file_sha256"), field="S4 amendment document")
    tests = _mapping(payload.get("offline_evidence"), label="offline evidence")
    if set(tests) != _OFFLINE_EVIDENCE_NAMES:
        raise S4ValidationBoundaryError("S4 amendment offline evidence drift")
    for name in (
        "red_junit_sha256",
        "focused_green_junit_sha256",
        "full_green_junit_sha256",
    ):
        _sha(tests.get(name), field=f"S4 amendment {name}")
    for name in (
        "red_failure_or_error_count",
        "focused_green_pass_count",
        "full_green_pass_count",
    ):
        count = tests.get(name)
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise S4ValidationBoundaryError("S4 amendment test count drift")
    sources = _mapping(payload.get("source_sha256"), label="source hashes")
    if set(sources) != _SOURCE_NAMES:
        raise S4ValidationBoundaryError("S4 amendment source inventory drift")
    for name, digest in sources.items():
        _sha(digest, field=f"S4 amendment source {name}")
    if payload.get("authority") != _AUTHORITY:
        raise S4ValidationBoundaryError("S4 amendment authority drift")
    _reject_private(artifact)
    return artifact
