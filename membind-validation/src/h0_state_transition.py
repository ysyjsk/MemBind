"""Offline, fail-closed state binding for Protocol v1.3 H0 execution.

This module only reads local, content-addressed evidence.  It never loads
``.env`` and never opens model, embedding, database, or SSH connections.  The
offline evidence-binding step and the live Q1/H0-A authorization step are
intentionally separate operations.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from h0_live_preflight import load_authorized_h0_runtime_identity
from h0_runtime import (
    H0ManifestError,
    H0StateGateError,
    authorize_h0_live_entry,
    canonical_json_bytes,
    sha256_file,
)


PROTOCOL_VERSION = "current-validation-v1.3"
OFFLINE_STATUS = "h0_offline_verified_not_live_authorized"
OFFLINE_SCOPE = "h0_offline_verified_only"
Q1_H0_A_SCOPE = "h0_q1_a_live_only"
REVOKED_STATUS = "h0_live_authorization_revoked"
REVOKED_SCOPE = "h0_live_forbidden"
PROTOCOL_GATE_ORDER_VIOLATION = "protocol_gate_order_violation"
H0_ARTIFACT_SET_ID = "v1_3_harness_r2"
H0_EXECUTION_HARNESS_REVISION = 2
H0_ARTIFACT_SET_ROOT = "artifacts/h0_manifest_sets/v1_3_harness_r2"
H0_RESOLVED_MANIFEST_INDEX = (
    f"{H0_ARTIFACT_SET_ROOT}/resolved_manifest_index_v1_3_harness_r2.json"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_EVIDENCE_NAMES = (
    "latest_red",
    "latest_green",
    "latest_focused",
    "latest_full_regression",
)
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
_BINDING_FIELDS = {
    "resolved_manifest_index_path",
    "resolved_manifest_index_sha256",
    "resolved_candidate_manifest_path",
    "resolved_candidate_manifest_sha256",
    "resolved_shared_base_manifest_path",
    "resolved_shared_base_manifest_sha256",
}
_FORBIDDEN_PATH_PARTS = {".env", "gpt55_temporary"}
_FORBIDDEN_STATE_KEYS = {
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


class H0StateTransitionError(RuntimeError):
    """A sanitized state-binding failure that authorizes no live work."""


def _fail(reason: str) -> H0StateTransitionError:
    return H0StateTransitionError(f"H0 state transition denied: {reason}")


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label}_not_object")
    return value


def _canonical_relative_file(
    root: Path,
    relative_value: Any,
    digest_value: Any,
    *,
    label: str,
) -> tuple[Path, str, str]:
    if not isinstance(relative_value, str) or not relative_value:
        raise _fail(f"{label}_path_invalid")
    relative = Path(relative_value)
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(part in _FORBIDDEN_PATH_PARTS for part in relative.parts)
    ):
        raise _fail(f"{label}_path_noncanonical")
    digest = str(digest_value or "")
    if _SHA256_RE.fullmatch(digest) is None:
        raise _fail(f"{label}_sha256_invalid")

    root = root.resolve()
    unresolved = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_symlink_forbidden")
    resolved = unresolved.resolve()
    try:
        normalized = resolved.relative_to(root).as_posix()
    except ValueError:
        raise _fail(f"{label}_path_escape") from None
    if normalized != relative_value or not resolved.is_file():
        raise _fail(f"{label}_missing_or_noncanonical")
    if sha256_file(resolved) != digest:
        raise _fail(f"{label}_hash_mismatch")
    return resolved, relative_value, digest


def _read_canonical_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        encoded = path.read_bytes()
        value = json.loads(encoded.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_invalid_json") from None
    if not isinstance(value, dict) or encoded != canonical_json_bytes(value):
        raise _fail(f"{label}_not_canonical_json")
    return value


def _assert_safe_state_value(value: Any, *, location: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _FORBIDDEN_STATE_KEYS:
                raise _fail(f"unsafe_source_state_field_at_{location}")
            _assert_safe_state_value(child, location=f"{location}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_safe_state_value(child, location=f"{location}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or ".env" in lowered or "gpt55_temporary" in lowered:
            raise _fail(f"unsafe_source_state_value_at_{location}")


def _validate_offline_source(state: Mapping[str, Any]) -> None:
    _assert_safe_state_value(state, location="source_state")
    progress = state.get("stage_progress")
    valid_status_scope = (
        (
            state.get("status") == "h0_protocol_accepted_harness_not_implemented"
            and state.get("current_action_scope") == "h0_offline_tdd_and_harness_only"
        )
        or (
            state.get("status") == OFFLINE_STATUS
            and state.get("current_action_scope") == OFFLINE_SCOPE
        )
    )
    exact = (
        state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("current_stage") == "H0"
        and valid_status_scope
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_live_actions") == []
        and state.get("authorized_h0_candidate_id") is None
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == "forbidden"
        and state.get("live_h0_authorization") is None
    )
    if not exact:
        raise _fail("source_state_not_exact_offline_h0")


def _validate_manifest_verification(
    root: Path,
    value: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    if set(value) != _VERIFICATION_FIELDS:
        raise _fail("manifest_verification_fields_mismatch")
    exact = (
        value.get("schema_version")
        == "membind.h0.offline-artifact-verification.v2"
        and value.get("protocol_version") == PROTOCOL_VERSION
        and value.get("artifact_set_id") == H0_ARTIFACT_SET_ID
        and value.get("execution_harness_revision")
        == H0_EXECUTION_HARNESS_REVISION
        and value.get("status") == "verified_offline_not_live_authorized"
        and value.get("generated_json_file_count") == 11
        and value.get("binding_count") == 10
        and value.get("resolved_wrapper_count") == 4
        and value.get("source_spec_count") == 4
        and value.get("execution_source_count") == 31
        and value.get("secret_scan_passed") is True
        and value.get("live_eligible") is False
    )
    if not exact:
        raise _fail("manifest_verification_not_exact")
    index_path, index_relative, index_sha = _canonical_relative_file(
        root,
        value.get("index_path"),
        value.get("index_sha256"),
        label="manifest_index",
    )
    if index_relative != H0_RESOLVED_MANIFEST_INDEX:
        raise _fail("manifest_index_path_unexpected")
    index = _read_canonical_json(index_path, label="manifest_index")
    index_exact = (
        index.get("schema_version") == "membind.h0.offline-artifacts.v2"
        and index.get("protocol_version") == PROTOCOL_VERSION
        and index.get("artifact_set_id") == H0_ARTIFACT_SET_ID
        and index.get("execution_harness_revision")
        == H0_EXECUTION_HARNESS_REVISION
        and index.get("status") == "offline_resolved_not_live_authorized"
        and index.get("live_h0_candidate_authorized") is False
        and index.get("unresolved_fields") == []
        and index.get("source_specs_immutable") is True
        and index.get("secrets_persisted") is False
    )
    resolved = index.get("resolved_manifests")
    shared_artifacts = index.get("shared_artifacts")
    if not index_exact or not isinstance(resolved, Mapping) or set(resolved) != {
        "shared_base",
        "Q1",
        "Q2",
        "Q3",
    } or not isinstance(shared_artifacts, Mapping) or "execution_source_bundle" not in shared_artifacts:
        raise _fail("manifest_index_contract_mismatch")

    source_reference = _require_mapping(
        shared_artifacts.get("execution_source_bundle"),
        "execution_source_bundle_reference",
    )
    if set(source_reference) != {"path", "sha256"}:
        raise _fail("execution_source_bundle_reference_fields_mismatch")
    _, source_relative, source_digest = _canonical_relative_file(
        root,
        source_reference.get("path"),
        source_reference.get("sha256"),
        label="execution_source_bundle",
    )
    expected_source_path = (
        f"{H0_ARTIFACT_SET_ROOT}/manifests/"
        f"execution_source_bundle_v1_3.{source_digest}.json"
    )
    if source_relative != expected_source_path:
        raise _fail("execution_source_bundle_namespace_mismatch")

    references: dict[str, tuple[Path, str, str]] = {}
    for name in ("Q1", "shared_base"):
        reference = _require_mapping(resolved.get(name), f"manifest_{name}_reference")
        if set(reference) != {"path", "sha256"}:
            raise _fail(f"manifest_{name}_reference_fields_mismatch")
        references[name] = _canonical_relative_file(
            root,
            reference.get("path"),
            reference.get("sha256"),
            label=f"manifest_{name}",
        )
        if not references[name][1].startswith(
            f"{H0_ARTIFACT_SET_ROOT}/resolved_candidates/"
        ):
            raise _fail(f"manifest_{name}_namespace_mismatch")
    candidate = _read_canonical_json(references["Q1"][0], label="manifest_Q1")
    shared = _read_canonical_json(references["shared_base"][0], label="manifest_shared_base")
    if not (
        candidate.get("schema_version") == "membind.h0.resolved-candidate.v1"
        and candidate.get("protocol_version") == PROTOCOL_VERSION
        and candidate.get("status") == "offline_resolved_not_live_authorized"
        and candidate.get("live_eligible") is False
        and candidate.get("candidate_id") == "Q1"
        and shared.get("schema_version") == "membind.h0.resolved-shared-host-base.v1"
        and shared.get("protocol_version") == PROTOCOL_VERSION
        and shared.get("status") == "offline_resolved_not_live_authorized"
        and shared.get("live_eligible") is False
        and candidate.get("resolved_shared_base_sha256") == references["shared_base"][2]
    ):
        raise _fail("resolved_manifest_wrapper_mismatch")
    bindings = {
        "resolved_manifest_index_path": index_relative,
        "resolved_manifest_index_sha256": index_sha,
        "resolved_candidate_manifest_path": references["Q1"][1],
        "resolved_candidate_manifest_sha256": references["Q1"][2],
        "resolved_shared_base_manifest_path": references["shared_base"][1],
        "resolved_shared_base_manifest_sha256": references["shared_base"][2],
    }
    return bindings, deepcopy(dict(value))


def _validate_tdd_evidence(
    root: Path,
    evidence: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if set(evidence) != set(_EVIDENCE_NAMES):
        raise _fail("tdd_evidence_set_mismatch")
    validated: dict[str, dict[str, Any]] = {}
    for name in _EVIDENCE_NAMES:
        reference = _require_mapping(evidence.get(name), f"tdd_evidence_{name}")
        if set(reference) != {"path", "sha256", "test_count"}:
            raise _fail(f"tdd_evidence_{name}_fields_mismatch")
        count = reference.get("test_count")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise _fail(f"tdd_evidence_{name}_test_count_invalid")
        _, relative, digest = _canonical_relative_file(
            root,
            reference.get("path"),
            reference.get("sha256"),
            label=f"tdd_evidence_{name}",
        )
        validated[name] = {
            "path": relative,
            "sha256": digest,
            "test_count": count,
        }
    return validated


def build_h0_offline_bound_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Purely derive a live-forbidden state from verified local evidence."""

    source = _require_mapping(source_state, "source_state")
    _validate_offline_source(source)
    root_path = Path(root).resolve()
    bindings, verification = _validate_manifest_verification(
        root_path, _require_mapping(manifest_verification, "manifest_verification")
    )
    evidence = _validate_tdd_evidence(
        root_path, _require_mapping(tdd_evidence, "tdd_evidence")
    )
    state = deepcopy(dict(source))
    progress = deepcopy(dict(_require_mapping(state.get("stage_progress"), "stage_progress")))
    progress["h0_live_gate"] = "forbidden"
    progress["h0_offline_manifest_binding"] = "verified_not_live_authorized"
    state.update(
        {
            "current_stage": "H0",
            "status": OFFLINE_STATUS,
            "current_action_scope": OFFLINE_SCOPE,
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "h0_offline_live_prerequisites": {
                "schema_version": "membind.h0.offline-live-prerequisites.v1",
                "protocol_version": PROTOCOL_VERSION,
                "status": "verified_offline_not_live_authorized",
                "candidate_id": "Q1",
                "phase": "H0-A",
                "artifact_bindings": bindings,
                "manifest_verification": verification,
                "tdd_evidence": evidence,
                "live_transition_performed": False,
            },
        }
    )
    state.pop("live_h0_authorization", None)
    return state


def _validated_offline_prerequisites(
    source_state: Mapping[str, Any], root: Path
) -> tuple[dict[str, str], dict[str, Any]]:
    _validate_offline_source(source_state)
    prerequisites = _require_mapping(
        source_state.get("h0_offline_live_prerequisites"),
        "h0_offline_live_prerequisites",
    )
    required = {
        "schema_version",
        "protocol_version",
        "status",
        "candidate_id",
        "phase",
        "artifact_bindings",
        "manifest_verification",
        "tdd_evidence",
        "live_transition_performed",
    }
    exact = (
        set(prerequisites) == required
        and prerequisites.get("schema_version")
        == "membind.h0.offline-live-prerequisites.v1"
        and prerequisites.get("protocol_version") == PROTOCOL_VERSION
        and prerequisites.get("status") == "verified_offline_not_live_authorized"
        and prerequisites.get("candidate_id") == "Q1"
        and prerequisites.get("phase") == "H0-A"
        and prerequisites.get("live_transition_performed") is False
    )
    if not exact:
        raise _fail("offline_live_prerequisites_mismatch")
    bindings, verification = _validate_manifest_verification(
        root,
        _require_mapping(prerequisites.get("manifest_verification"), "manifest_verification"),
    )
    recorded_bindings = _require_mapping(
        prerequisites.get("artifact_bindings"), "artifact_bindings"
    )
    if set(recorded_bindings) != _BINDING_FIELDS or dict(recorded_bindings) != bindings:
        raise _fail("recorded_artifact_bindings_mismatch")
    _validate_tdd_evidence(
        root, _require_mapping(prerequisites.get("tdd_evidence"), "tdd_evidence")
    )
    return bindings, verification


def build_q1_h0_a_live_state(
    source_state: Mapping[str, Any], *, root: str | Path
) -> dict[str, Any]:
    """Derive the one exact Q1/H0-A live state without writing it."""

    source = _require_mapping(source_state, "source_state")
    bindings, _ = _validated_offline_prerequisites(source, Path(root).resolve())
    state = deepcopy(dict(source))
    progress = deepcopy(dict(_require_mapping(state.get("stage_progress"), "stage_progress")))
    progress["h0_live_gate"] = Q1_H0_A_SCOPE
    state.update(
        {
            "current_stage": "H0",
            "status": Q1_H0_A_SCOPE,
            "current_action_scope": Q1_H0_A_SCOPE,
            "stage_progress": progress,
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-A",
                **bindings,
            },
        }
    )
    state["h0_offline_live_prerequisites"]["live_transition_performed"] = True
    return state


def _validate_live_revoke_source(
    state: Mapping[str, Any], *, candidate_id: str, phase: str
) -> None:
    """Require the exact live grant that the revocation names."""

    _assert_safe_state_value(state, location="source_state")
    if candidate_id not in {"Q1", "Q2", "Q3"} or phase not in {
        "H0-A",
        "H0-B",
        "H0-C",
    }:
        raise _fail("revoke_candidate_or_phase_invalid")
    phase_suffix = phase.removeprefix("H0-").casefold()
    expected_scope = f"h0_{candidate_id.casefold()}_{phase_suffix}_live_only"
    progress = state.get("stage_progress")
    authorization = state.get("live_h0_authorization")
    exact = (
        state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("current_stage") == "H0"
        and state.get("status") == expected_scope
        and state.get("current_action_scope") == expected_scope
        and state.get("live_h0_candidate_authorized") is True
        and state.get("authorized_live_actions") == ["h0_candidate"]
        and state.get("authorized_h0_candidate_id") == candidate_id
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == expected_scope
        and isinstance(authorization, Mapping)
        and authorization.get("candidate_id") == candidate_id
        and authorization.get("phase") == phase
    )
    if not exact:
        raise _fail("revoke_source_not_exact_live_authorization")

    for field in _BINDING_FIELDS:
        value = authorization.get(field)
        if field.endswith("_sha256"):
            if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
                raise _fail("revoke_source_manifest_binding_invalid")
            continue
        if not isinstance(value, str) or not value:
            raise _fail("revoke_source_manifest_binding_invalid")
        relative = Path(value)
        if (
            relative.is_absolute()
            or relative.as_posix() != value
            or any(part in {"", ".", ".."} for part in relative.parts)
            or any(part in _FORBIDDEN_PATH_PARTS for part in relative.parts)
        ):
            raise _fail("revoke_source_manifest_binding_invalid")


def _validate_revoke_checkpoint(
    *,
    root: Path,
    candidate_id: str,
    phase: str,
    stage_attempt_id: str,
    checkpoint_index_path: Any,
    checkpoint_index_sha256: Any,
) -> tuple[str, str]:
    if (
        not isinstance(stage_attempt_id, str)
        or _IDENTIFIER_RE.fullmatch(stage_attempt_id) is None
    ):
        raise _fail("revoke_stage_attempt_id_invalid")
    path, relative, digest = _canonical_relative_file(
        root,
        checkpoint_index_path,
        checkpoint_index_sha256,
        label="revoke_checkpoint_index",
    )
    expected_tail = ("h0", "checkpoints", stage_attempt_id, "index.json")
    if len(Path(relative).parts) < len(expected_tail) or tuple(
        Path(relative).parts[-len(expected_tail) :]
    ) != expected_tail:
        raise _fail("revoke_checkpoint_index_path_unexpected")
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("revoke_checkpoint_index_invalid_json") from None
    exact = (
        isinstance(index, Mapping)
        and index.get("schema_version") == "membind.h0.checkpoint-index.v1"
        and index.get("protocol_version") == PROTOCOL_VERSION
        and index.get("stage_attempt_id") == stage_attempt_id
        and index.get("candidate_id") == candidate_id
        and index.get("phase") == phase
    )
    if not exact:
        raise _fail("revoke_checkpoint_index_binding_mismatch")
    return relative, digest


def build_h0_live_authorization_revoked_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    candidate_id: str,
    phase: str,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
) -> dict[str, Any]:
    """Derive a fail-closed state that records why one live grant was revoked."""

    source = _require_mapping(source_state, "source_state")
    _validate_live_revoke_source(source, candidate_id=candidate_id, phase=phase)
    checkpoint_relative, checkpoint_digest = _validate_revoke_checkpoint(
        root=Path(root).resolve(),
        candidate_id=candidate_id,
        phase=phase,
        stage_attempt_id=stage_attempt_id,
        checkpoint_index_path=checkpoint_index_path,
        checkpoint_index_sha256=checkpoint_index_sha256,
    )
    state = deepcopy(dict(source))
    progress = deepcopy(dict(_require_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden",
            "h0_candidate_progression": "blocked_protocol_gate_order_violation",
        }
    )
    state.update(
        {
            "current_stage": "H0",
            "status": REVOKED_STATUS,
            "current_action_scope": REVOKED_SCOPE,
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "next_allowed_action": "offline_protocol_review_required",
            "current_blocker": "h0_protocol_gate_order_violation",
            "h0_live_authorization_invalidation": {
                "schema_version": "membind.h0.live-authorization-invalidation.v1",
                "protocol_version": PROTOCOL_VERSION,
                "status": "invalidated_no_rerun_or_advance_authorized",
                "reason": PROTOCOL_GATE_ORDER_VIOLATION,
                "candidate_id": candidate_id,
                "phase": phase,
                "stage_attempt_id": stage_attempt_id,
                "checkpoint_index_path": checkpoint_relative,
                "checkpoint_index_sha256": checkpoint_digest,
                "candidate_rerun_authorized": False,
                "candidate_advance_authorized": False,
                "live_transition_authorized": False,
            },
        }
    )
    state.pop("live_h0_authorization", None)
    return state


def _state_target(state_path: str | Path, root: Path) -> Path:
    path = Path(state_path)
    if not path.is_absolute():
        path = root / path
    try:
        lexical_relative = path.relative_to(root)
    except ValueError:
        raise _fail("state_path_escapes_root") from None
    cursor = root
    for part in lexical_relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail("state_path_symlink_forbidden")
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        raise _fail("state_path_escapes_root") from None
    if relative != lexical_relative:
        raise _fail("state_path_noncanonical")
    if not resolved.is_file():
        raise _fail("state_path_missing")
    return resolved


def _load_state_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("state_file_unreadable") from None
    if not isinstance(value, dict):
        raise _fail("state_file_not_object")
    return value


def _load_canonical_state_snapshot(path: Path) -> tuple[bytes, dict[str, Any]]:
    """Read one exact state snapshot suitable for compare-before-commit."""

    try:
        encoded = path.read_bytes()
        value = json.loads(encoded.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("state_file_unreadable") from None
    if not isinstance(value, dict) or encoded != canonical_json_bytes(value):
        raise _fail("state_file_not_canonical")
    return encoded, value


@contextmanager
def _state_transition_lock(path: Path):
    """Serialize state writers that follow this transition module's contract."""

    lock_path = path.parent / f".{path.name}.transition.lock"
    if lock_path.is_symlink():
        raise _fail("state_transition_lock_symlink_forbidden")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError:
        raise _fail("state_transition_lock_invalid") from None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_write(path: Path, state: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(state)
    mode = path.stat().st_mode & 0o777
    temporary: Path | None = None
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
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_live_preview(state: Mapping[str, Any], *, root: Path) -> None:
    preview: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=root,
            prefix=".h0-live-preview.",
            suffix=".json",
            delete=False,
        ) as handle:
            preview = Path(handle.name)
            handle.write(canonical_json_bytes(state))
            handle.flush()
            os.fsync(handle.fileno())
        authorization = authorize_h0_live_entry(
            state_path=preview,
            candidate_id="Q1",
            phase="H0-A",
        )
        load_authorized_h0_runtime_identity(authorization, root=root)
    except (H0StateGateError, H0ManifestError, OSError) as exc:
        raise _fail("generated_live_state_failed_runtime_validation") from exc
    finally:
        if preview is not None:
            preview.unlink(missing_ok=True)


def persist_h0_offline_bound_state(
    state_path: str | Path,
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically persist verified evidence while retaining a forbidden live gate."""

    root_path = Path(root).resolve()
    target = _state_target(state_path, root_path)
    with _state_transition_lock(target):
        derived = build_h0_offline_bound_state(
            _load_state_file(target),
            root=root_path,
            manifest_verification=manifest_verification,
            tdd_evidence=tdd_evidence,
        )
        _atomic_write(target, derived)
    return derived


def verify_and_persist_h0_offline_bound_state(
    state_path: str | Path,
    *,
    root: str | Path,
    tdd_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the full artifact verifier, then atomically bind its exact result."""

    from h0_artifacts import verify_h0_offline_artifacts

    root_path = Path(root).resolve()
    verification = verify_h0_offline_artifacts(root_path)
    return persist_h0_offline_bound_state(
        state_path,
        root=root_path,
        manifest_verification=verification,
        tdd_evidence=tdd_evidence,
    )


def transition_q1_h0_a_live(
    state_path: str | Path,
    *,
    root: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate and optionally atomically commit the exact Q1/H0-A live state."""

    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root_path = Path(root).resolve()
    target = _state_target(state_path, root_path)
    if dry_run:
        derived = build_q1_h0_a_live_state(_load_state_file(target), root=root_path)
        _validate_live_preview(derived, root=root_path)
        return derived
    with _state_transition_lock(target):
        derived = build_q1_h0_a_live_state(_load_state_file(target), root=root_path)
        _validate_live_preview(derived, root=root_path)
        _atomic_write(target, derived)
    return derived


def transition_h0_live_authorization_revoke(
    state_path: str | Path,
    *,
    root: str | Path,
    candidate_id: str,
    phase: str,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate and optionally commit a protocol-order live-grant revocation."""

    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root_path = Path(root).resolve()
    target = _state_target(state_path, root_path)
    arguments = {
        "root": root_path,
        "candidate_id": candidate_id,
        "phase": phase,
        "stage_attempt_id": stage_attempt_id,
        "checkpoint_index_path": checkpoint_index_path,
        "checkpoint_index_sha256": checkpoint_index_sha256,
    }

    def derive() -> tuple[bytes, str, dict[str, Any], dict[str, Any]]:
        initial_bytes, source = _load_canonical_state_snapshot(target)
        initial_sha256 = hashlib.sha256(initial_bytes).hexdigest()
        try:
            authorization = authorize_h0_live_entry(
                state_path=target,
                candidate_id=candidate_id,
                phase=phase,
            )
        except H0StateGateError:
            raise _fail("revoke_source_not_exact_live_authorization") from None
        if dict(
            _require_mapping(source.get("live_h0_authorization"), "authorization")
        ) != dict(authorization):
            raise _fail("revoke_authorization_changed")
        derived_state = build_h0_live_authorization_revoked_state(source, **arguments)
        return initial_bytes, initial_sha256, source, derived_state

    if dry_run:
        return derive()[3]

    with _state_transition_lock(target):
        initial_bytes, initial_sha256, source, derived = derive()
        # This second build deliberately rehashes the checkpoint evidence.
        confirmed = build_h0_live_authorization_revoked_state(source, **arguments)
        if canonical_json_bytes(confirmed) != canonical_json_bytes(derived):
            raise _fail("revoke_evidence_changed_before_commit")
        try:
            current_bytes = target.read_bytes()
        except OSError:
            raise _fail("revoke_state_changed_before_commit") from None
        current_sha256 = hashlib.sha256(current_bytes).hexdigest()
        if current_bytes != initial_bytes or current_sha256 != initial_sha256:
            raise _fail("revoke_state_changed_before_commit")
        _atomic_write(target, derived)
    return derived
