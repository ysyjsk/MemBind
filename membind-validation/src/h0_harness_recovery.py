"""Fail-closed offline state builders for the H0-B harness recovery.

This module only reads local, hash-bound JSON evidence.  It does not load the
project environment, construct clients, mutate machine state, or authorize a
live attempt by itself.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from h0_live_preflight import load_authorized_h0_runtime_identity
from h0_repair_admission import verify_h0_b_harness_repair_decision
from h0_runtime import (
    H0ManifestError,
    H0StateGateError,
    authorize_h0_live_entry,
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_file,
)
from h0_state_transition import (
    _atomic_write,
    _load_canonical_state_snapshot,
    _state_target,
    _state_transition_lock,
)


PROTOCOL_VERSION = "current-validation-v1.3"
R2_ARTIFACT_SET_ID = "v1_3_harness_r2"
R3_ARTIFACT_SET_ID = "v1_3_harness_r3"
R3_HARNESS_REVISION = 3
R3_INDEX_PATH = (
    "artifacts/h0_manifest_sets/v1_3_harness_r3/"
    "resolved_manifest_index_v1_3_harness_r3.json"
)
R4_ARTIFACT_SET_ID = "v1_3_harness_r4"
R4_HARNESS_REVISION = 4
R4_INDEX_PATH = (
    "artifacts/h0_manifest_sets/v1_3_harness_r4/"
    "resolved_manifest_index_v1_3_harness_r4.json"
)
R5_ARTIFACT_SET_ID = "v1_3_harness_r5"
R5_HARNESS_REVISION = 5
R5_INDEX_PATH = (
    "artifacts/h0_manifest_sets/v1_3_harness_r5/"
    "resolved_manifest_index_v1_3_harness_r5.json"
)
REVOKED_STATUS = "h0_b_harness_compatibility_failure_live_revoked"
REVOKED_SCOPE = "h0_b_harness_repair_offline_only"
REPAIR_BOUND_STATUS = "h0_b_harness_repair_verified_not_live_authorized"
REPAIR_BOUND_SCOPE = "h0_b_harness_repair_verified_only"
FAILURE_REASON = "h0_b_pre_workload_harness_compatibility_failure"
INFRASTRUCTURE_RERUN_STATUS = "h0_b_infrastructure_interrupted_live_revoked"
INFRASTRUCTURE_RERUN_SCOPE = "h0_b_infrastructure_recovery_offline_only"
INFRASTRUCTURE_RERUN_ATTEMPT_ID = "h0-q1-b-20260810-replacement-002"
INFRASTRUCTURE_INTERRUPTED_ATTEMPT_ID = "h0-q1-b-20260809-replacement-001"
INFRASTRUCTURE_STOP_REASON = "vllm_unreachable"
POST_WORKLOAD_FAILED_ATTEMPT_ID = "h0-q1-b-20260810-replacement-002"
POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID = "h0-q1-b-20260810-replacement-003"
POST_WORKLOAD_REVOKED_STATUS = (
    "h0_b_post_workload_harness_failure_live_revoked"
)
POST_WORKLOAD_REVOKED_SCOPE = (
    "h0_b_post_workload_harness_repair_offline_only"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_BINDING_FIELDS = {
    "resolved_manifest_index_path",
    "resolved_manifest_index_sha256",
    "resolved_candidate_manifest_path",
    "resolved_candidate_manifest_sha256",
    "resolved_shared_base_manifest_path",
    "resolved_shared_base_manifest_sha256",
}
_VERIFICATION_FIELDS = {
    "schema_version",
    "protocol_version",
    "artifact_set_id",
    "execution_harness_revision",
    "status",
    "index_path",
    "index_sha256",
    "generated_json_file_count",
    "binding_count",
    "resolved_wrapper_count",
    "source_spec_count",
    "execution_source_count",
    "secret_scan_passed",
    "live_eligible",
}
_TDD_NAMES = {
    "latest_red",
    "latest_green",
    "latest_focused",
    "latest_full_regression",
}
_ADMISSION_FIELDS = {
    "schema_version",
    "protocol_version",
    "candidate_id",
    "phase",
    "decision_path",
    "decision_sha256",
    "decision_result_blind",
    "prior_model_workload_output_observed",
    "repair_required_independent_of_model_output",
    "scientific_configuration_unchanged",
    "one_shot_whole_stage_replacement",
    "replacement_attempt_id",
    "invalidated_stage_attempt_id",
    "invalidated_checkpoint_index_sha256",
    "failure_report_sha256",
    "old_attempt_qualification_reusable",
    "old_and_new_trial_counts_mergeable",
    "prior_manifest_index_sha256",
    "repaired_manifest_index_sha256",
    "secrets_persisted",
}
_POST_WORKLOAD_ADMISSION_FIELDS = {
    "schema_version",
    "protocol_version",
    "candidate_id",
    "phase",
    "decision_path",
    "decision_sha256",
    "decision_result_blind",
    "prior_model_workload_output_observed",
    "repair_required_independent_of_model_response_content",
    "scientific_configuration_unchanged",
    "one_shot_whole_stage_replacement",
    "replacement_attempt_id",
    "invalidated_stage_attempt_id",
    "invalidated_checkpoint_index_sha256",
    "failure_segment_sha256",
    "source_checkpoint_sha256",
    "live_log_sha256",
    "offline_probe_sha256",
    "prior_harness_repair_admission_sha256",
    "prior_infrastructure_rerun_admission_sha256",
    "old_attempt_qualification_reusable",
    "old_and_new_trial_counts_mergeable",
    "resume_failed_attempt_allowed",
    "prior_manifest_index_sha256",
    "repaired_manifest_index_sha256",
    "secrets_persisted",
}
_FORBIDDEN_PATH_PARTS = {".env", "gpt55_temporary"}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "credentials",
    "env_dump",
    "environment_dump",
    "environ",
    "messages",
    "process_environment",
    "raw_prompt",
    "raw_prompts",
    "raw_response",
    "raw_responses",
    "secret",
}


class H0HarnessRecoveryError(RuntimeError):
    """A sanitized denial of H0-B harness recovery state progression."""


def _fail(reason: str) -> H0HarnessRecoveryError:
    return H0HarnessRecoveryError(f"H0-B harness recovery denied: {reason}")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label}_not_object")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_sha256_invalid")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _safe_value(value: Any, *, location: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().casefold().replace("-", "_")
            if key in _FORBIDDEN_KEYS:
                raise _fail(f"unsafe_field_at_{location}")
            _safe_value(child, location=f"{location}.{raw_key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _safe_value(child, location=f"{location}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or ".env" in lowered or "gpt55_temporary" in lowered:
            raise _fail(f"unsafe_value_at_{location}")


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(f"{label}_path_invalid")
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(part in _FORBIDDEN_PATH_PARTS for part in relative.parts)
    ):
        raise _fail(f"{label}_path_noncanonical")
    return value


def _bound_file(
    root: Path,
    relative_value: Any,
    digest_value: Any,
    *,
    label: str,
) -> tuple[Path, str, str]:
    relative = _relative_path(relative_value, label)
    digest = _sha(digest_value, label)
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_symlink_forbidden")
    path = (root / relative).resolve()
    try:
        normalized = path.relative_to(root).as_posix()
    except ValueError:
        raise _fail(f"{label}_path_escape") from None
    if normalized != relative or not path.is_file() or sha256_file(path) != digest:
        raise _fail(f"{label}_missing_or_hash_mismatch")
    return path, relative, digest


def _json_file(
    root: Path,
    relative_value: Any,
    digest_value: Any,
    *,
    label: str,
) -> tuple[dict[str, Any], str, str]:
    path, relative, digest = _bound_file(
        root, relative_value, digest_value, label=label
    )
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_invalid_json") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    _safe_value(value, location=label)
    return value, relative, digest


def classify_h0_b_post_workload_harness_failure(
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    failure_segment_path: str,
    failure_segment_sha256: str,
    source_checkpoint_path: str,
    source_checkpoint_sha256: str,
    live_log_path: str,
    live_log_sha256: str,
    offline_probe_path: str,
    offline_probe_sha256: str,
) -> dict[str, Any]:
    """Classify the exact replacement-002 failure from content-free evidence."""

    if stage_attempt_id != POST_WORKLOAD_FAILED_ATTEMPT_ID:
        raise _fail("post_workload_attempt_mismatch")
    root_path = Path(root).resolve()
    checkpoint, checkpoint_rel, checkpoint_sha = _json_file(
        root_path,
        checkpoint_index_path,
        checkpoint_index_sha256,
        label="post_workload_checkpoint",
    )
    failure, failure_rel, failure_sha = _json_file(
        root_path,
        failure_segment_path,
        failure_segment_sha256,
        label="post_workload_failure_segment",
    )
    source, source_rel, source_sha = _json_file(
        root_path,
        source_checkpoint_path,
        source_checkpoint_sha256,
        label="post_workload_source_checkpoint",
    )
    _, live_rel, live_sha = _bound_file(
        root_path, live_log_path, live_log_sha256, label="post_workload_live_log"
    )
    probe_path, probe_rel, probe_sha = _bound_file(
        root_path,
        offline_probe_path,
        offline_probe_sha256,
        label="post_workload_offline_probe",
    )
    try:
        probe = json.loads(probe_path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("post_workload_offline_probe_invalid") from None
    if not isinstance(probe, Mapping):
        raise _fail("post_workload_offline_probe_not_object")
    _safe_value(probe, location="post_workload_offline_probe")

    segments = checkpoint.get("segments")
    failure_payload = failure.get("payload")
    source_payload = source.get("payload")
    ledger = (
        failure_payload.get("attempt_ledger")
        if isinstance(failure_payload, Mapping)
        else None
    )
    runtime = (
        failure_payload.get("runtime_evidence")
        if isinstance(failure_payload, Mapping)
        else None
    )
    trials = ledger.get("logical_trials") if isinstance(ledger, Mapping) else None
    attempts = ledger.get("http_attempts") if isinstance(ledger, Mapping) else None
    histories = runtime.get("histories") if isinstance(runtime, Mapping) else None
    history = histories[0] if isinstance(histories, list) and len(histories) == 1 else None
    indexed_failure = segments[-1] if isinstance(segments, list) and segments else None
    indexed_source = segments[-2] if isinstance(segments, list) and len(segments) >= 2 else None
    expected_checkpoint_tail = (
        "artifacts/h0_runs/h0/checkpoints/"
        f"{stage_attempt_id}/index.json"
    )
    exact = (
        checkpoint_rel == expected_checkpoint_tail
        and checkpoint.get("schema_version") == "membind.h0.checkpoint-index.v1"
        and checkpoint.get("protocol_version") == PROTOCOL_VERSION
        and checkpoint.get("stage_attempt_id") == stage_attempt_id
        and checkpoint.get("candidate_id") == "Q1"
        and checkpoint.get("phase") == "H0-B"
        and checkpoint.get("status") == "candidate_failed"
        and checkpoint.get("failure_code") == "manifest_contract_failure"
        and checkpoint.get("candidate_advance_allowed") is False
        and checkpoint.get("partial_qualification_reusable") is False
        and isinstance(segments, list)
        and len(segments) == 15
        and isinstance(indexed_failure, Mapping)
        and indexed_failure.get("segment_kind") == "candidate_failure"
        and indexed_failure.get("segment_id") == "manifest_contract_failure"
        and indexed_failure.get("artifact_sha256") == failure_sha
        and isinstance(indexed_source, Mapping)
        and indexed_source.get("segment_kind") == "source_sequence"
        and indexed_source.get("segment_id") == "07741c45-000"
        and indexed_source.get("artifact_sha256") == source_sha
        and failure.get("schema_version") == "membind.h0.checkpoint-segment.v1"
        and failure.get("protocol_version") == PROTOCOL_VERSION
        and failure.get("stage_attempt_id") == stage_attempt_id
        and failure.get("segment_kind") == "candidate_failure"
        and failure.get("segment_id") == "manifest_contract_failure"
        and failure.get("segment_ordinal") == 14
        and isinstance(failure_payload, Mapping)
        and failure_payload.get("failure_code") == "manifest_contract_failure"
        and failure_payload.get("failure_stage") == "history_workload"
        and failure_payload.get("candidate_advance_allowed") is False
        and isinstance(trials, list)
        and len(trials) == 6
        and isinstance(attempts, list)
        and len(attempts) == 6
        and all(
            isinstance(item, Mapping)
            and item.get("http_200") is True
            and item.get("http_status") == 200
            and item.get("json_parse_success") is True
            and item.get("pydantic_validation_success") is True
            and item.get("semantic_utility_success") is True
            and item.get("retry_index") == 0
            and item.get("completed") is True
            and item.get("failure_class") is None
            for item in attempts
        )
        and isinstance(runtime, Mapping)
        and runtime.get("embedding_workload_request_count") == 4
        and runtime.get("fresh_graph_count") == 1
        and runtime.get("closed_graph_count") == 1
        and runtime.get("cross_encoder_rank_call_count") == 0
        and isinstance(history, Mapping)
        and history.get("cleanup_failure_count") == 0
        and history.get("close_count") == 1
        and history.get("closed") is True
        and source.get("schema_version") == "membind.h0.checkpoint-segment.v1"
        and source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("stage_attempt_id") == stage_attempt_id
        and source.get("segment_kind") == "source_sequence"
        and source.get("segment_id") == "07741c45-000"
        and source.get("segment_ordinal") == 13
        and isinstance(source_payload, Mapping)
        and isinstance(source_payload.get("phase_checkpoint"), Mapping)
        and source_payload["phase_checkpoint"].get("source_sequence") == 0
        and source_payload["phase_checkpoint"].get("final_stage_checks_passed")
        is False
        and probe.get("schema_version") == "membind.h0.embedding-contract-probe.v1"
        and probe.get("classification")
        == "local_adapter_interface_contract_failure_before_transport"
        and probe.get("input_runtime_type") == "list[str]"
        and probe.get("transport_request_count") == 0
        and probe.get("exception_type") == "H0EmbeddingValidationError"
        and probe.get("exception_message")
        == "H0 embedding validation denied: single_input_invalid"
        and probe.get("secrets_persisted") is False
    )
    if not exact:
        raise _fail("post_workload_evidence_contract_mismatch")

    return {
        "schema_version": "membind.h0.post-workload-harness-failure.v1",
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": "Q1",
        "phase": "H0-B",
        "stage_attempt_id": stage_attempt_id,
        "status": "candidate_failed",
        "failure_code": "manifest_contract_failure",
        "failure_stage": "history_workload",
        "failure_origin": "local_execution_harness_interface_contract",
        "workload_reached": True,
        "prior_model_workload_output_observed": True,
        "candidate_model_failure_supported": False,
        "infrastructure_failure_supported": False,
        "repair_required_independent_of_model_response_content": True,
        "logical_trial_count": 6,
        "http_attempt_count": 6,
        "http_200_count": 6,
        "json_parse_success_count": 6,
        "pydantic_validation_success_count": 6,
        "semantic_utility_success_count": 6,
        "retry_count": 0,
        "embedding_workload_request_count": 4,
        "source_checkpoint_count": 1,
        "fresh_graph_count": 1,
        "closed_graph_count": 1,
        "cleanup_failure_count": 0,
        "cross_encoder_rank_call_count": 0,
        "partial_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "resume_failed_attempt_allowed": False,
        "requires_whole_stage_replacement": True,
        "checkpoint_index_path": checkpoint_rel,
        "checkpoint_index_sha256": checkpoint_sha,
        "failure_segment_path": failure_rel,
        "failure_segment_sha256": failure_sha,
        "source_checkpoint_path": source_rel,
        "source_checkpoint_sha256": source_sha,
        "live_log_path": live_rel,
        "live_log_sha256": live_sha,
        "offline_probe_path": probe_rel,
        "offline_probe_sha256": probe_sha,
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }


def _validate_bindings(
    value: Any, *, artifact_set_id: str, require_r3_index: bool
) -> dict[str, str]:
    raw = _mapping(value, "artifact_bindings")
    if set(raw) != _BINDING_FIELDS:
        raise _fail("artifact_bindings_fields_mismatch")
    bindings: dict[str, str] = {}
    prefix = f"artifacts/h0_manifest_sets/{artifact_set_id}/"
    for field in _BINDING_FIELDS:
        item = raw.get(field)
        if field.endswith("_sha256"):
            bindings[field] = _sha(item, field)
        else:
            relative = _relative_path(item, field)
            if not relative.startswith(prefix):
                raise _fail("artifact_binding_namespace_mismatch")
            bindings[field] = relative
    if require_r3_index and bindings["resolved_manifest_index_path"] != R3_INDEX_PATH:
        raise _fail("r3_manifest_index_path_mismatch")
    return bindings


def _validate_h0_a_completion(value: Any) -> dict[str, Any]:
    completion = dict(_mapping(value, "h0_a_completion"))
    exact = (
        completion.get("schema_version")
        == "membind.h0.prior-phase-terminal-completion.v1"
        and completion.get("protocol_version") == PROTOCOL_VERSION
        and completion.get("status") == "qualified_terminal_completion"
        and completion.get("qualified") is True
        and completion.get("candidate_id") == "Q1"
        and completion.get("phase") == "H0-A"
        and completion.get("candidate_advance_allowed") is True
        and completion.get("partial_qualification_reusable") is True
        and completion.get("requires_whole_stage_rerun") is False
        and completion.get("secrets_persisted") is False
        and completion.get("raw_prompts_persisted") is False
        and completion.get("raw_responses_persisted") is False
    )
    for field in (
        "checkpoint_index_sha256",
        "terminal_result_sha256",
        "runtime_definition_sha256",
    ):
        _sha(completion.get(field), f"h0_a_{field}")
    _identifier(completion.get("stage_attempt_id"), "h0_a_stage_attempt_id")
    _relative_path(completion.get("checkpoint_index_path"), "h0_a_checkpoint")
    _safe_value(completion, location="h0_a_completion")
    if not exact:
        raise _fail("h0_a_completion_not_qualified")
    return completion


def _validate_r2_live_source(source_state: Any) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    source = dict(_mapping(source_state, "source_state"))
    _safe_value(source, location="source_state")
    progress = source.get("stage_progress")
    authorization = source.get("live_h0_authorization")
    completions = source.get("h0_phase_completions")
    exact = (
        source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("current_stage") == "H0"
        and source.get("status") == "h0_q1_b_live_only"
        and source.get("current_action_scope") == "h0_q1_b_live_only"
        and source.get("live_h0_candidate_authorized") is True
        and source.get("authorized_live_actions") == ["h0_candidate"]
        and source.get("authorized_h0_candidate_id") == "Q1"
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == "h0_q1_b_live_only"
        and isinstance(authorization, Mapping)
        and authorization.get("candidate_id") == "Q1"
        and authorization.get("phase") == "H0-B"
        and isinstance(completions, Mapping)
        and set(completions) == {"H0-A"}
    )
    if not exact:
        raise _fail("source_not_exact_r2_h0_b_live_state")
    bindings = _validate_bindings(
        {field: authorization.get(field) for field in _BINDING_FIELDS},
        artifact_set_id=R2_ARTIFACT_SET_ID,
        require_r3_index=False,
    )
    completion = _validate_h0_a_completion(completions.get("H0-A"))
    expected_prior = {
        field: completion.get(field)
        for field in (
            "stage_attempt_id",
            "checkpoint_index_path",
            "checkpoint_index_sha256",
            "runtime_definition_sha256",
            "terminal_result_sha256",
        )
    }
    if authorization.get("prior_phase_completion") != expected_prior:
        raise _fail("h0_a_prior_completion_binding_mismatch")
    return source, bindings, completion


def _checkpoint_segment_relative(
    checkpoint_relative: str, segment_relative: str
) -> str:
    relative = _relative_path(segment_relative, "failure_segment")
    if relative.startswith("artifacts/"):
        return relative
    marker = "/h0/checkpoints/"
    if marker not in f"/{checkpoint_relative}":
        raise _fail("checkpoint_namespace_invalid")
    prefix = checkpoint_relative.split(marker, 1)[0]
    return _relative_path(f"{prefix}/{relative}", "failure_segment")


def _validate_failure_segment(
    *,
    root: Path,
    stage_attempt_id: str,
    segment_path: Any,
    segment_sha256: Any,
) -> tuple[str, str]:
    segment, relative, digest = _json_file(
        root, segment_path, segment_sha256, label="failure_segment"
    )
    payload = segment.get("payload")
    ledger = payload.get("attempt_ledger") if isinstance(payload, Mapping) else None
    runtime = payload.get("runtime_evidence") if isinstance(payload, Mapping) else None
    exact = (
        segment.get("schema_version") == "membind.h0.checkpoint-segment.v1"
        and segment.get("protocol_version") == PROTOCOL_VERSION
        and segment.get("stage_attempt_id") == stage_attempt_id
        and segment.get("segment_kind") == "candidate_failure"
        and segment.get("segment_id") == "manifest_contract_failure"
        and isinstance(payload, Mapping)
        and payload.get("failure_code") == "manifest_contract_failure"
        and payload.get("candidate_advance_allowed") is False
        and isinstance(ledger, Mapping)
        and ledger.get("logical_trials") == []
        and ledger.get("http_attempts") == []
        and isinstance(runtime, Mapping)
        and runtime.get("fresh_graph_count") == 0
        and runtime.get("histories") == []
        and runtime.get("embedding_workload_request_count") == 0
        and segment.get("secrets_persisted") is False
        and segment.get("raw_prompts_persisted") is False
        and segment.get("raw_responses_persisted") is False
    )
    if not exact:
        raise _fail("failure_segment_not_zero_workload_manifest_failure")
    return relative, digest


def _validate_checkpoint(
    *,
    root: Path,
    stage_attempt_id: str,
    checkpoint_index_path: Any,
    checkpoint_index_sha256: Any,
) -> tuple[dict[str, Any], str, str, tuple[str, str] | None]:
    checkpoint, relative, digest = _json_file(
        root,
        checkpoint_index_path,
        checkpoint_index_sha256,
        label="checkpoint_index",
    )
    expected_tail = ("h0", "checkpoints", stage_attempt_id, "index.json")
    exact = (
        tuple(Path(relative).parts[-len(expected_tail) :]) == expected_tail
        and checkpoint.get("schema_version") == "membind.h0.checkpoint-index.v1"
        and checkpoint.get("protocol_version") == PROTOCOL_VERSION
        and checkpoint.get("stage_attempt_id") == stage_attempt_id
        and checkpoint.get("candidate_id") == "Q1"
        and checkpoint.get("phase") == "H0-B"
        and checkpoint.get("status") == "candidate_failed"
        and checkpoint.get("failure_code") == "manifest_contract_failure"
        and checkpoint.get("candidate_advance_allowed") is False
        and checkpoint.get("partial_qualification_reusable") is False
        and checkpoint.get("requires_whole_stage_rerun") is False
        and checkpoint.get("secrets_persisted") is False
        and checkpoint.get("raw_prompts_persisted") is False
        and checkpoint.get("raw_responses_persisted") is False
    )
    _sha(checkpoint.get("failure_evidence_sha256"), "failure_evidence")
    if not exact:
        raise _fail("checkpoint_not_terminal_h0_b_manifest_failure")

    terminal_reference: tuple[str, str] | None = None
    segments = checkpoint.get("segments")
    if segments is not None:
        if not isinstance(segments, list) or not segments:
            raise _fail("checkpoint_segments_invalid")
        matches = [
            item
            for item in segments
            if isinstance(item, Mapping)
            and item.get("segment_kind") == "candidate_failure"
            and item.get("segment_id") == "manifest_contract_failure"
        ]
        if len(matches) != 1 or matches[0] is not segments[-1]:
            raise _fail("checkpoint_terminal_failure_segment_mismatch")
        entry = matches[0]
        segment_relative = _checkpoint_segment_relative(
            relative, str(entry.get("artifact_path") or "")
        )
        terminal_reference = _validate_failure_segment(
            root=root,
            stage_attempt_id=stage_attempt_id,
            segment_path=segment_relative,
            segment_sha256=entry.get("artifact_sha256"),
        )
    return checkpoint, relative, digest, terminal_reference


def _normalize_failure_report(
    *,
    root: Path,
    report: Mapping[str, Any],
    report_relative: str,
    report_digest: str,
    stage_attempt_id: str,
    checkpoint_relative: str,
    checkpoint_digest: str,
    checkpoint_segment: tuple[str, str] | None,
) -> dict[str, Any]:
    schema = report.get("schema_version")
    if schema == "membind.h0.harness-compatibility-failure-report.v1":
        segment = _validate_failure_segment(
            root=root,
            stage_attempt_id=stage_attempt_id,
            segment_path=report.get("failure_segment_path"),
            segment_sha256=report.get("failure_segment_sha256"),
        )
        exact = (
            report.get("protocol_version") == PROTOCOL_VERSION
            and report.get("status") == FAILURE_REASON
            and report.get("classification")
            == "harness_compatibility_failure_not_candidate_result"
            and report.get("candidate_id") == "Q1"
            and report.get("phase") == "H0-B"
            and report.get("stage_attempt_id") == stage_attempt_id
            and report.get("checkpoint_index_path") == checkpoint_relative
            and report.get("checkpoint_index_sha256") == checkpoint_digest
            and report.get("failure_code") == "manifest_contract_failure"
            and report.get("readiness_qualified") is True
            and report.get("logical_trial_count") == 0
            and report.get("http_attempt_count") == 0
            and report.get("source_checkpoint_count") == 0
            and report.get("history_count") == 0
            and report.get("fresh_graph_count") == 0
            and report.get("embedding_workload_request_count") == 0
            and report.get("model_workload_output_observed") is False
            and report.get("candidate_qualification_interpretable") is False
            and report.get("secrets_persisted") is False
            and report.get("raw_prompts_persisted") is False
            and report.get("raw_responses_persisted") is False
        )
    elif schema == "membind.h0.preworkload-failure-report.v1":
        attempt = report.get("attempt")
        classification = report.get("classification")
        observed = report.get("observed_live_evidence")
        diagnosis = report.get("offline_diagnosis")
        probe = diagnosis.get("real_graphiti_contract_probe") if isinstance(diagnosis, Mapping) else None
        selection = diagnosis.get("h0_b_workload_selection") if isinstance(diagnosis, Mapping) else None
        recovery = report.get("recommended_recovery")
        if checkpoint_segment is None:
            raise _fail("preworkload_report_requires_checkpoint_failure_segment")
        segment = checkpoint_segment
        exact = (
            report.get("protocol_version") == PROTOCOL_VERSION
            and isinstance(attempt, Mapping)
            and attempt.get("candidate_id") == "Q1"
            and attempt.get("phase") == "H0-B"
            and attempt.get("stage_attempt_id") == stage_attempt_id
            and attempt.get("checkpoint_index_path") == checkpoint_relative
            and attempt.get("checkpoint_index_sha256") == checkpoint_digest
            and attempt.get("status") == "candidate_failed"
            and attempt.get("failure_code") == "manifest_contract_failure"
            and isinstance(classification, Mapping)
            and classification.get("failure_origin") == "execution_harness_compatibility"
            and classification.get("candidate_model_failure_supported") is False
            and classification.get("zero_workload_candidate_evidence") is True
            and classification.get("partial_qualification_reusable") is False
            and classification.get("terminal_attempt_must_remain_immutable") is True
            and isinstance(observed, Mapping)
            and observed.get("stage_readiness_status") == "ready"
            and observed.get("construction_readiness_count") == 1
            and observed.get("embedding_readiness_count") == 1
            and observed.get("neo4j_readiness_count") == 1
            and observed.get("authorization_recheck_count") == 1
            and observed.get("logical_trial_count") == 0
            and observed.get("http_attempt_count") == 0
            and observed.get("source_checkpoint_count") == 0
            and observed.get("history_count") == 0
            and observed.get("fresh_graph_count") == 0
            and observed.get("embedding_workload_request_count") == 0
            and observed.get("cross_encoder_rank_call_count") == 0
            and isinstance(probe, Mapping)
            and probe.get("status") == "fail"
            and probe.get("network_requests_issued") == 0
            and isinstance(selection, Mapping)
            and selection.get("status") == "pass"
            and selection.get("history_count") == 1
            and selection.get("source_count") == 49
            and isinstance(recovery, Mapping)
            and recovery.get("advance_allowed") is False
            and recovery.get("rerun_current_attempt_allowed") is False
            and recovery.get("current_live_grant_requires_fail_closed_revocation")
            is True
            and recovery.get("harness_revision_r2_reusable_after_source_change")
            is False
            and report.get("secrets_persisted") is False
            and report.get("raw_prompts_persisted") is False
            and report.get("raw_responses_persisted") is False
        )
    else:
        raise _fail("failure_report_schema_unsupported")
    if not exact:
        raise _fail("failure_report_not_zero_workload_harness_failure")
    if checkpoint_segment is not None and segment != checkpoint_segment:
        raise _fail("failure_report_segment_binding_mismatch")
    return {
        "schema_version": "membind.h0.normalized-harness-failure-evidence.v1",
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": "Q1",
        "phase": "H0-B",
        "stage_attempt_id": stage_attempt_id,
        "checkpoint_index_path": checkpoint_relative,
        "checkpoint_index_sha256": checkpoint_digest,
        "failure_segment_path": segment[0],
        "failure_segment_sha256": segment[1],
        "failure_report_path": report_relative,
        "failure_report_sha256": report_digest,
        "failure_code": "manifest_contract_failure",
        "readiness_qualified": True,
        "logical_trial_count": 0,
        "http_attempt_count": 0,
        "source_checkpoint_count": 0,
        "history_count": 0,
        "fresh_graph_count": 0,
        "embedding_workload_request_count": 0,
        "model_workload_output_observed": False,
        "candidate_qualification_interpretable": False,
        "secrets_persisted": False,
    }


def build_h0_b_harness_revoked_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    failure_report_path: str,
    failure_report_sha256: str,
) -> dict[str, Any]:
    """Revoke the stale r2 H0-B grant using exact zero-workload evidence."""

    root_path = Path(root).resolve()
    attempt_id = _identifier(stage_attempt_id, "stage_attempt_id")
    source, bindings, completion = _validate_r2_live_source(source_state)
    _, checkpoint_relative, checkpoint_digest, checkpoint_segment = _validate_checkpoint(
        root=root_path,
        stage_attempt_id=attempt_id,
        checkpoint_index_path=checkpoint_index_path,
        checkpoint_index_sha256=checkpoint_index_sha256,
    )
    report, report_relative, report_digest = _json_file(
        root_path,
        failure_report_path,
        failure_report_sha256,
        label="failure_report",
    )
    normalized = _normalize_failure_report(
        root=root_path,
        report=report,
        report_relative=report_relative,
        report_digest=report_digest,
        stage_attempt_id=attempt_id,
        checkpoint_relative=checkpoint_relative,
        checkpoint_digest=checkpoint_digest,
        checkpoint_segment=checkpoint_segment,
    )

    state = deepcopy(source)
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden",
            "h0_candidate_progression": "h0_b_harness_failure_live_revoked",
        }
    )
    state.update(
        {
            "status": REVOKED_STATUS,
            "current_action_scope": REVOKED_SCOPE,
            "current_blocker": FAILURE_REASON,
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "next_allowed_action": "offline_h0_b_harness_repair_only",
            "h0_phase_completions": {"H0-A": completion},
            "h0_b_harness_invalidation": {
                "schema_version": "membind.h0.harness-invalidation.v1",
                "protocol_version": PROTOCOL_VERSION,
                "status": "invalidated_no_rerun_or_advance_authorized",
                "reason": FAILURE_REASON,
                "candidate_id": "Q1",
                "phase": "H0-B",
                "stage_attempt_id": attempt_id,
                "checkpoint_index_path": checkpoint_relative,
                "checkpoint_index_sha256": checkpoint_digest,
                "failure_report_path": report_relative,
                "failure_report_sha256": report_digest,
                "prior_manifest_bindings": bindings,
                "normalized_failure_evidence": normalized,
                "candidate_rerun_authorized": False,
                "candidate_advance_authorized": False,
                "live_transition_authorized": False,
                "secrets_persisted": False,
            },
        }
    )
    state.pop("live_h0_authorization", None)
    return state


def _validate_r3_verification_shape(value: Any) -> dict[str, Any]:
    verification = dict(_mapping(value, "manifest_verification"))
    exact = (
        set(verification) == _VERIFICATION_FIELDS
        and verification.get("schema_version")
        == "membind.h0.offline-artifact-verification.v3"
        and verification.get("protocol_version") == PROTOCOL_VERSION
        and verification.get("artifact_set_id") == R3_ARTIFACT_SET_ID
        and verification.get("execution_harness_revision") == R3_HARNESS_REVISION
        and verification.get("status") == "verified_offline_not_live_authorized"
        and verification.get("index_path") == R3_INDEX_PATH
        and verification.get("generated_json_file_count") == 11
        and verification.get("binding_count") == 10
        and verification.get("resolved_wrapper_count") == 4
        and verification.get("source_spec_count") == 4
        and isinstance(verification.get("execution_source_count"), int)
        and not isinstance(verification.get("execution_source_count"), bool)
        and verification.get("execution_source_count") == 32
        and verification.get("secret_scan_passed") is True
        and verification.get("live_eligible") is False
    )
    _sha(verification.get("index_sha256"), "manifest_index")
    if not exact:
        raise _fail("r3_manifest_verification_mismatch")
    return verification


def _validate_r4_verification_shape(value: Any) -> dict[str, Any]:
    """Validate the source-bound manifest generated after infrastructure recovery."""

    verification = dict(_mapping(value, "r4_manifest_verification"))
    exact = (
        set(verification) == _VERIFICATION_FIELDS
        and verification.get("schema_version")
        == "membind.h0.offline-artifact-verification.v3"
        and verification.get("protocol_version") == PROTOCOL_VERSION
        and verification.get("artifact_set_id") == R4_ARTIFACT_SET_ID
        and verification.get("execution_harness_revision") == R4_HARNESS_REVISION
        and verification.get("status") == "verified_offline_not_live_authorized"
        and verification.get("index_path") == R4_INDEX_PATH
        and verification.get("generated_json_file_count") == 11
        and verification.get("binding_count") == 10
        and verification.get("resolved_wrapper_count") == 4
        and verification.get("source_spec_count") == 4
        and verification.get("execution_source_count") == 32
        and verification.get("secret_scan_passed") is True
        and verification.get("live_eligible") is False
    )
    _sha(verification.get("index_sha256"), "r4_manifest_index")
    if not exact:
        raise _fail("r4_manifest_verification_mismatch")
    return verification


def _validate_r5_verification_shape(value: Any) -> dict[str, Any]:
    """Validate the post-workload source-bound R5 manifest identity."""

    verification = dict(_mapping(value, "r5_manifest_verification"))
    exact = (
        set(verification) == _VERIFICATION_FIELDS
        and verification.get("schema_version")
        == "membind.h0.offline-artifact-verification.v3"
        and verification.get("protocol_version") == PROTOCOL_VERSION
        and verification.get("artifact_set_id") == R5_ARTIFACT_SET_ID
        and verification.get("execution_harness_revision") == R5_HARNESS_REVISION
        and verification.get("status") == "verified_offline_not_live_authorized"
        and verification.get("index_path") == R5_INDEX_PATH
        and verification.get("generated_json_file_count") == 11
        and verification.get("binding_count") == 10
        and verification.get("resolved_wrapper_count") == 4
        and verification.get("source_spec_count") == 4
        and verification.get("execution_source_count") == 32
        and verification.get("secret_scan_passed") is True
        and verification.get("live_eligible") is False
    )
    _sha(verification.get("index_sha256"), "r5_manifest_index")
    if not exact:
        raise _fail("r5_manifest_verification_mismatch")
    return verification


def _default_r5_manifest_validator(
    root: Path, value: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    verification = _validate_r5_verification_shape(value)
    index, index_path, index_sha = _json_file(
        root,
        verification["index_path"],
        verification["index_sha256"],
        label="r5_manifest_index",
    )
    resolved = index.get("resolved_manifests")
    exact = (
        index.get("protocol_version") == PROTOCOL_VERSION
        and index.get("artifact_set_id") == R5_ARTIFACT_SET_ID
        and index.get("execution_harness_revision") == R5_HARNESS_REVISION
        and index.get("status") == "offline_resolved_not_live_authorized"
        and index.get("live_h0_candidate_authorized") is False
        and index.get("unresolved_fields") == []
        and index.get("source_specs_immutable") is True
        and index.get("secrets_persisted") is False
        and isinstance(resolved, Mapping)
    )
    if not exact:
        raise _fail("r5_manifest_index_contract_mismatch")
    bindings = {
        "resolved_manifest_index_path": index_path,
        "resolved_manifest_index_sha256": index_sha,
    }
    for name, prefix in (
        ("Q1", "resolved_candidate"),
        ("shared_base", "resolved_shared_base"),
    ):
        reference = resolved.get(name)
        if not isinstance(reference, Mapping):
            raise _fail("r5_resolved_manifest_reference_missing")
        _, path, digest = _bound_file(
            root,
            reference.get("path"),
            reference.get("sha256"),
            label=f"r5_{name}",
        )
        bindings[f"{prefix}_manifest_path"] = path
        bindings[f"{prefix}_manifest_sha256"] = digest
    return (
        _validate_bindings(
            bindings, artifact_set_id=R5_ARTIFACT_SET_ID, require_r3_index=False
        ),
        verification,
    )


def _default_r4_manifest_validator(
    root: Path, value: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    verification = _validate_r4_verification_shape(value)
    index, index_path, index_sha = _json_file(
        root,
        verification["index_path"],
        verification["index_sha256"],
        label="r4_manifest_index",
    )
    resolved = index.get("resolved_manifests")
    exact = (
        index.get("protocol_version") == PROTOCOL_VERSION
        and index.get("artifact_set_id") == R4_ARTIFACT_SET_ID
        and index.get("execution_harness_revision") == R4_HARNESS_REVISION
        and index.get("status") == "offline_resolved_not_live_authorized"
        and index.get("live_h0_candidate_authorized") is False
        and index.get("unresolved_fields") == []
        and index.get("source_specs_immutable") is True
        and index.get("secrets_persisted") is False
        and isinstance(resolved, Mapping)
    )
    if not exact:
        raise _fail("r4_manifest_index_contract_mismatch")
    bindings = {
        "resolved_manifest_index_path": index_path,
        "resolved_manifest_index_sha256": index_sha,
    }
    for name, prefix in (("Q1", "resolved_candidate"), ("shared_base", "resolved_shared_base")):
        reference = resolved.get(name)
        if not isinstance(reference, Mapping):
            raise _fail("r4_resolved_manifest_reference_missing")
        _, path, digest = _bound_file(
            root,
            reference.get("path"),
            reference.get("sha256"),
            label=f"r4_{name}",
        )
        bindings[f"{prefix}_manifest_path"] = path
        bindings[f"{prefix}_manifest_sha256"] = digest
    return (
        _validate_bindings(
            bindings, artifact_set_id=R4_ARTIFACT_SET_ID, require_r3_index=False
        ),
        verification,
    )


def _default_manifest_validator(
    root: Path, value: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    verification = _validate_r3_verification_shape(value)
    index, index_path, index_sha = _json_file(
        root,
        verification["index_path"],
        verification["index_sha256"],
        label="r3_manifest_index",
    )
    resolved = index.get("resolved_manifests")
    exact = (
        index.get("protocol_version") == PROTOCOL_VERSION
        and index.get("artifact_set_id") == R3_ARTIFACT_SET_ID
        and index.get("execution_harness_revision") == R3_HARNESS_REVISION
        and index.get("status") == "offline_resolved_not_live_authorized"
        and index.get("live_h0_candidate_authorized") is False
        and index.get("unresolved_fields") == []
        and index.get("source_specs_immutable") is True
        and index.get("secrets_persisted") is False
        and isinstance(resolved, Mapping)
    )
    if not exact:
        raise _fail("r3_manifest_index_contract_mismatch")
    bindings = {
        "resolved_manifest_index_path": index_path,
        "resolved_manifest_index_sha256": index_sha,
    }
    for name, prefix in (("Q1", "resolved_candidate"), ("shared_base", "resolved_shared_base")):
        reference = resolved.get(name)
        if not isinstance(reference, Mapping):
            raise _fail("r3_resolved_manifest_reference_missing")
        _, path, digest = _bound_file(
            root,
            reference.get("path"),
            reference.get("sha256"),
            label=f"r3_{name}",
        )
        bindings[f"{prefix}_manifest_path"] = path
        bindings[f"{prefix}_manifest_sha256"] = digest
    return (
        _validate_bindings(
            bindings, artifact_set_id=R3_ARTIFACT_SET_ID, require_r3_index=True
        ),
        verification,
    )


def _validate_tdd_shape(value: Any) -> dict[str, dict[str, Any]]:
    evidence = _mapping(value, "tdd_evidence")
    if set(evidence) != _TDD_NAMES:
        raise _fail("tdd_evidence_set_mismatch")
    validated: dict[str, dict[str, Any]] = {}
    for name in _TDD_NAMES:
        reference = _mapping(evidence.get(name), f"tdd_{name}")
        count = reference.get("test_count")
        if (
            set(reference) != {"path", "sha256", "test_count"}
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
        ):
            raise _fail("tdd_evidence_reference_invalid")
        validated[name] = {
            "path": _relative_path(reference.get("path"), f"tdd_{name}"),
            "sha256": _sha(reference.get("sha256"), f"tdd_{name}"),
            "test_count": count,
        }
    return validated


def _default_tdd_validator(
    root: Path, value: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    validated = _validate_tdd_shape(value)
    for name, reference in validated.items():
        _bound_file(
            root,
            reference["path"],
            reference["sha256"],
            label=f"tdd_{name}",
        )
    return validated


def _default_decision_verifier(
    *,
    root: Path,
    decision_path: str,
    decision_sha256: str,
    manifest_verification: Mapping[str, Any],
) -> dict[str, Any]:
    return verify_h0_b_harness_repair_decision(
        root=root,
        decision_path=decision_path,
        decision_sha256=decision_sha256,
        manifest_verification=manifest_verification,
    )


def _validate_revoked_source(source_state: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = dict(_mapping(source_state, "source_state"))
    _safe_value(source, location="source_state")
    progress = source.get("stage_progress")
    invalidation = source.get("h0_b_harness_invalidation")
    completions = source.get("h0_phase_completions")
    exact = (
        source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("current_stage") == "H0"
        and source.get("status") == REVOKED_STATUS
        and source.get("current_action_scope") == REVOKED_SCOPE
        and source.get("current_blocker") == FAILURE_REASON
        and source.get("live_h0_candidate_authorized") is False
        and source.get("authorized_live_actions") == []
        and source.get("authorized_h0_candidate_id") is None
        and source.get("live_h0_authorization") is None
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == "forbidden"
        and isinstance(invalidation, Mapping)
        and invalidation.get("schema_version") == "membind.h0.harness-invalidation.v1"
        and invalidation.get("protocol_version") == PROTOCOL_VERSION
        and invalidation.get("status") == "invalidated_no_rerun_or_advance_authorized"
        and invalidation.get("reason") == FAILURE_REASON
        and invalidation.get("candidate_id") == "Q1"
        and invalidation.get("phase") == "H0-B"
        and invalidation.get("candidate_rerun_authorized") is False
        and invalidation.get("candidate_advance_authorized") is False
        and invalidation.get("live_transition_authorized") is False
        and invalidation.get("secrets_persisted") is False
        and isinstance(completions, Mapping)
        and set(completions) == {"H0-A"}
    )
    if not exact:
        raise _fail("source_not_exact_h0_b_harness_revoked_state")
    completion = _validate_h0_a_completion(completions.get("H0-A"))
    old_bindings = _validate_bindings(
        invalidation.get("prior_manifest_bindings"),
        artifact_set_id=R2_ARTIFACT_SET_ID,
        require_r3_index=False,
    )
    _identifier(invalidation.get("stage_attempt_id"), "invalidated_stage_attempt_id")
    _sha(invalidation.get("checkpoint_index_sha256"), "invalidated_checkpoint")
    _sha(invalidation.get("failure_report_sha256"), "invalidated_failure_report")
    return source, dict(invalidation), {"completion": completion, "bindings": old_bindings}


def _validate_admission(
    value: Any,
    *,
    invalidation: Mapping[str, Any],
    old_bindings: Mapping[str, str],
    new_bindings: Mapping[str, str],
    decision_path: str,
    decision_sha256: str,
) -> dict[str, Any]:
    admission = dict(_mapping(value, "repair_admission"))
    replacement = admission.get("replacement_attempt_id")
    exact = (
        set(admission) == _ADMISSION_FIELDS
        and admission.get("schema_version") == "membind.h0.harness-repair-admission.v1"
        and admission.get("protocol_version") == PROTOCOL_VERSION
        and admission.get("candidate_id") == "Q1"
        and admission.get("phase") == "H0-B"
        and admission.get("decision_path") == decision_path
        and admission.get("decision_sha256") == decision_sha256
        and admission.get("decision_result_blind") is False
        and admission.get("prior_model_workload_output_observed") is False
        and admission.get("repair_required_independent_of_model_output") is True
        and admission.get("scientific_configuration_unchanged") is True
        and admission.get("one_shot_whole_stage_replacement") is True
        and admission.get("invalidated_stage_attempt_id")
        == invalidation.get("stage_attempt_id")
        and admission.get("invalidated_checkpoint_index_sha256")
        == invalidation.get("checkpoint_index_sha256")
        and admission.get("failure_report_sha256")
        == invalidation.get("failure_report_sha256")
        and admission.get("old_attempt_qualification_reusable") is False
        and admission.get("old_and_new_trial_counts_mergeable") is False
        and admission.get("prior_manifest_index_sha256")
        == old_bindings.get("resolved_manifest_index_sha256")
        and admission.get("repaired_manifest_index_sha256")
        == new_bindings.get("resolved_manifest_index_sha256")
        and admission.get("secrets_persisted") is False
        and replacement != invalidation.get("stage_attempt_id")
    )
    _identifier(replacement, "replacement_attempt_id")
    for field in (
        "decision_sha256",
        "invalidated_checkpoint_index_sha256",
        "failure_report_sha256",
        "prior_manifest_index_sha256",
        "repaired_manifest_index_sha256",
    ):
        _sha(admission.get(field), field)
    _relative_path(admission.get("decision_path"), "decision")
    if not exact:
        raise _fail("h0_b_harness_repair_admission_mismatch")
    return admission


def build_h0_b_harness_repair_bound_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
    repair_decision_path: str,
    repair_decision_sha256: str,
    manifest_validator: Callable[..., Any] = _default_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    repair_decision_verifier: Callable[..., Any] = _default_decision_verifier,
) -> dict[str, Any]:
    """Bind r3, TDD, and a disclosed decision while retaining a closed gate."""

    root_path = Path(root).resolve()
    source, invalidation, retained = _validate_revoked_source(source_state)
    try:
        raw_bindings, raw_verification = manifest_validator(
            root_path, _mapping(manifest_verification, "manifest_verification")
        )
        raw_tdd = tdd_validator(root_path, _mapping(tdd_evidence, "tdd_evidence"))
        raw_admission = repair_decision_verifier(
            root=root_path,
            decision_path=repair_decision_path,
            decision_sha256=repair_decision_sha256,
            manifest_verification=manifest_verification,
        )
    except H0HarnessRecoveryError:
        raise
    except Exception as exc:
        raise _fail("offline_repair_evidence_validation_failed") from exc
    bindings = _validate_bindings(
        raw_bindings, artifact_set_id=R3_ARTIFACT_SET_ID, require_r3_index=True
    )
    verification = _validate_r3_verification_shape(raw_verification)
    verified_tdd = _validate_tdd_shape(raw_tdd)
    if verification != dict(manifest_verification) or verified_tdd != dict(tdd_evidence):
        raise _fail("verified_evidence_differs_from_requested_evidence")
    if verification.get("index_sha256") != bindings["resolved_manifest_index_sha256"]:
        raise _fail("r3_index_binding_mismatch")
    admission = _validate_admission(
        raw_admission,
        invalidation=invalidation,
        old_bindings=retained["bindings"],
        new_bindings=bindings,
        decision_path=repair_decision_path,
        decision_sha256=repair_decision_sha256,
    )

    state = deepcopy(source)
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden",
            "h0_candidate_progression": "h0_b_repair_verified_replacement_not_authorized",
            "h0_offline_manifest_binding": "v1_3_harness_r3_verified",
        }
    )
    state.update(
        {
            "status": REPAIR_BOUND_STATUS,
            "current_action_scope": REPAIR_BOUND_SCOPE,
            "current_blocker": "replacement_h0_b_not_yet_authorized",
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "next_allowed_action": "explicit_q1_h0_b_replacement_transition",
            "h0_phase_completions": {"H0-A": retained["completion"]},
            "h0_b_harness_repair_live_prerequisites": {
                "schema_version": "membind.h0.harness-repair-live-prerequisites.v1",
                "protocol_version": PROTOCOL_VERSION,
                "status": "verified_not_live_authorized",
                "candidate_id": "Q1",
                "phase": "H0-B",
                "artifact_bindings": bindings,
                "manifest_verification": verification,
                "tdd_evidence": verified_tdd,
                "repair_admission": admission,
                "live_transition_performed": False,
            },
        }
    )
    state.pop("live_h0_authorization", None)
    return state


def _load_checkpoint_index_for_recovery(
    root: Path,
    *,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
) -> dict[str, Any]:
    """Read one terminal checkpoint without accepting a resume or symlink."""

    _identifier(stage_attempt_id, "interrupted_stage_attempt_id")
    _sha(checkpoint_index_sha256, "interrupted_checkpoint")
    relative = _relative_path(checkpoint_index_path, "interrupted_checkpoint_path")
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise _fail("interrupted_checkpoint_missing")
    try:
        path.resolve().relative_to(root)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise _fail("interrupted_checkpoint_invalid") from None
    if sha256_file(path) != checkpoint_index_sha256:
        raise _fail("interrupted_checkpoint_hash_mismatch")
    if not isinstance(value, Mapping):
        raise _fail("interrupted_checkpoint_not_object")
    if (
        value.get("stage_attempt_id") != stage_attempt_id
        or value.get("candidate_id") != "Q1"
        or value.get("phase") != "H0-B"
        or value.get("status") != "infrastructure_interrupted"
        or value.get("stop_reason") != INFRASTRUCTURE_STOP_REASON
        or value.get("candidate_advance_allowed") is not False
        or value.get("partial_qualification_reusable") is not False
        or value.get("requires_whole_stage_rerun") is not True
        or value.get("segments") is None
        or value.get("secrets_persisted") is not False
    ):
        raise _fail("interrupted_checkpoint_terminal_contract_mismatch")
    _safe_value(value, location="interrupted_checkpoint")
    return dict(value)


def build_h0_b_infrastructure_interrupted_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
) -> dict[str, Any]:
    """Close the consumed r3 grant and preserve only its infrastructure fact."""

    source = _validate_transition_state(source_state, label="infrastructure_source")
    if (
        source.get("protocol_version") != PROTOCOL_VERSION
        or source.get("current_stage") != "H0"
        or source.get("status") != "h0_q1_b_live_only"
        or source.get("current_action_scope") != "h0_q1_b_live_only"
        or source.get("live_h0_candidate_authorized") is not True
        or source.get("authorized_h0_candidate_id") != "Q1"
        or source.get("authorized_live_actions") != ["h0_candidate"]
    ):
        raise _fail("source_not_consumed_h0_b_live_grant")
    authorization = _mapping(source.get("live_h0_authorization"), "live_authorization")
    if (
        authorization.get("candidate_id") != "Q1"
        or authorization.get("phase") != "H0-B"
        or authorization.get("authorized_stage_attempt_id") != stage_attempt_id
        or stage_attempt_id != INFRASTRUCTURE_INTERRUPTED_ATTEMPT_ID
    ):
        raise _fail("consumed_grant_attempt_mismatch")
    checkpoint = _load_checkpoint_index_for_recovery(
        Path(root).resolve(),
        stage_attempt_id=stage_attempt_id,
        checkpoint_index_path=checkpoint_index_path,
        checkpoint_index_sha256=checkpoint_index_sha256,
    )
    repair = _mapping(authorization.get("repair_admission"), "repair_admission")
    if (
        repair.get("replacement_attempt_id") != stage_attempt_id
        or repair.get("repaired_manifest_index_sha256")
        != authorization.get("resolved_manifest_index_sha256")
        or checkpoint.get("repair_admission") != dict(repair)
    ):
        raise _fail("consumed_grant_manifest_binding_mismatch")
    progress = deepcopy(dict(_mapping(source.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden",
            "h0_candidate_progression": "h0_b_infrastructure_interruption_recorded",
            "h0_offline_manifest_binding": "v1_3_harness_r3_verified",
        }
    )
    state = deepcopy(source)
    state.update(
        {
            "status": INFRASTRUCTURE_RERUN_STATUS,
            "current_action_scope": INFRASTRUCTURE_RERUN_SCOPE,
            "current_blocker": INFRASTRUCTURE_STOP_REASON,
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "next_allowed_action": "bind_h0_b_infrastructure_rerun",
            "h0_b_infrastructure_interruption": {
                "schema_version": "membind.h0.infrastructure-interruption.v1",
                "protocol_version": PROTOCOL_VERSION,
                "candidate_id": "Q1",
                "phase": "H0-B",
                "stage_attempt_id": stage_attempt_id,
                "checkpoint_index_path": checkpoint_index_path,
                "checkpoint_index_sha256": checkpoint_index_sha256,
                "stop_reason": INFRASTRUCTURE_STOP_REASON,
                "resume_authorized": False,
                "rerun_authorized": False,
                "old_and_new_trial_counts_mergeable": False,
                "partial_qualification_reusable": False,
                "prior_manifest_bindings": {
                    field: authorization.get(field) for field in _BINDING_FIELDS
                },
                "prior_phase_completion": deepcopy(
                    authorization.get("prior_phase_completion")
                ),
                "prior_harness_repair_admission": deepcopy(dict(repair)),
                "prior_harness_repair_admission_sha256": canonical_json_sha256(
                    repair
                ),
                "secrets_persisted": False,
            },
        }
    )
    state.pop("live_h0_authorization", None)
    return state


def transition_h0_b_infrastructure_interrupted(
    state_path: str | Path,
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = build_h0_b_infrastructure_interrupted_state,
) -> dict[str, Any]:
    """Preview or atomically close the consumed infrastructure-interrupted grant."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "stage_attempt_id": stage_attempt_id,
            "checkpoint_index_path": checkpoint_index_path,
            "checkpoint_index_sha256": checkpoint_index_sha256,
        },
        label="infrastructure_interrupted",
        state_validator=lambda state: _validate_live_forbidden(
            state, label="infrastructure_interrupted"
        ),
    )


def build_h0_b_post_workload_harness_revoked_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    failure_segment_path: str,
    failure_segment_sha256: str,
    source_checkpoint_path: str,
    source_checkpoint_sha256: str,
    live_log_path: str,
    live_log_sha256: str,
    offline_probe_path: str,
    offline_probe_sha256: str,
) -> dict[str, Any]:
    """Revoke the consumed 002 grant and retain only immutable diagnostics."""

    source = _validate_transition_state(source_state, label="post_workload_source")
    authorization = _mapping(
        source.get("live_h0_authorization"), "post_workload_live_authorization"
    )
    exact_live = (
        source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("current_stage") == "H0"
        and source.get("status") == "h0_q1_b_live_only"
        and source.get("current_action_scope") == "h0_q1_b_live_only"
        and source.get("live_h0_candidate_authorized") is True
        and source.get("authorized_live_actions") == ["h0_candidate"]
        and source.get("authorized_h0_candidate_id") == "Q1"
        and authorization.get("candidate_id") == "Q1"
        and authorization.get("phase") == "H0-B"
        and authorization.get("authorized_stage_attempt_id") == stage_attempt_id
        and stage_attempt_id == POST_WORKLOAD_FAILED_ATTEMPT_ID
        and authorization.get("resolved_manifest_index_path") == R4_INDEX_PATH
        and authorization.get("resolved_manifest_index_sha256")
        == "a08b3f704c9680476990f24edc239d4af50ced39edcf9aae0d529b5ed14332d7"
    )
    if not exact_live:
        raise _fail("source_not_consumed_post_workload_live_grant")
    classification = classify_h0_b_post_workload_harness_failure(
        root=root,
        stage_attempt_id=stage_attempt_id,
        checkpoint_index_path=checkpoint_index_path,
        checkpoint_index_sha256=checkpoint_index_sha256,
        failure_segment_path=failure_segment_path,
        failure_segment_sha256=failure_segment_sha256,
        source_checkpoint_path=source_checkpoint_path,
        source_checkpoint_sha256=source_checkpoint_sha256,
        live_log_path=live_log_path,
        live_log_sha256=live_log_sha256,
        offline_probe_path=offline_probe_path,
        offline_probe_sha256=offline_probe_sha256,
    )
    checkpoint, _, _ = _json_file(
        Path(root).resolve(),
        checkpoint_index_path,
        checkpoint_index_sha256,
        label="post_workload_checkpoint",
    )
    repair = _mapping(authorization.get("repair_admission"), "repair_admission")
    infrastructure = _mapping(
        authorization.get("infrastructure_rerun_admission"),
        "infrastructure_rerun_admission",
    )
    if (
        checkpoint.get("repair_admission") != dict(repair)
        or checkpoint.get("infrastructure_rerun_admission")
        != dict(infrastructure)
        or checkpoint.get("prior_matching_attempt_count") != 2
        or checkpoint.get("infrastructure_interrupted_attempt_count") != 1
    ):
        raise _fail("post_workload_prior_repair_chain_mismatch")

    state = deepcopy(source)
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden",
            "h0_candidate_progression": "h0_b_post_workload_harness_failure_recorded",
            "h0_offline_manifest_binding": "v1_3_harness_r4_failure_bound",
        }
    )
    state.update(
        {
            "status": POST_WORKLOAD_REVOKED_STATUS,
            "current_action_scope": POST_WORKLOAD_REVOKED_SCOPE,
            "current_blocker": "manifest_contract_failure",
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "next_allowed_action": "prepare_h0_b_post_workload_harness_repair",
            "h0_b_post_workload_harness_failure": classification,
            "h0_b_post_workload_harness_repair_context": {
                "schema_version": "membind.h0.post-workload-harness-repair-context.v1",
                "protocol_version": PROTOCOL_VERSION,
                "candidate_id": "Q1",
                "phase": "H0-B",
                "prior_manifest_bindings": {
                    field: authorization.get(field) for field in _BINDING_FIELDS
                },
                "prior_phase_completion": deepcopy(
                    authorization.get("prior_phase_completion")
                ),
                "prior_harness_repair_admission": deepcopy(dict(repair)),
                "prior_harness_repair_admission_sha256": canonical_json_sha256(
                    repair
                ),
                "prior_infrastructure_rerun_admission": deepcopy(
                    dict(infrastructure)
                ),
                "prior_infrastructure_rerun_admission_sha256": canonical_json_sha256(
                    infrastructure
                ),
                "replacement_attempt_id": POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID,
                "live_authorized": False,
                "secrets_persisted": False,
            },
        }
    )
    state.pop("live_h0_authorization", None)
    return state


def transition_h0_b_post_workload_harness_revoke(
    state_path: str | Path,
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    failure_segment_path: str,
    failure_segment_sha256: str,
    source_checkpoint_path: str,
    source_checkpoint_sha256: str,
    live_log_path: str,
    live_log_sha256: str,
    offline_probe_path: str,
    offline_probe_sha256: str,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = build_h0_b_post_workload_harness_revoked_state,
) -> dict[str, Any]:
    """Preview or atomically revoke the terminal replacement-002 grant."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "stage_attempt_id": stage_attempt_id,
            "checkpoint_index_path": checkpoint_index_path,
            "checkpoint_index_sha256": checkpoint_index_sha256,
            "failure_segment_path": failure_segment_path,
            "failure_segment_sha256": failure_segment_sha256,
            "source_checkpoint_path": source_checkpoint_path,
            "source_checkpoint_sha256": source_checkpoint_sha256,
            "live_log_path": live_log_path,
            "live_log_sha256": live_log_sha256,
            "offline_probe_path": offline_probe_path,
            "offline_probe_sha256": offline_probe_sha256,
        },
        label="post_workload_harness_revoke",
        state_validator=lambda state: _validate_live_forbidden(
            state, label="post_workload_harness_revoke"
        ),
    )


def _validate_infrastructure_rerun_source(
    source_state: Any,
    *,
    root: Path,
    manifest_validator: Callable[..., Any],
    tdd_validator: Callable[..., Any],
    rerun_decision_verifier: Callable[..., Any],
    manifest_verification: Mapping[str, Any] | None = None,
    tdd_evidence: Mapping[str, Any] | None = None,
    rerun_decision_path: str | None = None,
    rerun_decision_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Revalidate closed interruption evidence and the complete R4 offline set."""

    source = _validate_transition_state(source_state, label="rerun_source")
    state_mode_exact = (
        (
            source.get("status") == INFRASTRUCTURE_RERUN_STATUS
            and source.get("current_action_scope") == INFRASTRUCTURE_RERUN_SCOPE
            and source.get("current_blocker") == INFRASTRUCTURE_STOP_REASON
        )
        or (
            source.get("status")
            == "h0_b_infrastructure_rerun_verified_not_live_authorized"
            and source.get("current_action_scope")
            == "h0_b_infrastructure_rerun_verified_only"
            and source.get("current_blocker")
            == "replacement_h0_b_infrastructure_rerun_not_yet_authorized"
        )
    )
    if (
        source.get("protocol_version") != PROTOCOL_VERSION
        or source.get("current_stage") != "H0"
        or not state_mode_exact
        or source.get("live_h0_candidate_authorized") is not False
        or source.get("authorized_live_actions") != []
        or source.get("authorized_h0_candidate_id") is not None
    ):
        raise _fail("source_not_exact_infrastructure_interrupted_state")
    interruption = dict(
        _mapping(source.get("h0_b_infrastructure_interruption"), "interruption")
    )
    if (
        interruption.get("stage_attempt_id") != INFRASTRUCTURE_INTERRUPTED_ATTEMPT_ID
        or interruption.get("stop_reason") != INFRASTRUCTURE_STOP_REASON
        or interruption.get("resume_authorized") is not False
        or interruption.get("rerun_authorized") is not False
        or interruption.get("old_and_new_trial_counts_mergeable") is not False
        or interruption.get("partial_qualification_reusable") is not False
        or interruption.get("secrets_persisted") is not False
    ):
        raise _fail("interruption_record_contract_mismatch")
    prior_bindings = _validate_bindings(
        interruption.get("prior_manifest_bindings"),
        artifact_set_id=R3_ARTIFACT_SET_ID,
        require_r3_index=True,
    )
    repair = dict(
        _mapping(
            interruption.get("prior_harness_repair_admission"),
            "prior_harness_repair_admission",
        )
    )
    if interruption.get("prior_harness_repair_admission_sha256") != canonical_json_sha256(
        repair
    ):
        raise _fail("prior_repair_admission_hash_mismatch")
    if manifest_verification is None or tdd_evidence is None:
        raise _fail("rerun_offline_evidence_missing")
    try:
        raw_bindings, raw_verification = manifest_validator(
            root, _mapping(manifest_verification, "r4_manifest_verification")
        )
        raw_tdd = tdd_validator(root, _mapping(tdd_evidence, "r4_tdd_evidence"))
        raw_admission = rerun_decision_verifier(
            root=root,
            decision_path=rerun_decision_path,
            decision_sha256=rerun_decision_sha256,
            manifest_verification=manifest_verification,
        )
    except H0HarnessRecoveryError:
        raise
    except Exception as exc:
        raise _fail("rerun_offline_evidence_validation_failed") from exc
    bindings = _validate_bindings(
        raw_bindings, artifact_set_id=R4_ARTIFACT_SET_ID, require_r3_index=False
    )
    verification = _validate_r4_verification_shape(raw_verification)
    tdd = _validate_tdd_shape(raw_tdd)
    admission = dict(_mapping(raw_admission, "infrastructure_rerun_admission"))
    if (
        dict(raw_verification) != dict(manifest_verification)
        or dict(raw_tdd) != dict(tdd_evidence)
        or admission.get("schema_version")
        != "membind.h0.infrastructure-rerun-admission.v1"
        or admission.get("candidate_id") != "Q1"
        or admission.get("phase") != "H0-B"
        or admission.get("replacement_attempt_id") != INFRASTRUCTURE_RERUN_ATTEMPT_ID
        or admission.get("interrupted_stage_attempt_id")
        != interruption.get("stage_attempt_id")
        or admission.get("interrupted_checkpoint_index_sha256")
        != interruption.get("checkpoint_index_sha256")
        or admission.get("interrupted_stop_reason") != INFRASTRUCTURE_STOP_REASON
        or admission.get("prior_harness_repair_admission_sha256")
        != interruption.get("prior_harness_repair_admission_sha256")
        or admission.get("prior_manifest_index_sha256")
        != prior_bindings.get("resolved_manifest_index_sha256")
        or admission.get("recovered_manifest_index_sha256")
        != bindings.get("resolved_manifest_index_sha256")
        or admission.get("one_shot_whole_stage_replacement") is not True
        or admission.get("resume_interrupted_attempt_allowed") is not False
        or admission.get("prior_attempt_qualification_reusable") is not False
        or admission.get("old_and_new_trial_counts_mergeable") is not False
        or admission.get("scientific_configuration_unchanged") is not True
        or admission.get("secrets_persisted") is not False
    ):
        raise _fail("infrastructure_rerun_admission_mismatch")
    return source, bindings, verification, tdd, {
        "interruption": interruption,
        "prior_bindings": prior_bindings,
        "repair": repair,
        "admission": admission,
    }


def build_h0_b_infrastructure_rerun_bound_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
    rerun_decision_path: str,
    rerun_decision_sha256: str,
    manifest_validator: Callable[..., Any] = _default_r4_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    rerun_decision_verifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Bind R4 and the transparent infrastructure decision while live stays closed."""

    if rerun_decision_verifier is None:
        from h0_repair_admission import verify_h0_b_infrastructure_rerun_decision

        rerun_decision_verifier = verify_h0_b_infrastructure_rerun_decision
    source, bindings, verification, tdd, retained = _validate_infrastructure_rerun_source(
        source_state,
        root=Path(root).resolve(),
        manifest_validator=manifest_validator,
        tdd_validator=tdd_validator,
        rerun_decision_verifier=rerun_decision_verifier,
        manifest_verification=manifest_verification,
        tdd_evidence=tdd_evidence,
        rerun_decision_path=rerun_decision_path,
        rerun_decision_sha256=rerun_decision_sha256,
    )
    progress = deepcopy(dict(_mapping(source.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden",
            "h0_candidate_progression": "h0_b_infrastructure_rerun_verified_not_authorized",
            "h0_offline_manifest_binding": "v1_3_harness_r4_verified",
        }
    )
    state = deepcopy(source)
    state.update(
        {
            "status": "h0_b_infrastructure_rerun_verified_not_live_authorized",
            "current_action_scope": "h0_b_infrastructure_rerun_verified_only",
            "current_blocker": "replacement_h0_b_infrastructure_rerun_not_yet_authorized",
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "next_allowed_action": "explicit_q1_h0_b_infrastructure_rerun_transition",
            "h0_b_infrastructure_rerun_live_prerequisites": {
                "schema_version": "membind.h0.infrastructure-rerun-live-prerequisites.v1",
                "protocol_version": PROTOCOL_VERSION,
                "status": "verified_not_live_authorized",
                "candidate_id": "Q1",
                "phase": "H0-B",
                "artifact_bindings": bindings,
                "manifest_verification": verification,
                "tdd_evidence": tdd,
                "prior_manifest_bindings": retained["prior_bindings"],
                "prior_harness_repair_admission": retained["repair"],
                "infrastructure_rerun_admission": retained["admission"],
                "live_transition_performed": False,
                "secrets_persisted": False,
            },
        }
    )
    return state


def build_h0_b_infrastructure_rerun_live_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_validator: Callable[..., Any] = _default_r4_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    rerun_decision_verifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Consume exactly one R4 infrastructure-rerun grant for replacement-002."""

    prerequisites = _mapping(
        source_state.get("h0_b_infrastructure_rerun_live_prerequisites"),
        "rerun_prerequisites",
    )
    if (
        source_state.get("status")
        != "h0_b_infrastructure_rerun_verified_not_live_authorized"
        or prerequisites.get("live_transition_performed") is not False
    ):
        raise _fail("source_not_exact_rerun_bound_state")
    if rerun_decision_verifier is None:
        from h0_repair_admission import verify_h0_b_infrastructure_rerun_decision

        rerun_decision_verifier = verify_h0_b_infrastructure_rerun_decision
    persisted_admission = _mapping(
        prerequisites.get("infrastructure_rerun_admission"), "rerun_admission"
    )
    _, bindings, verification, tdd, retained = _validate_infrastructure_rerun_source(
        source_state,
        root=Path(root).resolve(),
        manifest_validator=manifest_validator,
        tdd_validator=tdd_validator,
        rerun_decision_verifier=rerun_decision_verifier,
        manifest_verification=prerequisites.get("manifest_verification"),
        tdd_evidence=prerequisites.get("tdd_evidence"),
        rerun_decision_path=str(persisted_admission.get("decision_path") or ""),
        rerun_decision_sha256=str(
            persisted_admission.get("decision_sha256") or ""
        ),
    )
    completion = _validate_h0_a_completion(
        _mapping(source_state.get("h0_phase_completions"), "phase_completions").get(
            "H0-A"
        )
    )
    progress = deepcopy(dict(_mapping(source_state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "h0_q1_b_live_only",
            "h0_candidate_progression": "h0_b_infrastructure_rerun_authorized_once",
            "h0_offline_manifest_binding": "v1_3_harness_r4_verified",
        }
    )
    state = deepcopy(dict(source_state))
    state.update(
        {
            "status": "h0_q1_b_live_only",
            "current_action_scope": "h0_q1_b_live_only",
            "current_blocker": None,
            "stage_progress": progress,
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "next_allowed_action": "run_q1_h0-b-infrastructure-rerun",
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-B",
                **bindings,
                "prior_phase_completion": {
                    field: completion[field]
                    for field in (
                        "stage_attempt_id",
                        "checkpoint_index_path",
                        "checkpoint_index_sha256",
                        "runtime_definition_sha256",
                        "terminal_result_sha256",
                    )
                },
                "authorized_stage_attempt_id": INFRASTRUCTURE_RERUN_ATTEMPT_ID,
                "repair_admission": retained["repair"],
                "infrastructure_rerun_admission": retained["admission"],
            },
        }
    )
    state["h0_b_infrastructure_rerun_live_prerequisites"] = {
        **deepcopy(dict(prerequisites)),
        "artifact_bindings": bindings,
        "manifest_verification": verification,
        "tdd_evidence": tdd,
        "live_transition_performed": True,
    }
    return state


def _validate_repair_bound_source(
    source_state: Any,
    *,
    root: Path,
    manifest_validator: Callable[..., Any],
    tdd_validator: Callable[..., Any],
    repair_decision_verifier: Callable[..., Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = dict(_mapping(source_state, "source_state"))
    _safe_value(source, location="source_state")
    progress = source.get("stage_progress")
    prerequisites = source.get("h0_b_harness_repair_live_prerequisites")
    exact = (
        source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("current_stage") == "H0"
        and source.get("status") == REPAIR_BOUND_STATUS
        and source.get("current_action_scope") == REPAIR_BOUND_SCOPE
        and source.get("current_blocker") == "replacement_h0_b_not_yet_authorized"
        and source.get("live_h0_candidate_authorized") is False
        and source.get("authorized_live_actions") == []
        and source.get("authorized_h0_candidate_id") is None
        and source.get("live_h0_authorization") is None
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == "forbidden"
        and isinstance(prerequisites, Mapping)
        and prerequisites.get("schema_version")
        == "membind.h0.harness-repair-live-prerequisites.v1"
        and prerequisites.get("protocol_version") == PROTOCOL_VERSION
        and prerequisites.get("status") == "verified_not_live_authorized"
        and prerequisites.get("candidate_id") == "Q1"
        and prerequisites.get("phase") == "H0-B"
        and prerequisites.get("live_transition_performed") is False
    )
    if not exact:
        raise _fail("source_not_exact_h0_b_repair_bound_state")
    invalidation = dict(_mapping(source.get("h0_b_harness_invalidation"), "invalidation"))
    completion = _validate_h0_a_completion(
        _mapping(source.get("h0_phase_completions"), "phase_completions").get("H0-A")
    )
    verification = _validate_r3_verification_shape(
        prerequisites.get("manifest_verification")
    )
    tdd = _validate_tdd_shape(prerequisites.get("tdd_evidence"))
    bindings = _validate_bindings(
        prerequisites.get("artifact_bindings"),
        artifact_set_id=R3_ARTIFACT_SET_ID,
        require_r3_index=True,
    )
    old_bindings = _validate_bindings(
        invalidation.get("prior_manifest_bindings"),
        artifact_set_id=R2_ARTIFACT_SET_ID,
        require_r3_index=False,
    )
    decision = _mapping(prerequisites.get("repair_admission"), "repair_admission")
    try:
        checked_bindings, checked_verification = manifest_validator(root, verification)
        checked_tdd = tdd_validator(root, tdd)
        checked_decision = repair_decision_verifier(
            root=root,
            decision_path=decision.get("decision_path"),
            decision_sha256=decision.get("decision_sha256"),
            manifest_verification=verification,
        )
    except H0HarnessRecoveryError:
        raise
    except Exception as exc:
        raise _fail("persisted_repair_evidence_revalidation_failed") from exc
    if (
        dict(checked_bindings) != bindings
        or dict(checked_verification) != verification
        or dict(checked_tdd) != tdd
    ):
        raise _fail("persisted_repair_evidence_changed")
    admission = _validate_admission(
        checked_decision,
        invalidation=invalidation,
        old_bindings=old_bindings,
        new_bindings=bindings,
        decision_path=str(decision.get("decision_path") or ""),
        decision_sha256=str(decision.get("decision_sha256") or ""),
    )
    if admission != dict(decision):
        raise _fail("persisted_repair_decision_changed")
    return source, dict(prerequisites), {"completion": completion, "admission": admission, "bindings": bindings}


def build_h0_b_replacement_live_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_validator: Callable[..., Any] = _default_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    repair_decision_verifier: Callable[..., Any] = _default_decision_verifier,
) -> dict[str, Any]:
    """Authorize exactly one r3 whole-stage H0-B replacement attempt."""

    source, prerequisites, retained = _validate_repair_bound_source(
        source_state,
        root=Path(root).resolve(),
        manifest_validator=manifest_validator,
        tdd_validator=tdd_validator,
        repair_decision_verifier=repair_decision_verifier,
    )
    completion = retained["completion"]
    prior_reference = {
        field: completion[field]
        for field in (
            "stage_attempt_id",
            "checkpoint_index_path",
            "checkpoint_index_sha256",
            "runtime_definition_sha256",
            "terminal_result_sha256",
        )
    }
    state = deepcopy(source)
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "h0_q1_b_live_only",
            "h0_candidate_progression": "h0_b_harness_replacement_authorized_once",
            "h0_offline_manifest_binding": "v1_3_harness_r3_verified",
        }
    )
    state.update(
        {
            "status": "h0_q1_b_live_only",
            "current_action_scope": "h0_q1_b_live_only",
            "current_blocker": None,
            "stage_progress": progress,
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "next_allowed_action": "run_q1_h0-b_replacement",
            "h0_phase_completions": {"H0-A": completion},
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-B",
                **retained["bindings"],
                "prior_phase_completion": prior_reference,
                "authorized_stage_attempt_id": retained["admission"][
                    "replacement_attempt_id"
                ],
                "repair_admission": retained["admission"],
            },
        }
    )
    state["h0_b_harness_repair_live_prerequisites"] = {
        **deepcopy(prerequisites),
        "live_transition_performed": True,
    }
    return state


def _validate_post_workload_admission(
    value: Any,
    *,
    classification: Mapping[str, Any],
    context: Mapping[str, Any],
    bindings: Mapping[str, str],
    decision_path: str,
    decision_sha256: str,
) -> dict[str, Any]:
    admission = dict(_mapping(value, "post_workload_repair_admission"))
    exact = (
        set(admission) == _POST_WORKLOAD_ADMISSION_FIELDS
        and admission.get("schema_version")
        == "membind.h0.post-workload-harness-repair-admission.v1"
        and admission.get("protocol_version") == PROTOCOL_VERSION
        and admission.get("candidate_id") == "Q1"
        and admission.get("phase") == "H0-B"
        and admission.get("decision_path") == decision_path
        and admission.get("decision_sha256") == decision_sha256
        and admission.get("decision_result_blind") is False
        and admission.get("prior_model_workload_output_observed") is True
        and admission.get(
            "repair_required_independent_of_model_response_content"
        )
        is True
        and admission.get("scientific_configuration_unchanged") is True
        and admission.get("one_shot_whole_stage_replacement") is True
        and admission.get("replacement_attempt_id")
        == POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID
        and admission.get("invalidated_stage_attempt_id")
        == classification.get("stage_attempt_id")
        and admission.get("invalidated_checkpoint_index_sha256")
        == classification.get("checkpoint_index_sha256")
        and admission.get("failure_segment_sha256")
        == classification.get("failure_segment_sha256")
        and admission.get("source_checkpoint_sha256")
        == classification.get("source_checkpoint_sha256")
        and admission.get("live_log_sha256")
        == classification.get("live_log_sha256")
        and admission.get("offline_probe_sha256")
        == classification.get("offline_probe_sha256")
        and admission.get("prior_harness_repair_admission_sha256")
        == context.get("prior_harness_repair_admission_sha256")
        and admission.get("prior_infrastructure_rerun_admission_sha256")
        == context.get("prior_infrastructure_rerun_admission_sha256")
        and admission.get("old_attempt_qualification_reusable") is False
        and admission.get("old_and_new_trial_counts_mergeable") is False
        and admission.get("resume_failed_attempt_allowed") is False
        and admission.get("prior_manifest_index_sha256")
        == context.get("prior_manifest_bindings", {}).get(
            "resolved_manifest_index_sha256"
        )
        and admission.get("repaired_manifest_index_sha256")
        == bindings.get("resolved_manifest_index_sha256")
        and admission.get("secrets_persisted") is False
    )
    for field in _POST_WORKLOAD_ADMISSION_FIELDS:
        if field.endswith("sha256"):
            _sha(admission.get(field), f"post_workload_{field}")
    _safe_value(admission, location="post_workload_repair_admission")
    if not exact:
        raise _fail("post_workload_repair_admission_mismatch")
    return admission


def _validate_post_workload_revoked_source(
    source_state: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = _validate_transition_state(
        source_state, label="post_workload_revoked_source"
    )
    classification = dict(
        _mapping(
            source.get("h0_b_post_workload_harness_failure"),
            "post_workload_failure",
        )
    )
    context = dict(
        _mapping(
            source.get("h0_b_post_workload_harness_repair_context"),
            "post_workload_context",
        )
    )
    repair = _mapping(
        context.get("prior_harness_repair_admission"), "prior_harness_repair"
    )
    infrastructure = _mapping(
        context.get("prior_infrastructure_rerun_admission"),
        "prior_infrastructure_rerun",
    )
    exact = (
        source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("current_stage") == "H0"
        and source.get("status") == POST_WORKLOAD_REVOKED_STATUS
        and source.get("current_action_scope") == POST_WORKLOAD_REVOKED_SCOPE
        and source.get("current_blocker") == "manifest_contract_failure"
        and source.get("live_h0_candidate_authorized") is False
        and source.get("authorized_live_actions") == []
        and source.get("authorized_h0_candidate_id") is None
        and "live_h0_authorization" not in source
        and classification.get("schema_version")
        == "membind.h0.post-workload-harness-failure.v1"
        and classification.get("stage_attempt_id")
        == POST_WORKLOAD_FAILED_ATTEMPT_ID
        and classification.get("workload_reached") is True
        and classification.get("prior_model_workload_output_observed") is True
        and classification.get("candidate_model_failure_supported") is False
        and classification.get("infrastructure_failure_supported") is False
        and classification.get("partial_qualification_reusable") is False
        and classification.get("old_and_new_trial_counts_mergeable") is False
        and context.get("schema_version")
        == "membind.h0.post-workload-harness-repair-context.v1"
        and context.get("replacement_attempt_id")
        == POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID
        and context.get("live_authorized") is False
        and context.get("secrets_persisted") is False
        and context.get("prior_harness_repair_admission_sha256")
        == canonical_json_sha256(repair)
        and context.get("prior_infrastructure_rerun_admission_sha256")
        == canonical_json_sha256(infrastructure)
    )
    if not exact:
        raise _fail("source_not_exact_post_workload_revoked_state")
    return source, classification, context


def build_h0_b_post_workload_harness_repair_bound_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
    repair_decision_path: str,
    repair_decision_sha256: str,
    manifest_validator: Callable[..., Any] = _default_r5_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    repair_decision_verifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Bind R5 and its non-blind decision while keeping live access closed."""

    if repair_decision_verifier is None:
        from h0_repair_admission import (
            verify_h0_b_post_workload_harness_repair_decision,
        )

        repair_decision_verifier = (
            verify_h0_b_post_workload_harness_repair_decision
        )
    source, classification, context = _validate_post_workload_revoked_source(
        source_state
    )
    root_path = Path(root).resolve()
    try:
        raw_bindings, raw_verification = manifest_validator(
            root_path, _mapping(manifest_verification, "r5_manifest_verification")
        )
        raw_tdd = tdd_validator(root_path, _mapping(tdd_evidence, "r5_tdd_evidence"))
        raw_admission = repair_decision_verifier(
            root=root_path,
            decision_path=repair_decision_path,
            decision_sha256=repair_decision_sha256,
            manifest_verification=manifest_verification,
        )
    except H0HarnessRecoveryError:
        raise
    except Exception as exc:
        raise _fail("post_workload_offline_evidence_validation_failed") from exc
    bindings = _validate_bindings(
        raw_bindings, artifact_set_id=R5_ARTIFACT_SET_ID, require_r3_index=False
    )
    verification = _validate_r5_verification_shape(raw_verification)
    tdd = _validate_tdd_shape(raw_tdd)
    if (
        dict(raw_verification) != dict(manifest_verification)
        or dict(raw_tdd) != dict(tdd_evidence)
    ):
        raise _fail("post_workload_offline_evidence_changed")
    admission = _validate_post_workload_admission(
        raw_admission,
        classification=classification,
        context=context,
        bindings=bindings,
        decision_path=repair_decision_path,
        decision_sha256=repair_decision_sha256,
    )
    state = deepcopy(source)
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden",
            "h0_candidate_progression": "h0_b_post_workload_r5_verified_not_authorized",
            "h0_offline_manifest_binding": "v1_3_harness_r5_verified",
        }
    )
    state.update(
        {
            "status": "h0_b_post_workload_harness_repair_verified_not_live_authorized",
            "current_action_scope": "h0_b_post_workload_harness_repair_verified_only",
            "current_blocker": "replacement_h0_b_post_workload_harness_not_yet_authorized",
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "next_allowed_action": "explicit_q1_h0_b_post_workload_replacement_transition",
            "h0_b_post_workload_harness_repair_live_prerequisites": {
                "schema_version": "membind.h0.post-workload-harness-repair-live-prerequisites.v1",
                "protocol_version": PROTOCOL_VERSION,
                "status": "verified_not_live_authorized",
                "candidate_id": "Q1",
                "phase": "H0-B",
                "artifact_bindings": bindings,
                "manifest_verification": verification,
                "tdd_evidence": tdd,
                "prior_harness_repair_admission": deepcopy(
                    context["prior_harness_repair_admission"]
                ),
                "prior_infrastructure_rerun_admission": deepcopy(
                    context["prior_infrastructure_rerun_admission"]
                ),
                "post_workload_repair_admission": admission,
                "prior_phase_completion": deepcopy(
                    context.get("prior_phase_completion")
                ),
                "live_transition_performed": False,
                "secrets_persisted": False,
            },
        }
    )
    state.pop("live_h0_authorization", None)
    return state


def build_h0_b_post_workload_harness_replacement_live_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_validator: Callable[..., Any] = _default_r5_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    repair_decision_verifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Consume the R5 grant for the exact replacement-003 attempt only."""

    if repair_decision_verifier is None:
        from h0_repair_admission import (
            verify_h0_b_post_workload_harness_repair_decision,
        )

        repair_decision_verifier = (
            verify_h0_b_post_workload_harness_repair_decision
        )
    source = _validate_transition_state(
        source_state, label="post_workload_bound_source"
    )
    prerequisites = _mapping(
        source.get("h0_b_post_workload_harness_repair_live_prerequisites"),
        "post_workload_prerequisites",
    )
    if not (
        source.get("status")
        == "h0_b_post_workload_harness_repair_verified_not_live_authorized"
        and source.get("current_action_scope")
        == "h0_b_post_workload_harness_repair_verified_only"
        and source.get("live_h0_candidate_authorized") is False
        and source.get("authorized_live_actions") == []
        and source.get("authorized_h0_candidate_id") is None
        and "live_h0_authorization" not in source
        and prerequisites.get("schema_version")
        == "membind.h0.post-workload-harness-repair-live-prerequisites.v1"
        and prerequisites.get("live_transition_performed") is False
    ):
        raise _fail("source_not_exact_post_workload_bound_state")
    classification = _mapping(
        source.get("h0_b_post_workload_harness_failure"),
        "post_workload_failure",
    )
    context = _mapping(
        source.get("h0_b_post_workload_harness_repair_context"),
        "post_workload_context",
    )
    persisted_admission = _mapping(
        prerequisites.get("post_workload_repair_admission"),
        "post_workload_repair_admission",
    )
    root_path = Path(root).resolve()
    try:
        raw_bindings, raw_verification = manifest_validator(
            root_path,
            _mapping(prerequisites.get("manifest_verification"), "r5_verification"),
        )
        raw_tdd = tdd_validator(
            root_path, _mapping(prerequisites.get("tdd_evidence"), "r5_tdd")
        )
        raw_admission = repair_decision_verifier(
            root=root_path,
            decision_path=persisted_admission.get("decision_path"),
            decision_sha256=persisted_admission.get("decision_sha256"),
            manifest_verification=prerequisites.get("manifest_verification"),
        )
    except H0HarnessRecoveryError:
        raise
    except Exception as exc:
        raise _fail("post_workload_persisted_evidence_revalidation_failed") from exc
    bindings = _validate_bindings(
        raw_bindings, artifact_set_id=R5_ARTIFACT_SET_ID, require_r3_index=False
    )
    verification = _validate_r5_verification_shape(raw_verification)
    tdd = _validate_tdd_shape(raw_tdd)
    admission = _validate_post_workload_admission(
        raw_admission,
        classification=classification,
        context=context,
        bindings=bindings,
        decision_path=str(persisted_admission.get("decision_path") or ""),
        decision_sha256=str(persisted_admission.get("decision_sha256") or ""),
    )
    if (
        bindings != dict(_mapping(prerequisites.get("artifact_bindings"), "bindings"))
        or verification
        != dict(_mapping(prerequisites.get("manifest_verification"), "verification"))
        or tdd != dict(_mapping(prerequisites.get("tdd_evidence"), "tdd"))
        or admission != dict(persisted_admission)
    ):
        raise _fail("post_workload_persisted_evidence_changed")
    state = deepcopy(source)
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "h0_q1_b_live_only",
            "h0_candidate_progression": "h0_b_post_workload_replacement_authorized_once",
            "h0_offline_manifest_binding": "v1_3_harness_r5_verified",
        }
    )
    state.update(
        {
            "status": "h0_q1_b_live_only",
            "current_action_scope": "h0_q1_b_live_only",
            "current_blocker": None,
            "stage_progress": progress,
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "next_allowed_action": "run_q1_h0-b-post-workload-replacement",
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-B",
                **bindings,
                "prior_phase_completion": deepcopy(
                    prerequisites.get("prior_phase_completion")
                ),
                "authorized_stage_attempt_id": POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID,
                "repair_admission": deepcopy(
                    prerequisites["prior_harness_repair_admission"]
                ),
                "infrastructure_rerun_admission": deepcopy(
                    prerequisites["prior_infrastructure_rerun_admission"]
                ),
                "post_workload_repair_admission": admission,
            },
        }
    )
    state["h0_b_post_workload_harness_repair_live_prerequisites"] = {
        **deepcopy(dict(prerequisites)),
        "artifact_bindings": bindings,
        "manifest_verification": verification,
        "tdd_evidence": tdd,
        "post_workload_repair_admission": admission,
        "live_transition_performed": True,
    }
    return state


def _validate_transition_state(value: Any, *, label: str) -> dict[str, Any]:
    state = deepcopy(dict(_mapping(value, label)))
    _safe_value(state, location=label)
    return state


def _validate_live_forbidden(state: Mapping[str, Any], *, label: str) -> None:
    progress = state.get("stage_progress")
    exact = (
        state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_live_actions") == []
        and state.get("authorized_h0_candidate_id") is None
        and "live_h0_authorization" not in state
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == "forbidden"
    )
    if not exact:
        raise _fail(f"{label}_live_forbidden_contract_mismatch")


def _validate_h0_b_live_preview(
    state: Mapping[str, Any],
    *,
    root: Path,
    candidate_id: str,
    phase: str,
) -> None:
    """Validate a temporary canonical H0-B grant through both runtime gates."""

    if candidate_id != "Q1" or phase != "H0-B":
        raise _fail("replacement_preview_target_mismatch")
    preview: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=root,
            prefix=".h0-b-live-preview.",
            suffix=".json",
            delete=False,
        ) as handle:
            preview = Path(handle.name)
            handle.write(canonical_json_bytes(state))
            handle.flush()
            os.fsync(handle.fileno())
        authorization = authorize_h0_live_entry(
            state_path=preview,
            candidate_id=candidate_id,
            phase=phase,
        )
        load_authorized_h0_runtime_identity(authorization, root=root)
    except (H0StateGateError, H0ManifestError, OSError) as exc:
        raise _fail("replacement_live_preview_validation_failed") from exc
    finally:
        if preview is not None:
            preview.unlink(missing_ok=True)


def _call_transition_builder(
    state_builder: Callable[..., Any],
    source: Mapping[str, Any],
    *,
    root: Path,
    builder_kwargs: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    try:
        raw_state = state_builder(
            deepcopy(dict(source)),
            root=root,
            **dict(builder_kwargs),
        )
    except H0HarnessRecoveryError:
        raise
    except Exception as exc:
        raise _fail(f"{label}_builder_failed") from exc
    return _validate_transition_state(raw_state, label=f"{label}_derived_state")


def _transition_h0_b_recovery_state(
    state_path: str | Path,
    *,
    root: str | Path,
    dry_run: bool,
    state_builder: Callable[..., Any],
    builder_kwargs: Mapping[str, Any],
    label: str,
    state_validator: Callable[[Mapping[str, Any]], None],
    preview_validator: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root_path = Path(root).resolve()
    target = _state_target(state_path, root_path)

    def build(source: Mapping[str, Any]) -> dict[str, Any]:
        state = _call_transition_builder(
            state_builder,
            source,
            root=root_path,
            builder_kwargs=builder_kwargs,
            label=label,
        )
        state_validator(state)
        return state

    def validate_preview(state: Mapping[str, Any]) -> None:
        if preview_validator is None:
            return
        try:
            preview_validator(
                state,
                root=root_path,
                candidate_id="Q1",
                phase="H0-B",
            )
        except H0HarnessRecoveryError:
            raise
        except Exception as exc:
            raise _fail(f"{label}_preview_validation_failed") from exc

    if dry_run:
        _, source = _load_canonical_state_snapshot(target)
        derived = build(source)
        validate_preview(derived)
        return derived

    with _state_transition_lock(target):
        initial_bytes, source = _load_canonical_state_snapshot(target)
        derived = build(source)

        confirmed_bytes, confirmed_source = _load_canonical_state_snapshot(target)
        if confirmed_bytes != initial_bytes:
            raise _fail(f"{label}_state_changed_before_commit")
        confirmed = build(confirmed_source)
        if canonical_json_bytes(confirmed) != canonical_json_bytes(derived):
            raise _fail(f"{label}_evidence_changed_before_commit")

        validate_preview(confirmed)
        try:
            current_bytes = target.read_bytes()
        except OSError:
            raise _fail(f"{label}_state_changed_before_commit") from None
        if current_bytes != initial_bytes:
            raise _fail(f"{label}_state_changed_before_commit")
        _atomic_write(target, confirmed)
    return confirmed


def transition_h0_b_harness_revoke(
    state_path: str | Path,
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    failure_report_path: str,
    failure_report_sha256: str,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = build_h0_b_harness_revoked_state,
) -> dict[str, Any]:
    """Preview or atomically commit the evidence-bound stale-r2 revocation."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "stage_attempt_id": stage_attempt_id,
            "checkpoint_index_path": checkpoint_index_path,
            "checkpoint_index_sha256": checkpoint_index_sha256,
            "failure_report_path": failure_report_path,
            "failure_report_sha256": failure_report_sha256,
        },
        label="harness_revoke",
        state_validator=lambda state: _validate_live_forbidden(
            state, label="harness_revoke"
        ),
    )


def transition_h0_b_harness_repair_bound(
    state_path: str | Path,
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
    repair_decision_path: str,
    repair_decision_sha256: str,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = build_h0_b_harness_repair_bound_state,
    manifest_validator: Callable[..., Any] = _default_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    repair_decision_verifier: Callable[..., Any] = _default_decision_verifier,
) -> dict[str, Any]:
    """Preview or atomically bind r3 repair evidence with live still closed."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "manifest_verification": manifest_verification,
            "tdd_evidence": tdd_evidence,
            "repair_decision_path": repair_decision_path,
            "repair_decision_sha256": repair_decision_sha256,
            "manifest_validator": manifest_validator,
            "tdd_validator": tdd_validator,
            "repair_decision_verifier": repair_decision_verifier,
        },
        label="harness_repair_bound",
        state_validator=lambda state: _validate_live_forbidden(
            state, label="harness_repair_bound"
        ),
    )


def transition_h0_b_replacement_live(
    state_path: str | Path,
    *,
    root: str | Path,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = build_h0_b_replacement_live_state,
    preview_validator: Callable[..., Any] = _validate_h0_b_live_preview,
    manifest_validator: Callable[..., Any] = _default_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    repair_decision_verifier: Callable[..., Any] = _default_decision_verifier,
) -> dict[str, Any]:
    """Preview or atomically commit the one-shot replacement H0-B grant."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "manifest_validator": manifest_validator,
            "tdd_validator": tdd_validator,
            "repair_decision_verifier": repair_decision_verifier,
        },
        label="harness_replacement_live",
        state_validator=lambda _state: None,
        preview_validator=preview_validator,
    )


def transition_h0_b_infrastructure_rerun_bound(
    state_path: str | Path,
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
    rerun_decision_path: str,
    rerun_decision_sha256: str,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = build_h0_b_infrastructure_rerun_bound_state,
    manifest_validator: Callable[..., Any] = _default_r4_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    rerun_decision_verifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Preview or atomically bind R4 recovery evidence with live disabled."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "manifest_verification": manifest_verification,
            "tdd_evidence": tdd_evidence,
            "rerun_decision_path": rerun_decision_path,
            "rerun_decision_sha256": rerun_decision_sha256,
            "manifest_validator": manifest_validator,
            "tdd_validator": tdd_validator,
            "rerun_decision_verifier": rerun_decision_verifier,
        },
        label="infrastructure_rerun_bound",
        state_validator=lambda state: _validate_live_forbidden(
            state, label="infrastructure_rerun_bound"
        ),
    )


def transition_h0_b_infrastructure_rerun_live(
    state_path: str | Path,
    *,
    root: str | Path,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = build_h0_b_infrastructure_rerun_live_state,
    preview_validator: Callable[..., Any] = _validate_h0_b_live_preview,
    manifest_validator: Callable[..., Any] = _default_r4_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    rerun_decision_verifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Preview or atomically authorize exact replacement-002 after R4 binding."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "manifest_validator": manifest_validator,
            "tdd_validator": tdd_validator,
            "rerun_decision_verifier": rerun_decision_verifier,
        },
        label="infrastructure_rerun_live",
        state_validator=lambda _state: None,
        preview_validator=preview_validator,
    )


def transition_h0_b_post_workload_harness_repair_bound(
    state_path: str | Path,
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
    repair_decision_path: str,
    repair_decision_sha256: str,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = (
        build_h0_b_post_workload_harness_repair_bound_state
    ),
    manifest_validator: Callable[..., Any] = _default_r5_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    repair_decision_verifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Preview or atomically bind R5 while live authorization stays closed."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "manifest_verification": manifest_verification,
            "tdd_evidence": tdd_evidence,
            "repair_decision_path": repair_decision_path,
            "repair_decision_sha256": repair_decision_sha256,
            "manifest_validator": manifest_validator,
            "tdd_validator": tdd_validator,
            "repair_decision_verifier": repair_decision_verifier,
        },
        label="post_workload_harness_repair_bound",
        state_validator=lambda state: _validate_live_forbidden(
            state, label="post_workload_harness_repair_bound"
        ),
    )


def transition_h0_b_post_workload_harness_replacement_live(
    state_path: str | Path,
    *,
    root: str | Path,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = (
        build_h0_b_post_workload_harness_replacement_live_state
    ),
    preview_validator: Callable[..., Any] = _validate_h0_b_live_preview,
    manifest_validator: Callable[..., Any] = _default_r5_manifest_validator,
    tdd_validator: Callable[..., Any] = _default_tdd_validator,
    repair_decision_verifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Preview or atomically authorize only replacement-003 from R5."""

    return _transition_h0_b_recovery_state(
        state_path,
        root=root,
        dry_run=dry_run,
        state_builder=state_builder,
        builder_kwargs={
            "manifest_validator": manifest_validator,
            "tdd_validator": tdd_validator,
            "repair_decision_verifier": repair_decision_verifier,
        },
        label="post_workload_harness_replacement_live",
        state_validator=lambda _state: None,
        preview_validator=preview_validator,
    )
