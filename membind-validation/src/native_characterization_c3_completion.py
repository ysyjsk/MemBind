"""Bind completed offline C3/E2 evidence and enter the C4 offline TDD lane."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from native_characterization_state_transition import (
    _atomic_write,
    _canonical_json_bytes,
    _state_transition_lock,
)


STATE_RELATIVE_PATH = "membind-validation/CURRENT_STATE.json"
SOURCE_SCOPE = "native_characterization_c3_offline_only"
TARGET_SCOPE = "native_characterization_c4_offline_only"
SOURCE_NEXT_ACTION = "build_native_characterization_dependency_map_offline"
TARGET_NEXT_ACTION = "build_native_characterization_e3_harness_offline"
SOURCE_PROGRESS = "c2_verified_c3_dependency_audit_pending"
TARGET_PROGRESS = "c2_c3_complete_c4_offline_tdd_pending"
COMPLETION_KEY = "native_characterization_c3_completion"
DEPENDENCY_MAP_PATH = "artifacts/native_characterization/dependency_map.json"
E2_PATH = "artifacts/native_characterization/e2_dependency_opportunity.json"
ANALYZER_PATH = "src/native_characterization_c3.py"
DEPENDENCY_MAP_SCHEMA = "membind.native-characterization-dependency-map.v1"
E2_SCHEMA = "membind.native-characterization-e2-opportunity.v1"
_RUN_ID_RE = re.compile(r"^c2-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class C3CompletionBindings:
    source_state_sha256: str
    dependency_map_relative_path: str
    dependency_map_sha256: str
    dependency_map_payload_sha256: str
    e2_relative_path: str
    e2_sha256: str
    e2_payload_sha256: str
    focused_log_relative_path: str
    focused_log_sha256: str
    focused_test_count: int


class NativeCharacterizationC3CompletionError(RuntimeError):
    """Sanitized fail-closed C3 completion transition error."""


def _fail(reason: str) -> NativeCharacterizationC3CompletionError:
    return NativeCharacterizationC3CompletionError(
        f"native characterization C3 completion denied: {reason}"
    )


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


def _validate_seal(value: Mapping[str, Any], label: str) -> None:
    candidate = deepcopy(dict(value))
    observed = candidate.pop("payload_sha256", None)
    if observed != _sha(_canonical_json_bytes(candidate)):
        raise _fail(f"{label}_payload_mismatch")


def _validate_bindings(bindings: C3CompletionBindings) -> None:
    if not isinstance(bindings, C3CompletionBindings):
        raise _fail("bindings_invalid")
    for label, value in (
        ("source_state_sha256", bindings.source_state_sha256),
        ("dependency_map_sha256", bindings.dependency_map_sha256),
        ("dependency_map_payload_sha256", bindings.dependency_map_payload_sha256),
        ("e2_sha256", bindings.e2_sha256),
        ("e2_payload_sha256", bindings.e2_payload_sha256),
        ("focused_log_sha256", bindings.focused_log_sha256),
    ):
        _require_sha(value, label)
    if bindings.dependency_map_relative_path != DEPENDENCY_MAP_PATH:
        raise _fail("dependency_map_path_invalid")
    if bindings.e2_relative_path != E2_PATH:
        raise _fail("e2_path_invalid")
    focused = _safe_relative(bindings.focused_log_relative_path, "focused_log")
    if not focused.startswith("artifacts/tdd/"):
        raise _fail("focused_log_path_invalid")
    if not isinstance(bindings.focused_test_count, int) or bindings.focused_test_count <= 0:
        raise _fail("focused_test_count_invalid")


def _validate_source(state: Mapping[str, Any]) -> Mapping[str, Any]:
    c2 = state.get("native_characterization_c2_completion")
    exact = (
        state.get("protocol_version") == "current-validation-v1.3"
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
        and isinstance(c2, Mapping)
        and c2.get("status") == "verified"
        and isinstance(c2.get("run_id"), str)
        and _RUN_ID_RE.fullmatch(str(c2.get("run_id"))) is not None
        and isinstance(c2.get("episode_count"), int)
        and c2.get("episode_count", 0) > 0
        and isinstance(c2.get("block_count"), int)
        and c2.get("block_count", 0) > 0
        and c2.get("grant_consumed") is True
        and c2.get("live_authorized") is False
        and COMPLETION_KEY not in state
    )
    if not exact:
        raise _fail("source_state_not_exact_c3_offline")
    return c2


def _validate_c2_provenance(
    provenance: Any,
    c2: Mapping[str, Any],
    label: str,
) -> None:
    if not isinstance(provenance, Mapping):
        raise _fail(f"{label}_missing")
    exact = (
        provenance.get("status") == "verified"
        and provenance.get("run_id") == c2.get("run_id")
        and provenance.get("manifest_sha256") == c2.get("manifest_sha256")
        and provenance.get("checkpoint_sha256") == c2.get("checkpoint_sha256")
        and provenance.get("e1_breakdown_sha256") == c2.get("e1_breakdown_sha256")
        and provenance.get("top_level_e1_breakdown_sha256")
        == c2.get("top_level_e1_breakdown_sha256")
    )
    if not exact:
        raise _fail(f"{label}_mismatch")


def _read_bound_artifact(
    validation: Path,
    relative: str,
    expected_sha256: str,
    expected_payload_sha256: str,
    label: str,
) -> dict[str, Any]:
    path = _resolve_existing(validation, relative, label)
    raw, value = _read_object(path, label)
    # C3 artifacts deliberately use canonical JSON plus one trailing newline.
    if raw != _canonical_json_bytes(value) + b"\n":
        raise _fail(f"{label}_not_canonical")
    if _sha(raw) != expected_sha256:
        raise _fail(f"{label}_hash_mismatch")
    _validate_seal(value, label)
    if value.get("payload_sha256") != expected_payload_sha256:
        raise _fail(f"{label}_payload_binding_mismatch")
    return value


def _validate_artifacts(
    validation: Path,
    bindings: C3CompletionBindings,
    c2: Mapping[str, Any],
) -> dict[str, Any]:
    dependency_map = _read_bound_artifact(
        validation,
        bindings.dependency_map_relative_path,
        bindings.dependency_map_sha256,
        bindings.dependency_map_payload_sha256,
        "dependency_map",
    )
    e2 = _read_bound_artifact(
        validation,
        bindings.e2_relative_path,
        bindings.e2_sha256,
        bindings.e2_payload_sha256,
        "e2",
    )
    analyzer_path = _resolve_existing(validation, ANALYZER_PATH, "analyzer_source")
    analyzer_sha256 = _sha(analyzer_path.read_bytes())
    focused_path = _resolve_existing(
        validation,
        bindings.focused_log_relative_path,
        "focused_log",
    )
    if _sha(focused_path.read_bytes()) != bindings.focused_log_sha256:
        raise _fail("focused_log_hash_mismatch")

    run_id = c2.get("run_id")
    dependency_provenance = dependency_map.get("provenance")
    e2_provenance = e2.get("provenance")
    aggregate = e2.get("aggregate")
    phase_rules = dependency_map.get("phase_rules")
    histories = e2.get("histories")
    intervals = e2.get("intervals")
    exact = (
        dependency_map.get("schema_version") == DEPENDENCY_MAP_SCHEMA
        and dependency_map.get("status") == "complete"
        and dependency_map.get("stage") == "C3/E2"
        and dependency_map.get("run_id") == run_id
        and isinstance(phase_rules, list)
        and len(phase_rules) == 8
        and isinstance(dependency_provenance, Mapping)
        and dependency_provenance.get("builder_source_sha256") == analyzer_sha256
        and e2.get("schema_version") == E2_SCHEMA
        and e2.get("status") == "complete"
        and e2.get("stage") == "C3/E2"
        and e2.get("run_id") == run_id
        and isinstance(e2_provenance, Mapping)
        and e2_provenance.get("analyzer_source_sha256") == analyzer_sha256
        and e2_provenance.get("dependency_map_payload_sha256")
        == bindings.dependency_map_payload_sha256
        and isinstance(aggregate, Mapping)
        and aggregate.get("episode_count") == c2.get("episode_count")
        and isinstance(aggregate.get("T_total_ns"), int)
        and aggregate.get("T_total_ns", 0) > 0
        and isinstance(aggregate.get("p_L"), (int, float))
        and isinstance(aggregate.get("p_U"), (int, float))
        and 0 <= aggregate.get("p_L", -1) <= aggregate.get("p_U", 2) <= 1
        and isinstance(histories, list)
        and len(histories) == c2.get("block_count")
        and isinstance(intervals, list)
        and len(intervals) == c2.get("episode_count", 0) * 8
    )
    if not exact:
        raise _fail("artifact_contract_mismatch")
    _validate_c2_provenance(
        dependency_provenance.get("c2_verification"),
        c2,
        "dependency_map_c2_provenance",
    )
    _validate_c2_provenance(
        e2_provenance.get("c2_verification"),
        c2,
        "e2_c2_provenance",
    )
    return {
        "run_id": run_id,
        "analyzer_source_sha256": analyzer_sha256,
        "episode_count": aggregate["episode_count"],
        "history_count": len(histories),
        "interval_count": len(intervals),
        "T_total_ns": aggregate["T_total_ns"],
        "p_L": aggregate["p_L"],
        "p_U": aggregate["p_U"],
        "speedup_bounds": deepcopy(aggregate.get("speedup_bounds", {})),
    }


def _completion_metadata(
    bindings: C3CompletionBindings,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "membind.native-characterization-c3-completion.v1",
        "status": "complete",
        "source_state_sha256": bindings.source_state_sha256,
        "run_id": evidence["run_id"],
        "dependency_map_path": bindings.dependency_map_relative_path,
        "dependency_map_sha256": bindings.dependency_map_sha256,
        "dependency_map_payload_sha256": bindings.dependency_map_payload_sha256,
        "e2_path": bindings.e2_relative_path,
        "e2_sha256": bindings.e2_sha256,
        "e2_payload_sha256": bindings.e2_payload_sha256,
        "analyzer_source_path": ANALYZER_PATH,
        "analyzer_source_sha256": evidence["analyzer_source_sha256"],
        "focused_log_path": bindings.focused_log_relative_path,
        "focused_log_sha256": bindings.focused_log_sha256,
        "focused_test_count": bindings.focused_test_count,
        "episode_count": evidence["episode_count"],
        "history_count": evidence["history_count"],
        "interval_count": evidence["interval_count"],
        "T_total_ns": evidence["T_total_ns"],
        "p_L": evidence["p_L"],
        "p_U": evidence["p_U"],
        "speedup_bounds": deepcopy(evidence["speedup_bounds"]),
        "live_authorized": False,
    }


def _build_target(
    source: Mapping[str, Any],
    bindings: C3CompletionBindings,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
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
    target[COMPLETION_KEY] = _completion_metadata(bindings, evidence)
    return target


def _validate_target(
    state: Mapping[str, Any],
    bindings: C3CompletionBindings,
    evidence: Mapping[str, Any],
) -> None:
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
        and state.get(COMPLETION_KEY) == _completion_metadata(bindings, evidence)
    )
    if not exact:
        raise _fail("target_state_drift")


def complete_native_characterization_c3(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    bindings: C3CompletionBindings,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate C3 evidence and atomically enter C4 offline-only preparation."""

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
        c2 = state.get("native_characterization_c2_completion")
        if not isinstance(c2, Mapping):
            raise _fail("c2_completion_missing")
        evidence = _validate_artifacts(validation, bindings, c2)
        if COMPLETION_KEY in state:
            _validate_target(state, bindings, evidence)
            return deepcopy(state), True
        if _sha(raw) != bindings.source_state_sha256:
            raise _fail("source_state_drift")
        _validate_source(state)
        return _build_target(state, bindings, evidence), False

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
    "C3CompletionBindings",
    "NativeCharacterizationC3CompletionError",
    "complete_native_characterization_c3",
]
