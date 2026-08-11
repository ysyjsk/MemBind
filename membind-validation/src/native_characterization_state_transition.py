"""Deterministic offline transition into Native characterization C1 work.

The module uses local, content-addressed inputs and the Python standard library
only.  It never discovers runtime configuration or creates service clients.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


PROTOCOL_VERSION = "current-validation-v1.3"
WORKPLAN_ID = "native-characterization-v1.1"
WORKPLAN_RELATIVE_PATH = (
    "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
)
STATE_RELATIVE_PATH = "membind-validation/CURRENT_STATE.json"
CHECKPOINT_RELATIVE_PATH = (
    "membind-validation/artifacts/h0_runs/h0/checkpoints/"
    "h0-q1-b-20260810-replacement-004/index.json"
)

EXPECTED_SOURCE_STATE_SHA256 = (
    "fb57c0edb6388c2ae94c6ba338e1671c39fa08e218cfc96566ee4d315b2e231d"
)
EXPECTED_WORKPLAN_SHA256 = (
    "be3112cc2da4080ce98f9c94f1ab510ba5cc8350dca108a15e304da04c996b5b"
)
EXPECTED_CHECKPOINT_INDEX_SHA256 = (
    "6fc056401756bb8290cf216ff376de09d5053865e695e7ba4c34e15b6ac43e49"
)

SOURCE_STAGE = "H0"
SOURCE_SCOPE = "h0_q1_b_live_only"
SOURCE_NEXT_ACTION = "run_q1_h0-b-r6-replacement-004"
SOURCE_CANDIDATE_PROGRESS = "h0_b_r6_replacement_authorized_once"
TARGET_STAGE = "NATIVE_CHARACTERIZATION"
TARGET_SCOPE = "native_characterization_offline_only"
TARGET_NEXT_ACTION = "implement_c1_instrumentation_offline"
RETIRED_ATTEMPT_ID = "h0-q1-b-20260810-replacement-004"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_FORBIDDEN_STATE_KEYS = {
    "api_key",
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
_TRANSITION_FIELDS = {
    "schema_version",
    "workplan_id",
    "workplan_path",
    "workplan_sha256",
    "source_state_sha256",
    "source_stage",
    "source_status",
    "source_action_scope",
    "retired_stage_attempt_id",
    "interruption_checkpoint_path",
    "interruption_checkpoint_sha256",
    "live_authorized",
}


class NativeCharacterizationStateTransitionError(RuntimeError):
    """Sanitized failure that cannot authorize or perform live work."""


def _fail(reason: str) -> NativeCharacterizationStateTransitionError:
    return NativeCharacterizationStateTransitionError(
        f"native characterization state transition denied: {reason}"
    )


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise _fail("state_not_canonicalizable") from None


def _sha256_bytes(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label}_not_object")
    return value


def _assert_safe_state(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _FORBIDDEN_STATE_KEYS:
                raise _fail("unsafe_source_state")
            _assert_safe_state(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_safe_state(child)
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or ".env" in lowered or "gpt55_temporary" in lowered:
            raise _fail("unsafe_source_state")


def _validate_source_shape(state: Mapping[str, Any]) -> Mapping[str, Any]:
    progress = state.get("stage_progress")
    authorization = state.get("live_h0_authorization")
    exact = (
        state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("current_stage") == SOURCE_STAGE
        and state.get("status") == SOURCE_SCOPE
        and state.get("current_action_scope") == SOURCE_SCOPE
        and state.get("current_blocker") is None
        and state.get("next_allowed_action") == SOURCE_NEXT_ACTION
        and state.get("live_h0_candidate_authorized") is True
        and state.get("authorized_live_actions") == ["h0_candidate"]
        and state.get("authorized_h0_candidate_id") == "Q1"
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == SOURCE_SCOPE
        and progress.get("h0_candidate_progression")
        == SOURCE_CANDIDATE_PROGRESS
        and isinstance(authorization, Mapping)
        and authorization.get("candidate_id") == "Q1"
        and authorization.get("phase") == "H0-B"
        and authorization.get("authorized_stage_attempt_id")
        == RETIRED_ATTEMPT_ID
        and "historical_h0_live_authorization" not in state
        and "native_characterization_transition" not in state
    )
    if not exact:
        raise _fail("source_state_not_exact_r6_live_grant")
    return authorization


def _transition_metadata(
    *,
    source_state_sha256: str,
    workplan_sha256: str,
    checkpoint_index_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "membind.native-characterization-transition.v1",
        "workplan_id": WORKPLAN_ID,
        "workplan_path": WORKPLAN_RELATIVE_PATH,
        "workplan_sha256": workplan_sha256,
        "source_state_sha256": source_state_sha256,
        "source_stage": SOURCE_STAGE,
        "source_status": SOURCE_SCOPE,
        "source_action_scope": SOURCE_SCOPE,
        "retired_stage_attempt_id": RETIRED_ATTEMPT_ID,
        "interruption_checkpoint_path": CHECKPOINT_RELATIVE_PATH,
        "interruption_checkpoint_sha256": checkpoint_index_sha256,
        "live_authorized": False,
    }


def build_native_characterization_offline_state(
    source_state: Mapping[str, Any],
    *,
    source_state_sha256: str,
    workplan_sha256: str,
    checkpoint_index_sha256: str,
) -> dict[str, Any]:
    """Purely replace one exact R6 grant with a deterministic offline state."""

    source = _require_mapping(source_state, "source_state")
    source_digest = _require_sha256(source_state_sha256, "source_state_sha256")
    workplan_digest = _require_sha256(workplan_sha256, "workplan_sha256")
    checkpoint_digest = _require_sha256(
        checkpoint_index_sha256, "checkpoint_index_sha256"
    )
    _assert_safe_state(source)
    if _sha256_bytes(_canonical_json_bytes(source)) != source_digest:
        raise _fail("source_state_drift")
    authorization = _validate_source_shape(source)

    target = deepcopy(dict(source))
    target["historical_h0_live_authorization"] = deepcopy(dict(authorization))
    target.pop("live_h0_authorization", None)
    progress = deepcopy(dict(_require_mapping(target.get("stage_progress"), "progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden_native_characterization",
            "h0_candidate_progression": "frozen_historical_no_rerun",
            "native_characterization": "c1_instrumentation_tdd_pending",
        }
    )
    target.update(
        {
            "current_stage": TARGET_STAGE,
            "status": TARGET_SCOPE,
            "current_action_scope": TARGET_SCOPE,
            "current_blocker": None,
            "next_allowed_action": TARGET_NEXT_ACTION,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": progress,
            "native_characterization_transition": _transition_metadata(
                source_state_sha256=source_digest,
                workplan_sha256=workplan_digest,
                checkpoint_index_sha256=checkpoint_digest,
            ),
        }
    )
    return target


def _reconstruct_source_from_target(
    target: Mapping[str, Any],
    *,
    source_state_sha256: str,
    workplan_sha256: str,
    checkpoint_index_sha256: str,
) -> dict[str, Any]:
    _assert_safe_state(target)
    metadata = target.get("native_characterization_transition")
    history = target.get("historical_h0_live_authorization")
    progress = target.get("stage_progress")
    expected_metadata = _transition_metadata(
        source_state_sha256=source_state_sha256,
        workplan_sha256=workplan_sha256,
        checkpoint_index_sha256=checkpoint_index_sha256,
    )
    exact = (
        target.get("protocol_version") == PROTOCOL_VERSION
        and target.get("current_stage") == TARGET_STAGE
        and target.get("status") == TARGET_SCOPE
        and target.get("current_action_scope") == TARGET_SCOPE
        and target.get("current_blocker") is None
        and target.get("next_allowed_action") == TARGET_NEXT_ACTION
        and target.get("live_h0_candidate_authorized") is False
        and target.get("authorized_live_actions") == []
        and target.get("authorized_h0_candidate_id") is None
        and target.get("service_admin_authorized") is False
        and target.get("v3_smoke_003_authorized") is False
        and "live_h0_authorization" not in target
        and isinstance(history, Mapping)
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == "forbidden_native_characterization"
        and progress.get("h0_candidate_progression")
        == "frozen_historical_no_rerun"
        and progress.get("native_characterization")
        == "c1_instrumentation_tdd_pending"
        and isinstance(metadata, Mapping)
        and set(metadata) == _TRANSITION_FIELDS
        and dict(metadata) == expected_metadata
    )
    if not exact:
        raise _fail("target_state_drift")

    source = deepcopy(dict(target))
    source.pop("native_characterization_transition", None)
    source["live_h0_authorization"] = deepcopy(
        dict(source.pop("historical_h0_live_authorization"))
    )
    source_progress = deepcopy(
        dict(_require_mapping(source.get("stage_progress"), "progress"))
    )
    source_progress.pop("native_characterization", None)
    source_progress["h0_live_gate"] = SOURCE_SCOPE
    source_progress["h0_candidate_progression"] = SOURCE_CANDIDATE_PROGRESS
    source.update(
        {
            "current_stage": SOURCE_STAGE,
            "status": SOURCE_SCOPE,
            "current_action_scope": SOURCE_SCOPE,
            "current_blocker": None,
            "next_allowed_action": SOURCE_NEXT_ACTION,
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": source_progress,
        }
    )
    if _sha256_bytes(_canonical_json_bytes(source)) != source_state_sha256:
        raise _fail("target_state_drift")
    _validate_source_shape(source)
    return source


def _derive_or_validate_target(
    state: Mapping[str, Any],
    *,
    source_state_sha256: str,
    workplan_sha256: str,
    checkpoint_index_sha256: str,
) -> tuple[dict[str, Any], bool]:
    looks_like_target = (
        state.get("current_stage") == TARGET_STAGE
        or state.get("status") == TARGET_SCOPE
        or "native_characterization_transition" in state
    )
    if not looks_like_target:
        return (
            build_native_characterization_offline_state(
                state,
                source_state_sha256=source_state_sha256,
                workplan_sha256=workplan_sha256,
                checkpoint_index_sha256=checkpoint_index_sha256,
            ),
            False,
        )

    source = _reconstruct_source_from_target(
        state,
        source_state_sha256=source_state_sha256,
        workplan_sha256=workplan_sha256,
        checkpoint_index_sha256=checkpoint_index_sha256,
    )
    confirmed = build_native_characterization_offline_state(
        source,
        source_state_sha256=source_state_sha256,
        workplan_sha256=workplan_sha256,
        checkpoint_index_sha256=checkpoint_index_sha256,
    )
    if _canonical_json_bytes(confirmed) != _canonical_json_bytes(state):
        raise _fail("target_state_drift")
    return deepcopy(dict(state)), True


def _resolve_file_under_root(root: Path, value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        lexical = path.relative_to(root)
    except ValueError:
        raise _fail(f"{label}_path_escapes_root") from None
    if any(part in {"", ".", ".."} for part in lexical.parts):
        raise _fail(f"{label}_path_noncanonical")
    cursor = root
    for part in lexical.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_path_symlink_forbidden")
    resolved = path.resolve()
    try:
        resolved_relative = resolved.relative_to(root)
    except ValueError:
        raise _fail(f"{label}_path_escapes_root") from None
    if resolved_relative != lexical or not resolved.is_file():
        raise _fail(f"{label}_path_missing_or_noncanonical")
    return resolved


def _read_canonical_state(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        encoded = path.read_bytes()
        value = json.loads(encoded.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("state_file_unreadable") from None
    if not isinstance(value, dict) or encoded != _canonical_json_bytes(value):
        raise _fail("state_file_not_canonical")
    return encoded, value


def _validate_local_evidence(root: Path) -> None:
    workplan = _resolve_file_under_root(root, WORKPLAN_RELATIVE_PATH, "workplan")
    try:
        workplan_bytes = workplan.read_bytes()
        workplan_text = workplan_bytes.decode("utf-8")
    except (OSError, UnicodeError):
        raise _fail("workplan_unreadable") from None
    if _sha256_bytes(workplan_bytes) != EXPECTED_WORKPLAN_SHA256:
        raise _fail("workplan_hash_mismatch")
    required_markers = (
        "# MemBind Native Graphiti Construction Characterization Workplan v1.1",
        "`native-characterization-v1.1`",
        "WORKPLAN_FREEZE=true",
    )
    if any(marker not in workplan_text for marker in required_markers):
        raise _fail("workplan_identity_mismatch")

    checkpoint = _resolve_file_under_root(
        root, CHECKPOINT_RELATIVE_PATH, "checkpoint"
    )
    try:
        checkpoint_bytes = checkpoint.read_bytes()
        checkpoint_value = json.loads(checkpoint_bytes.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("checkpoint_unreadable") from None
    if _sha256_bytes(checkpoint_bytes) != EXPECTED_CHECKPOINT_INDEX_SHA256:
        raise _fail("checkpoint_hash_mismatch")
    # This is immutable historical evidence whose exact bytes are already
    # content-addressed above.  Accept its original pretty-printed JSON form;
    # canonical-byte enforcement applies to the mutable authority state only.
    if not isinstance(checkpoint_value, dict):
        raise _fail("checkpoint_not_object")
    exact_checkpoint = (
        checkpoint_value.get("schema_version")
        == "membind.h0.checkpoint-index.v1"
        and checkpoint_value.get("protocol_version") == PROTOCOL_VERSION
        and checkpoint_value.get("stage_attempt_id") == RETIRED_ATTEMPT_ID
        and checkpoint_value.get("candidate_id") == "Q1"
        and checkpoint_value.get("phase") == "H0-B"
        and checkpoint_value.get("status") == "infrastructure_interrupted"
        and checkpoint_value.get("stop_reason") == "vllm_unreachable"
    )
    if not exact_checkpoint:
        raise _fail("checkpoint_identity_mismatch")


@contextmanager
def _state_transition_lock(path: Path):
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
    encoded = _canonical_json_bytes(state)
    try:
        mode = path.stat().st_mode & 0o777
        # Keep the old inode linked until the replacement has been fsynced.
        # This gives us a bounded rollback path if the durability acknowledgement
        # fails after os.replace(), rather than reporting failure with new state
        # already visible.
        backup_fd, backup_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".bak",
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
                # Preserve the original failure classification.  The caller
                # still fails closed if the filesystem cannot roll back.
                pass
        raise _fail("atomic_write_failed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if "backup" in locals() and backup is not None:
            backup.unlink(missing_ok=True)


def _main() -> int:
    """Small offline CLI; dry-run is the default and never opens a client."""

    module_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Retire the historical H0 grant for offline characterization"
    )
    parser.add_argument("--repo-root", type=Path, default=module_root.parent)
    parser.add_argument(
        "--state",
        type=Path,
        default=module_root / "CURRENT_STATE.json",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="preview only (default)")
    mode.add_argument("--apply", action="store_true", help="atomically persist")
    args = parser.parse_args()
    try:
        result = transition_native_characterization_offline(
            args.state,
            repo_root=args.repo_root,
            dry_run=not args.apply,
        )
    except NativeCharacterizationStateTransitionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


def transition_native_characterization_offline(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate frozen local inputs and optionally atomically retire the grant."""

    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise _fail("repo_root_invalid")
    target = _resolve_file_under_root(root, state_path, "state")

    def derive() -> tuple[bytes, dict[str, Any], bool]:
        _validate_local_evidence(root)
        initial_bytes, state = _read_canonical_state(target)
        derived, already_transitioned = _derive_or_validate_target(
            state,
            source_state_sha256=EXPECTED_SOURCE_STATE_SHA256,
            workplan_sha256=EXPECTED_WORKPLAN_SHA256,
            checkpoint_index_sha256=EXPECTED_CHECKPOINT_INDEX_SHA256,
        )
        return initial_bytes, derived, already_transitioned

    if dry_run:
        return derive()[1]

    with _state_transition_lock(target):
        initial_bytes, derived, already_transitioned = derive()
        if already_transitioned:
            return derived
        _validate_local_evidence(root)
        current_bytes, current_state = _read_canonical_state(target)
        if current_bytes != initial_bytes:
            raise _fail("state_changed_before_commit")
        confirmed, confirmed_is_target = _derive_or_validate_target(
            current_state,
            source_state_sha256=EXPECTED_SOURCE_STATE_SHA256,
            workplan_sha256=EXPECTED_WORKPLAN_SHA256,
            checkpoint_index_sha256=EXPECTED_CHECKPOINT_INDEX_SHA256,
        )
        if confirmed_is_target or _canonical_json_bytes(confirmed) != _canonical_json_bytes(
            derived
        ):
            raise _fail("state_changed_before_commit")
        _atomic_write(target, derived)
    return derived
