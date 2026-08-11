"""Consume a successful one-shot C0 grant and return the lane to offline work."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from native_characterization_state_transition import (
    _atomic_write,
    _canonical_json_bytes,
    _state_transition_lock,
)


STATE_RELATIVE_PATH = "membind-validation/CURRENT_STATE.json"
SOURCE_SCOPE = "native_characterization_c0_live_only"
TARGET_SCOPE = "native_characterization_offline_only"
SOURCE_ACTION = "native_characterization_c0"
SOURCE_NEXT_ACTION = "run_native_characterization_c0"
TARGET_NEXT_ACTION = "implement_c2_runner_offline"
COMPLETION_KEY = "native_characterization_c0_completion"
TARGET_PROGRESS = "c0_pass_c2_runner_tdd_pending"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class C0CompletionBindings:
    source_state_sha256: str
    manifest_sha256: str
    checkpoint_sha256: str
    manifest_relative_path: str
    checkpoint_relative_path: str


class NativeCharacterizationC0CompletionError(RuntimeError):
    """Sanitized fail-closed C0 completion error."""


def _fail(reason: str) -> NativeCharacterizationC0CompletionError:
    return NativeCharacterizationC0CompletionError(
        f"native characterization C0 completion denied: {reason}"
    )


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_bindings(bindings: C0CompletionBindings) -> None:
    if not isinstance(bindings, C0CompletionBindings):
        raise _fail("bindings_invalid")
    for name in ("source_state_sha256", "manifest_sha256", "checkpoint_sha256"):
        if _SHA256_RE.fullmatch(str(getattr(bindings, name))) is None:
            raise _fail(f"{name}_invalid")


def _resolve(validation: Path, relative: str, label: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        raise _fail(f"{label}_path_invalid")
    path = validation / value
    cursor = validation
    for part in value.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_path_symlink")
    if not path.is_file() or path.resolve().parent != path.parent.resolve():
        raise _fail(f"{label}_missing")
    return path


def _read_json(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_unreadable") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return raw, value


def _validate_payload(value: Mapping[str, Any], label: str) -> None:
    candidate = deepcopy(dict(value))
    observed = candidate.pop("payload_sha256", None)
    if observed != _sha(_canonical_json_bytes(candidate)):
        raise _fail(f"{label}_payload_mismatch")


def _validate_evidence(
    validation: Path, bindings: C0CompletionBindings
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = _resolve(
        validation, bindings.manifest_relative_path, "manifest"
    )
    checkpoint_path = _resolve(
        validation, bindings.checkpoint_relative_path, "checkpoint"
    )
    try:
        manifest_raw = manifest_path.read_bytes()
        checkpoint_raw = checkpoint_path.read_bytes()
    except OSError:
        raise _fail("c0_evidence_unreadable") from None
    if _sha(manifest_raw) != bindings.manifest_sha256:
        raise _fail("manifest_hash_mismatch")
    if _sha(checkpoint_raw) != bindings.checkpoint_sha256:
        raise _fail("checkpoint_hash_mismatch")
    try:
        manifest = json.loads(manifest_raw.decode("ascii"))
        checkpoint = json.loads(checkpoint_raw.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise _fail("c0_evidence_unreadable") from None
    if not isinstance(manifest, dict) or not isinstance(checkpoint, dict):
        raise _fail("c0_evidence_not_object")
    _validate_payload(manifest, "manifest")
    _validate_payload(checkpoint, "checkpoint")
    exact = (
        manifest.get("schema_version")
        == "membind.native-characterization-c0-result.v1"
        and manifest.get("artifact_id") == "native-characterization-c0"
        and manifest.get("stage") == "C0"
        and manifest.get("status") == "pass"
        and manifest.get("error_code") is None
        and isinstance(manifest.get("add_episode_latency_ns"), int)
        and manifest.get("add_episode_latency_ns", 0) > 0
        and manifest.get("runtime_config", {}).get("classification") == "U0"
        and checkpoint.get("schema_version")
        == "membind.native-characterization-checkpoint.v1"
        and checkpoint.get("run_id") == manifest.get("run_id")
        and checkpoint.get("stage") == "C0"
        and checkpoint.get("status") == "pass"
        and checkpoint.get("completed_source_sequences")
        == [manifest.get("source_sequence")]
        and checkpoint.get("manifest_payload_sha256")
        == manifest.get("payload_sha256")
        and checkpoint.get("error_code") is None
    )
    if not exact:
        raise _fail("c0_pass_contract_mismatch")
    return manifest, checkpoint


def _validate_source(state: Mapping[str, Any]) -> None:
    authorization = state.get("native_characterization_c0_authorization")
    exact = (
        state.get("protocol_version") == "current-validation-v1.3"
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("status") == TARGET_SCOPE
        and state.get("current_action_scope") == SOURCE_SCOPE
        and state.get("current_blocker") is None
        and state.get("next_allowed_action") == SOURCE_NEXT_ACTION
        and state.get("authorized_live_actions") == [SOURCE_ACTION]
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and isinstance(authorization, Mapping)
        and authorization.get("schema_version")
        == "membind.native-characterization-c0-authorization.v1"
        and authorization.get("live_authorized") is True
        and COMPLETION_KEY not in state
    )
    if not exact:
        raise _fail("source_state_not_exact_c0_grant")


def _metadata(
    bindings: C0CompletionBindings, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "membind.native-characterization-c0-completion.v1",
        "source_state_sha256": bindings.source_state_sha256,
        "manifest_path": bindings.manifest_relative_path,
        "manifest_sha256": bindings.manifest_sha256,
        "manifest_payload_sha256": manifest["payload_sha256"],
        "checkpoint_path": bindings.checkpoint_relative_path,
        "checkpoint_sha256": bindings.checkpoint_sha256,
        "run_id": manifest["run_id"],
        "grant_consumed": True,
        "c0_status": "pass",
    }


def _build_target(
    source: Mapping[str, Any],
    bindings: C0CompletionBindings,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_source(source)
    target = deepcopy(dict(source))
    target["current_action_scope"] = TARGET_SCOPE
    target["authorized_live_actions"] = []
    target["next_allowed_action"] = TARGET_NEXT_ACTION
    progress = deepcopy(dict(target.get("stage_progress", {})))
    progress["native_characterization"] = TARGET_PROGRESS
    target["stage_progress"] = progress
    target[COMPLETION_KEY] = _metadata(bindings, manifest)
    return target


def _validate_target(
    state: Mapping[str, Any], bindings: C0CompletionBindings, manifest: Mapping[str, Any]
) -> None:
    metadata = state.get(COMPLETION_KEY)
    expected_metadata = _metadata(
        C0CompletionBindings(
            source_state_sha256=str(
                metadata.get("source_state_sha256")
                if isinstance(metadata, Mapping)
                else ""
            ),
            manifest_sha256=bindings.manifest_sha256,
            checkpoint_sha256=bindings.checkpoint_sha256,
            manifest_relative_path=bindings.manifest_relative_path,
            checkpoint_relative_path=bindings.checkpoint_relative_path,
        ),
        manifest,
    )
    exact = (
        state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("status") == TARGET_SCOPE
        and state.get("current_action_scope") == TARGET_SCOPE
        and state.get("authorized_live_actions") == []
        and state.get("next_allowed_action") == TARGET_NEXT_ACTION
        and state.get("stage_progress", {}).get("native_characterization")
        == TARGET_PROGRESS
        and isinstance(metadata, Mapping)
        and dict(metadata) == expected_metadata
    )
    if not exact:
        raise _fail("target_state_drift")


def complete_native_characterization_c0(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    bindings: C0CompletionBindings,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate C0 pass evidence and optionally consume its exact live grant."""

    _validate_bindings(bindings)
    root = Path(repo_root).resolve()
    path = Path(state_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if path != root / STATE_RELATIVE_PATH or path.is_symlink():
        raise _fail("state_path_invalid")
    validation = path.parent

    def derive() -> tuple[dict[str, Any], bool]:
        raw, state = _read_json(path, "state")
        if raw != _canonical_json_bytes(state):
            raise _fail("state_not_canonical")
        if _sha(raw) != bindings.source_state_sha256:
            raise _fail("source_state_drift")
        manifest, _checkpoint = _validate_evidence(validation, bindings)
        if COMPLETION_KEY in state:
            _validate_target(state, bindings, manifest)
            return deepcopy(state), True
        return _build_target(state, bindings, manifest), False

    if dry_run:
        return derive()[0]
    with _state_transition_lock(path):
        target, already = derive()
        if not already:
            try:
                _atomic_write(path, target)
            except RuntimeError:
                raise _fail("atomic_write_failed") from None
        return target


__all__ = [
    "C0CompletionBindings",
    "NativeCharacterizationC0CompletionError",
    "complete_native_characterization_c0",
]
