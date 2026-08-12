"""Fail-closed offline transition into the frozen C4/E3 live lane.

The module only validates local, content-addressed evidence and updates the
authorization state. It never opens model, embedding, database, or SSH clients.
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
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


PROTOCOL_VERSION = "current-validation-v1.3"
STATE_RELATIVE_PATH = "membind-validation/CURRENT_STATE.json"
SOURCE_SCOPE = "native_characterization_c4_offline_only"
TARGET_SCOPE = "native_characterization_c4_live_only"
SOURCE_NEXT_ACTION = "build_native_characterization_e3_harness_offline"
TARGET_NEXT_ACTION = "run_native_characterization_c4"
SOURCE_PROGRESS = "c2_c3_complete_c4_offline_tdd_pending"
TARGET_ACTION = "native_characterization_c4"
AUTHORIZATION_KEY = "native_characterization_c4_authorization"
FREEZE_PATH = "artifacts/native_characterization/freeze_reference_aligned_64k.json"

_RUN_ID_RE = re.compile(r"^c2-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOADS = [0.5, 0.8, 1.0, 1.2, 1.5]
_METHODS = ["Native-Sync"] * 5 + ["Native-Async-Serial"] * 5
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
class C4AuthorizationBindings:
    """Exact local inputs to the one-shot C4 live authorization."""

    source_state_sha256: str
    schedule_relative_path: str
    schedule_sha256: str
    schedule_payload_sha256: str
    freeze_relative_path: str
    freeze_sha256: str
    freeze_payload_sha256: str
    c4_source_relative_path: str
    c4_source_sha256: str
    c4_test_relative_path: str
    c4_test_sha256: str
    c4_green_log_relative_path: str
    c4_green_log_sha256: str
    c4_focused_test_count: int
    operator_authorized: bool


class NativeCharacterizationC4AuthorizationError(RuntimeError):
    """Sanitized authorization failure without state or credential contents."""


def _fail(reason: str) -> NativeCharacterizationC4AuthorizationError:
    return NativeCharacterizationC4AuthorizationError(
        f"native characterization C4 authorization denied: {reason}"
    )


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise _fail("value_not_canonicalizable") from None


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


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


def _read_object(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_unreadable") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return raw, value


def _validate_seal(value: Mapping[str, Any], expected: str, label: str) -> None:
    candidate = deepcopy(dict(value))
    observed = candidate.pop("payload_sha256", None)
    if observed != expected or observed != _sha(_canonical(candidate)):
        raise _fail(f"{label}_payload_mismatch")


def _read_bound_object(
    validation: Path,
    relative: str,
    expected_sha: str,
    expected_payload: str,
    label: str,
    *,
    require_canonical: bool = True,
) -> dict[str, Any]:
    path = _resolve_existing(validation, relative, label)
    raw, value = _read_object(path, label)
    if _sha(raw) != expected_sha:
        raise _fail(f"{label}_hash_mismatch")
    if require_canonical and raw not in {_canonical(value), _canonical(value) + b"\n"}:
        raise _fail(f"{label}_not_canonical")
    _validate_seal(value, expected_payload, label)
    return value


def _validate_bindings(bindings: C4AuthorizationBindings) -> None:
    if not isinstance(bindings, C4AuthorizationBindings):
        raise _fail("bindings_invalid")
    for label in (
        "source_state_sha256",
        "schedule_sha256",
        "schedule_payload_sha256",
        "freeze_sha256",
        "freeze_payload_sha256",
        "c4_source_sha256",
        "c4_test_sha256",
        "c4_green_log_sha256",
    ):
        _require_sha(getattr(bindings, label), label)
    if bindings.freeze_relative_path != FREEZE_PATH:
        raise _fail("freeze_path_invalid")
    for label in (
        "schedule_relative_path",
        "c4_source_relative_path",
        "c4_test_relative_path",
        "c4_green_log_relative_path",
    ):
        _safe_relative(getattr(bindings, label), label)
    if not bindings.schedule_relative_path.startswith("artifacts/diagnostics/"):
        raise _fail("schedule_path_invalid")
    if bindings.c4_source_relative_path != "src/native_characterization_c4.py":
        raise _fail("c4_source_path_invalid")
    if bindings.c4_test_relative_path != "tests/test_native_characterization_c4.py":
        raise _fail("c4_test_path_invalid")
    if not bindings.c4_green_log_relative_path.startswith("artifacts/tdd/"):
        raise _fail("c4_green_log_path_invalid")
    if (
        not isinstance(bindings.c4_focused_test_count, int)
        or isinstance(bindings.c4_focused_test_count, bool)
        or bindings.c4_focused_test_count <= 0
    ):
        raise _fail("c4_focused_test_count_invalid")
    if bindings.operator_authorized is not True:
        raise _fail("operator_authorization_required")


def _validate_source(state: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    c2 = state.get("native_characterization_c2_completion")
    c3 = state.get("native_characterization_c3_completion")
    exact = (
        state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("status") == SOURCE_SCOPE
        and state.get("current_action_scope") == SOURCE_SCOPE
        and state.get("current_blocker") is None
        and state.get("next_allowed_action") == SOURCE_NEXT_ACTION
        and state.get("authorized_live_actions") == []
        and state.get("native_characterization_live_authorized") is False
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and state.get("stage_progress", {}).get("native_characterization")
        == SOURCE_PROGRESS
        and AUTHORIZATION_KEY not in state
        and isinstance(c2, Mapping)
        and c2.get("schema_version")
        == "membind.native-characterization-c2-completion.v1"
        and c2.get("status") == "verified"
        and c2.get("grant_consumed") is True
        and c2.get("live_authorized") is False
        and isinstance(c3, Mapping)
        and c3.get("schema_version")
        == "membind.native-characterization-c3-completion.v1"
        and c3.get("status") == "complete"
        and c3.get("live_authorized") is False
        and c3.get("run_id") == c2.get("run_id")
        and isinstance(c2.get("run_id"), str)
        and _RUN_ID_RE.fullmatch(str(c2.get("run_id"))) is not None
    )
    if not exact:
        raise _fail("source_state_not_exact_c4_offline")
    return c2, c3


def _validate_freeze(
    validation: Path, bindings: C4AuthorizationBindings
) -> dict[str, Any]:
    freeze = _read_bound_object(
        validation,
        bindings.freeze_relative_path,
        bindings.freeze_sha256,
        bindings.freeze_payload_sha256,
        "freeze",
    )
    runtime = freeze.get("runtime_identities")
    construction = runtime.get("construction") if isinstance(runtime, Mapping) else None
    transition = freeze.get("state_transition")
    exact = (
        freeze.get("schema_version") == "membind.native-characterization-freeze.v1"
        and freeze.get("run_id")
        == "native-characterization-freeze-reference-aligned-64k"
        and isinstance(construction, Mapping)
        and construction.get("vllm_version") == "0.26.0"
        and construction.get("served_model_id") == "qwen3-32b-fp8"
        and construction.get("max_model_len") == 65536
        and construction.get("rope_type") == "yarn"
        and construction.get("yarn_factor") == 2.0
        and construction.get("original_max_position_embeddings") == 32768
        and construction.get("rope_theta") == 1000000
        and isinstance(transition, Mapping)
        and transition.get("execution_envelope_updated") is True
        and transition.get("live_authorized") is False
    )
    if not exact:
        raise _fail("freeze_64k_contract_mismatch")
    return freeze


def _validate_c2(
    validation: Path,
    c2: Mapping[str, Any],
    freeze_sha256: str,
) -> dict[str, Any]:
    required_hashes = (
        "verification_sha256",
        "verification_payload_sha256",
        "manifest_sha256",
        "checkpoint_sha256",
        "e1_breakdown_sha256",
        "top_level_e1_breakdown_sha256",
    )
    for label in required_hashes:
        _require_sha(c2.get(label), f"c2_{label}")
    if c2.get("freeze_sha256") != freeze_sha256:
        raise _fail("c2_freeze_binding_mismatch")
    verification = _read_bound_object(
        validation,
        str(c2.get("verification_path", "")),
        str(c2["verification_sha256"]),
        str(c2["verification_payload_sha256"]),
        "c2_verification",
    )
    result = verification.get("result")
    exact = (
        verification.get("schema_version")
        == "membind.native-characterization-c2-verification-evidence.v1"
        and verification.get("status") == "verified"
        and verification.get("run_id") == c2.get("run_id")
        and isinstance(result, Mapping)
        and result.get("status") == "verified"
        and result.get("run_id") == c2.get("run_id")
        and result.get("manifest_sha256") == c2.get("manifest_sha256")
        and result.get("checkpoint_sha256") == c2.get("checkpoint_sha256")
        and result.get("e1_breakdown_sha256") == c2.get("e1_breakdown_sha256")
        and result.get("top_level_e1_breakdown_sha256")
        == c2.get("top_level_e1_breakdown_sha256")
    )
    if not exact:
        raise _fail("c2_verification_contract_mismatch")
    return {
        "status": "verified",
        "run_id": c2["run_id"],
        "verification_path": c2["verification_path"],
        "verification_sha256": c2["verification_sha256"],
        "verification_payload_sha256": c2["verification_payload_sha256"],
        "manifest_sha256": c2["manifest_sha256"],
        "checkpoint_sha256": c2["checkpoint_sha256"],
        "e1_breakdown_sha256": c2["e1_breakdown_sha256"],
        "top_level_e1_breakdown_sha256": c2[
            "top_level_e1_breakdown_sha256"
        ],
    }


def _validate_c3(
    validation: Path, c3: Mapping[str, Any], run_id: str
) -> dict[str, Any]:
    artifact_specs = (
        ("dependency_map", "dependency_map_path", "dependency_map_sha256", "dependency_map_payload_sha256"),
        ("e2", "e2_path", "e2_sha256", "e2_payload_sha256"),
    )
    for label, path_key, sha_key, payload_key in artifact_specs:
        _require_sha(c3.get(sha_key), f"c3_{sha_key}")
        _require_sha(c3.get(payload_key), f"c3_{payload_key}")
        artifact = _read_bound_object(
            validation,
            str(c3.get(path_key, "")),
            str(c3[sha_key]),
            str(c3[payload_key]),
            f"c3_{label}",
        )
        if (
            artifact.get("status") != "complete"
            or artifact.get("stage") != "C3/E2"
            or artifact.get("run_id") != run_id
        ):
            raise _fail(f"c3_{label}_contract_mismatch")

    analyzer = _resolve_existing(
        validation, str(c3.get("analyzer_source_path", "")), "c3_analyzer_source"
    )
    if _sha(analyzer.read_bytes()) != c3.get("analyzer_source_sha256"):
        raise _fail("c3_analyzer_source_hash_mismatch")
    log = _resolve_existing(
        validation, str(c3.get("focused_log_path", "")), "c3_focused_log"
    )
    try:
        log_raw = log.read_bytes()
        log_text = log_raw.decode("ascii")
    except (OSError, UnicodeError):
        raise _fail("c3_focused_log_unreadable") from None
    if _sha(log_raw) != c3.get("focused_log_sha256"):
        raise _fail("c3_focused_log_hash_mismatch")
    if "OK" not in log_text or "FAILED" in log_text or "ERROR" in log_text:
        raise _fail("c3_focused_log_not_green")
    if not isinstance(c3.get("focused_test_count"), int) or c3.get("focused_test_count", 0) <= 0:
        raise _fail("c3_focused_test_count_invalid")
    return {
        "schema_version": c3["schema_version"],
        "status": "complete",
        "run_id": run_id,
        "dependency_map_path": c3["dependency_map_path"],
        "dependency_map_sha256": c3["dependency_map_sha256"],
        "dependency_map_payload_sha256": c3["dependency_map_payload_sha256"],
        "e2_path": c3["e2_path"],
        "e2_sha256": c3["e2_sha256"],
        "e2_payload_sha256": c3["e2_payload_sha256"],
        "analyzer_source_sha256": c3["analyzer_source_sha256"],
        "focused_log_sha256": c3["focused_log_sha256"],
        "focused_test_count": c3["focused_test_count"],
    }


def _validate_schedule(
    validation: Path,
    bindings: C4AuthorizationBindings,
    c2_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    schedule = _read_bound_object(
        validation,
        bindings.schedule_relative_path,
        bindings.schedule_sha256,
        bindings.schedule_payload_sha256,
        "schedule",
        require_canonical=False,
    )
    run_id = c2_evidence["run_id"]
    episode_ids = schedule.get("episode_ids")
    loads = schedule.get("load_schedules")
    blocks = schedule.get("block_schedules")
    provenance = schedule.get("provenance")
    schedule_c2 = provenance.get("c2_verification") if isinstance(provenance, Mapping) else None
    exact_c2 = isinstance(schedule_c2, Mapping) and all(
        schedule_c2.get(key) == c2_evidence.get(key)
        for key in (
            "status",
            "run_id",
            "manifest_sha256",
            "checkpoint_sha256",
            "e1_breakdown_sha256",
            "top_level_e1_breakdown_sha256",
        )
    )
    exact = (
        schedule.get("schema_version")
        == "membind.native-characterization-c4-schedule-dry-run.v1"
        and schedule.get("status") == "dry_run"
        and schedule.get("stage") == "C4/E3_OFFLINE_SCHEDULE"
        and schedule.get("run_id") == run_id
        and schedule.get("history_id") == "07741c45"
        and schedule.get("schedule_semantics")
        == "controlled_deterministic_absolute_open_loop_replay"
        and isinstance(episode_ids, list)
        and len(episode_ids) == 49
        and len(set(episode_ids)) == 49
        and isinstance(loads, list)
        and [item.get("normalized_offered_load") for item in loads if isinstance(item, Mapping)]
        == _LOADS
        and isinstance(blocks, list)
        and len(blocks) == 10
        and [item.get("block_index") for item in blocks if isinstance(item, Mapping)]
        == list(range(10))
        and [item.get("method") for item in blocks if isinstance(item, Mapping)]
        == _METHODS
        and [item.get("normalized_offered_load") for item in blocks if isinstance(item, Mapping)]
        == _LOADS * 2
        and len(
            {
                item.get("graph_namespace")
                for item in blocks
                if isinstance(item, Mapping)
            }
        )
        == 10
        and isinstance(provenance, Mapping)
        and provenance.get("freeze_path") == bindings.freeze_relative_path
        and provenance.get("freeze_sha256") == bindings.freeze_sha256
        and provenance.get("freeze_payload_sha256")
        == bindings.freeze_payload_sha256
        and exact_c2
    )
    if not exact:
        raise _fail("schedule_contract_mismatch")
    for load in loads:
        if not isinstance(load, Mapping):
            raise _fail("schedule_contract_mismatch")
        interval = load.get("interarrival_ns")
        offsets = load.get("absolute_arrival_offsets_ns")
        if (
            not isinstance(interval, int)
            or isinstance(interval, bool)
            or interval <= 0
            or offsets != [index * interval for index in range(49)]
        ):
            raise _fail("schedule_contract_mismatch")
    return schedule


def _validate_c4_code(
    validation: Path, bindings: C4AuthorizationBindings
) -> None:
    for label, relative, expected in (
        ("c4_source", bindings.c4_source_relative_path, bindings.c4_source_sha256),
        ("c4_test", bindings.c4_test_relative_path, bindings.c4_test_sha256),
    ):
        path = _resolve_existing(validation, relative, label)
        if _sha(path.read_bytes()) != expected:
            raise _fail(f"{label}_hash_mismatch")
    log = _resolve_existing(
        validation, bindings.c4_green_log_relative_path, "c4_green_log"
    )
    try:
        raw = log.read_bytes()
        text = raw.decode("ascii")
    except (OSError, UnicodeError):
        raise _fail("c4_green_log_unreadable") from None
    if _sha(raw) != bindings.c4_green_log_sha256:
        raise _fail("c4_green_log_hash_mismatch")
    if "OK" not in text or "FAILED" in text or "ERROR" in text:
        raise _fail("c4_green_log_not_green")


def _metadata(
    bindings: C4AuthorizationBindings,
    c2_evidence: Mapping[str, Any],
    c3_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "membind.native-characterization-c4-authorization.v1",
        "source_state_sha256": bindings.source_state_sha256,
        "schedule_path": bindings.schedule_relative_path,
        "schedule_sha256": bindings.schedule_sha256,
        "schedule_payload_sha256": bindings.schedule_payload_sha256,
        "freeze_path": bindings.freeze_relative_path,
        "freeze_sha256": bindings.freeze_sha256,
        "freeze_payload_sha256": bindings.freeze_payload_sha256,
        "c4_source_path": bindings.c4_source_relative_path,
        "c4_source_sha256": bindings.c4_source_sha256,
        "c4_test_path": bindings.c4_test_relative_path,
        "c4_test_sha256": bindings.c4_test_sha256,
        "c4_green_log_path": bindings.c4_green_log_relative_path,
        "c4_green_log_sha256": bindings.c4_green_log_sha256,
        "c4_focused_test_count": bindings.c4_focused_test_count,
        "c2_evidence": deepcopy(dict(c2_evidence)),
        "c3_evidence": deepcopy(dict(c3_evidence)),
        "operator_authorization_input": True,
        "live_authorized": True,
    }


def _build_target(
    source: Mapping[str, Any],
    bindings: C4AuthorizationBindings,
    c2_evidence: Mapping[str, Any],
    c3_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    target = deepcopy(dict(source))
    target.update(
        {
            "current_action_scope": TARGET_SCOPE,
            "next_allowed_action": TARGET_NEXT_ACTION,
            "authorized_live_actions": [TARGET_ACTION],
            "native_characterization_live_authorized": True,
            AUTHORIZATION_KEY: _metadata(bindings, c2_evidence, c3_evidence),
        }
    )
    return target


def _validate_target(
    target: Mapping[str, Any],
    bindings: C4AuthorizationBindings,
    c2_evidence: Mapping[str, Any],
    c3_evidence: Mapping[str, Any],
) -> None:
    metadata = target.get(AUTHORIZATION_KEY)
    if not isinstance(metadata, Mapping):
        raise _fail("target_state_drift")
    original_source_sha = metadata.get("source_state_sha256")
    if not isinstance(original_source_sha, str):
        raise _fail("target_state_drift")
    metadata_bindings = C4AuthorizationBindings(
        **{
            **bindings.__dict__,
            "source_state_sha256": original_source_sha,
            "operator_authorized": True,
        }
    )
    exact = (
        target.get("protocol_version") == PROTOCOL_VERSION
        and target.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and target.get("status") == SOURCE_SCOPE
        and target.get("current_action_scope") == TARGET_SCOPE
        and target.get("current_blocker") is None
        and target.get("next_allowed_action") == TARGET_NEXT_ACTION
        and target.get("authorized_live_actions") == [TARGET_ACTION]
        and target.get("native_characterization_live_authorized") is True
        and target.get("live_h0_candidate_authorized") is False
        and target.get("authorized_h0_candidate_id") is None
        and target.get("service_admin_authorized") is False
        and target.get("v3_smoke_003_authorized") is False
        and target.get("stage_progress", {}).get("native_characterization")
        == SOURCE_PROGRESS
        and dict(metadata)
        == _metadata(metadata_bindings, c2_evidence, c3_evidence)
    )
    if not exact:
        raise _fail("target_state_drift")


@contextmanager
def _lock(path: Path):
    lock = path.parent / f".{path.name}.c4-authorization.lock"
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
    temporary: Path | None = None
    try:
        mode = path.stat().st_mode & 0o777
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
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise _fail("atomic_write_failed") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def authorize_native_characterization_c4_live_only(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    bindings: C4AuthorizationBindings,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate and optionally atomically apply the exact C4 live grant."""

    _validate_bindings(bindings)
    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
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
        if raw != _canonical(state):
            raise _fail("state_not_canonical")
        _safe(state)
        if _sha(raw) != bindings.source_state_sha256:
            raise _fail("source_state_drift")
        c2 = state.get("native_characterization_c2_completion")
        c3 = state.get("native_characterization_c3_completion")
        if not isinstance(c2, Mapping) or not isinstance(c3, Mapping):
            raise _fail("source_state_not_exact_c4_offline")
        _validate_freeze(validation, bindings)
        c2_evidence = _validate_c2(validation, c2, bindings.freeze_sha256)
        c3_evidence = _validate_c3(validation, c3, str(c2.get("run_id")))
        _validate_schedule(validation, bindings, c2_evidence)
        _validate_c4_code(validation, bindings)
        if state.get("current_action_scope") == TARGET_SCOPE:
            _validate_target(state, bindings, c2_evidence, c3_evidence)
            return deepcopy(state), True
        _validate_source(state)
        return _build_target(state, bindings, c2_evidence, c3_evidence), False

    if dry_run:
        return derive()[0]
    with _lock(path):
        target, already = derive()
        if not already:
            _atomic_write(path, target)
        return target


__all__ = [
    "C4AuthorizationBindings",
    "NativeCharacterizationC4AuthorizationError",
    "authorize_native_characterization_c4_live_only",
]
