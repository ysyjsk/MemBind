"""Narrow offline transition from verified C2 cleanup to C2-only authority.

This module validates already-persisted local evidence. It never opens a model,
embedding, database, SSH, or other live client, and it is intentionally not a
general recovery state machine.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from native_characterization_c2_cleanup import (
    FAILED_C2_ATTEMPT_ID as CLEANUP_FAILED_C2_ATTEMPT_ID,
    INTERRUPTED_C2_ATTEMPT_ID,
    INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH,
    PLANNED_EVIDENCE_RELATIVE_PATH,
    POLLUTED_C2_GROUP_ID as CLEANUP_POLLUTED_C2_GROUP_ID,
    SOURCE_FREEZE_RELATIVE_PATH,
    SOURCE_FREEZE_SHA256,
    SERVING_ENVELOPE_C2_ATTEMPT_ID,
)
from native_characterization_c2_serving_envelope_recovery import (
    ENVELOPE_EVIDENCE_RELATIVE_PATH,
    FAILURE_METADATA_KEY as SERVING_FAILURE_METADATA_KEY,
    NEW_REFERENCE_FREEZE_RELATIVE_PATH,
    derive_64k_reference_freeze,
    validate_64k_envelope_evidence,
)


PROTOCOL_VERSION = "current-validation-v1.3"
STATE_RELATIVE_PATH = "membind-validation/CURRENT_STATE.json"
CLEANUP_FREEZE_RELATIVE_PATH = SOURCE_FREEZE_RELATIVE_PATH
REFERENCE_FREEZE_RELATIVE_PATH = (
    "artifacts/native_characterization/freeze_reference_aligned.json"
)
FAILED_C2_ATTEMPT_ID = CLEANUP_FAILED_C2_ATTEMPT_ID
POLLUTED_C2_GROUP_ID = CLEANUP_POLLUTED_C2_GROUP_ID
SOURCE_STAGE = "NATIVE_CHARACTERIZATION"
SOURCE_STATUS = "native_characterization_cleanup_only"
SOURCE_SCOPE = "native_characterization_c2_cleanup_only"
SOURCE_BLOCKER = "c2_reference_aligned_cleanup_pending"
SOURCE_NEXT_ACTION = "execute_scoped_c2_cleanup_reference_aligned_precondition"
SOURCE_PROGRESS = "c0_c1_pass_reference_alignment_cleanup_only_pending"
TARGET_STATUS = "native_characterization_c2_live_only"
TARGET_SCOPE = "native_characterization_c2_live_only"
TARGET_ACTION = "native_characterization_c2"
TARGET_NEXT_ACTION = "run_native_characterization_c2"
TARGET_PROGRESS = "c0_c1_pass_reference_aligned_c2_authorized_from_episode_0"
METADATA_KEY = "native_characterization_reference_c2_authorization"
REFERENCE_ALIGNMENT_KEY = "native_characterization_reference_alignment"
_REFERENCE_SOURCE_BINDINGS = {
    "u0_runtime_source_sha256": "src/native_characterization_runtime.py",
    "qwen_transport_source_sha256": "src/graphiti_native.py",
}
_C2_RUNNER_SOURCE_PATH = "src/native_characterization_c2.py"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REGRESSION_COUNT_RE = re.compile(r"^Ran ([1-9][0-9]*) tests? in ", re.MULTILINE)
_REGRESSION_OK_RE = re.compile(r"^OK(?: \([^\n]*\))?$")
_REGRESSION_EXIT_RE = re.compile(r"^exit_code: ([0-9]+)$")
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization_header",
    "bearer",
    "credentials",
    "env_dump",
    "environment_dump",
    "environ",
    "password",
    "raw_prompt",
    "raw_prompts",
    "raw_response",
    "raw_responses",
    "secret",
    "token",
}


@dataclass(frozen=True)
class C2ReauthorizationBindings:
    """Content identities required for the post-cleanup C2 transition."""

    source_state_sha256: str
    cleanup_evidence_path: str
    cleanup_evidence_sha256: str
    final_full_regression_path: str
    final_full_regression_sha256: str
    final_full_regression_test_count: int
    reference_freeze_sha256: str
    c2_runner_source_sha256: str
    reference_freeze_path: str = REFERENCE_FREEZE_RELATIVE_PATH
    execution_envelope_path: str | None = None
    execution_envelope_sha256: str | None = None
    focused_test_log_path: str | None = None
    focused_test_log_sha256: str | None = None
    focused_test_count: int | None = None


class NativeCharacterizationC2ReauthorizationError(RuntimeError):
    """Sanitized fail-closed transition error."""


def _fail(reason: str) -> NativeCharacterizationC2ReauthorizationError:
    return NativeCharacterizationC2ReauthorizationError(
        f"native characterization C2 reauthorization denied: {reason}"
    )


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise _fail("state_not_canonicalizable") from None


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise _fail("unsafe_evidence_or_state")
            _safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe(child)
    elif isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or ".env" in lowered:
            raise _fail("unsafe_evidence_or_state")


def _resolve_under(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise _fail(f"{label}_path_invalid")
    value = Path(relative)
    if any(part in {"", ".", ".."} for part in value.parts):
        raise _fail(f"{label}_path_noncanonical")
    path = root / value
    try:
        path.relative_to(root)
    except ValueError:
        raise _fail(f"{label}_path_escape") from None
    cursor = root
    for part in value.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_symlink")
    if not path.is_file():
        raise _fail(f"{label}_missing")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_unreadable") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    _safe(value)
    return value


def _validate_payload_hash(value: Mapping[str, Any], label: str) -> str:
    candidate = deepcopy(dict(value))
    observed = candidate.pop("payload_sha256", None)
    if not isinstance(observed, str) or observed != _sha(_canonical(candidate)):
        raise _fail(f"{label}_payload_mismatch")
    return observed


def _validate_bindings(bindings: C2ReauthorizationBindings) -> None:
    if not isinstance(bindings, C2ReauthorizationBindings):
        raise _fail("bindings_invalid")
    for name in (
        "source_state_sha256",
        "cleanup_evidence_sha256",
        "final_full_regression_sha256",
        "reference_freeze_sha256",
        "c2_runner_source_sha256",
    ):
        _require_digest(getattr(bindings, name), name)
    if (
        not isinstance(bindings.final_full_regression_test_count, int)
        or isinstance(bindings.final_full_regression_test_count, bool)
        or bindings.final_full_regression_test_count <= 0
    ):
        raise _fail("final_full_regression_test_count_invalid")
    recovery_fields = (
        bindings.execution_envelope_path,
        bindings.execution_envelope_sha256,
        bindings.focused_test_log_path,
        bindings.focused_test_log_sha256,
        bindings.focused_test_count,
    )
    if bindings.reference_freeze_path == REFERENCE_FREEZE_RELATIVE_PATH:
        if any(value is not None for value in recovery_fields):
            raise _fail("unexpected_64k_recovery_bindings")
    elif bindings.reference_freeze_path == NEW_REFERENCE_FREEZE_RELATIVE_PATH:
        if any(value is None for value in recovery_fields):
            raise _fail("64k_recovery_bindings_missing")
        _require_digest(bindings.execution_envelope_sha256, "execution_envelope_sha256")
        _require_digest(bindings.focused_test_log_sha256, "focused_test_log_sha256")
        if (
            not isinstance(bindings.focused_test_count, int)
            or isinstance(bindings.focused_test_count, bool)
            or bindings.focused_test_count <= 0
        ):
            raise _fail("focused_test_count_invalid")
    else:
        raise _fail("reference_freeze_path_invalid")


def _validate_source(state: Mapping[str, Any]) -> None:
    progress = state.get("stage_progress")
    alignment = state.get(REFERENCE_ALIGNMENT_KEY)
    cleanup = alignment.get("cleanup") if isinstance(alignment, Mapping) else None
    fresh_c2 = alignment.get("fresh_c2") if isinstance(alignment, Mapping) else None
    common = (
        state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("current_stage") == SOURCE_STAGE
        and state.get("status") == SOURCE_STATUS
        and state.get("current_action_scope") == SOURCE_SCOPE
        and state.get("authorized_live_actions") == []
        and state.get("native_characterization_live_authorized") is False
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and isinstance(progress, Mapping)
        and isinstance(alignment, Mapping)
        and alignment.get("schema_version")
        == "membind.native-characterization-reference-alignment.v1"
        and alignment.get("reference_freeze_path")
        == REFERENCE_FREEZE_RELATIVE_PATH
        and isinstance(cleanup, Mapping)
        and cleanup.get("operator_authorized") is True
        and cleanup.get("execution_status") == "pending"
        and cleanup.get("failed_attempt_valid") is False
        and cleanup.get("failed_attempt_mergeable") is False
        and cleanup.get("target_group_id") == POLLUTED_C2_GROUP_ID
        and _SHA256_RE.fullmatch(str(cleanup.get("source_freeze_sha256")))
        is not None
        and cleanup.get("required_post_node_count") == 0
        and cleanup.get("required_post_relationship_count") == 0
        and isinstance(fresh_c2, Mapping)
        and fresh_c2.get("semantic_attempts_remaining") == 1
        and fresh_c2.get("run_id_pattern") == "c2-[0-9a-f]{16}"
        and fresh_c2.get("start_source_sequence") == 0
        and fresh_c2.get("resume_allowed") is False
        and fresh_c2.get("prefix_merge_allowed") is False
        and fresh_c2.get("structured_output_mode") == "json_schema"
    )
    historical = (
        common
        and state.get("current_blocker") == SOURCE_BLOCKER
        and state.get("next_allowed_action") == SOURCE_NEXT_ACTION
        and progress.get("native_characterization") == SOURCE_PROGRESS
        and alignment.get("status") == "offline_green_cleanup_pending"
        and cleanup.get("failed_attempt_id") == FAILED_C2_ATTEMPT_ID
        and cleanup.get("source_freeze_path") == CLEANUP_FREEZE_RELATIVE_PATH
        and cleanup.get("planned_evidence_path") == PLANNED_EVIDENCE_RELATIVE_PATH
        and METADATA_KEY not in state
    )
    interruption = state.get("native_characterization_c2_interruption")
    prior_receipt = state.get(METADATA_KEY)
    interrupted = (
        common
        and state.get("current_blocker")
        == "c2_infrastructure_interruption_cleanup_pending"
        and state.get("next_allowed_action")
        == "execute_scoped_c2_cleanup_after_infrastructure_interruption"
        and progress.get("native_characterization")
        == "c0_c1_pass_reference_c2_infrastructure_interrupted_cleanup_pending"
        and alignment.get("status")
        == "c2_infrastructure_interrupted_cleanup_pending"
        and cleanup.get("failed_attempt_id") == INTERRUPTED_C2_ATTEMPT_ID
        and cleanup.get("replacement_resume_allowed") is False
        and cleanup.get("source_freeze_path")
        == INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH
        and cleanup.get("planned_evidence_path")
        == (
            "artifacts/native_characterization/c2_cleanup/"
            f"{INTERRUPTED_C2_ATTEMPT_ID}.json"
        )
        and fresh_c2.get("live_authorized") is False
        and isinstance(prior_receipt, Mapping)
        and prior_receipt.get("live_authorized") is False
        and prior_receipt.get("replacement_resume_allowed") is False
        and prior_receipt.get("replacement_start_source_sequence") == 0
        and prior_receipt.get("semantic_attempts_authorized") == 1
        and prior_receipt.get("consumed_by_run_id") == INTERRUPTED_C2_ATTEMPT_ID
        and isinstance(interruption, Mapping)
        and interruption.get("run_id") == INTERRUPTED_C2_ATTEMPT_ID
        and interruption.get("error_code") == "openai.APIConnectionError"
        and interruption.get("attempt_valid") is False
        and interruption.get("attempt_mergeable") is False
        and interruption.get("resume_allowed") is False
        and interruption.get("prefix_merge_allowed") is False
        and interruption.get("semantic_attempt_consumed") is False
        and interruption.get("semantic_attempts_remaining") == 1
        and interruption.get("cleanup_authorized") is True
        and interruption.get("live_authorized") is False
    )
    serving_failure = state.get(SERVING_FAILURE_METADATA_KEY)
    envelope = state.get("native_characterization_64k_serving_envelope")
    serving_envelope = (
        common
        and state.get("current_blocker")
        == "c2_serving_envelope_failure_cleanup_pending"
        and state.get("next_allowed_action")
        == "execute_scoped_c2_cleanup_after_serving_envelope_failure"
        and progress.get("native_characterization")
        == "c0_c1_pass_reference_c2_serving_envelope_failed_cleanup_pending"
        and alignment.get("status")
        == "c2_serving_envelope_failed_cleanup_pending"
        and cleanup.get("failed_attempt_id") == SERVING_ENVELOPE_C2_ATTEMPT_ID
        and cleanup.get("replacement_resume_allowed") is False
        and cleanup.get("source_freeze_path")
        == INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH
        and cleanup.get("planned_evidence_path")
        == (
            "artifacts/native_characterization/c2_cleanup/"
            f"{SERVING_ENVELOPE_C2_ATTEMPT_ID}.json"
        )
        and fresh_c2.get("live_authorized") is False
        and isinstance(prior_receipt, Mapping)
        and prior_receipt.get("live_authorized") is False
        and prior_receipt.get("replacement_resume_allowed") is False
        and prior_receipt.get("replacement_start_source_sequence") == 0
        and prior_receipt.get("semantic_attempts_authorized") == 1
        and prior_receipt.get("consumed_by_run_id")
        == SERVING_ENVELOPE_C2_ATTEMPT_ID
        and isinstance(serving_failure, Mapping)
        and serving_failure.get("run_id") == SERVING_ENVELOPE_C2_ATTEMPT_ID
        and serving_failure.get("error_code") == "openai.BadRequestError"
        and serving_failure.get("attempt_valid") is False
        and serving_failure.get("attempt_mergeable") is False
        and serving_failure.get("resume_allowed") is False
        and serving_failure.get("prefix_merge_allowed") is False
        and serving_failure.get("semantic_attempt_consumed") is False
        and serving_failure.get("semantic_attempts_remaining") == 1
        and serving_failure.get("cleanup_authorized") is True
        and serving_failure.get("live_authorized") is False
        and isinstance(envelope, Mapping)
        and envelope.get("qualification_status") == "64K_ENVELOPE_PASS"
        and envelope.get("max_model_len") == 65536
        and envelope.get("requested_max_tokens") == 16384
    )
    if not historical and not interrupted and not serving_envelope:
        raise _fail("source_state_not_cleanup_pending")


def _nonnegative_count(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _fail(f"{label}_invalid")
    return value


def _validate_freeze_block(
    value: Mapping[str, Any], *, expected_mode: str, label: str
) -> None:
    policy = value.get("construction_compatibility_policy")
    screening = value.get("screening")
    e1_e2 = screening.get("e1_e2") if isinstance(screening, Mapping) else None
    block_order = e1_e2.get("block_order") if isinstance(e1_e2, Mapping) else None
    exact = (
        value.get("schema_version")
        == "membind.native-characterization-freeze.v1"
        and isinstance(policy, Mapping)
        and policy.get("structured_output_mode") == expected_mode
        and isinstance(block_order, list)
        and len(block_order) == 4
        and isinstance(block_order[0], Mapping)
        and block_order[0].get("block_index") == 0
        and block_order[0].get("graph_namespace") == POLLUTED_C2_GROUP_ID
    )
    if not exact:
        raise _fail(f"{label}_contract_mismatch")


def _validate_cleanup_freeze(
    validation: Path, state: Mapping[str, Any]
) -> str:
    alignment = state[REFERENCE_ALIGNMENT_KEY]
    assert isinstance(alignment, Mapping)
    cleanup = alignment["cleanup"]
    assert isinstance(cleanup, Mapping)
    freeze_relative = cleanup.get("source_freeze_path")
    if freeze_relative == CLEANUP_FREEZE_RELATIVE_PATH:
        expected_mode = "json_object"
    elif freeze_relative == INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH:
        expected_mode = "json_schema"
    else:
        raise _fail("cleanup_source_freeze_path_mismatch")
    freeze = _resolve_under(validation, freeze_relative, "cleanup_freeze")
    freeze_sha256 = _sha(freeze.read_bytes())
    if cleanup.get("source_freeze_sha256") != freeze_sha256:
        raise _fail("cleanup_source_freeze_hash_mismatch")
    value = _read_json(freeze, "cleanup_freeze")
    _validate_payload_hash(value, "cleanup_freeze")
    _validate_freeze_block(value, expected_mode=expected_mode, label="cleanup_freeze")
    return freeze_sha256


def _validate_reference_freeze(
    validation: Path,
    state: Mapping[str, Any],
    bindings: C2ReauthorizationBindings,
) -> str:
    alignment = state[REFERENCE_ALIGNMENT_KEY]
    assert isinstance(alignment, Mapping)
    freeze = _resolve_under(
        validation, bindings.reference_freeze_path, "reference_freeze"
    )
    freeze_sha256 = _sha(freeze.read_bytes())
    if freeze_sha256 != bindings.reference_freeze_sha256:
        raise _fail("reference_freeze_hash_mismatch")
    value = _read_json(freeze, "reference_freeze")
    _validate_payload_hash(value, "reference_freeze")
    _validate_freeze_block(
        value, expected_mode="json_schema", label="reference_freeze"
    )

    policy = value.get("construction_compatibility_policy")
    derivation = value.get("derivation")
    common_policy = (
        isinstance(policy, Mapping)
        and policy.get("classification")
        == "reference_aligned_with_declared_project_deviations"
        and policy.get("structured_output_backend_requested") is None
        and policy.get("upstream_graphiti_behavior") is False
        and policy.get("project_generate_response_override") is False
        and policy.get("project_structured_parser") is False
        and policy.get("project_context_probe") is False
        and policy.get("project_retry_budget_matrix") is False
        and policy.get("requested_max_tokens") == 16384
    )
    historical_derivation = (
        bindings.reference_freeze_path == REFERENCE_FREEZE_RELATIVE_PATH
        and alignment.get("reference_freeze_sha256") == freeze_sha256
        and isinstance(derivation, Mapping)
        and derivation.get("parent_freeze_path")
        == alignment.get("canonical_freeze_path")
        and derivation.get("parent_freeze_sha256")
        == alignment.get("canonical_freeze_sha256")
        and derivation.get("reason")
        == "restore_pinned_graphiti_openai_generic_provider_path"
    )
    if not common_policy or (
        bindings.reference_freeze_path == REFERENCE_FREEZE_RELATIVE_PATH
        and not historical_derivation
    ):
        raise _fail("reference_freeze_policy_mismatch")

    inputs = value.get("input_hashes")
    if not isinstance(inputs, Mapping):
        raise _fail("reference_freeze_source_bindings_missing")
    for field, relative in _REFERENCE_SOURCE_BINDINGS.items():
        source = _resolve_under(validation, relative, field)
        if inputs.get(field) != _sha(source.read_bytes()):
            raise _fail(f"{field}_mismatch")
    if bindings.reference_freeze_path == NEW_REFERENCE_FREEZE_RELATIVE_PATH:
        assert bindings.execution_envelope_path is not None
        assert bindings.execution_envelope_sha256 is not None
        parent = _resolve_under(
            validation, REFERENCE_FREEZE_RELATIVE_PATH, "parent_reference_freeze"
        )
        parent_sha256 = _sha(parent.read_bytes())
        cleanup = alignment.get("cleanup")
        if (
            alignment.get("reference_freeze_sha256") != parent_sha256
            or not isinstance(cleanup, Mapping)
            or cleanup.get("source_freeze_sha256") != parent_sha256
        ):
            raise _fail("parent_reference_freeze_hash_mismatch")
        expected = derive_64k_reference_freeze(
            _read_json(parent, "parent_reference_freeze"),
            parent_freeze_sha256=parent_sha256,
            envelope_evidence_sha256=bindings.execution_envelope_sha256,
            u0_runtime_source_sha256=str(inputs["u0_runtime_source_sha256"]),
        )
        if value != expected:
            raise _fail("reference_freeze_64k_derivation_mismatch")
    runner = _resolve_under(validation, _C2_RUNNER_SOURCE_PATH, "c2_runner_source")
    if _sha(runner.read_bytes()) != bindings.c2_runner_source_sha256:
        raise _fail("c2_runner_source_hash_mismatch")
    return freeze_sha256


def _validate_cleanup(
    validation: Path,
    state: Mapping[str, Any],
    bindings: C2ReauthorizationBindings,
) -> tuple[dict[str, Any], str]:
    alignment = state[REFERENCE_ALIGNMENT_KEY]
    assert isinstance(alignment, Mapping)
    cleanup_grant = alignment["cleanup"]
    assert isinstance(cleanup_grant, Mapping)
    if bindings.cleanup_evidence_path != cleanup_grant.get(
        "planned_evidence_path"
    ):
        raise _fail("cleanup_evidence_path_mismatch")
    path = _resolve_under(
        validation, bindings.cleanup_evidence_path, "cleanup_evidence"
    )
    if _sha(path.read_bytes()) != bindings.cleanup_evidence_sha256:
        raise _fail("cleanup_evidence_hash_mismatch")
    value = _read_json(path, "cleanup_evidence")
    payload_sha256 = _validate_payload_hash(value, "cleanup_evidence")
    if value.get("schema_version") != "membind.native-characterization-c2-cleanup.v1":
        raise _fail("cleanup_schema_mismatch")
    if value.get("status") != "verified_empty":
        raise _fail("cleanup_not_verified")
    if value.get("target_group_id") != POLLUTED_C2_GROUP_ID:
        raise _fail("cleanup_target_mismatch")
    cleanup_freeze_sha256 = _require_digest(
        value.get("freeze_sha256"), "cleanup_freeze_sha256"
    )
    if cleanup_freeze_sha256 != cleanup_grant.get("source_freeze_sha256"):
        raise _fail("cleanup_freeze_mismatch")
    if value.get("cleanup_primitive") != (
        "graphiti.clear_data(driver,group_ids=[target_group])"
    ):
        raise _fail("cleanup_primitive_mismatch")
    failed_attempt_id = cleanup_grant.get("failed_attempt_id")
    failed_exact = (
        failed_attempt_id
        in {
            FAILED_C2_ATTEMPT_ID,
            INTERRUPTED_C2_ATTEMPT_ID,
            SERVING_ENVELOPE_C2_ATTEMPT_ID,
        }
        and value.get("failed_attempt_id") == failed_attempt_id
        and value.get("failed_attempt_valid") is False
        and value.get("failed_attempt_mergeable") is False
        and value.get("replacement_resume_allowed") is False
    )
    if not failed_exact:
        raise _fail("failed_attempt_contract_mismatch")

    pre = value.get("pre_cleanup")
    post = value.get("post_cleanup")
    if not isinstance(pre, Mapping) or not isinstance(post, Mapping):
        raise _fail("cleanup_counts_missing")
    pre_nodes = _nonnegative_count(pre.get("node_count"), "pre_cleanup_node_count")
    pre_relationships = _nonnegative_count(
        pre.get("relationship_count"), "pre_cleanup_relationship_count"
    )
    post_nodes = _nonnegative_count(post.get("node_count"), "post_cleanup_node_count")
    post_relationships = _nonnegative_count(
        post.get("relationship_count"), "post_cleanup_relationship_count"
    )
    if post_nodes != 0 or post_relationships != 0:
        raise _fail("cleanup_residual")
    preexisting_empty = value.get("preexisting_empty")
    if not isinstance(preexisting_empty, bool) or preexisting_empty != (
        pre_nodes == 0 and pre_relationships == 0
    ):
        raise _fail("cleanup_preexisting_empty_mismatch")

    freeze_sha256 = _validate_cleanup_freeze(validation, state)
    if cleanup_freeze_sha256 != freeze_sha256:
        raise _fail("cleanup_freeze_mismatch")
    return value, payload_sha256


def _validate_regression(
    validation: Path, bindings: C2ReauthorizationBindings
) -> None:
    path = _resolve_under(
        validation,
        bindings.final_full_regression_path,
        "final_full_regression",
    )
    if _sha(path.read_bytes()) != bindings.final_full_regression_sha256:
        raise _fail("final_full_regression_hash_mismatch")
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise _fail("final_full_regression_unreadable") from None
    matches = _REGRESSION_COUNT_RE.findall(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    exit_lines = [line for line in lines if line.startswith("exit_code:")]
    if exit_lines:
        exit_match = _REGRESSION_EXIT_RE.fullmatch(lines[-1])
        if len(exit_lines) != 1 or exit_match is None or exit_match.group(1) != "0":
            raise _fail("final_full_regression_not_green")
    if (
        len(matches) != 1
        or sum(_REGRESSION_OK_RE.fullmatch(line) is not None for line in lines) != 1
        or any(line.startswith(("FAILED", "ERROR")) for line in lines)
    ):
        raise _fail("final_full_regression_not_green")
    if int(matches[0]) != bindings.final_full_regression_test_count:
        raise _fail("final_full_regression_test_count_mismatch")


def _validate_focused_test_log(
    validation: Path, bindings: C2ReauthorizationBindings
) -> None:
    if bindings.reference_freeze_path != NEW_REFERENCE_FREEZE_RELATIVE_PATH:
        return
    assert bindings.focused_test_log_path is not None
    assert bindings.focused_test_log_sha256 is not None
    assert bindings.focused_test_count is not None
    path = _resolve_under(validation, bindings.focused_test_log_path, "focused_test_log")
    if _sha(path.read_bytes()) != bindings.focused_test_log_sha256:
        raise _fail("focused_test_log_hash_mismatch")
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise _fail("focused_test_log_unreadable") from None
    matches = _REGRESSION_COUNT_RE.findall(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if (
        matches != [str(bindings.focused_test_count)]
        or sum(_REGRESSION_OK_RE.fullmatch(line) is not None for line in lines) != 1
        or lines[-1] != "exit_code: 0"
        or any(line.startswith(("FAILED", "ERROR")) for line in lines)
    ):
        raise _fail("focused_test_log_not_green")


def _validate_execution_envelope(
    validation: Path,
    state: Mapping[str, Any],
    bindings: C2ReauthorizationBindings,
) -> None:
    if bindings.reference_freeze_path != NEW_REFERENCE_FREEZE_RELATIVE_PATH:
        return
    assert bindings.execution_envelope_path is not None
    assert bindings.execution_envelope_sha256 is not None
    metadata = state.get("native_characterization_64k_serving_envelope")
    exact = (
        bindings.execution_envelope_path == ENVELOPE_EVIDENCE_RELATIVE_PATH
        and isinstance(metadata, Mapping)
        and metadata.get("qualification_status") == "64K_ENVELOPE_PASS"
        and metadata.get("evidence_path") == bindings.execution_envelope_path
        and metadata.get("evidence_sha256") == bindings.execution_envelope_sha256
        and metadata.get("max_model_len") == 65536
        and metadata.get("requested_max_tokens") == 16384
    )
    if not exact:
        raise _fail("execution_envelope_state_binding_mismatch")
    path = _resolve_under(
        validation, bindings.execution_envelope_path, "execution_envelope"
    )
    if _sha(path.read_bytes()) != bindings.execution_envelope_sha256:
        raise _fail("execution_envelope_hash_mismatch")
    validate_64k_envelope_evidence(_read_json(path, "execution_envelope"))


def _metadata(
    bindings: C2ReauthorizationBindings,
    cleanup: Mapping[str, Any],
    cleanup_payload_sha256: str,
) -> dict[str, Any]:
    failed_attempt_id = cleanup.get("failed_attempt_id")
    cleanup_source_freeze_path = (
        INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH
        if failed_attempt_id
        in {INTERRUPTED_C2_ATTEMPT_ID, SERVING_ENVELOPE_C2_ATTEMPT_ID}
        else CLEANUP_FREEZE_RELATIVE_PATH
    )
    metadata = {
        "schema_version": (
            "membind.native-characterization-reference-c2-authorization.v1"
        ),
        "source_state_sha256": bindings.source_state_sha256,
        "failed_attempt_id": failed_attempt_id,
        "failed_attempt_mergeable": False,
        "replacement_resume_allowed": False,
        "replacement_start_source_sequence": 0,
        "polluted_group_id": POLLUTED_C2_GROUP_ID,
        "cleanup_source_freeze_path": cleanup_source_freeze_path,
        "cleanup_source_freeze_sha256": cleanup.get("freeze_sha256"),
        "reference_freeze_path": bindings.reference_freeze_path,
        "reference_freeze_sha256": bindings.reference_freeze_sha256,
        "structured_output_mode": "json_schema",
        "semantic_attempts_authorized": 1,
        "run_id_pattern": "c2-[0-9a-f]{16}",
        "c2_runner_source_path": _C2_RUNNER_SOURCE_PATH,
        "c2_runner_source_sha256": bindings.c2_runner_source_sha256,
        "cleanup_evidence_path": bindings.cleanup_evidence_path,
        "cleanup_evidence_sha256": bindings.cleanup_evidence_sha256,
        "cleanup_evidence_payload_sha256": cleanup_payload_sha256,
        "cleanup_preexisting_empty": cleanup.get("preexisting_empty"),
        "final_full_regression_path": bindings.final_full_regression_path,
        "final_full_regression_sha256": bindings.final_full_regression_sha256,
        "final_full_regression_test_count": (
            bindings.final_full_regression_test_count
        ),
        "live_authorized": True,
    }
    if bindings.reference_freeze_path == NEW_REFERENCE_FREEZE_RELATIVE_PATH:
        metadata.update(
            {
                "execution_envelope_path": bindings.execution_envelope_path,
                "execution_envelope_sha256": bindings.execution_envelope_sha256,
                "focused_test_log_path": bindings.focused_test_log_path,
                "focused_test_log_sha256": bindings.focused_test_log_sha256,
                "focused_test_count": bindings.focused_test_count,
            }
        )
    return metadata


def build_native_characterization_c2_reauthorized_state(
    source_state: Mapping[str, Any],
    *,
    bindings: C2ReauthorizationBindings,
    cleanup: Mapping[str, Any],
    cleanup_payload_sha256: str,
) -> dict[str, Any]:
    """Build the exact C2-only target without filesystem or live I/O."""

    source = deepcopy(dict(source_state))
    _safe(source)
    _validate_source(source)
    if _sha(_canonical(source)) != bindings.source_state_sha256:
        raise _fail("source_state_drift")
    target = deepcopy(source)
    target["status"] = TARGET_STATUS
    target["current_blocker"] = None
    target["current_action_scope"] = TARGET_SCOPE
    target["authorized_live_actions"] = [TARGET_ACTION]
    target["native_characterization_live_authorized"] = True
    target["next_allowed_action"] = TARGET_NEXT_ACTION
    progress = deepcopy(dict(target["stage_progress"]))
    progress["native_characterization"] = TARGET_PROGRESS
    target["stage_progress"] = progress
    alignment = deepcopy(dict(target[REFERENCE_ALIGNMENT_KEY]))
    alignment["status"] = "c2_live_authorized"
    alignment["reference_freeze_path"] = bindings.reference_freeze_path
    alignment["reference_freeze_sha256"] = bindings.reference_freeze_sha256
    cleanup_state = deepcopy(dict(alignment["cleanup"]))
    cleanup_state["operator_authorized"] = False
    cleanup_state["execution_status"] = "verified_empty"
    cleanup_state["evidence_path"] = bindings.cleanup_evidence_path
    cleanup_state["evidence_sha256"] = bindings.cleanup_evidence_sha256
    cleanup_state["evidence_payload_sha256"] = cleanup_payload_sha256
    cleanup_state["pre_cleanup"] = deepcopy(cleanup.get("pre_cleanup"))
    cleanup_state["post_cleanup"] = deepcopy(cleanup.get("post_cleanup"))
    alignment["cleanup"] = cleanup_state
    fresh_c2 = deepcopy(dict(alignment["fresh_c2"]))
    fresh_c2["live_authorized"] = True
    alignment["fresh_c2"] = fresh_c2
    target[REFERENCE_ALIGNMENT_KEY] = alignment
    target[METADATA_KEY] = _metadata(
        bindings, cleanup, cleanup_payload_sha256
    )
    return target


@contextmanager
def _lock(path: Path):
    lock = path.parent / ".native-characterization-c2-reauthorization.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        raise _fail("reauthorization_lock_invalid") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    encoded = _canonical(value)
    try:
        mode = path.stat().st_mode & 0o777
        backup_fd, backup_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".bak"
        )
        os.close(backup_fd)
        backup = Path(backup_name)
        backup.unlink()
        os.link(path, backup)
    except OSError:
        raise _fail("atomic_write_failed") from None
    temporary: Path | None = None
    replaced = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
        replaced = True
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        backup.unlink(missing_ok=True)
    except OSError:
        if replaced:
            try:
                os.replace(backup, path)
                backup = None  # type: ignore[assignment]
            except OSError:
                pass
        raise _fail("atomic_write_failed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if backup is not None:
            backup.unlink(missing_ok=True)


def reauthorize_native_characterization_c2_live_only(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    bindings: C2ReauthorizationBindings,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate local cleanup/regression evidence and optionally authorize C2."""

    _validate_bindings(bindings)
    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root = Path(repo_root).resolve()
    validation = root / "membind-validation"
    path = Path(state_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    expected = root / STATE_RELATIVE_PATH
    try:
        path.relative_to(root)
    except ValueError:
        raise _fail("state_path_escape") from None
    if path != expected or path.is_symlink():
        raise _fail("state_path_not_current_state")

    def derive() -> dict[str, Any]:
        try:
            source = json.loads(path.read_bytes().decode("ascii"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise _fail("state_unreadable") from None
        if not isinstance(source, dict):
            raise _fail("state_not_object")
        _safe(source)
        if _sha(_canonical(source)) != bindings.source_state_sha256:
            raise _fail("source_state_drift")
        _validate_source(source)
        _validate_reference_freeze(validation, source, bindings)
        _validate_execution_envelope(validation, source, bindings)
        cleanup, cleanup_payload_sha256 = _validate_cleanup(
            validation, source, bindings
        )
        _validate_regression(validation, bindings)
        _validate_focused_test_log(validation, bindings)
        return build_native_characterization_c2_reauthorized_state(
            source,
            bindings=bindings,
            cleanup=cleanup,
            cleanup_payload_sha256=cleanup_payload_sha256,
        )

    if dry_run:
        return derive()
    with _lock(path):
        target = derive()
        _atomic_write(path, target)
        return target


__all__ = [
    "CLEANUP_FREEZE_RELATIVE_PATH",
    "C2ReauthorizationBindings",
    "FAILED_C2_ATTEMPT_ID",
    "METADATA_KEY",
    "NativeCharacterizationC2ReauthorizationError",
    "REFERENCE_FREEZE_RELATIVE_PATH",
    "build_native_characterization_c2_reauthorized_state",
    "reauthorize_native_characterization_c2_live_only",
]
