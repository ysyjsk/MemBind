"""One-shot, offline-authorized transition for Native characterization C2.

The operator starts the pinned services outside this module.  This transition
only records that confirmation after checking local, content-addressed C0/C1
and C2-runner evidence.  It never opens model, embedding, database, SSH, or
other live clients.
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


PROTOCOL_VERSION = "current-validation-v1.3"
WORKPLAN_ID = "native-characterization-v1.1"
WORKPLAN_RELATIVE_PATH = "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
STATE_RELATIVE_PATH = "membind-validation/CURRENT_STATE.json"
TARGET_STAGE = "NATIVE_CHARACTERIZATION"
SOURCE_SCOPE = "native_characterization_offline_only"
TARGET_SCOPE = "native_characterization_c2_live_only"
SOURCE_NEXT_ACTION = "implement_c2_runner_offline"
TARGET_NEXT_ACTION = "run_native_characterization_c2"
TARGET_ACTION = "native_characterization_c2"
AUTHORIZATION_KEY = "native_characterization_c2_authorization"
SOURCE_PROGRESS = "c0_pass_c2_runner_tdd_pending"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
class C2AuthorizationBindings:
    """Content identities for the C2 live-authorization transition."""

    source_state_sha256: str
    workplan_sha256: str
    freeze_sha256: str
    c0_manifest_sha256: str
    c0_checkpoint_sha256: str
    c2_runner_source_sha256: str
    c2_runner_test_sha256: str
    c2_runner_green_log_sha256: str
    c2_runner_source_path: str
    c2_runner_test_path: str
    c2_runner_green_log_path: str
    operator_service_ready: bool


class NativeCharacterizationC2AuthorizationError(RuntimeError):
    """Sanitized fail-closed error; never includes state or credential values."""


def _fail(reason: str) -> NativeCharacterizationC2AuthorizationError:
    return NativeCharacterizationC2AuthorizationError(
        f"native characterization C2 authorization denied: {reason}"
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


def _require_digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{name}_invalid")
    return value


def _safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise _fail("unsafe_state")
            _safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe(child)
    elif isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or ".env" in lowered:
            raise _fail("unsafe_state")


def _validate_bindings(bindings: C2AuthorizationBindings) -> None:
    if not isinstance(bindings, C2AuthorizationBindings):
        raise _fail("bindings_invalid")
    for name in (
        "source_state_sha256",
        "workplan_sha256",
        "freeze_sha256",
        "c0_manifest_sha256",
        "c0_checkpoint_sha256",
        "c2_runner_source_sha256",
        "c2_runner_test_sha256",
        "c2_runner_green_log_sha256",
    ):
        _require_digest(getattr(bindings, name), name)
    if bindings.operator_service_ready is not True:
        raise _fail("operator_service_not_confirmed")


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
    return value


def _validate_payload_hash(value: Mapping[str, Any], label: str) -> None:
    candidate = deepcopy(dict(value))
    observed = candidate.pop("payload_sha256", None)
    if observed != _sha(_canonical(candidate)):
        raise _fail(f"{label}_payload_mismatch")


def _state_c0_paths(state: Mapping[str, Any]) -> tuple[str, str]:
    completion = state.get("native_characterization_c0_completion")
    if not isinstance(completion, Mapping):
        raise _fail("c0_completion_missing")
    exact = (
        completion.get("schema_version")
        == "membind.native-characterization-c0-completion.v1"
        and completion.get("grant_consumed") is True
        and completion.get("c0_status") == "pass"
    )
    if not exact:
        raise _fail("c0_completion_contract_mismatch")
    manifest = str(completion.get("manifest_path", ""))
    checkpoint = str(completion.get("checkpoint_path", ""))
    return manifest, checkpoint


def _validate_evidence(
    state: Mapping[str, Any], repo_root: Path, bindings: C2AuthorizationBindings
) -> None:
    workplan = _resolve_under(repo_root, WORKPLAN_RELATIVE_PATH, "workplan")
    if _sha(workplan.read_bytes()) != bindings.workplan_sha256:
        raise _fail("workplan_hash_mismatch")
    if "WORKPLAN_FREEZE=true" not in workplan.read_text(encoding="ascii"):
        raise _fail("workplan_not_frozen")

    validation = repo_root / "membind-validation"
    freeze = _resolve_under(
        validation, "artifacts/native_characterization/freeze.json", "freeze"
    )
    if _sha(freeze.read_bytes()) != bindings.freeze_sha256:
        raise _fail("freeze_hash_mismatch")
    freeze_value = _read_json(freeze, "freeze")
    if freeze_value.get("schema_version") != "membind.native-characterization-freeze.v1":
        raise _fail("freeze_contract_mismatch")

    manifest_relative, checkpoint_relative = _state_c0_paths(state)
    manifest = _resolve_under(validation, manifest_relative, "c0_manifest")
    checkpoint = _resolve_under(validation, checkpoint_relative, "c0_checkpoint")
    if _sha(manifest.read_bytes()) != bindings.c0_manifest_sha256:
        raise _fail("c0_manifest_hash_mismatch")
    if _sha(checkpoint.read_bytes()) != bindings.c0_checkpoint_sha256:
        raise _fail("c0_checkpoint_hash_mismatch")
    manifest_value = _read_json(manifest, "c0_manifest")
    checkpoint_value = _read_json(checkpoint, "c0_checkpoint")
    _validate_payload_hash(manifest_value, "c0_manifest")
    _validate_payload_hash(checkpoint_value, "c0_checkpoint")
    exact_c0 = (
        manifest_value.get("schema_version")
        == "membind.native-characterization-c0-result.v1"
        and manifest_value.get("stage") == "C0"
        and manifest_value.get("status") == "pass"
        and manifest_value.get("error_code") is None
        and checkpoint_value.get("stage") == "C0"
        and checkpoint_value.get("status") == "pass"
        and checkpoint_value.get("run_id") == manifest_value.get("run_id")
    )
    if not exact_c0:
        raise _fail("c0_pass_contract_mismatch")

    source_path = _resolve_under(
        validation, bindings.c2_runner_source_path, "c2_runner_source"
    )
    test_path = _resolve_under(
        validation, bindings.c2_runner_test_path, "c2_runner_test"
    )
    log_path = _resolve_under(
        validation, bindings.c2_runner_green_log_path, "c2_runner_green_log"
    )
    if _sha(source_path.read_bytes()) != bindings.c2_runner_source_sha256:
        raise _fail("c2_runner_source_hash_mismatch")
    if _sha(test_path.read_bytes()) != bindings.c2_runner_test_sha256:
        raise _fail("c2_runner_test_hash_mismatch")
    if _sha(log_path.read_bytes()) != bindings.c2_runner_green_log_sha256:
        raise _fail("c2_runner_green_log_hash_mismatch")
    try:
        log_text = log_path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise _fail("c2_runner_green_log_unreadable") from None
    if "FAILED" in log_text or "ERROR" in log_text or "OK" not in log_text:
        raise _fail("c2_runner_green_log_not_green")


def _validate_source(state: Mapping[str, Any]) -> None:
    progress = state.get("stage_progress")
    exact = (
        state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("current_stage") == TARGET_STAGE
        and state.get("status") == SOURCE_SCOPE
        and state.get("current_action_scope") == SOURCE_SCOPE
        and state.get("current_blocker") is None
        and state.get("next_allowed_action") == SOURCE_NEXT_ACTION
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_live_actions") == []
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and isinstance(progress, Mapping)
        and progress.get("native_characterization") == SOURCE_PROGRESS
        and AUTHORIZATION_KEY not in state
    )
    if not exact:
        raise _fail("source_state_not_c2_ready_offline")


def _metadata(bindings: C2AuthorizationBindings) -> dict[str, Any]:
    return {
        "schema_version": "membind.native-characterization-c2-authorization.v1",
        "workplan_id": WORKPLAN_ID,
        "workplan_path": WORKPLAN_RELATIVE_PATH,
        "workplan_sha256": bindings.workplan_sha256,
        "source_state_sha256": bindings.source_state_sha256,
        "freeze_sha256": bindings.freeze_sha256,
        "c0_manifest_sha256": bindings.c0_manifest_sha256,
        "c0_checkpoint_sha256": bindings.c0_checkpoint_sha256,
        "c2_runner_source_path": bindings.c2_runner_source_path,
        "c2_runner_source_sha256": bindings.c2_runner_source_sha256,
        "c2_runner_test_path": bindings.c2_runner_test_path,
        "c2_runner_test_sha256": bindings.c2_runner_test_sha256,
        "c2_runner_green_log_path": bindings.c2_runner_green_log_path,
        "c2_runner_green_log_sha256": bindings.c2_runner_green_log_sha256,
        "live_authorized": True,
    }


def build_native_characterization_c2_authorized_state(
    source_state: Mapping[str, Any], bindings: C2AuthorizationBindings
) -> dict[str, Any]:
    """Pure builder.  It performs no filesystem or live-service I/O."""

    _validate_bindings(bindings)
    source = deepcopy(dict(source_state))
    _safe(source)
    _validate_source(source)
    if _sha(_canonical(source)) != bindings.source_state_sha256:
        raise _fail("source_state_drift")
    target = deepcopy(source)
    target["current_action_scope"] = TARGET_SCOPE
    target["authorized_live_actions"] = [TARGET_ACTION]
    target["next_allowed_action"] = TARGET_NEXT_ACTION
    target[AUTHORIZATION_KEY] = _metadata(bindings)
    return target


def _validate_target(target: Mapping[str, Any], bindings: C2AuthorizationBindings) -> None:
    metadata = target.get(AUTHORIZATION_KEY)
    if not isinstance(metadata, Mapping):
        raise _fail("target_state_drift")
    expected_metadata = _metadata(
        C2AuthorizationBindings(
            source_state_sha256=str(metadata.get("source_state_sha256")),
            workplan_sha256=bindings.workplan_sha256,
            freeze_sha256=bindings.freeze_sha256,
            c0_manifest_sha256=bindings.c0_manifest_sha256,
            c0_checkpoint_sha256=bindings.c0_checkpoint_sha256,
            c2_runner_source_sha256=bindings.c2_runner_source_sha256,
            c2_runner_test_sha256=bindings.c2_runner_test_sha256,
            c2_runner_green_log_sha256=bindings.c2_runner_green_log_sha256,
            c2_runner_source_path=bindings.c2_runner_source_path,
            c2_runner_test_path=bindings.c2_runner_test_path,
            c2_runner_green_log_path=bindings.c2_runner_green_log_path,
            operator_service_ready=True,
        )
    )
    exact = (
        target.get("protocol_version") == PROTOCOL_VERSION
        and target.get("current_stage") == TARGET_STAGE
        and target.get("status") == SOURCE_SCOPE
        and target.get("current_action_scope") == TARGET_SCOPE
        and target.get("current_blocker") is None
        and target.get("next_allowed_action") == TARGET_NEXT_ACTION
        and target.get("live_h0_candidate_authorized") is False
        and target.get("authorized_live_actions") == [TARGET_ACTION]
        and target.get("authorized_h0_candidate_id") is None
        and target.get("service_admin_authorized") is False
        and target.get("v3_smoke_003_authorized") is False
        and dict(metadata) == expected_metadata
    )
    if not exact:
        raise _fail("target_state_drift")


@contextmanager
def _lock(path: Path):
    lock = path.parent / f".{path.name}.c2-authorization.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        raise _fail("authorization_lock_invalid") from None
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


def authorize_native_characterization_c2_live_only(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    bindings: C2AuthorizationBindings,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate and optionally apply the exact one-shot C2 live authorization."""

    _validate_bindings(bindings)
    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root = Path(repo_root).resolve()
    path = Path(state_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    expected_path = root / STATE_RELATIVE_PATH
    try:
        path.relative_to(root)
    except ValueError:
        raise _fail("state_path_escape") from None
    if path != expected_path or path.is_symlink():
        raise _fail("state_path_not_current_state")

    def derive() -> tuple[dict[str, Any], bool]:
        try:
            raw = path.read_bytes()
            state = json.loads(raw.decode("ascii"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise _fail("state_unreadable") from None
        if not isinstance(state, dict):
            raise _fail("state_not_object")
        _safe(state)
        if _sha(_canonical(state)) != bindings.source_state_sha256:
            raise _fail("source_state_drift")
        if state.get("current_action_scope") == TARGET_SCOPE:
            _validate_target(state, bindings)
            _validate_evidence(state, root, bindings)
            return deepcopy(state), True
        _validate_source(state)
        _validate_evidence(state, root, bindings)
        return build_native_characterization_c2_authorized_state(state, bindings), False

    if dry_run:
        target, _ = derive()
        return target
    with _lock(path):
        target, already = derive()
        if not already:
            _atomic_write(path, target)
        return target


__all__ = [
    "C2AuthorizationBindings",
    "NativeCharacterizationC2AuthorizationError",
    "authorize_native_characterization_c2_live_only",
    "build_native_characterization_c2_authorized_state",
]
