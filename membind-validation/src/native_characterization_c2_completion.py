"""Persist verified C2 evidence and consume the one-shot C2 live grant."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from native_characterization_c2_verify import verify_c2_run
from native_characterization_state_transition import (
    _atomic_write,
    _canonical_json_bytes,
    _state_transition_lock,
)


STATE_RELATIVE_PATH = "membind-validation/CURRENT_STATE.json"
SOURCE_SCOPE = "native_characterization_c2_live_only"
TARGET_SCOPE = "native_characterization_c3_offline_only"
SOURCE_ACTION = "native_characterization_c2"
SOURCE_NEXT_ACTION = "run_native_characterization_c2"
TARGET_NEXT_ACTION = "build_native_characterization_dependency_map_offline"
TARGET_PROGRESS = "c2_verified_c3_dependency_audit_pending"
COMPLETION_KEY = "native_characterization_c2_completion"
VERIFICATION_SCHEMA = "membind.native-characterization-c2-verification-evidence.v1"
VERIFIER_SOURCE = "src/native_characterization_c2_verify.py"
_RUN_ID_RE = re.compile(r"^c2-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class C2CompletionBindings:
    source_state_sha256: str
    verification_relative_path: str
    verification_sha256: str
    verification_payload_sha256: str


class NativeCharacterizationC2CompletionError(RuntimeError):
    """Sanitized fail-closed C2 completion transition error."""


def _fail(reason: str) -> NativeCharacterizationC2CompletionError:
    return NativeCharacterizationC2CompletionError(
        f"native characterization C2 completion denied: {reason}"
    )


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _read_object(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_unreadable") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return raw, value


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop("payload_sha256", None)
    result["payload_sha256"] = _sha(_canonical_json_bytes(result))
    return result


def _validate_seal(value: Mapping[str, Any], label: str) -> None:
    candidate = deepcopy(dict(value))
    observed = candidate.pop("payload_sha256", None)
    if observed != _sha(_canonical_json_bytes(candidate)):
        raise _fail(f"{label}_payload_mismatch")


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise _fail(f"{label}_path_invalid")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or value != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise _fail(f"{label}_path_invalid")
    return value


def _resolve_existing(root: Path, relative: str, label: str) -> Path:
    safe = _safe_relative(relative, label)
    path = root
    for part in PurePosixPath(safe).parts:
        path = path / part
        if path.is_symlink():
            raise _fail(f"{label}_path_symlink")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        raise _fail(f"{label}_missing") from None
    if not resolved.is_file():
        raise _fail(f"{label}_missing")
    return resolved


def _verification_relative_path(run_id: str) -> str:
    return f"artifacts/diagnostics/native_characterization_{run_id}_verification.json"


def _atomic_create(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = _canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise _fail("verification_path_symlink")
    if path.exists():
        try:
            if path.read_bytes() != encoded:
                raise _fail("verification_artifact_drift")
        except OSError:
            raise _fail("verification_artifact_unreadable") from None
        return
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
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise _fail("verification_artifact_write_failed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def persist_c2_verification(
    validation_root: str | Path,
    run_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Recompute, seal, and optionally persist one read-only C2 verification."""

    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise _fail("run_id_invalid")
    try:
        validation = Path(validation_root).resolve(strict=True)
    except (OSError, RuntimeError):
        raise _fail("validation_root_invalid") from None
    if not validation.is_dir():
        raise _fail("validation_root_invalid")
    verifier_path = _resolve_existing(validation, VERIFIER_SOURCE, "verifier_source")
    result = verify_c2_run(validation, run_id)
    record = _seal(
        {
            "schema_version": VERIFICATION_SCHEMA,
            "status": "verified",
            "run_id": run_id,
            "verification_command": (
                ".venv/bin/python src/native_characterization_c2_verify.py "
                f"--validation-root . --run-id {run_id}"
            ),
            "verifier_source_path": VERIFIER_SOURCE,
            "verifier_source_sha256": _sha(verifier_path.read_bytes()),
            "result": result,
        }
    )
    relative = _verification_relative_path(run_id)
    path = validation / relative
    if not dry_run:
        _atomic_create(path, record)
    encoded = _canonical_json_bytes(record)
    return {
        "relative_path": relative,
        "sha256": _sha(encoded),
        "payload_sha256": record["payload_sha256"],
        "record": record,
    }


def _validate_bindings(bindings: C2CompletionBindings) -> None:
    if not isinstance(bindings, C2CompletionBindings):
        raise _fail("bindings_invalid")
    _require_sha(bindings.source_state_sha256, "source_state_sha256")
    _require_sha(bindings.verification_sha256, "verification_sha256")
    _require_sha(
        bindings.verification_payload_sha256,
        "verification_payload_sha256",
    )
    _safe_relative(bindings.verification_relative_path, "verification")


def _validate_verification(
    validation: Path,
    bindings: C2CompletionBindings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _resolve_existing(
        validation,
        bindings.verification_relative_path,
        "verification",
    )
    raw, record = _read_object(path, "verification")
    if raw != _canonical_json_bytes(record):
        raise _fail("verification_not_canonical")
    if _sha(raw) != bindings.verification_sha256:
        raise _fail("verification_hash_mismatch")
    _validate_seal(record, "verification")
    if record.get("payload_sha256") != bindings.verification_payload_sha256:
        raise _fail("verification_payload_binding_mismatch")
    run_id = record.get("run_id")
    expected_path = _verification_relative_path(str(run_id))
    if (
        record.get("schema_version") != VERIFICATION_SCHEMA
        or record.get("status") != "verified"
        or not isinstance(run_id, str)
        or _RUN_ID_RE.fullmatch(run_id) is None
        or bindings.verification_relative_path != expected_path
    ):
        raise _fail("verification_contract_mismatch")
    verifier_path = _resolve_existing(validation, VERIFIER_SOURCE, "verifier_source")
    if record.get("verifier_source_sha256") != _sha(verifier_path.read_bytes()):
        raise _fail("verifier_source_hash_mismatch")
    recomputed = verify_c2_run(validation, run_id)
    if record.get("result") != recomputed:
        raise _fail("verification_result_mismatch")

    manifest_path = validation / "artifacts/native_characterization/runs" / run_id / "manifest.json"
    manifest_raw, manifest = _read_object(manifest_path, "manifest")
    result = record["result"]
    exact = (
        manifest.get("status") == "completed"
        and manifest.get("run_id") == run_id
        and isinstance(manifest.get("block_count"), int)
        and manifest.get("block_count", 0) > 0
        and isinstance(manifest.get("episode_count"), int)
        and manifest.get("episode_count", 0) > 0
        and result.get("status") == "verified"
        and result.get("run_id") == run_id
        and result.get("manifest_sha256") == _sha(manifest_raw)
        and result.get("checkpoint_sha256") == manifest.get("checkpoint_sha256")
        and result.get("e1_breakdown_sha256") == manifest.get("e1_breakdown_sha256")
        and result.get("top_level_e1_breakdown_sha256")
        == manifest.get("top_level_e1_breakdown_sha256")
    )
    if not exact:
        raise _fail("completed_run_binding_mismatch")
    return record, manifest


def _validate_source(state: Mapping[str, Any]) -> None:
    alignment = state.get("native_characterization_reference_alignment")
    fresh = alignment.get("fresh_c2") if isinstance(alignment, Mapping) else None
    receipt = state.get("native_characterization_reference_c2_authorization")
    exact = (
        state.get("protocol_version") == "current-validation-v1.3"
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("status") == SOURCE_SCOPE
        and state.get("current_action_scope") == SOURCE_SCOPE
        and state.get("current_blocker") is None
        and state.get("next_allowed_action") == SOURCE_NEXT_ACTION
        and state.get("authorized_live_actions") == [SOURCE_ACTION]
        and state.get("native_characterization_live_authorized") is True
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and isinstance(alignment, Mapping)
        and alignment.get("status") == "c2_live_authorized"
        and isinstance(fresh, Mapping)
        and fresh.get("live_authorized") is True
        and fresh.get("semantic_attempts_remaining") == 1
        and fresh.get("start_source_sequence") == 0
        and fresh.get("resume_allowed") is False
        and isinstance(receipt, Mapping)
        and receipt.get("live_authorized") is True
        and receipt.get("replacement_start_source_sequence") == 0
        and receipt.get("replacement_resume_allowed") is False
        and COMPLETION_KEY not in state
    )
    if not exact:
        raise _fail("source_state_not_exact_c2_grant")


def _completion_metadata(
    bindings: C2CompletionBindings,
    record: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    result = record["result"]
    return {
        "schema_version": "membind.native-characterization-c2-completion.v1",
        "status": "verified",
        "source_state_sha256": bindings.source_state_sha256,
        "run_id": record["run_id"],
        "verification_path": bindings.verification_relative_path,
        "verification_sha256": bindings.verification_sha256,
        "verification_payload_sha256": bindings.verification_payload_sha256,
        "verifier_source_sha256": record["verifier_source_sha256"],
        "manifest_sha256": result["manifest_sha256"],
        "checkpoint_sha256": result["checkpoint_sha256"],
        "e1_breakdown_sha256": result["e1_breakdown_sha256"],
        "top_level_e1_breakdown_sha256": result[
            "top_level_e1_breakdown_sha256"
        ],
        "freeze_sha256": manifest["freeze_sha256"],
        "indexed_file_count": result["indexed_file_count"],
        "jsonl_line_count": result["jsonl_line_count"],
        "block_count": manifest["block_count"],
        "episode_count": manifest["episode_count"],
        "grant_consumed": True,
        "live_authorized": False,
    }


def _revoked_receipt(value: Any, run_id: str) -> Any:
    if not isinstance(value, Mapping):
        return deepcopy(value)
    receipt = deepcopy(dict(value))
    receipt["live_authorized"] = False
    receipt["grant_consumed"] = True
    receipt["completed_run_id"] = run_id
    return receipt


def _build_target(
    source: Mapping[str, Any],
    bindings: C2CompletionBindings,
    record: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_source(source)
    run_id = str(record["run_id"])
    target = deepcopy(dict(source))
    target.update(
        {
            "status": TARGET_SCOPE,
            "current_action_scope": TARGET_SCOPE,
            "current_blocker": None,
            "next_allowed_action": TARGET_NEXT_ACTION,
            "authorized_live_actions": [],
            "native_characterization_live_authorized": False,
        }
    )
    progress = deepcopy(dict(target.get("stage_progress", {})))
    progress["native_characterization"] = TARGET_PROGRESS
    target["stage_progress"] = progress

    alignment = deepcopy(dict(target["native_characterization_reference_alignment"]))
    fresh = deepcopy(dict(alignment["fresh_c2"]))
    fresh.update(
        {
            "live_authorized": False,
            "semantic_attempts_remaining": 0,
            "completed_run_id": run_id,
            "completion_status": "verified",
        }
    )
    alignment.update(
        {
            "status": "c2_completed_verified_c3_offline",
            "fresh_c2": fresh,
        }
    )
    target["native_characterization_reference_alignment"] = alignment
    for key in (
        "native_characterization_reference_c2_authorization",
        "native_characterization_c2_authorization",
        "native_characterization_c2_reauthorization",
    ):
        if key in target:
            target[key] = _revoked_receipt(target[key], run_id)
    target[COMPLETION_KEY] = _completion_metadata(bindings, record, manifest)
    return target


def _validate_target(
    state: Mapping[str, Any],
    bindings: C2CompletionBindings,
    record: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    expected = _completion_metadata(bindings, record, manifest)
    alignment = state.get("native_characterization_reference_alignment")
    fresh = alignment.get("fresh_c2") if isinstance(alignment, Mapping) else None
    exact = (
        state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("status") == TARGET_SCOPE
        and state.get("current_action_scope") == TARGET_SCOPE
        and state.get("current_blocker") is None
        and state.get("next_allowed_action") == TARGET_NEXT_ACTION
        and state.get("authorized_live_actions") == []
        and state.get("native_characterization_live_authorized") is False
        and state.get("stage_progress", {}).get("native_characterization")
        == TARGET_PROGRESS
        and state.get(COMPLETION_KEY) == expected
        and isinstance(alignment, Mapping)
        and alignment.get("status") == "c2_completed_verified_c3_offline"
        and isinstance(fresh, Mapping)
        and fresh.get("live_authorized") is False
        and fresh.get("semantic_attempts_remaining") == 0
        and fresh.get("completed_run_id") == record.get("run_id")
    )
    if not exact:
        raise _fail("target_state_drift")


def complete_native_characterization_c2(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    bindings: C2CompletionBindings,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Bind a verified C2 run and atomically enter the C3 offline lane."""

    _validate_bindings(bindings)
    try:
        root = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError):
        raise _fail("repo_root_invalid") from None
    path = Path(state_path)
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _fail("state_path_invalid") from None
    if path != root / STATE_RELATIVE_PATH or path.is_symlink():
        raise _fail("state_path_invalid")
    validation = path.parent

    def derive() -> tuple[dict[str, Any], bool]:
        raw, state = _read_object(path, "state")
        if raw != _canonical_json_bytes(state):
            raise _fail("state_not_canonical")
        record, manifest = _validate_verification(validation, bindings)
        if COMPLETION_KEY in state:
            _validate_target(state, bindings, record, manifest)
            return deepcopy(state), True
        if _sha(raw) != bindings.source_state_sha256:
            raise _fail("source_state_drift")
        return _build_target(state, bindings, record, manifest), False

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
    "C2CompletionBindings",
    "NativeCharacterizationC2CompletionError",
    "complete_native_characterization_c2",
    "persist_c2_verification",
]
