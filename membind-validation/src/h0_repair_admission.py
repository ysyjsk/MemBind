"""Transparent one-shot admissions for the invalid Q1/H0-A and H0-B runs.

The H0-A decision discloses its prior technical 3/3 observation.  The separate
H0-B decision proves that its nominal-client harness repair precedes all model
workload.  Neither path changes or consumes its immutable failed evidence.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from h0_runtime import canonical_json_bytes, canonical_json_sha256, sha256_file


PROTOCOL_VERSION = "current-validation-v1.3"
ARTIFACT_SET_ID = "v1_3_harness_r2"
HARNESS_REVISION = 2
R2_INDEX_PATH = (
    "artifacts/h0_manifest_sets/v1_3_harness_r2/"
    "resolved_manifest_index_v1_3_harness_r2.json"
)
LEGACY_INDEX_PATH = "artifacts/h0/resolved_manifest_index_v1_3.json"
LEGACY_INDEX_SHA256 = "9fe8f4e5c4a8c85f5a8cf56a4e3f887f1d82d9082bc96f0775b327dd6c633334"
INVALIDATION_PATH = "artifacts/diagnostics/h0_q1_a_protocol_invalidation_20260809.json"
INVALIDATION_SHA256 = "86989915acd603e8192f2a19b96b0f6bbcc5a603ba0b75256db837b076d0f71a"
RESULT_PATH = "artifacts/diagnostics/h0_q1_a_result_20260809.json"
RESULT_SHA256 = "bbe375410b1962fceab71c495e5c29ec04e4e9077279a8276f4aa025593066d5"
INVALIDATED_ATTEMPT_ID = "h0-q1-a-20260809-attempt-001"
INVALIDATED_CHECKPOINT_PATH = (
    "artifacts/h0_runs/h0/checkpoints/"
    "h0-q1-a-20260809-attempt-001/index.json"
)
INVALIDATED_CHECKPOINT_SHA256 = (
    "127c81b39ccd705d7c67dc936e953992d5be97f4065fd56f3655db52d12ad309"
)
DECISION_ROOT = "artifacts/h0_protocol_repair/decisions"
H0_B_R2_ARTIFACT_SET_ID = "v1_3_harness_r2"
H0_B_R3_ARTIFACT_SET_ID = "v1_3_harness_r3"
H0_B_R3_HARNESS_REVISION = 3
H0_B_R3_INDEX_PATH = (
    "artifacts/h0_manifest_sets/v1_3_harness_r3/"
    "resolved_manifest_index_v1_3_harness_r3.json"
)
H0_B_INVALIDATED_ATTEMPT_ID = "h0-q1-b-20260809-attempt-001"
H0_B_INVALIDATED_CHECKPOINT_PATH = (
    "artifacts/h0_runs/h0/checkpoints/"
    "h0-q1-b-20260809-attempt-001/index.json"
)
H0_B_INVALIDATED_CHECKPOINT_SHA256 = (
    "fa6280ede4387775c719abd410478b5e1db358d840a10a69025c5a6cddd48896"
)
H0_B_FAILURE_REPORT_PATH = (
    "artifacts/diagnostics/h0_q1_b_preworkload_failure_report_20260809.json"
)
H0_B_FAILURE_REPORT_SHA256 = (
    "2bde8463ba862a13d4e3b580e3accc7ce0cf15f1eccdd923fee167eb91b7be31"
)
H0_B_R2_INDEX_PATH = (
    "artifacts/h0_manifest_sets/v1_3_harness_r2/"
    "resolved_manifest_index_v1_3_harness_r2.json"
)
H0_B_R2_INDEX_SHA256 = (
    "be31de29de13fb0d607570cbc1832c7df32fe83af51ec3ab31722ec036f172cf"
)
H0_B_REPLACEMENT_ATTEMPT_ID = "h0-q1-b-20260809-replacement-001"
H0_B_DECISION_PATH = (
    "artifacts/h0_protocol_repair/decisions/"
    "q1_h0_b_harness_compatibility_repair.json"
)
H0_B_R4_ARTIFACT_SET_ID = "v1_3_harness_r4"
H0_B_R4_HARNESS_REVISION = 4
H0_B_R4_INDEX_PATH = (
    "artifacts/h0_manifest_sets/v1_3_harness_r4/"
    "resolved_manifest_index_v1_3_harness_r4.json"
)
H0_B_R3_INDEX_SHA256 = (
    "13adf4852194399985f5750ed8e91eed6990f9a07d8409feabc0dd3c9f9d7624"
)
H0_B_INTERRUPTED_ATTEMPT_ID = "h0-q1-b-20260809-replacement-001"
H0_B_INTERRUPTED_CHECKPOINT_PATH = (
    "artifacts/h0_runs/h0/checkpoints/"
    "h0-q1-b-20260809-replacement-001/index.json"
)
H0_B_INTERRUPTED_CHECKPOINT_SHA256 = (
    "7305c1ff2c5790223bb22a0ad8a3e6749c3752950164641eb5a546cfe8aa4553"
)
H0_B_INTERRUPTION_REPORT_PATH = (
    "artifacts/diagnostics/"
    "h0_q1_b_replacement_001_infrastructure_interruption_20260809.json"
)
H0_B_INTERRUPTION_REPORT_SHA256 = (
    "b55661f6e2635683512b59a6f6a75b81a8813a21e30025d9f17688a45bd50513"
)
H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID = (
    "h0-q1-b-20260810-replacement-002"
)
H0_B_INFRASTRUCTURE_RERUN_DECISION_PATH = (
    "artifacts/h0_protocol_repair/decisions/"
    "q1_h0_b_infrastructure_rerun.json"
)
H0_B_POST_WORKLOAD_FAILED_ATTEMPT_ID = "h0-q1-b-20260810-replacement-002"
H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID = (
    "h0-q1-b-20260810-replacement-003"
)
H0_B_R4_INDEX_SHA256 = (
    "a08b3f704c9680476990f24edc239d4af50ced39edcf9aae0d529b5ed14332d7"
)
H0_B_R5_ARTIFACT_SET_ID = "v1_3_harness_r5"
H0_B_R5_HARNESS_REVISION = 5
H0_B_R5_INDEX_PATH = (
    "artifacts/h0_manifest_sets/v1_3_harness_r5/"
    "resolved_manifest_index_v1_3_harness_r5.json"
)
H0_B_POST_WORKLOAD_DECISION_PATH = (
    "artifacts/h0_protocol_repair/decisions/"
    "q1_h0_b_post_workload_harness_repair.json"
)
H0_B_POST_WORKLOAD_CHECKPOINT_PATH = (
    "artifacts/h0_runs/h0/checkpoints/"
    "h0-q1-b-20260810-replacement-002/index.json"
)
H0_B_POST_WORKLOAD_CHECKPOINT_SHA256 = (
    "e2187d3e101459e9c9a873d8dffb3fbcc858d139833f7f392eedff1c2c78c665"
)
H0_B_POST_WORKLOAD_FAILURE_SEGMENT_PATH = (
    "artifacts/h0_runs/h0/checkpoints/h0-q1-b-20260810-replacement-002/"
    "000014.candidate_failure.manifest_contract_failure."
    "689285595818aac01f008cb279d3a71cdb084abe35dd79e04e23e93d9d3eadd5.json"
)
H0_B_POST_WORKLOAD_FAILURE_SEGMENT_SHA256 = (
    "689285595818aac01f008cb279d3a71cdb084abe35dd79e04e23e93d9d3eadd5"
)
H0_B_POST_WORKLOAD_SOURCE_CHECKPOINT_PATH = (
    "artifacts/h0_runs/h0/checkpoints/h0-q1-b-20260810-replacement-002/"
    "000013.source_sequence.07741c45-000."
    "1cdb5b70c86790d144179e855143018d2a97cd32d9e9fc70d5c1e218cd88211c.json"
)
H0_B_POST_WORKLOAD_SOURCE_CHECKPOINT_SHA256 = (
    "1cdb5b70c86790d144179e855143018d2a97cd32d9e9fc70d5c1e218cd88211c"
)
H0_B_POST_WORKLOAD_LIVE_LOG_PATH = (
    "artifacts/live_logs/h0_q1_b_20260810_replacement_002.log"
)
H0_B_POST_WORKLOAD_LIVE_LOG_SHA256 = (
    "3e6819b01be43045739cdc4c2d5cd95bf8e7b85bd001300dfa92eb1d36dc4deb"
)
H0_B_POST_WORKLOAD_OFFLINE_PROBE_PATH = (
    "artifacts/diagnostics/"
    "h0_q1_b_replacement_002_embedding_contract_offline_probe_20260810_002.log"
)
H0_B_POST_WORKLOAD_OFFLINE_PROBE_SHA256 = (
    "06b255f8450852c31afce839d13bedad97f32857c86ac204e86fc6857cb06a3e"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class H0RepairAdmissionError(RuntimeError):
    """A sanitized denial of the protocol-repair decision or admission."""


def _fail(reason: str) -> H0RepairAdmissionError:
    return H0RepairAdmissionError(f"H0 repair admission denied: {reason}")


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_sha256_invalid")
    return value


def _read_bound_json(
    root: Path,
    relative_value: Any,
    digest_value: Any,
    *,
    label: str,
) -> tuple[dict[str, Any], str, str]:
    if not isinstance(relative_value, str) or not relative_value:
        raise _fail(f"{label}_path_invalid")
    relative = Path(relative_value)
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_value
        or any(part in {"", ".", "..", ".env", "gpt55_temporary"} for part in relative.parts)
    ):
        raise _fail(f"{label}_path_noncanonical")
    digest = _sha(digest_value, label)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_symlink_forbidden")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise _fail(f"{label}_path_escape") from None
    if not path.is_file() or sha256_file(path) != digest:
        raise _fail(f"{label}_missing_or_hash_mismatch")
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_invalid_json") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return value, relative.as_posix(), digest


def _validated_r2(
    root: Path, verification: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not isinstance(verification, Mapping):
        raise _fail("manifest_verification_not_object")
    exact = (
        verification.get("schema_version")
        == "membind.h0.offline-artifact-verification.v2"
        and verification.get("protocol_version") == PROTOCOL_VERSION
        and verification.get("artifact_set_id") == ARTIFACT_SET_ID
        and verification.get("execution_harness_revision") == HARNESS_REVISION
        and verification.get("status") == "verified_offline_not_live_authorized"
        and verification.get("index_path") == R2_INDEX_PATH
        and verification.get("secret_scan_passed") is True
        and verification.get("live_eligible") is False
    )
    if not exact:
        raise _fail("manifest_verification_mismatch")
    index, _, index_sha = _read_bound_json(
        root,
        verification.get("index_path"),
        verification.get("index_sha256"),
        label="r2_index",
    )
    if not (
        index.get("schema_version") == "membind.h0.offline-artifacts.v2"
        and index.get("protocol_version") == PROTOCOL_VERSION
        and index.get("artifact_set_id") == ARTIFACT_SET_ID
        and index.get("execution_harness_revision") == HARNESS_REVISION
        and index.get("status") == "offline_resolved_not_live_authorized"
        and index.get("live_h0_candidate_authorized") is False
        and index.get("source_specs_immutable") is True
        and index.get("unresolved_fields") == []
        and index.get("secrets_persisted") is False
    ):
        raise _fail("r2_index_contract_mismatch")
    resolved = index.get("resolved_manifests")
    q1_ref = resolved.get("Q1") if isinstance(resolved, Mapping) else None
    if not isinstance(q1_ref, Mapping):
        raise _fail("r2_q1_reference_missing")
    q1, q1_path, q1_sha = _read_bound_json(
        root, q1_ref.get("path"), q1_ref.get("sha256"), label="r2_q1"
    )
    if not q1_path.startswith(
        "artifacts/h0_manifest_sets/v1_3_harness_r2/resolved_candidates/Q1."
    ) or not q1_path.endswith(f".{q1_sha}.json"):
        raise _fail("r2_q1_path_not_content_addressed")
    return index, q1, index_sha


def _semantic_projection(root: Path, candidate: Mapping[str, Any]) -> Any:
    bindings = candidate.get("resolved_shared_artifacts")
    reference = (
        bindings.get("semantic_guardrail_manifest_sha256")
        if isinstance(bindings, Mapping)
        else None
    )
    if not isinstance(reference, Mapping):
        # Minimal offline test fixtures may omit a copied semantic artifact;
        # the binding itself still remains part of the candidate projection.
        return None
    manifest, _, _ = _read_bound_json(
        root,
        reference.get("path"),
        reference.get("sha256"),
        label="semantic_guardrail",
    )
    return {
        key: manifest.get(key)
        for key in (
            "data_scope",
            "candidate_outputs_used_to_set_invariants",
            "invariants_frozen_before_candidate_execution",
            "normalization",
            "expected_nonempty_call_ids",
        )
    }


def build_h0_repair_decision(
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    replacement_attempt_id: str,
) -> dict[str, Any]:
    """Derive the exact disclosed decision without writing it."""

    if (
        not isinstance(replacement_attempt_id, str)
        or _IDENTIFIER_RE.fullmatch(replacement_attempt_id) is None
        or replacement_attempt_id == INVALIDATED_ATTEMPT_ID
    ):
        raise _fail("replacement_attempt_id_invalid")
    root_path = Path(root).resolve()
    _, r2_q1, r2_index_sha = _validated_r2(root_path, manifest_verification)
    legacy_index, _, legacy_index_sha = _read_bound_json(
        root_path,
        LEGACY_INDEX_PATH,
        LEGACY_INDEX_SHA256,
        label="legacy_index",
    )
    legacy_resolved = legacy_index.get("resolved_manifests")
    legacy_q1_ref = (
        legacy_resolved.get("Q1") if isinstance(legacy_resolved, Mapping) else None
    )
    if not isinstance(legacy_q1_ref, Mapping):
        raise _fail("legacy_q1_reference_missing")
    legacy_q1, legacy_q1_path, legacy_q1_sha = _read_bound_json(
        root_path,
        legacy_q1_ref.get("path"),
        legacy_q1_ref.get("sha256"),
        label="legacy_q1",
    )
    if not (
        r2_q1.get("candidate_id") == "Q1"
        and legacy_q1.get("candidate_id") == "Q1"
        and r2_q1.get("candidate_configuration")
        == legacy_q1.get("candidate_configuration")
        and r2_q1.get("source_delta_spec") == legacy_q1.get("source_delta_spec")
    ):
        raise _fail("q1_scientific_configuration_changed")
    legacy_semantic = _semantic_projection(root_path, legacy_q1)
    r2_semantic = _semantic_projection(root_path, r2_q1)
    if (
        legacy_semantic is not None
        and r2_semantic is not None
        and legacy_semantic != r2_semantic
    ):
        raise _fail("semantic_thresholds_changed")

    invalidation, invalidation_path, invalidation_sha = _read_bound_json(
        root_path,
        INVALIDATION_PATH,
        INVALIDATION_SHA256,
        label="invalidation",
    )
    result, result_path, result_sha = _read_bound_json(
        root_path,
        RESULT_PATH,
        RESULT_SHA256,
        label="prior_result",
    )
    _, checkpoint_path, checkpoint_sha = _read_bound_json(
        root_path,
        INVALIDATED_CHECKPOINT_PATH,
        INVALIDATED_CHECKPOINT_SHA256,
        label="invalidated_checkpoint",
    )
    observation = invalidation.get("technical_observation")
    disposition = invalidation.get("protocol_disposition")
    invalidation_checkpoint = invalidation.get("checkpoint")
    result_execution = result.get("technical_execution")
    result_observations = (
        result_execution.get("observations")
        if isinstance(result_execution, Mapping)
        else None
    )
    if not (
        invalidation.get("schema_version")
        == "membind.h0.protocol-invalidation-diagnostic.v1"
        and invalidation.get("protocol_version") == PROTOCOL_VERSION
        and invalidation.get("status") == "invalidated_protocol_gate_order"
        and invalidation.get("stage_attempt_id") == INVALIDATED_ATTEMPT_ID
        and invalidation.get("candidate_id") == "Q1"
        and invalidation.get("phase") == "H0-A"
        and invalidation.get("discovered_after_execution") is True
        and isinstance(observation, Mapping)
        and observation.get("logical_trial_count") == 3
        and observation.get("http_attempt_count") == 3
        and observation.get("retry_count") == 0
        and observation.get("recorded_checks_passed") is True
        and isinstance(disposition, Mapping)
        and disposition.get("reason") == "protocol_gate_order_violation"
        and disposition.get("protocol_qualified") is False
        and disposition.get("automatic_rerun_authorized") is False
        and isinstance(invalidation_checkpoint, Mapping)
        and invalidation_checkpoint.get("index_path") == checkpoint_path
        and invalidation_checkpoint.get("index_sha256") == checkpoint_sha
        and result.get("schema_version") == "membind.h0.q1-a-result-summary.v2"
        and result.get("status") == "invalidated_protocol_gate_order"
        and result.get("stage_attempt_id") == INVALIDATED_ATTEMPT_ID
        and isinstance(result_observations, Mapping)
        and result_observations.get("logical_call_count") == 3
        and result_observations.get("http_attempt_count") == 3
        and result_observations.get("retry_count") == 0
    ):
        raise _fail("invalidated_observation_contract_mismatch")

    configuration = r2_q1["candidate_configuration"]
    return {
        "schema_version": "membind.h0.protocol-repair-decision.v1",
        "protocol_version": PROTOCOL_VERSION,
        "artifact_set_id": ARTIFACT_SET_ID,
        "execution_harness_revision": HARNESS_REVISION,
        "status": "approved_one_shot_replacement_not_live_authorized",
        "decision_result_blind": False,
        "prior_technical_outcome_observed": True,
        "repair_required_independent_of_output": True,
        "repair_reason": "protocol_gate_order_violation",
        "repair_counterfactual": (
            "the gate-before-configuration repair is mandatory for every output"
        ),
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "candidate_order": ["Q1", "Q2", "Q3"],
        "scientific_configuration_unchanged": True,
        "candidate_spec_projection_sha256": canonical_json_sha256(configuration),
        "frozen_policy": {
            "candidate_id": "Q1",
            "calibration_question_id": "07741c45",
            "source_sequence": 0,
            "seed_policy": configuration.get("seed_policy"),
            "server_request_seed": 20260806,
            "trial_count": 3,
            "requested_max_tokens": configuration.get("requested_max_tokens"),
            "semantic_thresholds_changed": False,
            "request_policy_changed": False,
            "retry_policy_changed": False,
            "candidate_order_changed": False,
            "calibration_input_changed": False,
        },
        "prior_observation": {
            "logical_trial_count": 3,
            "http_attempt_count": 3,
            "retry_count": 0,
            "recorded_checks_passed": True,
            "statistically_independent_trials": False,
        },
        "prior_evidence": {
            "invalidation_path": invalidation_path,
            "invalidation_sha256": invalidation_sha,
            "result_path": result_path,
            "result_sha256": result_sha,
            "checkpoint_index_path": checkpoint_path,
            "checkpoint_index_sha256": checkpoint_sha,
        },
        "legacy_scientific_binding": {
            "manifest_index_path": LEGACY_INDEX_PATH,
            "manifest_index_sha256": legacy_index_sha,
            "candidate_manifest_path": legacy_q1_path,
            "candidate_manifest_sha256": legacy_q1_sha,
        },
        "repaired_execution_binding": {
            "manifest_index_path": R2_INDEX_PATH,
            "manifest_index_sha256": r2_index_sha,
            "manifest_verification_sha256": canonical_json_sha256(
                manifest_verification
            ),
        },
        "replacement": {
            "candidate_id": "Q1",
            "phase": "H0-A",
            "attempt_id": replacement_attempt_id,
            "whole_stage": True,
            "one_shot": True,
            "old_attempt_id": INVALIDATED_ATTEMPT_ID,
            "old_attempt_trials_reused": False,
            "live_authorized_by_this_artifact": False,
        },
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }


def write_h0_repair_decision(
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    replacement_attempt_id: str,
) -> dict[str, str]:
    """Write the sole content-addressed decision, without authorizing live work."""

    root_path = Path(root).resolve()
    decision = build_h0_repair_decision(
        root=root_path,
        manifest_verification=manifest_verification,
        replacement_attempt_id=replacement_attempt_id,
    )
    digest = canonical_json_sha256(decision)
    directory = root_path / DECISION_ROOT
    if directory.is_symlink():
        raise _fail("decision_directory_symlink_forbidden")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"q1_h0_a_gate_order_repair.{digest}.json"
    existing = list(directory.glob("*.json"))
    if existing and existing != [path]:
        raise _fail("one_shot_decision_already_exists")
    encoded = canonical_json_bytes(decision)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != encoded:
            raise _fail("existing_decision_mismatch")
    else:
        path.write_bytes(encoded)
    return {
        "decision_path": path.relative_to(root_path).as_posix(),
        "decision_sha256": sha256_file(path),
    }


def verify_h0_repair_decision(
    *,
    root: str | Path,
    decision_path: str,
    decision_sha256: str,
    manifest_verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the decision and return its exact safe checkpoint admission."""

    root_path = Path(root).resolve()
    decision, relative, digest = _read_bound_json(
        root_path, decision_path, decision_sha256, label="repair_decision"
    )
    replacement = decision.get("replacement")
    attempt_id = replacement.get("attempt_id") if isinstance(replacement, Mapping) else None
    expected = build_h0_repair_decision(
        root=root_path,
        manifest_verification=manifest_verification,
        replacement_attempt_id=attempt_id,
    )
    if decision != expected or digest != canonical_json_sha256(expected):
        raise _fail("repair_decision_not_reproducible")
    return {
        "schema_version": "membind.h0.repair-admission.v1",
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": "Q1",
        "phase": "H0-A",
        "decision_path": relative,
        "decision_sha256": digest,
        "decision_result_blind": False,
        "one_shot_replacement": True,
        "replacement_attempt_id": attempt_id,
        "invalidated_stage_attempt_id": INVALIDATED_ATTEMPT_ID,
        "invalidated_checkpoint_index_sha256": INVALIDATED_CHECKPOINT_SHA256,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "candidate_spec_projection_sha256": expected[
            "candidate_spec_projection_sha256"
        ],
        "repaired_manifest_index_sha256": expected["repaired_execution_binding"][
            "manifest_index_sha256"
        ],
        "secrets_persisted": False,
    }


def _h0_b_exact_mapping(value: Any, expected: Mapping[str, Any], label: str) -> None:
    if not isinstance(value, Mapping) or dict(value) != dict(expected):
        raise _fail(f"h0_b_{label}_mismatch")


def _h0_b_validate_index(
    root: Path,
    *,
    path: str,
    digest: str,
    artifact_set_id: str,
    harness_revision: int,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    index, relative, actual_digest = _read_bound_json(
        root, path, digest, label=f"h0_b_{label}_index"
    )
    if not (
        relative == path
        and actual_digest == digest
        and index.get("schema_version") == "membind.h0.offline-artifacts.v2"
        and index.get("protocol_version") == PROTOCOL_VERSION
        and index.get("artifact_set_id") == artifact_set_id
        and index.get("execution_harness_revision") == harness_revision
        and index.get("status") == "offline_resolved_not_live_authorized"
        and index.get("live_h0_candidate_authorized") is False
        and index.get("source_specs_immutable") is True
        and index.get("unresolved_fields") == []
        and index.get("secrets_persisted") is False
    ):
        raise _fail(f"h0_b_{label}_index_contract_mismatch")
    resolved = index.get("resolved_manifests")
    q1_reference = resolved.get("Q1") if isinstance(resolved, Mapping) else None
    if not isinstance(q1_reference, Mapping) or set(q1_reference) != {
        "path",
        "sha256",
    }:
        raise _fail(f"h0_b_{label}_q1_reference_mismatch")
    q1_digest = _sha(q1_reference.get("sha256"), f"h0_b_{label}_q1")
    q1_path = q1_reference.get("path")
    prefix = f"artifacts/h0_manifest_sets/{artifact_set_id}/resolved_candidates/Q1."
    if (
        not isinstance(q1_path, str)
        or not q1_path.startswith(prefix)
        or not q1_path.endswith(f".{q1_digest}.json")
    ):
        raise _fail(f"h0_b_{label}_q1_path_not_content_addressed")
    q1, _, _ = _read_bound_json(
        root, q1_path, q1_digest, label=f"h0_b_{label}_q1"
    )
    if not (
        q1.get("schema_version") == "membind.h0.resolved-candidate.v1"
        and q1.get("protocol_version") == PROTOCOL_VERSION
        and q1.get("status") == "offline_resolved_not_live_authorized"
        and q1.get("candidate_id") == "Q1"
        and q1.get("live_eligible") is False
        and isinstance(q1.get("candidate_configuration"), Mapping)
        and isinstance(q1.get("source_delta_spec"), Mapping)
    ):
        raise _fail(f"h0_b_{label}_q1_contract_mismatch")
    return index, q1


def _h0_b_validate_r3(
    root: Path, manifest_verification: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    expected_fields = {
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
    if not isinstance(manifest_verification, Mapping) or set(
        manifest_verification
    ) != expected_fields:
        raise _fail("h0_b_r3_manifest_verification_fields_mismatch")
    exact = (
        manifest_verification.get("schema_version")
        == "membind.h0.offline-artifact-verification.v3"
        and manifest_verification.get("protocol_version") == PROTOCOL_VERSION
        and manifest_verification.get("artifact_set_id")
        == H0_B_R3_ARTIFACT_SET_ID
        and manifest_verification.get("execution_harness_revision")
        == H0_B_R3_HARNESS_REVISION
        and manifest_verification.get("status")
        == "verified_offline_not_live_authorized"
        and manifest_verification.get("index_path") == H0_B_R3_INDEX_PATH
        and manifest_verification.get("generated_json_file_count") == 11
        and manifest_verification.get("binding_count") == 10
        and manifest_verification.get("resolved_wrapper_count") == 4
        and manifest_verification.get("source_spec_count") == 4
        and manifest_verification.get("execution_source_count") == 32
        and manifest_verification.get("secret_scan_passed") is True
        and manifest_verification.get("live_eligible") is False
    )
    if not exact:
        raise _fail("h0_b_r3_manifest_verification_mismatch")
    index_digest = _sha(
        manifest_verification.get("index_sha256"), "h0_b_r3_index"
    )
    _, q1 = _h0_b_validate_index(
        root,
        path=H0_B_R3_INDEX_PATH,
        digest=index_digest,
        artifact_set_id=H0_B_R3_ARTIFACT_SET_ID,
        harness_revision=H0_B_R3_HARNESS_REVISION,
        label="r3",
    )
    return q1, index_digest


def _h0_b_validate_old_evidence(root: Path) -> dict[str, Any]:
    _, r2_q1 = _h0_b_validate_index(
        root,
        path=H0_B_R2_INDEX_PATH,
        digest=H0_B_R2_INDEX_SHA256,
        artifact_set_id=H0_B_R2_ARTIFACT_SET_ID,
        harness_revision=2,
        label="r2",
    )
    checkpoint, checkpoint_path, checkpoint_digest = _read_bound_json(
        root,
        H0_B_INVALIDATED_CHECKPOINT_PATH,
        H0_B_INVALIDATED_CHECKPOINT_SHA256,
        label="h0_b_invalidated_checkpoint",
    )
    checkpoint_exact = (
        checkpoint_path == H0_B_INVALIDATED_CHECKPOINT_PATH
        and checkpoint_digest == H0_B_INVALIDATED_CHECKPOINT_SHA256
        and checkpoint.get("schema_version") == "membind.h0.checkpoint-index.v1"
        and checkpoint.get("protocol_version") == PROTOCOL_VERSION
        and checkpoint.get("stage_attempt_id") == H0_B_INVALIDATED_ATTEMPT_ID
        and checkpoint.get("candidate_id") == "Q1"
        and checkpoint.get("phase") == "H0-B"
        and checkpoint.get("status") == "candidate_failed"
        and checkpoint.get("failure_code") == "manifest_contract_failure"
        and checkpoint.get("candidate_advance_allowed") is False
        and checkpoint.get("partial_qualification_reusable") is False
        and checkpoint.get("requires_whole_stage_rerun") is False
        and checkpoint.get("protocol_repair_replacement") is False
        and checkpoint.get("prior_matching_attempt_count") == 0
        and checkpoint.get("secrets_persisted") is False
        and checkpoint.get("raw_prompts_persisted") is False
        and checkpoint.get("raw_responses_persisted") is False
    )
    segments = checkpoint.get("segments")
    if not checkpoint_exact or not isinstance(segments, list) or len(segments) != 10:
        raise _fail("h0_b_invalidated_checkpoint_contract_mismatch")
    readiness = [
        item
        for item in segments
        if isinstance(item, Mapping)
        and item.get("segment_kind") == "stage_readiness_result"
        and item.get("segment_id") == "ready"
    ]
    failures = [
        item
        for item in segments
        if isinstance(item, Mapping)
        and item.get("segment_kind") == "candidate_failure"
        and item.get("segment_id") == "manifest_contract_failure"
    ]
    if len(readiness) != 1 or len(failures) != 1 or failures[0] is not segments[-1]:
        raise _fail("h0_b_checkpoint_terminal_order_mismatch")

    failure_reference = failures[0]
    failure_relative = failure_reference.get("artifact_path")
    if not isinstance(failure_relative, str) or not failure_relative.startswith(
        f"h0/checkpoints/{H0_B_INVALIDATED_ATTEMPT_ID}/"
    ):
        raise _fail("h0_b_failure_segment_path_mismatch")
    failure_segment, _, _ = _read_bound_json(
        root,
        f"artifacts/h0_runs/{failure_relative}",
        failure_reference.get("artifact_sha256"),
        label="h0_b_failure_segment",
    )
    payload = failure_segment.get("payload")
    ledger = payload.get("attempt_ledger") if isinstance(payload, Mapping) else None
    runtime = payload.get("runtime_evidence") if isinstance(payload, Mapping) else None
    failure_exact = (
        failure_segment.get("schema_version")
        == "membind.h0.checkpoint-segment.v1"
        and failure_segment.get("protocol_version") == PROTOCOL_VERSION
        and failure_segment.get("stage_attempt_id") == H0_B_INVALIDATED_ATTEMPT_ID
        and failure_segment.get("segment_kind") == "candidate_failure"
        and failure_segment.get("segment_id") == "manifest_contract_failure"
        and isinstance(payload, Mapping)
        and payload.get("failure_code") == "manifest_contract_failure"
        and payload.get("candidate_advance_allowed") is False
        and isinstance(ledger, Mapping)
        and ledger.get("logical_trials") == []
        and ledger.get("http_attempts") == []
        and isinstance(runtime, Mapping)
        and runtime.get("histories") == []
        and runtime.get("fresh_graph_count") == 0
        and runtime.get("closed_graph_count") == 0
        and runtime.get("embedding_workload_request_count") == 0
        and runtime.get("cross_encoder_rank_call_count") == 0
        and failure_segment.get("secrets_persisted") is False
        and failure_segment.get("raw_prompts_persisted") is False
        and failure_segment.get("raw_responses_persisted") is False
    )
    if not failure_exact:
        raise _fail("h0_b_failure_segment_not_zero_workload")

    report, report_path, report_digest = _read_bound_json(
        root,
        H0_B_FAILURE_REPORT_PATH,
        H0_B_FAILURE_REPORT_SHA256,
        label="h0_b_failure_report",
    )
    attempt = report.get("attempt")
    classification = report.get("classification")
    observed = report.get("observed_live_evidence")
    diagnosis = report.get("offline_diagnosis")
    probe = (
        diagnosis.get("real_graphiti_contract_probe")
        if isinstance(diagnosis, Mapping)
        else None
    )
    selection = (
        diagnosis.get("h0_b_workload_selection")
        if isinstance(diagnosis, Mapping)
        else None
    )
    recovery = report.get("recommended_recovery")
    report_exact = (
        report_path == H0_B_FAILURE_REPORT_PATH
        and report_digest == H0_B_FAILURE_REPORT_SHA256
        and report.get("schema_version") == "membind.h0.preworkload-failure-report.v1"
        and report.get("protocol_version") == PROTOCOL_VERSION
        and isinstance(attempt, Mapping)
        and attempt.get("candidate_id") == "Q1"
        and attempt.get("phase") == "H0-B"
        and attempt.get("stage_attempt_id") == H0_B_INVALIDATED_ATTEMPT_ID
        and attempt.get("checkpoint_index_path") == H0_B_INVALIDATED_CHECKPOINT_PATH
        and attempt.get("checkpoint_index_sha256")
        == H0_B_INVALIDATED_CHECKPOINT_SHA256
        and attempt.get("status") == "candidate_failed"
        and attempt.get("failure_code") == "manifest_contract_failure"
        and isinstance(classification, Mapping)
        and classification.get("failure_origin") == "execution_harness_compatibility"
        and classification.get("failed_boundary") == "first_real_graphiti_construction"
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
        and probe.get("root_exception_type") == "ValidationError"
        and set(probe.get("violations") or [])
        == {
            "H0EmbeddingAdapter is not an EmbedderClient instance",
            "H0ForbiddenCrossEncoder is not a CrossEncoderClient instance",
        }
        and isinstance(selection, Mapping)
        and selection.get("status") == "pass"
        and selection.get("history_count") == 1
        and selection.get("source_count") == 49
        and isinstance(recovery, Mapping)
        and recovery.get("advance_allowed") is False
        and recovery.get("rerun_current_attempt_allowed") is False
        and recovery.get("current_live_grant_requires_fail_closed_revocation") is True
        and report.get("secrets_persisted") is False
        and report.get("raw_prompts_persisted") is False
        and report.get("raw_responses_persisted") is False
    )
    if not report_exact:
        raise _fail("h0_b_failure_report_contract_mismatch")
    return r2_q1


def _h0_b_assert_sanitized(value: Any, *, location: str = "decision") -> None:
    forbidden_keys = {
        "api_key",
        "authorization",
        "credentials",
        "env_dump",
        "environment_dump",
        "messages",
        "raw_prompt",
        "raw_response",
        "secret",
    }
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).casefold().replace("-", "_")
            if key in forbidden_keys:
                raise _fail(f"h0_b_unsafe_field_at_{location}")
            _h0_b_assert_sanitized(child, location=f"{location}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _h0_b_assert_sanitized(child, location=f"{location}[{index}]")
    elif isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or ".env" in lowered or "gpt55_temporary" in lowered:
            raise _fail(f"h0_b_unsafe_value_at_{location}")


def build_h0_b_harness_repair_decision(
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    replacement_attempt_id: str,
) -> dict[str, Any]:
    """Build the exact non-blind Q1/H0-B harness repair decision offline."""

    if replacement_attempt_id != H0_B_REPLACEMENT_ATTEMPT_ID:
        raise _fail("h0_b_replacement_attempt_id_mismatch")
    root_path = Path(root).resolve()
    r2_q1 = _h0_b_validate_old_evidence(root_path)
    r3_q1, r3_index_digest = _h0_b_validate_r3(
        root_path, manifest_verification
    )
    if not (
        r2_q1.get("candidate_configuration")
        == r3_q1.get("candidate_configuration")
        and r2_q1.get("source_delta_spec") == r3_q1.get("source_delta_spec")
    ):
        raise _fail("h0_b_scientific_configuration_changed")

    decision = {
        "schema_version": "membind.h0.harness-compatibility-repair-decision.v1",
        "protocol_version": PROTOCOL_VERSION,
        "status": "approved_one_shot_whole_stage_replacement_not_live_authorized",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_result_blind": False,
        "prior_model_workload_output_observed": False,
        "repair_required_independent_of_model_output": True,
        "repair_reason": "preworkload_harness_compatibility_failure",
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "invalidated_attempt": {
            "stage_attempt_id": H0_B_INVALIDATED_ATTEMPT_ID,
            "checkpoint_index_path": H0_B_INVALIDATED_CHECKPOINT_PATH,
            "checkpoint_index_sha256": H0_B_INVALIDATED_CHECKPOINT_SHA256,
            "failure_report_path": H0_B_FAILURE_REPORT_PATH,
            "failure_report_sha256": H0_B_FAILURE_REPORT_SHA256,
            "logical_trial_count": 0,
            "http_attempt_count": 0,
            "source_checkpoint_count": 0,
            "fresh_graph_count": 0,
            "embedding_workload_request_count": 0,
        },
        "prior_execution_binding": {
            "artifact_set_id": H0_B_R2_ARTIFACT_SET_ID,
            "execution_harness_revision": 2,
            "manifest_index_path": H0_B_R2_INDEX_PATH,
            "manifest_index_sha256": H0_B_R2_INDEX_SHA256,
        },
        "repaired_execution_binding": {
            "artifact_set_id": H0_B_R3_ARTIFACT_SET_ID,
            "execution_harness_revision": H0_B_R3_HARNESS_REVISION,
            "manifest_index_path": H0_B_R3_INDEX_PATH,
            "manifest_index_sha256": r3_index_digest,
            "manifest_verification_sha256": canonical_json_sha256(
                manifest_verification
            ),
        },
        "replacement": {
            "attempt_id": H0_B_REPLACEMENT_ATTEMPT_ID,
            "candidate_id": "Q1",
            "phase": "H0-B",
            "whole_stage": True,
            "one_shot": True,
            "old_attempt_id": H0_B_INVALIDATED_ATTEMPT_ID,
            "old_attempt_trials_reused": False,
            "live_authorized_by_this_artifact": False,
        },
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }
    _h0_b_assert_sanitized(decision)
    return decision


def _h0_b_fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_h0_b_harness_repair_decision(
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    replacement_attempt_id: str,
) -> dict[str, str]:
    """Atomically publish the exact immutable H0-B decision at its fixed path."""

    root_path = Path(root).resolve()
    decision = build_h0_b_harness_repair_decision(
        root=root_path,
        manifest_verification=manifest_verification,
        replacement_attempt_id=replacement_attempt_id,
    )
    encoded = canonical_json_bytes(decision)
    target = root_path / H0_B_DECISION_PATH
    cursor = root_path
    for part in Path(H0_B_DECISION_PATH).parent.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail("h0_b_decision_directory_symlink_forbidden")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise _fail("h0_b_existing_decision_symlink_forbidden")
    if target.exists():
        if not target.is_file() or target.read_bytes() != encoded:
            raise _fail("h0_b_existing_decision_mismatch")
        return {
            "decision_path": H0_B_DECISION_PATH,
            "decision_sha256": sha256_file(target),
        }

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".h0-b-repair-", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != encoded:
                raise _fail("h0_b_existing_decision_mismatch") from None
        _h0_b_fsync_directory(target.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "decision_path": H0_B_DECISION_PATH,
        "decision_sha256": sha256_file(target),
    }


def verify_h0_b_harness_repair_decision(
    *,
    root: str | Path,
    decision_path: str,
    decision_sha256: str,
    manifest_verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the H0-B decision and return its exact 20-field admission."""

    if decision_path != H0_B_DECISION_PATH:
        raise _fail("h0_b_decision_path_mismatch")
    root_path = Path(root).resolve()
    decision, relative, digest = _read_bound_json(
        root_path,
        decision_path,
        decision_sha256,
        label="h0_b_repair_decision",
    )
    replacement = decision.get("replacement")
    replacement_attempt_id = (
        replacement.get("attempt_id") if isinstance(replacement, Mapping) else None
    )
    expected = build_h0_b_harness_repair_decision(
        root=root_path,
        manifest_verification=manifest_verification,
        replacement_attempt_id=replacement_attempt_id,
    )
    expected_digest = canonical_json_sha256(expected)
    if decision != expected or digest != expected_digest:
        raise _fail("h0_b_repair_decision_not_reproducible")
    repaired = expected["repaired_execution_binding"]
    admission = {
        "schema_version": "membind.h0.harness-repair-admission.v1",
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": relative,
        "decision_sha256": digest,
        "decision_result_blind": False,
        "prior_model_workload_output_observed": False,
        "repair_required_independent_of_model_output": True,
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "replacement_attempt_id": H0_B_REPLACEMENT_ATTEMPT_ID,
        "invalidated_stage_attempt_id": H0_B_INVALIDATED_ATTEMPT_ID,
        "invalidated_checkpoint_index_sha256": (
            H0_B_INVALIDATED_CHECKPOINT_SHA256
        ),
        "failure_report_sha256": H0_B_FAILURE_REPORT_SHA256,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "prior_manifest_index_sha256": H0_B_R2_INDEX_SHA256,
        "repaired_manifest_index_sha256": repaired["manifest_index_sha256"],
        "secrets_persisted": False,
    }
    _h0_b_assert_sanitized(admission, location="admission")
    return admission


def _h0_b_validate_r4(
    root: Path, manifest_verification: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    """Validate the exact R4 artifact graph used by the infrastructure rerun."""

    expected_fields = {
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
    exact = (
        isinstance(manifest_verification, Mapping)
        and set(manifest_verification) == expected_fields
        and manifest_verification.get("schema_version")
        == "membind.h0.offline-artifact-verification.v3"
        and manifest_verification.get("protocol_version") == PROTOCOL_VERSION
        and manifest_verification.get("artifact_set_id")
        == H0_B_R4_ARTIFACT_SET_ID
        and manifest_verification.get("execution_harness_revision")
        == H0_B_R4_HARNESS_REVISION
        and manifest_verification.get("status")
        == "verified_offline_not_live_authorized"
        and manifest_verification.get("index_path") == H0_B_R4_INDEX_PATH
        and manifest_verification.get("generated_json_file_count") == 11
        and manifest_verification.get("binding_count") == 10
        and manifest_verification.get("resolved_wrapper_count") == 4
        and manifest_verification.get("source_spec_count") == 4
        and manifest_verification.get("execution_source_count") == 32
        and manifest_verification.get("secret_scan_passed") is True
        and manifest_verification.get("live_eligible") is False
    )
    if not exact:
        raise _fail("h0_b_r4_manifest_verification_mismatch")
    digest = _sha(manifest_verification.get("index_sha256"), "h0_b_r4_index")
    _, q1 = _h0_b_validate_index(
        root,
        path=H0_B_R4_INDEX_PATH,
        digest=digest,
        artifact_set_id=H0_B_R4_ARTIFACT_SET_ID,
        harness_revision=H0_B_R4_HARNESS_REVISION,
        label="r4",
    )
    return q1, digest


def _h0_b_validate_infrastructure_interruption(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate that replacement-001 stopped before any candidate workload."""

    checkpoint, _, checkpoint_digest = _read_bound_json(
        root,
        H0_B_INTERRUPTED_CHECKPOINT_PATH,
        H0_B_INTERRUPTED_CHECKPOINT_SHA256,
        label="h0_b_interrupted_checkpoint",
    )
    segments = checkpoint.get("segments")
    exact = (
        checkpoint_digest == H0_B_INTERRUPTED_CHECKPOINT_SHA256
        and checkpoint.get("schema_version") == "membind.h0.checkpoint-index.v1"
        and checkpoint.get("protocol_version") == PROTOCOL_VERSION
        and checkpoint.get("stage_attempt_id") == H0_B_INTERRUPTED_ATTEMPT_ID
        and checkpoint.get("candidate_id") == "Q1"
        and checkpoint.get("phase") == "H0-B"
        and checkpoint.get("status") == "infrastructure_interrupted"
        and checkpoint.get("stop_reason") == "vllm_unreachable"
        and checkpoint.get("candidate_advance_allowed") is False
        and checkpoint.get("partial_qualification_reusable") is False
        and checkpoint.get("requires_whole_stage_rerun") is True
        and checkpoint.get("prior_matching_attempt_count") == 1
        and checkpoint.get("infrastructure_interrupted_attempt_count") == 0
        and isinstance(segments, list)
        and len(segments) == 3
        and checkpoint.get("secrets_persisted") is False
    )
    if not exact:
        raise _fail("h0_b_interrupted_checkpoint_contract_mismatch")
    kinds = [item.get("segment_kind") for item in segments if isinstance(item, Mapping)]
    if kinds != [
        "prior_phase_completion",
        "stage_readiness_check",
        "infrastructure_failure",
    ]:
        raise _fail("h0_b_interrupted_checkpoint_segment_order_mismatch")
    terminal = segments[-1]
    segment_path = terminal.get("artifact_path")
    if not isinstance(segment_path, str):
        raise _fail("h0_b_interrupted_terminal_segment_path_invalid")
    terminal_segment, _, _ = _read_bound_json(
        root,
        f"artifacts/h0_runs/{segment_path}",
        terminal.get("artifact_sha256"),
        label="h0_b_interrupted_terminal_segment",
    )
    payload = terminal_segment.get("payload")
    ledger = payload.get("attempt_ledger") if isinstance(payload, Mapping) else None
    runtime = payload.get("runtime_evidence") if isinstance(payload, Mapping) else None
    if not (
        terminal_segment.get("segment_kind") == "infrastructure_failure"
        and terminal_segment.get("segment_id") == "vllm-unreachable"
        and isinstance(payload, Mapping)
        and payload.get("failure_code") == "vllm_unreachable"
        and payload.get("failure_stage") == "stage_readiness"
        and payload.get("partial_qualification_reusable") is False
        and isinstance(ledger, Mapping)
        and ledger.get("logical_trials") == []
        and ledger.get("http_attempts") == []
        and isinstance(runtime, Mapping)
        and runtime.get("fresh_graph_count") == 0
        and runtime.get("histories") == []
        and terminal_segment.get("secrets_persisted") is False
    ):
        raise _fail("h0_b_interrupted_terminal_segment_mismatch")

    report, _, report_digest = _read_bound_json(
        root,
        H0_B_INTERRUPTION_REPORT_PATH,
        H0_B_INTERRUPTION_REPORT_SHA256,
        label="h0_b_infrastructure_report",
    )
    attempt = report.get("attempt")
    classification = report.get("classification")
    evidence = report.get("live_attempt_evidence")
    disposition = report.get("recovery_disposition")
    report_exact = (
        report_digest == H0_B_INTERRUPTION_REPORT_SHA256
        and report.get("schema_version")
        == "membind.h0.infrastructure-interruption-report.v1"
        and report.get("protocol_version") == PROTOCOL_VERSION
        and isinstance(attempt, Mapping)
        and attempt.get("stage_attempt_id") == H0_B_INTERRUPTED_ATTEMPT_ID
        and attempt.get("checkpoint_index_sha256")
        == H0_B_INTERRUPTED_CHECKPOINT_SHA256
        and attempt.get("status") == "infrastructure_interrupted"
        and attempt.get("stop_reason") == "vllm_unreachable"
        and isinstance(classification, Mapping)
        and classification.get("infrastructure_failure") is True
        and classification.get("candidate_model_failure_supported") is False
        and classification.get("candidate_qualification_interpretable") is False
        and classification.get("partial_qualification_reusable") is False
        and classification.get("requires_whole_stage_rerun") is True
        and isinstance(evidence, Mapping)
        and evidence.get("construction_version_probe_attempt_count") == 1
        and evidence.get("model_workload_http_attempt_count") == 0
        and evidence.get("logical_trial_count") == 0
        and evidence.get("embedding_workload_request_count") == 0
        and evidence.get("fresh_graph_count") == 0
        and evidence.get("history_count") == 0
        and evidence.get("source_checkpoint_count") == 0
        and evidence.get("workload_reached") is False
        and isinstance(disposition, Mapping)
        and disposition.get("current_attempt_resumable") is False
        and disposition.get("old_and_new_evidence_mergeable") is False
        and disposition.get("next_attempt_requires_new_stage_attempt_id") is True
        and report.get("secrets_persisted") is False
    )
    if not report_exact:
        raise _fail("h0_b_infrastructure_report_contract_mismatch")
    return checkpoint, report


def build_h0_b_infrastructure_rerun_decision(
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    replacement_attempt_id: str,
) -> dict[str, Any]:
    """Build the disclosed one-shot decision after a pre-workload infra stop."""

    if replacement_attempt_id != H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID:
        raise _fail("h0_b_infrastructure_rerun_attempt_id_mismatch")
    root_path = Path(root).resolve()
    _, r3_q1 = _h0_b_validate_index(
        root_path,
        path=H0_B_R3_INDEX_PATH,
        digest=H0_B_R3_INDEX_SHA256,
        artifact_set_id=H0_B_R3_ARTIFACT_SET_ID,
        harness_revision=H0_B_R3_HARNESS_REVISION,
        label="infrastructure_prior_r3",
    )
    r4_q1, r4_index_digest = _h0_b_validate_r4(
        root_path, manifest_verification
    )
    if not (
        r3_q1.get("candidate_configuration")
        == r4_q1.get("candidate_configuration")
        and r3_q1.get("source_delta_spec") == r4_q1.get("source_delta_spec")
    ):
        raise _fail("h0_b_infrastructure_scientific_configuration_changed")
    checkpoint, report = _h0_b_validate_infrastructure_interruption(root_path)
    repair = checkpoint.get("repair_admission")
    if not isinstance(repair, Mapping):
        raise _fail("h0_b_interrupted_repair_admission_missing")
    repair_decision, _, repair_decision_digest = _read_bound_json(
        root_path,
        repair.get("decision_path"),
        repair.get("decision_sha256"),
        label="h0_b_prior_harness_repair_decision",
    )
    if not (
        repair.get("decision_path") == H0_B_DECISION_PATH
        and repair.get("replacement_attempt_id") == H0_B_INTERRUPTED_ATTEMPT_ID
        and repair.get("repaired_manifest_index_sha256") == H0_B_R3_INDEX_SHA256
        and repair_decision.get("schema_version")
        == "membind.h0.harness-compatibility-repair-decision.v1"
        and repair_decision_digest == repair.get("decision_sha256")
    ):
        raise _fail("h0_b_prior_harness_repair_binding_mismatch")
    evidence = report["live_attempt_evidence"]
    decision = {
        "schema_version": "membind.h0.infrastructure-rerun-decision.v1",
        "protocol_version": PROTOCOL_VERSION,
        "status": "approved_one_shot_whole_stage_rerun_not_live_authorized",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_reason": "construction_vllm_unreachable_before_model_workload",
        "operator_service_restore_confirmed": True,
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "resume_interrupted_attempt_allowed": False,
        "prior_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "interrupted_attempt": {
            "stage_attempt_id": H0_B_INTERRUPTED_ATTEMPT_ID,
            "checkpoint_index_path": H0_B_INTERRUPTED_CHECKPOINT_PATH,
            "checkpoint_index_sha256": H0_B_INTERRUPTED_CHECKPOINT_SHA256,
            "interruption_report_path": H0_B_INTERRUPTION_REPORT_PATH,
            "interruption_report_sha256": H0_B_INTERRUPTION_REPORT_SHA256,
            "stop_reason": "vllm_unreachable",
            "construction_version_probe_attempt_count": evidence[
                "construction_version_probe_attempt_count"
            ],
            "logical_trial_count": 0,
            "model_workload_http_attempt_count": 0,
            "embedding_workload_request_count": 0,
            "history_count": 0,
            "source_checkpoint_count": 0,
            "fresh_graph_count": 0,
        },
        "prior_harness_repair": {
            "decision_path": H0_B_DECISION_PATH,
            "decision_sha256": repair_decision_digest,
            "admission_sha256": canonical_json_sha256(repair),
        },
        "prior_execution_binding": {
            "artifact_set_id": H0_B_R3_ARTIFACT_SET_ID,
            "execution_harness_revision": H0_B_R3_HARNESS_REVISION,
            "manifest_index_path": H0_B_R3_INDEX_PATH,
            "manifest_index_sha256": H0_B_R3_INDEX_SHA256,
        },
        "recovered_execution_binding": {
            "artifact_set_id": H0_B_R4_ARTIFACT_SET_ID,
            "execution_harness_revision": H0_B_R4_HARNESS_REVISION,
            "manifest_index_path": H0_B_R4_INDEX_PATH,
            "manifest_index_sha256": r4_index_digest,
            "manifest_verification_sha256": canonical_json_sha256(
                manifest_verification
            ),
        },
        "replacement": {
            "attempt_id": H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID,
            "interrupted_attempt_id": H0_B_INTERRUPTED_ATTEMPT_ID,
            "candidate_id": "Q1",
            "phase": "H0-B",
            "whole_stage": True,
            "one_shot": True,
            "old_attempt_evidence_reused": False,
            "live_authorized_by_this_artifact": False,
        },
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }
    _h0_b_assert_sanitized(decision, location="infrastructure_rerun_decision")
    return decision


def write_h0_b_infrastructure_rerun_decision(
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    replacement_attempt_id: str,
) -> dict[str, str]:
    """Atomically publish the immutable infrastructure-rerun decision."""

    root_path = Path(root).resolve()
    decision = build_h0_b_infrastructure_rerun_decision(
        root=root_path,
        manifest_verification=manifest_verification,
        replacement_attempt_id=replacement_attempt_id,
    )
    encoded = canonical_json_bytes(decision)
    target = root_path / H0_B_INFRASTRUCTURE_RERUN_DECISION_PATH
    cursor = root_path
    for part in Path(H0_B_INFRASTRUCTURE_RERUN_DECISION_PATH).parent.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail("h0_b_infrastructure_decision_directory_symlink_forbidden")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise _fail("h0_b_existing_infrastructure_decision_symlink_forbidden")
    if target.exists():
        if not target.is_file() or target.read_bytes() != encoded:
            raise _fail("h0_b_existing_infrastructure_decision_mismatch")
        return {
            "decision_path": H0_B_INFRASTRUCTURE_RERUN_DECISION_PATH,
            "decision_sha256": sha256_file(target),
        }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".h0-b-infrastructure-rerun-", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != encoded:
                raise _fail("h0_b_existing_infrastructure_decision_mismatch") from None
        _h0_b_fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "decision_path": H0_B_INFRASTRUCTURE_RERUN_DECISION_PATH,
        "decision_sha256": sha256_file(target),
    }


def verify_h0_b_infrastructure_rerun_decision(
    *,
    root: str | Path,
    decision_path: str,
    decision_sha256: str,
    manifest_verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the decision and return its exact runtime admission envelope."""

    if decision_path != H0_B_INFRASTRUCTURE_RERUN_DECISION_PATH:
        raise _fail("h0_b_infrastructure_decision_path_mismatch")
    root_path = Path(root).resolve()
    decision, relative, digest = _read_bound_json(
        root_path,
        decision_path,
        decision_sha256,
        label="h0_b_infrastructure_rerun_decision",
    )
    expected = build_h0_b_infrastructure_rerun_decision(
        root=root_path,
        manifest_verification=manifest_verification,
        replacement_attempt_id=H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID,
    )
    if decision != expected or digest != canonical_json_sha256(expected):
        raise _fail("h0_b_infrastructure_decision_not_reproducible")
    admission = {
        "schema_version": "membind.h0.infrastructure-rerun-admission.v1",
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": relative,
        "decision_sha256": digest,
        "interrupted_stage_attempt_id": H0_B_INTERRUPTED_ATTEMPT_ID,
        "interrupted_checkpoint_index_sha256": (
            H0_B_INTERRUPTED_CHECKPOINT_SHA256
        ),
        "interrupted_stop_reason": "vllm_unreachable",
        "prior_harness_repair_admission_sha256": expected[
            "prior_harness_repair"
        ]["admission_sha256"],
        "replacement_attempt_id": H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID,
        "one_shot_whole_stage_replacement": True,
        "resume_interrupted_attempt_allowed": False,
        "prior_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "scientific_configuration_unchanged": True,
        "prior_manifest_index_sha256": H0_B_R3_INDEX_SHA256,
        "recovered_manifest_index_sha256": expected[
            "recovered_execution_binding"
        ]["manifest_index_sha256"],
        "secrets_persisted": False,
    }
    _h0_b_assert_sanitized(admission, location="infrastructure_rerun_admission")
    return admission


def _post_workload_execution_bindings(
    root: Path,
    manifest_verification: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Validate the immutable R4 input and isolated R5 scientific projection."""

    if not isinstance(manifest_verification, Mapping):
        raise _fail("h0_b_post_workload_manifest_verification_not_object")
    r5_sha = _sha(
        manifest_verification.get("index_sha256"),
        "h0_b_post_workload_r5_index",
    )
    if not (
        manifest_verification.get("artifact_set_id") == H0_B_R5_ARTIFACT_SET_ID
        and manifest_verification.get("execution_harness_revision")
        == H0_B_R5_HARNESS_REVISION
        and manifest_verification.get("index_path") == H0_B_R5_INDEX_PATH
    ):
        raise _fail("h0_b_post_workload_r5_verification_mismatch")
    r4, _, _ = _read_bound_json(
        root,
        H0_B_R4_INDEX_PATH,
        H0_B_R4_INDEX_SHA256,
        label="h0_b_post_workload_r4_index",
    )
    r5, _, _ = _read_bound_json(
        root,
        H0_B_R5_INDEX_PATH,
        r5_sha,
        label="h0_b_post_workload_r5_index",
    )
    if not (
        r4.get("artifact_set_id") == H0_B_R4_ARTIFACT_SET_ID
        and r4.get("execution_harness_revision") == H0_B_R4_HARNESS_REVISION
        and r5.get("artifact_set_id") == H0_B_R5_ARTIFACT_SET_ID
        and r5.get("execution_harness_revision") == H0_B_R5_HARNESS_REVISION
        and r5.get("status") == "offline_resolved_not_live_authorized"
        and r5.get("live_h0_candidate_authorized") is False
        and r5.get("secrets_persisted") is False
    ):
        raise _fail("h0_b_post_workload_execution_index_mismatch")

    def wrapper(index: Mapping[str, Any], name: str, label: str) -> dict[str, Any]:
        resolved = index.get("resolved_manifests")
        reference = resolved.get(name) if isinstance(resolved, Mapping) else None
        if not isinstance(reference, Mapping):
            raise _fail(f"{label}_reference_missing")
        value, _, _ = _read_bound_json(
            root,
            reference.get("path"),
            reference.get("sha256"),
            label=label,
        )
        return value

    r4_candidate = wrapper(r4, "Q1", "h0_b_post_workload_r4_q1")
    r5_candidate = wrapper(r5, "Q1", "h0_b_post_workload_r5_q1")
    r4_shared = wrapper(r4, "shared_base", "h0_b_post_workload_r4_shared")
    r5_shared = wrapper(r5, "shared_base", "h0_b_post_workload_r5_shared")
    old_science = {
        "candidate_configuration": r4_candidate.get("candidate_configuration"),
        "source_delta_spec": r4_candidate.get("source_delta_spec"),
        "source_base": r4_shared.get("source_base"),
        "source_base_spec": r4_shared.get("source_base_spec"),
    }
    new_science = {
        "candidate_configuration": r5_candidate.get("candidate_configuration"),
        "source_delta_spec": r5_candidate.get("source_delta_spec"),
        "source_base": r5_shared.get("source_base"),
        "source_base_spec": r5_shared.get("source_base_spec"),
    }
    if old_science != new_science:
        raise _fail("h0_b_post_workload_scientific_configuration_changed")
    return r4, r5, r5_sha


def build_h0_b_post_workload_harness_repair_decision(
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    replacement_attempt_id: str = H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID,
) -> dict[str, Any]:
    """Build the transparent, non-blind R5 whole-stage replacement decision."""

    if replacement_attempt_id != H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID:
        raise _fail("h0_b_post_workload_replacement_attempt_mismatch")
    root_path = Path(root).resolve()
    _, _, r5_sha = _post_workload_execution_bindings(
        root_path, manifest_verification
    )
    checkpoint, _, _ = _read_bound_json(
        root_path,
        H0_B_POST_WORKLOAD_CHECKPOINT_PATH,
        H0_B_POST_WORKLOAD_CHECKPOINT_SHA256,
        label="h0_b_post_workload_checkpoint",
    )
    repair = checkpoint.get("repair_admission")
    infrastructure = checkpoint.get("infrastructure_rerun_admission")
    if not isinstance(repair, Mapping) or not isinstance(infrastructure, Mapping):
        raise _fail("h0_b_post_workload_prior_repair_chain_missing")
    from h0_harness_recovery import classify_h0_b_post_workload_harness_failure

    invalidated = classify_h0_b_post_workload_harness_failure(
        root=root_path,
        stage_attempt_id=H0_B_POST_WORKLOAD_FAILED_ATTEMPT_ID,
        checkpoint_index_path=H0_B_POST_WORKLOAD_CHECKPOINT_PATH,
        checkpoint_index_sha256=H0_B_POST_WORKLOAD_CHECKPOINT_SHA256,
        failure_segment_path=H0_B_POST_WORKLOAD_FAILURE_SEGMENT_PATH,
        failure_segment_sha256=H0_B_POST_WORKLOAD_FAILURE_SEGMENT_SHA256,
        source_checkpoint_path=H0_B_POST_WORKLOAD_SOURCE_CHECKPOINT_PATH,
        source_checkpoint_sha256=H0_B_POST_WORKLOAD_SOURCE_CHECKPOINT_SHA256,
        live_log_path=H0_B_POST_WORKLOAD_LIVE_LOG_PATH,
        live_log_sha256=H0_B_POST_WORKLOAD_LIVE_LOG_SHA256,
        offline_probe_path=H0_B_POST_WORKLOAD_OFFLINE_PROBE_PATH,
        offline_probe_sha256=H0_B_POST_WORKLOAD_OFFLINE_PROBE_SHA256,
    )
    decision = {
        "schema_version": "membind.h0.post-workload-harness-repair-decision.v1",
        "protocol_version": PROTOCOL_VERSION,
        "status": "approved_one_shot_whole_stage_replacement_not_live_authorized",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_result_blind": False,
        "prior_model_workload_output_observed": True,
        "repair_required_independent_of_model_response_content": True,
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "resume_failed_attempt_allowed": False,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "invalidated_attempt": invalidated,
        "prior_repair_chain": {
            "harness_repair_admission_sha256": canonical_json_sha256(repair),
            "infrastructure_rerun_admission_sha256": canonical_json_sha256(
                infrastructure
            ),
        },
        "prior_execution_binding": {
            "artifact_set_id": H0_B_R4_ARTIFACT_SET_ID,
            "execution_harness_revision": H0_B_R4_HARNESS_REVISION,
            "manifest_index_path": H0_B_R4_INDEX_PATH,
            "manifest_index_sha256": H0_B_R4_INDEX_SHA256,
        },
        "repaired_execution_binding": {
            "artifact_set_id": H0_B_R5_ARTIFACT_SET_ID,
            "execution_harness_revision": H0_B_R5_HARNESS_REVISION,
            "manifest_index_path": H0_B_R5_INDEX_PATH,
            "manifest_index_sha256": r5_sha,
        },
        "replacement": {
            "attempt_id": replacement_attempt_id,
            "invalidated_attempt_id": H0_B_POST_WORKLOAD_FAILED_ATTEMPT_ID,
            "candidate_id": "Q1",
            "phase": "H0-B",
            "whole_stage": True,
            "one_shot": True,
            "old_attempt_evidence_reused": False,
            "live_authorized_by_this_artifact": False,
        },
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }
    _h0_b_assert_sanitized(decision, location="post_workload_decision")
    return decision


def write_h0_b_post_workload_harness_repair_decision(
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    replacement_attempt_id: str = H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID,
) -> dict[str, str]:
    """Persist the canonical post-workload decision without overwriting drift."""

    root_path = Path(root).resolve()
    decision = build_h0_b_post_workload_harness_repair_decision(
        root=root_path,
        manifest_verification=manifest_verification,
        replacement_attempt_id=replacement_attempt_id,
    )
    encoded = canonical_json_bytes(decision)
    target = root_path / H0_B_POST_WORKLOAD_DECISION_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".h0-b-post-workload-", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != encoded:
                raise _fail("h0_b_existing_post_workload_decision_mismatch") from None
        _h0_b_fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "decision_path": H0_B_POST_WORKLOAD_DECISION_PATH,
        "decision_sha256": sha256_file(target),
    }


def verify_h0_b_post_workload_harness_repair_decision(
    *,
    root: str | Path,
    decision_path: str,
    decision_sha256: str,
    manifest_verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the exact decision and return a one-shot runtime admission."""

    if decision_path != H0_B_POST_WORKLOAD_DECISION_PATH:
        raise _fail("h0_b_post_workload_decision_path_mismatch")
    root_path = Path(root).resolve()
    decision, relative, digest = _read_bound_json(
        root_path,
        decision_path,
        decision_sha256,
        label="h0_b_post_workload_decision",
    )
    expected = build_h0_b_post_workload_harness_repair_decision(
        root=root_path,
        manifest_verification=manifest_verification,
        replacement_attempt_id=H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID,
    )
    if decision != expected or digest != canonical_json_sha256(expected):
        raise _fail("h0_b_post_workload_decision_not_reproducible")
    checkpoint, _, _ = _read_bound_json(
        root_path,
        H0_B_POST_WORKLOAD_CHECKPOINT_PATH,
        H0_B_POST_WORKLOAD_CHECKPOINT_SHA256,
        label="h0_b_post_workload_checkpoint",
    )
    repair = checkpoint.get("repair_admission")
    infrastructure = checkpoint.get("infrastructure_rerun_admission")
    if not isinstance(repair, Mapping) or not isinstance(infrastructure, Mapping):
        raise _fail("h0_b_post_workload_prior_repair_chain_missing")
    admission = {
        "schema_version": "membind.h0.post-workload-harness-repair-admission.v1",
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": relative,
        "decision_sha256": digest,
        "decision_result_blind": False,
        "prior_model_workload_output_observed": True,
        "repair_required_independent_of_model_response_content": True,
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "replacement_attempt_id": H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID,
        "invalidated_stage_attempt_id": H0_B_POST_WORKLOAD_FAILED_ATTEMPT_ID,
        "invalidated_checkpoint_index_sha256": H0_B_POST_WORKLOAD_CHECKPOINT_SHA256,
        "failure_segment_sha256": H0_B_POST_WORKLOAD_FAILURE_SEGMENT_SHA256,
        "source_checkpoint_sha256": H0_B_POST_WORKLOAD_SOURCE_CHECKPOINT_SHA256,
        "live_log_sha256": H0_B_POST_WORKLOAD_LIVE_LOG_SHA256,
        "offline_probe_sha256": H0_B_POST_WORKLOAD_OFFLINE_PROBE_SHA256,
        "prior_harness_repair_admission_sha256": canonical_json_sha256(repair),
        "prior_infrastructure_rerun_admission_sha256": canonical_json_sha256(
            infrastructure
        ),
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "resume_failed_attempt_allowed": False,
        "prior_manifest_index_sha256": H0_B_R4_INDEX_SHA256,
        "repaired_manifest_index_sha256": expected[
            "repaired_execution_binding"
        ]["manifest_index_sha256"],
        "secrets_persisted": False,
    }
    _h0_b_assert_sanitized(admission, location="post_workload_admission")
    return admission
