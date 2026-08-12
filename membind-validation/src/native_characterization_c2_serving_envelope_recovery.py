"""One-shot recovery for the C2 attempt blocked by the old 40K envelope.

This module is deliberately not a generic recovery framework.  It accepts one
immutable failed run, persists the already-completed 64K qualification, revokes
the consumed C2 grant, and derives a new reference freeze without touching the
old freeze that remains bound to the failed attempt.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FAILED_RUN_ID = "c2-4cc7d0599bbbbdac"
FAILED_GROUP_ID = "nc-e1e2-400b9b78c2c218df"
ERROR_CODE = "openai.BadRequestError"
SOURCE_STATE_SHA256 = (
    "2adabe8ced3d94139c167151c7740bc4119c8da61230aacf6b8354d72119fae6"
)
OLD_REFERENCE_FREEZE_RELATIVE_PATH = (
    "artifacts/native_characterization/freeze_reference_aligned.json"
)
OLD_REFERENCE_FREEZE_SHA256 = (
    "cea700f73f7dc942deeb49195e0a3ca235c35ec51a1c06fdab0edd94738330a7"
)
NEW_REFERENCE_FREEZE_RELATIVE_PATH = (
    "artifacts/native_characterization/freeze_reference_aligned_64k.json"
)
ENVELOPE_EVIDENCE_RELATIVE_PATH = (
    "artifacts/environment/native_characterization_64k_serving_envelope_20260812.json"
)
FAILURE_REPORT_RELATIVE_PATH = (
    "artifacts/diagnostics/"
    f"native_characterization_{FAILED_RUN_ID}_serving_envelope_failure.json"
)
FAILURE_METADATA_KEY = "native_characterization_c2_serving_envelope_failure"
ENVELOPE_METADATA_KEY = "native_characterization_64k_serving_envelope"
WORKPLAN_RELATIVE_PATH = "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
WORKPLAN_SHA256 = (
    "be3112cc2da4080ce98f9c94f1ab510ba5cc8350dca108a15e304da04c996b5b"
)

_RUN_RE = re.compile(r"^c2-[0-9a-f]{16}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ServingEnvelopeRecoveryError(RuntimeError):
    """Sanitized fail-closed error for the exact recovery transition."""


def _fail(reason: str) -> ServingEnvelopeRecoveryError:
    return ServingEnvelopeRecoveryError(
        f"C2 64K serving-envelope recovery denied: {reason}"
    )


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise _fail("value_not_canonicalizable") from None


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    value["payload_sha256"] = _sha(canonical_bytes(value))
    return value


def _validate_seal(value: Mapping[str, Any], label: str) -> str:
    candidate = deepcopy(dict(value))
    observed = candidate.pop("payload_sha256", None)
    if not isinstance(observed, str) or observed != _sha(canonical_bytes(candidate)):
        raise _fail(f"{label}_payload_hash_mismatch")
    return observed


def _json(encoded: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(encoded.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_invalid_json") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return value


def _safe(value: Any) -> None:
    forbidden = {
        "api_key",
        "authorization",
        "bearer",
        "credentials",
        "password",
        "raw_prompt",
        "raw_response",
        "secret",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold().replace("-", "_") in forbidden:
                raise _fail("unsafe_persisted_value")
            _safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe(child)
    elif isinstance(value, str) and (
        "bearer " in value.casefold() or "authorization:" in value.casefold()
    ):
        raise _fail("unsafe_persisted_value")


@dataclass(frozen=True)
class ArtifactBinding:
    path: str
    sha256: str
    line_count: int


@dataclass(frozen=True)
class ServingEnvelopeFailureBindings:
    run_id: str
    source_state_sha256: str
    checkpoint: ArtifactBinding
    block_checkpoint: ArtifactBinding
    jsonl_artifacts: tuple[ArtifactBinding, ...]
    outer_log: ArtifactBinding
    old_freeze_path: str
    old_freeze_sha256: str
    workplan_path: str
    workplan_sha256: str
    failure_report_path: str


def build_64k_envelope_evidence() -> dict[str, Any]:
    """Persist the already-completed single 64K probe; never send a request."""

    return _seal(
        {
            "schema_version": "membind.native-characterization-64k-envelope.v1",
            "qualification_status": "64K_ENVELOPE_PASS",
            "qualification_scope": "single_serving_admission_envelope_probe",
            "probe_reexecuted_by_recovery": False,
            "runtime": {
                "vllm_version": "0.26.0",
                "served_model_id": "qwen3-32b-fp8",
                "max_model_len": 65_536,
                "rope_type": "yarn",
                "yarn_factor": 2.0,
                "original_max_position_embeddings": 32_768,
                "rope_theta": 1_000_000,
                "structured_output_mode": "json_schema",
                "enable_thinking": False,
            },
            "probe": {
                "actual_prompt_tokens": 26_024,
                "requested_max_tokens": 16_384,
                "admission_envelope_tokens": 42_408,
                "http_status": 200,
                "completion_tokens": 6,
                "finish_reason": "stop",
                "structured_json_valid": True,
            },
            "server_capacity": {
                "engine_max_seq_len": 65_536,
                "gpu_kv_cache_tokens": 201_920,
                "max_concurrency_at_65536": 3.08,
            },
            "server_log": {
                "remote_relative_path": "logs/qwen3-32b-fp8-server.log",
                "sha256": (
                    "87cba52e7b6b40206ca5bc1af145355576ded94389a9a4a4ea78c98ee9abf2f1"
                ),
                "context_error": False,
                "kv_cache_error": False,
                "rope_error": False,
                "oom_error": False,
            },
            "decision": {
                "old_40960_admission_blocker_resolved": True,
                "automatic_131072_escalation": False,
                "completion_cap_changed": False,
            },
            "secrets_persisted": False,
        }
    )


def validate_64k_envelope_evidence(value: Mapping[str, Any]) -> None:
    _validate_seal(value, "64k_envelope")
    _safe(value)
    runtime = value.get("runtime")
    probe = value.get("probe")
    server_log = value.get("server_log")
    exact = (
        value.get("schema_version")
        == "membind.native-characterization-64k-envelope.v1"
        and value.get("qualification_status") == "64K_ENVELOPE_PASS"
        and value.get("probe_reexecuted_by_recovery") is False
        and isinstance(runtime, Mapping)
        and runtime.get("vllm_version") == "0.26.0"
        and runtime.get("served_model_id") == "qwen3-32b-fp8"
        and runtime.get("max_model_len") == 65_536
        and runtime.get("rope_type") == "yarn"
        and runtime.get("yarn_factor") == 2.0
        and runtime.get("original_max_position_embeddings") == 32_768
        and runtime.get("rope_theta") == 1_000_000
        and runtime.get("structured_output_mode") == "json_schema"
        and runtime.get("enable_thinking") is False
        and isinstance(probe, Mapping)
        and probe.get("actual_prompt_tokens") == 26_024
        and probe.get("requested_max_tokens") == 16_384
        and probe.get("admission_envelope_tokens") == 42_408
        and probe.get("http_status") == 200
        and probe.get("structured_json_valid") is True
        and isinstance(server_log, Mapping)
        and all(
            server_log.get(name) is False
            for name in ("context_error", "kv_cache_error", "rope_error", "oom_error")
        )
        and value.get("secrets_persisted") is False
    )
    if not exact:
        raise _fail("64k_envelope_contract_mismatch")


def derive_64k_reference_freeze(
    parent: Mapping[str, Any],
    *,
    parent_freeze_sha256: str,
    envelope_evidence_sha256: str,
    u0_runtime_source_sha256: str,
) -> dict[str, Any]:
    """Derive the new freeze while preserving every scientific input and block."""

    _validate_seal(parent, "parent_freeze")
    _require_sha(parent_freeze_sha256, "parent_freeze_sha256")
    _require_sha(envelope_evidence_sha256, "envelope_evidence_sha256")
    _require_sha(u0_runtime_source_sha256, "u0_runtime_source_sha256")
    if parent.get("schema_version") != "membind.native-characterization-freeze.v1":
        raise _fail("parent_freeze_schema_mismatch")
    identities = parent.get("runtime_identities")
    construction = identities.get("construction") if isinstance(identities, Mapping) else None
    policy = parent.get("construction_compatibility_policy")
    if not isinstance(construction, Mapping) or (
        construction.get("served_model_id") != "qwen3-32b-fp8"
        or construction.get("vllm_version") != "0.26.0"
        or construction.get("max_model_len") != 40_960
        or construction.get("enable_thinking") is not False
        or not isinstance(policy, Mapping)
        or policy.get("structured_output_mode") != "json_schema"
        or policy.get("requested_max_tokens") != 16_384
    ):
        raise _fail("parent_freeze_construction_contract_mismatch")

    derived = deepcopy(dict(parent))
    derived.pop("payload_sha256", None)
    derived["artifact_id"] = "native-characterization-freeze-reference-aligned-64k"
    derived["run_id"] = "native-characterization-freeze-reference-aligned-64k"
    derived["creation_command"] = (
        "one-shot mechanical derivation from freeze_reference_aligned.json "
        "after the persisted 64K serving-envelope qualification"
    )
    derived["derivation"] = {
        "parent_freeze_path": OLD_REFERENCE_FREEZE_RELATIVE_PATH,
        "parent_freeze_sha256": parent_freeze_sha256,
        "reason": "bind_qualified_64k_serving_envelope_after_context_admission_failure",
        "execution_envelope_evidence_path": ENVELOPE_EVIDENCE_RELATIVE_PATH,
        "execution_envelope_evidence_sha256": envelope_evidence_sha256,
    }
    transition = deepcopy(dict(derived.get("state_transition", {})))
    transition.update(
        {
            "authorization_status": "pending_verified_cleanup",
            "live_authorized": False,
            "execution_envelope_updated": True,
        }
    )
    derived["state_transition"] = transition
    runtime_identities = deepcopy(dict(identities))
    updated_construction = deepcopy(dict(construction))
    updated_construction.update(
        {
            "max_model_len": 65_536,
            "rope_type": "yarn",
            "yarn_factor": 2.0,
            "original_max_position_embeddings": 32_768,
            "rope_theta": 1_000_000,
        }
    )
    runtime_identities["construction"] = updated_construction
    derived["runtime_identities"] = runtime_identities
    inputs = deepcopy(dict(derived.get("input_hashes", {})))
    inputs["u0_runtime_source_sha256"] = u0_runtime_source_sha256
    inputs[ENVELOPE_EVIDENCE_RELATIVE_PATH] = envelope_evidence_sha256
    derived["input_hashes"] = inputs
    return _seal(derived)


def _validate_live_source(source: Mapping[str, Any], old_freeze_sha256: str) -> None:
    alignment = source.get("native_characterization_reference_alignment")
    fresh = alignment.get("fresh_c2") if isinstance(alignment, Mapping) else None
    receipt = source.get("native_characterization_reference_c2_authorization")
    exact = (
        source.get("protocol_version") == "current-validation-v1.3"
        and source.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and source.get("status") == "native_characterization_c2_live_only"
        and source.get("current_blocker") is None
        and source.get("current_action_scope") == "native_characterization_c2_live_only"
        and source.get("authorized_live_actions") == ["native_characterization_c2"]
        and source.get("native_characterization_live_authorized") is True
        and source.get("live_h0_candidate_authorized") is False
        and source.get("service_admin_authorized") is False
        and source.get("next_allowed_action") == "run_native_characterization_c2"
        and isinstance(alignment, Mapping)
        and alignment.get("status") == "c2_live_authorized"
        and alignment.get("reference_freeze_path")
        == OLD_REFERENCE_FREEZE_RELATIVE_PATH
        and alignment.get("reference_freeze_sha256") == old_freeze_sha256
        and isinstance(fresh, Mapping)
        and fresh.get("live_authorized") is True
        and fresh.get("semantic_attempts_remaining") == 1
        and fresh.get("start_source_sequence") == 0
        and fresh.get("resume_allowed") is False
        and fresh.get("prefix_merge_allowed") is False
        and fresh.get("structured_output_mode") == "json_schema"
        and isinstance(receipt, Mapping)
        and receipt.get("live_authorized") is True
        and receipt.get("replacement_resume_allowed") is False
        and receipt.get("replacement_start_source_sequence") == 0
        and receipt.get("semantic_attempts_authorized") == 1
        and FAILURE_METADATA_KEY not in source
    )
    if not exact:
        raise _fail("source_state_not_exact_live_c2_grant")


def build_cleanup_only_state(
    source_state: Mapping[str, Any],
    *,
    source_state_sha256: str,
    failure_report: Mapping[str, Any],
    failure_report_sha256: str,
    envelope_evidence: Mapping[str, Any],
    envelope_evidence_sha256: str,
    old_freeze_sha256: str,
) -> dict[str, Any]:
    """Build the fail-closed cleanup grant after all evidence is validated."""

    _require_sha(source_state_sha256, "source_state_sha256")
    _require_sha(failure_report_sha256, "failure_report_sha256")
    _require_sha(envelope_evidence_sha256, "envelope_evidence_sha256")
    _require_sha(old_freeze_sha256, "old_freeze_sha256")
    source = deepcopy(dict(source_state))
    if _sha(canonical_bytes(source)) != source_state_sha256:
        raise _fail("source_state_hash_mismatch")
    _validate_live_source(source, old_freeze_sha256)
    report_payload_sha = _validate_seal(failure_report, "failure_report")
    validate_64k_envelope_evidence(envelope_evidence)
    envelope_payload_sha = str(envelope_evidence["payload_sha256"])
    if _sha(canonical_bytes(failure_report) + b"\n") != failure_report_sha256:
        raise _fail("failure_report_file_hash_mismatch")
    if _sha(canonical_bytes(envelope_evidence) + b"\n") != envelope_evidence_sha256:
        raise _fail("envelope_evidence_file_hash_mismatch")

    target = deepcopy(source)
    target.update(
        {
            "status": "native_characterization_cleanup_only",
            "current_blocker": "c2_serving_envelope_failure_cleanup_pending",
            "current_action_scope": "native_characterization_c2_cleanup_only",
            "authorized_live_actions": [],
            "native_characterization_live_authorized": False,
            "next_allowed_action": (
                "execute_scoped_c2_cleanup_after_serving_envelope_failure"
            ),
        }
    )
    progress = deepcopy(dict(target["stage_progress"]))
    progress["native_characterization"] = (
        "c0_c1_pass_reference_c2_serving_envelope_failed_cleanup_pending"
    )
    target["stage_progress"] = progress
    alignment = deepcopy(dict(target["native_characterization_reference_alignment"]))
    alignment["status"] = "c2_serving_envelope_failed_cleanup_pending"
    fresh = deepcopy(dict(alignment["fresh_c2"]))
    fresh["live_authorized"] = False
    alignment["fresh_c2"] = fresh
    alignment["cleanup"] = {
        "operator_authorized": True,
        "execution_status": "pending",
        "failed_attempt_id": FAILED_RUN_ID,
        "failed_attempt_valid": False,
        "failed_attempt_mergeable": False,
        "replacement_resume_allowed": False,
        "target_group_id": FAILED_GROUP_ID,
        "source_freeze_path": OLD_REFERENCE_FREEZE_RELATIVE_PATH,
        "source_freeze_sha256": old_freeze_sha256,
        "planned_evidence_path": (
            f"artifacts/native_characterization/c2_cleanup/{FAILED_RUN_ID}.json"
        ),
        "required_post_node_count": 0,
        "required_post_relationship_count": 0,
    }
    target["native_characterization_reference_alignment"] = alignment
    receipt = deepcopy(dict(target["native_characterization_reference_c2_authorization"]))
    receipt["live_authorized"] = False
    receipt["consumed_by_run_id"] = FAILED_RUN_ID
    target["native_characterization_reference_c2_authorization"] = receipt
    target[ENVELOPE_METADATA_KEY] = {
        "schema_version": "membind.native-characterization-64k-envelope-state.v1",
        "qualification_status": "64K_ENVELOPE_PASS",
        "evidence_path": ENVELOPE_EVIDENCE_RELATIVE_PATH,
        "evidence_payload_sha256": envelope_payload_sha,
        "evidence_sha256": envelope_evidence_sha256,
        "max_model_len": 65_536,
        "requested_max_tokens": 16_384,
        "actual_prompt_tokens": 26_024,
    }
    target[FAILURE_METADATA_KEY] = {
        "schema_version": (
            "membind.native-characterization-c2-serving-envelope-failure-state.v1"
        ),
        "source_state_sha256": source_state_sha256,
        "run_id": FAILED_RUN_ID,
        "error_code": ERROR_CODE,
        "completed_episode_count": 10,
        "completed_block_count": 0,
        "failed_source_sequence": 10,
        "attempt_valid": False,
        "attempt_mergeable": False,
        "resume_allowed": False,
        "prefix_merge_allowed": False,
        "semantic_attempt_consumed": False,
        "semantic_attempts_remaining": 1,
        "live_authorized": False,
        "cleanup_authorized": True,
        "report_path": FAILURE_REPORT_RELATIVE_PATH,
        "report_payload_sha256": report_payload_sha,
        "report_sha256": failure_report_sha256,
    }
    return target


def _read_bound(root: Path, binding: ArtifactBinding, label: str) -> bytes:
    _require_sha(binding.sha256, f"{label}_sha256")
    relative = Path(binding.path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise _fail(f"{label}_path_invalid")
    path = root / relative
    if path.is_symlink():
        raise _fail(f"{label}_symlink")
    try:
        encoded = path.read_bytes()
    except OSError:
        raise _fail(f"{label}_unreadable") from None
    if _sha(encoded) != binding.sha256:
        raise _fail(f"{label}_hash_mismatch")
    return encoded


def _validate_checkpoint(encoded: bytes, *, block: bool) -> dict[str, Any]:
    value = _json(encoded, "block_checkpoint" if block else "checkpoint")
    _validate_seal(value, "block_checkpoint" if block else "checkpoint")
    expected_ids = [f"07741c45:{index}" for index in range(10)]
    exact = (
        value.get("schema_version")
        == "membind.native-characterization-c2-checkpoint.v1"
        and value.get("run_id") == FAILED_RUN_ID
        and value.get("stage") == "C2"
        and value.get("status") == ("episode_completed" if block else "error")
        and value.get("error_code") == (None if block else ERROR_CODE)
        and value.get("completed_block_indices") == []
        and value.get("completed_episode_ids") == expected_ids
        and len(value.get("checkpoint_history", [])) == 10
    )
    if not exact:
        raise _fail("checkpoint_contract_mismatch")
    return value


def validate_failed_attempt(
    *,
    validation_root: Path,
    repo_root: Path,
    bindings: ServingEnvelopeFailureBindings,
) -> dict[str, Any]:
    """Validate the immutable failed-run evidence and return its sealed report."""

    if (
        bindings.run_id != FAILED_RUN_ID
        or _RUN_RE.fullmatch(bindings.run_id) is None
        or bindings.source_state_sha256 != SOURCE_STATE_SHA256
        or bindings.old_freeze_path != OLD_REFERENCE_FREEZE_RELATIVE_PATH
        or bindings.old_freeze_sha256 != OLD_REFERENCE_FREEZE_SHA256
        or bindings.workplan_path != WORKPLAN_RELATIVE_PATH
        or bindings.workplan_sha256 != WORKPLAN_SHA256
        or bindings.failure_report_path != FAILURE_REPORT_RELATIVE_PATH
    ):
        raise _fail("bindings_not_exact_failed_attempt")
    checkpoint = _validate_checkpoint(
        _read_bound(validation_root, bindings.checkpoint, "checkpoint"), block=False
    )
    _validate_checkpoint(
        _read_bound(validation_root, bindings.block_checkpoint, "block_checkpoint"),
        block=True,
    )
    names: set[str] = set()
    inventory: list[dict[str, Any]] = []
    for binding in bindings.jsonl_artifacts:
        name = "trace" if Path(binding.path).name == "trace.jsonl" else Path(binding.path).stem
        if name in names or name not in {
            "db", "embedding", "errors", "events", "llm", "spans", "trace"
        }:
            raise _fail("jsonl_inventory_mismatch")
        names.add(name)
        encoded = _read_bound(validation_root, binding, f"jsonl_{name}")
        lines = encoded.splitlines()
        if binding.line_count != 11 or len(lines) != 11:
            raise _fail(f"jsonl_{name}_line_count_mismatch")
        rows = [_json(line, f"jsonl_{name}_line") for line in lines]
        if any(
            row.get("run_id") != FAILED_RUN_ID
            or row.get("episode_id") != f"07741c45:{index}"
            or row.get("source_sequence") != index
            for index, row in enumerate(rows)
        ):
            raise _fail(f"jsonl_{name}_trajectory_mismatch")
        if name == "errors":
            spans = rows[10].get("spans")
            if not isinstance(spans, list) or not any(
                isinstance(span, Mapping)
                and span.get("status") == "error"
                and span.get("error_code") == ERROR_CODE
                for span in spans
            ):
                raise _fail("failure_error_envelope_missing")
        inventory.append(
            {"path": binding.path, "sha256": binding.sha256, "line_count": 11}
        )
    if names != {"db", "embedding", "errors", "events", "llm", "spans", "trace"}:
        raise _fail("jsonl_inventory_mismatch")

    outer = _read_bound(validation_root, bindings.outer_log, "outer_log")
    try:
        text = outer.decode("utf-8")
    except UnicodeError:
        raise _fail("outer_log_not_utf8") from None
    if "bearer " in text.casefold() or "authorization:" in text.casefold():
        raise _fail("outer_log_unsafe")
    lines = [line for line in text.splitlines() if line.strip()]
    if (
        len(lines) < 2
        or "maximum context length is 40960 tokens" not in lines[-2]
        or "requested 16384 output tokens" not in lines[-2]
        or "at least 24577 input tokens" not in lines[-2]
        or lines[-1] != '{"error_code":"openai.BadRequestError","status":"error"}'
    ):
        raise _fail("outer_log_terminal_envelope_mismatch")
    old_freeze = _read_bound(
        validation_root,
        ArtifactBinding(bindings.old_freeze_path, bindings.old_freeze_sha256, 0),
        "old_freeze",
    )
    _validate_seal(_json(old_freeze, "old_freeze"), "old_freeze")
    _read_bound(
        repo_root,
        ArtifactBinding(bindings.workplan_path, bindings.workplan_sha256, 0),
        "workplan",
    )

    return _seal(
        {
            "schema_version": (
                "membind.native-characterization-c2-serving-envelope-failure.v1"
            ),
            "classification": "serving_envelope_misconfiguration",
            "run_id": FAILED_RUN_ID,
            "status": "incomplete_invalid_non_mergeable",
            "failure_position": {
                "block_index": 0,
                "history_id": "07741c45",
                "failed_source_sequence": 10,
                "completed_episode_count": len(checkpoint["completed_episode_ids"]),
                "completed_block_count": 0,
            },
            "error_class": ERROR_CODE,
            "token_envelope": {
                "prompt_tokens_lower_bound": 24_577,
                "requested_max_tokens": 16_384,
                "output_tokens_observed": 0,
                "admission_total_lower_bound": 40_961,
                "old_max_model_len": 40_960,
                "http_status": 400,
            },
            "attempt_valid": False,
            "attempt_mergeable": False,
            "resume_allowed": False,
            "prefix_merge_allowed": False,
            "semantic_attempt_consumed": False,
            "semantic_attempts_remaining": 1,
            "checkpoint": {
                "path": bindings.checkpoint.path,
                "sha256": bindings.checkpoint.sha256,
            },
            "block_checkpoint": {
                "path": bindings.block_checkpoint.path,
                "sha256": bindings.block_checkpoint.sha256,
            },
            "run_artifacts": sorted(inventory, key=lambda item: item["path"]),
            "outer_log": {
                "path": bindings.outer_log.path,
                "sha256": bindings.outer_log.sha256,
            },
            "frozen_inputs": {
                "freeze_path": bindings.old_freeze_path,
                "freeze_sha256": bindings.old_freeze_sha256,
                "workplan_path": bindings.workplan_path,
                "workplan_sha256": bindings.workplan_sha256,
            },
            "cleanup": {
                "authorized": True,
                "target_group_id": FAILED_GROUP_ID,
                "planned_evidence_path": (
                    f"artifacts/native_characterization/c2_cleanup/{FAILED_RUN_ID}.json"
                ),
                "required_post_node_count": 0,
                "required_post_relationship_count": 0,
            },
            "secrets_persisted": False,
            "source_state_sha256": bindings.source_state_sha256,
        }
    )


def default_bindings() -> ServingEnvelopeFailureBindings:
    run_root = f"artifacts/native_characterization/runs/{FAILED_RUN_ID}"
    return ServingEnvelopeFailureBindings(
        run_id=FAILED_RUN_ID,
        source_state_sha256=SOURCE_STATE_SHA256,
        checkpoint=ArtifactBinding(
            f"{run_root}/checkpoint.json",
            "4fc29a435790c55e17c8d4966203fc39784237100131475e82993dc2bf5df120",
            1,
        ),
        block_checkpoint=ArtifactBinding(
            f"{run_root}/blocks/000_07741c45/checkpoint.json",
            "fb4bf9ae63f00fa690e8d51958c81e9c3856d64a19c7d99073a38473bab5de99",
            1,
        ),
        jsonl_artifacts=(
            ArtifactBinding(f"{run_root}/db.jsonl", "3d31de11f3d510280b450e85725201c199951dc35f6d605663691b5e10827e7b", 11),
            ArtifactBinding(f"{run_root}/embedding.jsonl", "673b51ddd0ff19a1a2f23fdc5b8c95a16830b2d5e7a046766b4e7161b5a3aa02", 11),
            ArtifactBinding(f"{run_root}/errors.jsonl", "849fa6cc40476801779d188df0328a01285f2245793dbd0344bbba715cbe319e", 11),
            ArtifactBinding(f"{run_root}/events.jsonl", "7c953211de7076ce14e2191d2fd71f2f963d32fa853cbf2e7fb444c3ec4ae7d5", 11),
            ArtifactBinding(f"{run_root}/llm.jsonl", "d14da83043b74b99d215351f0f50bfeb2f5e5592bdf26330be59c9b933ba7757", 11),
            ArtifactBinding(f"{run_root}/spans.jsonl", "05bdaa81888dafca56d9ba1961b67974866a4d64d3c88d1f0c2cf7728a9b84e3", 11),
            ArtifactBinding(f"{run_root}/blocks/000_07741c45/trace.jsonl", "7cf33bcbeaab285b23fbe399115dbeab2646331aae488e1cd540104bcc763eb0", 11),
        ),
        outer_log=ArtifactBinding(
            f"artifacts/tdd/native_characterization_{FAILED_RUN_ID}_live_20260811.log",
            "68544c5a79be0e30ca6a97da54baa7916aeb1c94913d2cd1ad00af202c8de81f",
            0,
        ),
        old_freeze_path=OLD_REFERENCE_FREEZE_RELATIVE_PATH,
        old_freeze_sha256=OLD_REFERENCE_FREEZE_SHA256,
        workplan_path=WORKPLAN_RELATIVE_PATH,
        workplan_sha256=WORKPLAN_SHA256,
        failure_report_path=FAILURE_REPORT_RELATIVE_PATH,
    )


def _atomic_write(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise _fail("atomic_write_failed") from None


@contextmanager
def _locked(state_path: Path):
    lock_path = state_path.parent / ".native-characterization-c2-64k-recovery.lock"
    try:
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
    except OSError:
        raise _fail("lock_failed") from None


def finalize_serving_envelope_failure(
    *,
    state_path: Path,
    validation_root: Path,
    repo_root: Path,
    bindings: ServingEnvelopeFailureBindings | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Dry-run or atomically persist failure, qualification, and cleanup grant."""

    bindings = default_bindings() if bindings is None else bindings
    report = validate_failed_attempt(
        validation_root=validation_root,
        repo_root=repo_root,
        bindings=bindings,
    )
    envelope = build_64k_envelope_evidence()
    report_encoded = canonical_bytes(report) + b"\n"
    envelope_encoded = canonical_bytes(envelope) + b"\n"
    report_path = validation_root / bindings.failure_report_path
    envelope_path = validation_root / ENVELOPE_EVIDENCE_RELATIVE_PATH
    with _locked(state_path):
        try:
            source = _json(state_path.read_bytes(), "state")
        except OSError:
            raise _fail("state_unreadable") from None
        existing = source.get(FAILURE_METADATA_KEY)
        if isinstance(existing, Mapping):
            if (
                existing.get("run_id") != FAILED_RUN_ID
                or source.get("status") != "native_characterization_cleanup_only"
                or source.get("native_characterization_live_authorized") is not False
                or not report_path.is_file()
                or report_path.read_bytes() != report_encoded
                or not envelope_path.is_file()
                or envelope_path.read_bytes() != envelope_encoded
            ):
                raise _fail("partial_or_drifted_applied_state")
            return {
                "status": "already_applied",
                "run_id": FAILED_RUN_ID,
                "target_state_sha256": _sha(canonical_bytes(source)),
                "failure_report_sha256": _sha(report_encoded),
                "envelope_evidence_sha256": _sha(envelope_encoded),
            }
        target = build_cleanup_only_state(
            source,
            source_state_sha256=bindings.source_state_sha256,
            failure_report=report,
            failure_report_sha256=_sha(report_encoded),
            envelope_evidence=envelope,
            envelope_evidence_sha256=_sha(envelope_encoded),
            old_freeze_sha256=bindings.old_freeze_sha256,
        )
        result = {
            "status": "validated_not_applied",
            "run_id": FAILED_RUN_ID,
            "target_state_sha256": _sha(canonical_bytes(target)),
            "failure_report_sha256": _sha(report_encoded),
            "envelope_evidence_sha256": _sha(envelope_encoded),
        }
        if not apply:
            return result
        for path, encoded in (
            (report_path, report_encoded),
            (envelope_path, envelope_encoded),
        ):
            if path.exists() and path.read_bytes() != encoded:
                raise _fail("evidence_path_conflict")
            _atomic_write(path, encoded)
        _atomic_write(state_path, canonical_bytes(target) + b"\n")
        result["status"] = "applied"
        return result


def write_64k_reference_freeze(
    *, validation_root: Path, state_path: Path
) -> dict[str, Any]:
    """Persist the independently named 64K freeze after the exact failure seal."""

    state = _json(state_path.read_bytes(), "state")
    failure = state.get(FAILURE_METADATA_KEY)
    envelope_meta = state.get(ENVELOPE_METADATA_KEY)
    if (
        state.get("status") != "native_characterization_cleanup_only"
        or not isinstance(failure, Mapping)
        or failure.get("run_id") != FAILED_RUN_ID
        or not isinstance(envelope_meta, Mapping)
        or envelope_meta.get("qualification_status") != "64K_ENVELOPE_PASS"
    ):
        raise _fail("state_not_sealed_for_64k_freeze")
    envelope_path = validation_root / ENVELOPE_EVIDENCE_RELATIVE_PATH
    envelope_encoded = envelope_path.read_bytes()
    if _sha(envelope_encoded) != envelope_meta.get("evidence_sha256"):
        raise _fail("envelope_evidence_hash_mismatch")
    validate_64k_envelope_evidence(_json(envelope_encoded, "64k_envelope"))
    parent_path = validation_root / OLD_REFERENCE_FREEZE_RELATIVE_PATH
    parent_encoded = parent_path.read_bytes()
    if _sha(parent_encoded) != OLD_REFERENCE_FREEZE_SHA256:
        raise _fail("parent_freeze_hash_mismatch")
    runtime_source = validation_root / "src/native_characterization_runtime.py"
    derived = derive_64k_reference_freeze(
        _json(parent_encoded, "parent_freeze"),
        parent_freeze_sha256=OLD_REFERENCE_FREEZE_SHA256,
        envelope_evidence_sha256=_sha(envelope_encoded),
        u0_runtime_source_sha256=_sha(runtime_source.read_bytes()),
    )
    encoded = canonical_bytes(derived) + b"\n"
    target = validation_root / NEW_REFERENCE_FREEZE_RELATIVE_PATH
    if target.exists() and target.read_bytes() != encoded:
        raise _fail("derived_freeze_path_conflict")
    _atomic_write(target, encoded)
    return {
        "status": "written",
        "path": NEW_REFERENCE_FREEZE_RELATIVE_PATH,
        "sha256": _sha(encoded),
        "payload_sha256": derived["payload_sha256"],
        "u0_runtime_source_sha256": derived["input_hashes"][
            "u0_runtime_source_sha256"
        ],
    }


__all__ = [
    "ENVELOPE_EVIDENCE_RELATIVE_PATH",
    "FAILED_GROUP_ID",
    "FAILED_RUN_ID",
    "FAILURE_METADATA_KEY",
    "NEW_REFERENCE_FREEZE_RELATIVE_PATH",
    "OLD_REFERENCE_FREEZE_RELATIVE_PATH",
    "ServingEnvelopeFailureBindings",
    "ServingEnvelopeRecoveryError",
    "build_64k_envelope_evidence",
    "build_cleanup_only_state",
    "default_bindings",
    "derive_64k_reference_freeze",
    "finalize_serving_envelope_failure",
    "validate_64k_envelope_evidence",
    "write_64k_reference_freeze",
]
