"""Bind the final pre-C0 offline regression without granting live access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from current_state_gate import LiveAction, evaluate_live_action
from native_characterization_qualification_state import (
    NativeCharacterizationQualificationStateError,
    TARGET_NEXT_ACTION,
    TARGET_PROGRESS,
    _validate_evidence,
)
from native_characterization_state_transition import (
    _atomic_write,
    _canonical_json_bytes,
    _state_transition_lock,
)


SOURCE_STATE_SHA256 = (
    "9bcdfaa327e7445af99214d6723a03e464d59e6da124392b45133581ab7dd0ed"
)
SCHEMA_VERSION = (
    "membind.native-characterization-offline-evidence-finalization.v1"
)
FINALIZATION_FIELD = "native_characterization_offline_evidence_finalization"
DEFAULT_REGRESSION_PATH = (
    "artifacts/tdd/"
    "native_characterization_pre_c0_final_full_offline_green_20260811.log"
)
OLD_REGRESSION_PATH = (
    "artifacts/tdd/native_characterization_pre_c0_full_offline_green_20260811.log"
)
MINIMUM_TEST_COUNT = 679

_SUMMARY_RE = re.compile(r"^Ran ([0-9]+) tests in [^\r\n]+$", re.MULTILINE)
_OK_RE = re.compile(r"^OK$", re.MULTILINE)
_FAILURE_RE = re.compile(r"^(?:FAILED|ERROR)(?:\s|$)", re.MULTILINE)


class NativeCharacterizationEvidenceFinalizationError(RuntimeError):
    """Sanitized refusal to bind incomplete or drifting offline evidence."""


def _fail(reason: str) -> NativeCharacterizationEvidenceFinalizationError:
    return NativeCharacterizationEvidenceFinalizationError(
        f"Native characterization evidence finalization denied: {reason}"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_source(state: Mapping[str, Any]) -> None:
    progress = state.get("stage_progress")
    transition = state.get("native_characterization_transition")
    qualification = state.get("native_characterization_offline_qualification")
    exact = (
        state.get("protocol_version") == "current-validation-v1.3"
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("status") == "native_characterization_offline_only"
        and state.get("current_action_scope")
        == "native_characterization_offline_only"
        and state.get("current_blocker") is None
        and state.get("next_allowed_action") == TARGET_NEXT_ACTION
        and state.get("authorized_live_actions") == []
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and isinstance(progress, Mapping)
        and progress.get("native_characterization") == TARGET_PROGRESS
        and progress.get("h0_live_gate") == "forbidden_native_characterization"
        and isinstance(transition, Mapping)
        and transition.get("live_authorized") is False
        and isinstance(qualification, Mapping)
        and qualification.get("schema_version")
        == "membind.native-characterization-offline-qualification.v1"
        and qualification.get("instrumentation_contract_status") == "qualified"
        and qualification.get("live_authorized") is False
        and FINALIZATION_FIELD not in state
    )
    if not exact:
        raise _fail("source_state_shape_mismatch")
    if _sha256(_canonical_json_bytes(state)) != SOURCE_STATE_SHA256:
        raise _fail("source_state_hash_mismatch")


def _assert_all_live_actions_denied(state: Mapping[str, Any]) -> None:
    for action in LiveAction:
        decision = evaluate_live_action(
            state,
            action,
            candidate_id="Q1" if action is LiveAction.H0_CANDIDATE else None,
        )
        if decision.allowed:
            raise _fail("live_action_became_authorized")


def _resolve_regression(validation: Path, relative: str | Path) -> tuple[str, Path]:
    if not isinstance(relative, (str, Path)):
        raise _fail("regression_path_invalid")
    raw = str(relative)
    candidate = Path(raw)
    if candidate.is_absolute() or raw != candidate.as_posix():
        raise _fail("regression_path_invalid")
    if (
        len(candidate.parts) < 3
        or candidate.parts[:2] != ("artifacts", "tdd")
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or raw == OLD_REGRESSION_PATH
    ):
        raise _fail("regression_path_out_of_scope")

    path = validation / candidate
    cursor = validation
    for part in candidate.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail("regression_path_symlink")
    if not path.is_file():
        raise _fail("regression_missing")
    return raw, path


def _regression_metadata(
    validation: Path, regression_path: str | Path
) -> dict[str, Any]:
    relative, path = _resolve_regression(validation, regression_path)
    try:
        encoded = path.read_bytes()
        text = encoded.decode("utf-8")
    except (OSError, UnicodeError):
        raise _fail("regression_unreadable") from None

    summaries = _SUMMARY_RE.findall(text)
    if len(summaries) != 1 or len(_OK_RE.findall(text)) != 1:
        raise _fail("regression_terminal_summary_invalid")
    if _FAILURE_RE.search(text) is not None:
        raise _fail("regression_not_green")
    test_count = int(summaries[0])
    if test_count < MINIMUM_TEST_COUNT:
        raise _fail("regression_test_count_too_small")
    return {
        "path": relative,
        "sha256": _sha256(encoded),
        "test_count": test_count,
        "status": "green",
    }


def _metadata(regression: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_state_sha256": SOURCE_STATE_SHA256,
        "final_full_offline_regression": deepcopy(dict(regression)),
        "live_authorized": False,
    }


def _derive(
    state: Mapping[str, Any], regression: Mapping[str, Any]
) -> tuple[dict[str, Any], bool]:
    source = deepcopy(dict(state))
    observed = source.pop(FINALIZATION_FIELD, None)
    _validate_source(source)
    _assert_all_live_actions_denied(source)

    target = deepcopy(source)
    target[FINALIZATION_FIELD] = _metadata(regression)
    _assert_all_live_actions_denied(target)
    if observed is None:
        return target, False
    if observed != target[FINALIZATION_FIELD]:
        raise _fail("finalized_evidence_drift")
    if _canonical_json_bytes(target) != _canonical_json_bytes(state):
        raise _fail("finalized_state_drift")
    return deepcopy(dict(state)), True


def finalize_offline_evidence(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    regression_path: str | Path = DEFAULT_REGRESSION_PATH,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Append final offline evidence while preserving a deny-all live gate."""

    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    repo = Path(repo_root).resolve()
    state_file = Path(state_path).resolve()
    try:
        state_file.relative_to(repo)
    except ValueError:
        raise _fail("state_path_escape") from None
    if state_file.is_symlink() or not state_file.is_file():
        raise _fail("state_path_invalid")
    validation = state_file.parent

    def derive() -> tuple[bytes, dict[str, Any], bool, dict[str, Any]]:
        try:
            _validate_evidence(repo, validation)
        except NativeCharacterizationQualificationStateError:
            raise _fail("qualification_evidence_invalid") from None
        regression = _regression_metadata(validation, regression_path)
        raw = state_file.read_bytes()
        try:
            state = json.loads(raw.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError):
            raise _fail("source_state_unreadable") from None
        if not isinstance(state, dict) or raw != _canonical_json_bytes(state):
            raise _fail("source_state_not_canonical")
        target, already = _derive(state, regression)
        return raw, target, already, regression

    if dry_run:
        return derive()[1]
    with _state_transition_lock(state_file):
        initial, target, already, regression = derive()
        if already:
            return target
        if state_file.read_bytes() != initial:
            raise _fail("source_state_changed_before_commit")
        if _regression_metadata(validation, regression_path) != regression:
            raise _fail("regression_changed_before_commit")
        _atomic_write(state_file, target)
    return target


def _main() -> int:
    validation = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state", type=Path, default=validation / "CURRENT_STATE.json")
    parser.add_argument("--repo-root", type=Path, default=validation.parent)
    parser.add_argument("--regression-path", default=DEFAULT_REGRESSION_PATH)
    args = parser.parse_args()
    try:
        result = finalize_offline_evidence(
            args.state,
            repo_root=args.repo_root,
            regression_path=args.regression_path,
            dry_run=not args.apply,
        )
    except NativeCharacterizationEvidenceFinalizationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    metadata = result[FINALIZATION_FIELD]
    print(json.dumps(metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
