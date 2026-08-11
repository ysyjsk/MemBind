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


PROTOCOL_VERSION = "current-validation-v1.3"
STATE_RELATIVE_PATH = "membind-validation/CURRENT_STATE.json"
FREEZE_RELATIVE_PATH = "artifacts/native_characterization/freeze.json"
FAILED_C2_ATTEMPT_ID = "c2-efb58c477f12adf6"
POLLUTED_C2_GROUP_ID = "nc-e1e2-400b9b78c2c218df"
SOURCE_STAGE = "NATIVE_CHARACTERIZATION"
SOURCE_STATUS = "native_characterization_offline_only"
SOURCE_SCOPE = "native_characterization_offline_only"
SOURCE_BLOCKER = "c2_polluted_namespace_cleanup_pending"
SOURCE_NEXT_ACTION = "implement_scoped_c2_cleanup_offline"
SOURCE_PROGRESS = "c0_c1_pass_c2_failed_attempt_invalid_cleanup_tdd_pending"
TARGET_SCOPE = "native_characterization_c2_live_only"
TARGET_ACTION = "native_characterization_c2"
TARGET_NEXT_ACTION = "run_native_characterization_c2"
METADATA_KEY = "native_characterization_c2_reauthorization"

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
    ):
        _require_digest(getattr(bindings, name), name)
    if (
        not isinstance(bindings.final_full_regression_test_count, int)
        or isinstance(bindings.final_full_regression_test_count, bool)
        or bindings.final_full_regression_test_count <= 0
    ):
        raise _fail("final_full_regression_test_count_invalid")


def _validate_source(state: Mapping[str, Any]) -> None:
    progress = state.get("stage_progress")
    prior = state.get("native_characterization_c2_authorization")
    exact = (
        state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("current_stage") == SOURCE_STAGE
        and state.get("status") == SOURCE_STATUS
        and state.get("current_blocker") == SOURCE_BLOCKER
        and state.get("current_action_scope") == SOURCE_SCOPE
        and state.get("authorized_live_actions") == []
        and state.get("next_allowed_action") == SOURCE_NEXT_ACTION
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and isinstance(progress, Mapping)
        and progress.get("native_characterization") == SOURCE_PROGRESS
        and isinstance(prior, Mapping)
        and prior.get("schema_version")
        == "membind.native-characterization-c2-authorization.v1"
        and prior.get("live_authorized") is True
        and prior.get("workplan_id") == "native-characterization-v1.1"
        and METADATA_KEY not in state
    )
    if not exact:
        raise _fail("source_state_not_cleanup_pending")


def _nonnegative_count(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _fail(f"{label}_invalid")
    return value


def _validate_freeze(
    validation: Path,
    cleanup: Mapping[str, Any],
    state: Mapping[str, Any],
) -> str:
    freeze = _resolve_under(validation, FREEZE_RELATIVE_PATH, "freeze")
    freeze_sha256 = _sha(freeze.read_bytes())
    if cleanup.get("freeze_sha256") != freeze_sha256:
        raise _fail("freeze_hash_mismatch")
    prior = state["native_characterization_c2_authorization"]
    assert isinstance(prior, Mapping)
    if prior.get("freeze_sha256") != freeze_sha256:
        raise _fail("prior_authorization_freeze_mismatch")
    value = _read_json(freeze, "freeze")
    block_order = value.get("screening", {}).get("e1_e2", {}).get("block_order")
    exact = (
        value.get("schema_version")
        == "membind.native-characterization-freeze.v1"
        and isinstance(block_order, list)
        and len(block_order) == 4
        and isinstance(block_order[0], Mapping)
        and block_order[0].get("block_index") == 0
        and block_order[0].get("graph_namespace") == POLLUTED_C2_GROUP_ID
    )
    if not exact:
        raise _fail("freeze_polluted_group_mismatch")
    return freeze_sha256


def _validate_cleanup(
    validation: Path,
    state: Mapping[str, Any],
    bindings: C2ReauthorizationBindings,
) -> tuple[dict[str, Any], str]:
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
    prior = state["native_characterization_c2_authorization"]
    assert isinstance(prior, Mapping)
    if cleanup_freeze_sha256 != prior.get("freeze_sha256"):
        raise _fail("cleanup_freeze_mismatch")
    if value.get("cleanup_primitive") != (
        "graphiti.clear_data(driver,group_ids=[target_group])"
    ):
        raise _fail("cleanup_primitive_mismatch")
    failed_exact = (
        value.get("failed_attempt_id") == FAILED_C2_ATTEMPT_ID
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

    freeze_sha256 = _validate_freeze(validation, value, state)
    if value.get("freeze_sha256") != freeze_sha256:
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


def _metadata(
    bindings: C2ReauthorizationBindings,
    cleanup: Mapping[str, Any],
    cleanup_payload_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "membind.native-characterization-c2-reauthorization.v1",
        "source_state_sha256": bindings.source_state_sha256,
        "failed_attempt_id": FAILED_C2_ATTEMPT_ID,
        "failed_attempt_mergeable": False,
        "replacement_resume_allowed": False,
        "replacement_start_source_sequence": 0,
        "polluted_group_id": POLLUTED_C2_GROUP_ID,
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
    target["current_blocker"] = None
    target["current_action_scope"] = TARGET_SCOPE
    target["authorized_live_actions"] = [TARGET_ACTION]
    target["next_allowed_action"] = TARGET_NEXT_ACTION
    progress = deepcopy(dict(target["stage_progress"]))
    progress["native_characterization"] = (
        "c0_c1_pass_c2_replacement_authorized_from_episode_0"
    )
    target["stage_progress"] = progress
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
        cleanup, cleanup_payload_sha256 = _validate_cleanup(
            validation, source, bindings
        )
        _validate_regression(validation, bindings)
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
    "C2ReauthorizationBindings",
    "NativeCharacterizationC2ReauthorizationError",
    "build_native_characterization_c2_reauthorized_state",
    "reauthorize_native_characterization_c2_live_only",
]
