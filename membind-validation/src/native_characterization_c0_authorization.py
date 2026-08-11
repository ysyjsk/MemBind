"""One-shot, offline-authorized transition for Native characterization C0.

The operator is responsible for starting the pinned services.  This module only
records that confirmation after checking the immutable local workplan, freeze,
C1 qualification, and C0 dry-run evidence.  It never performs a health check or
constructs a client.  Applying the transition is an explicit caller choice;
``dry_run=True`` is read-only.
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
TARGET_SCOPE = "native_characterization_c0_live_only"
SOURCE_NEXT_ACTION = "operator_start_vllm_then_authorize_c0"
TARGET_NEXT_ACTION = "run_native_characterization_c0"
TARGET_ACTION = "native_characterization_c0"
AUTHORIZATION_KEY = "native_characterization_c0_authorization"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
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
class C0AuthorizationBindings:
    """Content identities supplied by the operator-facing authorization step."""

    source_state_sha256: str
    workplan_sha256: str
    freeze_sha256: str
    c1_evidence_sha256: str
    c0_dry_run_sha256: str
    operator_service_ready: bool


class NativeCharacterizationC0AuthorizationError(RuntimeError):
    """Sanitized fail-closed error; never contains state or credential values."""


def _fail(reason: str) -> NativeCharacterizationC0AuthorizationError:
    return NativeCharacterizationC0AuthorizationError(
        f"native characterization C0 authorization denied: {reason}"
    )


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise _fail("state_not_canonicalizable") from None


def _sha_bytes(value: bytes) -> str:
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


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_unreadable") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return value


def _resolve_under(root: Path, value: str, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise _fail(f"{label}_path_invalid")
    path = root / value
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise _fail(f"{label}_path_escape") from None
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise _fail(f"{label}_path_noncanonical")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_symlink")
    if not path.is_file():
        raise _fail(f"{label}_missing")
    return path


def _validate_bindings(bindings: C0AuthorizationBindings) -> None:
    if not isinstance(bindings, C0AuthorizationBindings):
        raise _fail("bindings_invalid")
    for name in (
        "source_state_sha256",
        "workplan_sha256",
        "freeze_sha256",
        "c1_evidence_sha256",
        "c0_dry_run_sha256",
    ):
        _require_digest(getattr(bindings, name), name)
    if bindings.operator_service_ready is not True:
        raise _fail("operator_service_not_confirmed")


def _qualification_paths(
    state: Mapping[str, Any], validation_root: Path
) -> tuple[Path, Path, Path]:
    qualification = state.get("native_characterization_offline_qualification")
    if not isinstance(qualification, Mapping):
        raise _fail("offline_qualification_missing")
    freeze = qualification.get("freeze")
    c1 = qualification.get("c1_aa")
    c0 = qualification.get("c0_dry_run")
    if not all(isinstance(item, Mapping) for item in (freeze, c1, c0)):
        raise _fail("offline_evidence_binding_missing")
    assert isinstance(freeze, Mapping)
    assert isinstance(c1, Mapping)
    assert isinstance(c0, Mapping)
    return (
        _resolve_under(validation_root, str(freeze.get("path")), "freeze"),
        _resolve_under(validation_root, str(c1.get("path")), "c1_evidence"),
        _resolve_under(validation_root, str(c0.get("path")), "c0_dry_run"),
    )


def _validate_evidence(
    state: Mapping[str, Any], root: Path, bindings: C0AuthorizationBindings
) -> None:
    workplan = _resolve_under(root, WORKPLAN_RELATIVE_PATH, "workplan")
    if _sha_bytes(workplan.read_bytes()) != bindings.workplan_sha256:
        raise _fail("workplan_hash_mismatch")
    workplan_text = workplan.read_text(encoding="ascii")
    if "WORKPLAN_FREEZE=true" not in workplan_text:
        raise _fail("workplan_not_frozen")

    freeze, c1, c0 = _qualification_paths(state, root / "membind-validation")
    if _sha_bytes(freeze.read_bytes()) != bindings.freeze_sha256:
        raise _fail("freeze_hash_mismatch")
    if _sha_bytes(c1.read_bytes()) != bindings.c1_evidence_sha256:
        raise _fail("c1_evidence_hash_mismatch")
    if _sha_bytes(c0.read_bytes()) != bindings.c0_dry_run_sha256:
        raise _fail("c0_dry_run_hash_mismatch")

    qualification = state["native_characterization_offline_qualification"]
    assert isinstance(qualification, Mapping)
    expected = (
        qualification.get("schema_version")
        == "membind.native-characterization-offline-qualification.v1"
        and qualification.get("instrumentation_contract_status") == "qualified"
        and qualification.get("live_authorized") is False
        and qualification.get("workplan_id") == WORKPLAN_ID
        and qualification.get("workplan_sha256") == bindings.workplan_sha256
    )
    if not expected:
        raise _fail("offline_qualification_contract_mismatch")

    freeze_value = _read_json(freeze, "freeze")
    if not (
        freeze_value.get("schema_version")
        == "membind.native-characterization-freeze.v1"
        and freeze_value.get("protocol", {}).get("freeze_marker") is True
        and freeze_value.get("protocol", {}).get("id") == WORKPLAN_ID
    ):
        raise _fail("freeze_contract_mismatch")
    c1_value = _read_json(c1, "c1_evidence")
    if not (
        c1_value.get("schema_version")
        == "membind.native-characterization-c1-qualification.v1"
        and c1_value.get("classification") == "clean_pass"
        and c1_value.get("semantic_parity", {}).get("passed") is True
    ):
        raise _fail("c1_evidence_contract_mismatch")
    c0_value = _read_json(c0, "c0_dry_run")
    if not (
        c0_value.get("schema_version")
        == "membind.native-characterization-c0-preview.v1"
        and c0_value.get("live_request_performed") is False
    ):
        raise _fail("c0_dry_run_contract_mismatch")

    # State's own recorded identities must agree with the operator binding.
    for section, digest in (
        ("freeze", bindings.freeze_sha256),
        ("c1_aa", bindings.c1_evidence_sha256),
        ("c0_dry_run", bindings.c0_dry_run_sha256),
    ):
        value = qualification.get(section)
        if not isinstance(value, Mapping) or value.get("sha256") != digest:
            raise _fail(f"{section}_state_binding_mismatch")


def _validate_source(state: Mapping[str, Any]) -> None:
    if (
        state.get("live_h0_candidate_authorized") is not False
        or state.get("authorized_live_actions") != []
        or state.get("authorized_h0_candidate_id") is not None
        or state.get("service_admin_authorized") is not False
        or state.get("v3_smoke_003_authorized") is not False
    ):
        raise _fail("old_live_grant_present")
    expected = (
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
        and AUTHORIZATION_KEY not in state
    )
    if not expected:
        raise _fail("source_state_not_offline_only")


def _metadata(bindings: C0AuthorizationBindings) -> dict[str, Any]:
    return {
        "schema_version": "membind.native-characterization-c0-authorization.v1",
        "workplan_id": WORKPLAN_ID,
        "workplan_path": WORKPLAN_RELATIVE_PATH,
        "workplan_sha256": bindings.workplan_sha256,
        "source_state_sha256": bindings.source_state_sha256,
        "freeze_sha256": bindings.freeze_sha256,
        "c1_evidence_sha256": bindings.c1_evidence_sha256,
        "c0_dry_run_sha256": bindings.c0_dry_run_sha256,
        "live_authorized": True,
    }


def _validate_target(target: Mapping[str, Any], bindings: C0AuthorizationBindings) -> None:
    metadata = target.get(AUTHORIZATION_KEY)
    if not isinstance(metadata, Mapping):
        raise _fail("target_state_drift")
    expected = (
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
        and dict(metadata) == _metadata(
            C0AuthorizationBindings(
                source_state_sha256=metadata.get("source_state_sha256"),
                workplan_sha256=bindings.workplan_sha256,
                freeze_sha256=bindings.freeze_sha256,
                c1_evidence_sha256=bindings.c1_evidence_sha256,
                c0_dry_run_sha256=bindings.c0_dry_run_sha256,
                operator_service_ready=True,
            )
        )
    )
    if not expected:
        raise _fail("target_state_drift")


def build_native_characterization_c0_authorized_state(
    source_state: Mapping[str, Any], bindings: C0AuthorizationBindings
) -> dict[str, Any]:
    """Pure builder.  It does not mutate ``source_state`` or perform I/O."""

    _validate_bindings(bindings)
    source = deepcopy(dict(source_state))
    _safe(source)
    _validate_source(source)
    if _sha_bytes(_canonical(source)) != bindings.source_state_sha256:
        raise _fail("source_state_drift")
    target = deepcopy(source)
    target["current_action_scope"] = TARGET_SCOPE
    target["authorized_live_actions"] = [TARGET_ACTION]
    target["next_allowed_action"] = TARGET_NEXT_ACTION
    target[AUTHORIZATION_KEY] = _metadata(bindings)
    return target


@contextmanager
def _lock(path: Path):
    lock = path.parent / f".{path.name}.c0-authorization.lock"
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


def authorize_native_characterization_c0_live_only(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    bindings: C0AuthorizationBindings,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate and optionally apply the one-shot C0 live authorization.

    ``repo_root`` is the repository containing the v1.1 workplan; all evidence
    paths are read beneath it.  No live service operation occurs here.
    """

    _validate_bindings(bindings)
    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root = Path(repo_root).resolve()
    path = Path(state_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise _fail("state_path_escape") from None
    if path != root / STATE_RELATIVE_PATH:
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
        if _sha_bytes(_canonical(state)) != bindings.source_state_sha256:
            raise _fail("source_state_drift")
        if state.get("current_action_scope") == TARGET_SCOPE:
            _validate_target(state, bindings)
            _validate_evidence(state, root, bindings)
            return deepcopy(state), True
        _validate_source(state)
        _validate_evidence(state, root, bindings)
        return build_native_characterization_c0_authorized_state(state, bindings), False

    if dry_run:
        target, _ = derive()
        return target
    with _lock(path):
        target, already = derive()
        if not already:
            _atomic_write(path, target)
        return target


__all__ = [
    "C0AuthorizationBindings",
    "NativeCharacterizationC0AuthorizationError",
    "authorize_native_characterization_c0_live_only",
    "build_native_characterization_c0_authorized_state",
]
