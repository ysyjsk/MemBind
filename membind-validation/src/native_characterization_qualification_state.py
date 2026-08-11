"""Advance Native characterization to the offline-qualified C0 waiting point.

This transition binds completed local evidence but grants no live action.  It is
separate from the later, operator-triggered C0 authorization transition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from native_characterization_state_transition import (
    _atomic_write,
    _canonical_json_bytes,
    _state_transition_lock,
)


BASE_STATE_SHA256 = "af7651fb8d5e5f6e4b6b43fe028969ce45182387326c162bcd8d45df0b47b731"
WORKPLAN_PATH = "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
WORKPLAN_SHA256 = "be3112cc2da4080ce98f9c94f1ab510ba5cc8350dca108a15e304da04c996b5b"
TARGET_PROGRESS = "c1_qualified_c0_dry_run_pass_waiting_for_services"
TARGET_NEXT_ACTION = "operator_start_vllm_then_authorize_c0"

EVIDENCE: dict[str, tuple[str, str]] = {
    "c1_lifecycle": (
        "artifacts/tdd/native_characterization_c1_lifecycle_green_20260810.log",
        "b87af35690eaba9143b463bea3a4afb5beb2132437ca3d3d141cf0fc8222c405",
    ),
    "c1_aa": (
        "artifacts/tdd/native_characterization_c1_aa_qualification_20260810.json",
        "3465a1e3b5a340debe53008111f4391376d7e28e76b7ec4941cbade2374ba328",
    ),
    "focused_regression": (
        "artifacts/tdd/native_characterization_pre_c0_focused_regression_20260811.log",
        "bc6d2e9bc403fe8dd129e8f82a8a463e299abe663501637557273b4b4418c461",
    ),
    "full_offline_regression": (
        "artifacts/tdd/native_characterization_pre_c0_full_offline_green_20260811.log",
        "f6533fc5fd03a36fb7a25221592977d0327fcff0ba382c0ebdbaa05c626bdb7b",
    ),
    "c0_dry_run": (
        "artifacts/tdd/native_characterization_c0_final_dry_run_20260811.log",
        "33daa46df9185bb40dc09d60da1babc3fb3fa0494e98d78441dddf8192460bbd",
    ),
    "freeze": (
        "artifacts/native_characterization/freeze.json",
        "3bca97e1f531dbd23584dd02248a0cbed783f2153f3c756880826ea0c48e001c",
    ),
    "phase_map": (
        "artifacts/native_characterization/phase_map.json",
        "afdfd18d17e285fe5b23d9ba8eed2cb893ddabb71723259947a3e7317bd72f31",
    ),
}


class NativeCharacterizationQualificationStateError(RuntimeError):
    """Sanitized offline state-transition failure."""


def _fail(reason: str) -> NativeCharacterizationQualificationStateError:
    return NativeCharacterizationQualificationStateError(
        f"Native characterization qualification state denied: {reason}"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_unreadable") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return value


def _resolve_under(root: Path, relative: str, label: str) -> Path:
    path = root / relative
    try:
        lexical = path.relative_to(root)
    except ValueError:
        raise _fail(f"{label}_path_escape") from None
    cursor = root
    for part in lexical.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_symlink")
    if not path.is_file():
        raise _fail(f"{label}_missing")
    return path


def _validate_evidence(repo: Path, validation: Path) -> dict[str, dict[str, Any]]:
    workplan = _resolve_under(repo, WORKPLAN_PATH, "workplan")
    if _sha256(workplan) != WORKPLAN_SHA256:
        raise _fail("workplan_hash_mismatch")

    paths: dict[str, Path] = {}
    for name, (relative, expected) in EVIDENCE.items():
        path = _resolve_under(validation, relative, name)
        if _sha256(path) != expected:
            raise _fail("evidence_hash_mismatch")
        paths[name] = path

    aa = _read_json(paths["c1_aa"], "c1_aa")
    freeze = _read_json(paths["freeze"], "freeze")
    phase_map = _read_json(paths["phase_map"], "phase_map")
    try:
        c0 = json.loads(paths["c0_dry_run"].read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("c0_dry_run_unreadable") from None
    if not isinstance(c0, dict):
        raise _fail("c0_dry_run_not_object")

    exact = (
        aa.get("schema_version")
        == "membind.native-characterization-c1-qualification.v1"
        and aa.get("classification") == "clean_pass"
        and aa.get("semantic_parity", {}).get("passed") is True
        and len(aa.get("pairs", [])) == 5
        and freeze.get("schema_version")
        == "membind.native-characterization-freeze.v1"
        and phase_map.get("schema_version")
        == "membind.native-characterization-phase-map.v1"
        and c0.get("schema_version")
        == "membind.native-characterization-c0-preview.v1"
        and c0.get("live_request_performed") is False
        and c0.get("freeze_file_sha256") == EVIDENCE["freeze"][1]
        and c0.get("freeze_payload_sha256") == freeze.get("payload_sha256")
    )
    if not exact:
        raise _fail("evidence_contract_mismatch")

    lifecycle_text = paths["c1_lifecycle"].read_text(encoding="utf-8")
    focused_text = paths["focused_regression"].read_text(encoding="utf-8")
    full_text = paths["full_offline_regression"].read_text(encoding="utf-8")
    if "Ran 34 tests" not in lifecycle_text or "OK" not in lifecycle_text:
        raise _fail("c1_lifecycle_not_green")
    if "Ran 154 tests" not in focused_text or "OK" not in focused_text:
        raise _fail("focused_regression_not_green")
    if "Ran 675 tests" not in full_text or "OK" not in full_text:
        raise _fail("full_regression_not_green")
    return {"aa": aa, "freeze": freeze, "phase_map": phase_map, "c0": c0}


def _validate_base(state: Mapping[str, Any]) -> None:
    progress = state.get("stage_progress")
    transition = state.get("native_characterization_transition")
    exact = (
        state.get("protocol_version") == "current-validation-v1.3"
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("status") == "native_characterization_offline_only"
        and state.get("current_action_scope")
        == "native_characterization_offline_only"
        and state.get("current_blocker") is None
        and state.get("next_allowed_action") == "implement_c1_instrumentation_offline"
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_live_actions") == []
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and isinstance(progress, Mapping)
        and progress.get("native_characterization")
        == "c1_instrumentation_tdd_pending"
        and progress.get("h0_live_gate") == "forbidden_native_characterization"
        and isinstance(transition, Mapping)
        and transition.get("workplan_sha256") == WORKPLAN_SHA256
        and transition.get("live_authorized") is False
        and "native_characterization_offline_qualification" not in state
    )
    if not exact:
        raise _fail("base_state_mismatch")


def _metadata(evidence: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    aa = evidence["aa"]
    freeze = evidence["freeze"]
    phase_map = evidence["phase_map"]
    c0 = evidence["c0"]

    def bound(name: str, **extra: Any) -> dict[str, Any]:
        relative, digest = EVIDENCE[name]
        return {"path": relative, "sha256": digest, **extra}

    return {
        "schema_version": "membind.native-characterization-offline-qualification.v1",
        "workplan_id": "native-characterization-v1.1",
        "workplan_sha256": WORKPLAN_SHA256,
        "offline_transition_state_sha256": BASE_STATE_SHA256,
        "instrumentation_contract_status": "qualified",
        "c1_lifecycle": bound("c1_lifecycle", test_count=34),
        "c1_aa": bound(
            "c1_aa",
            payload_sha256=aa["payload_sha256"],
            pair_count=5,
            classification=aa["classification"],
            median_overhead_ratio=aa["paired_distribution"]["median_ratio"],
            semantic_parity=aa["semantic_parity"]["passed"],
        ),
        "focused_regression": bound("focused_regression", test_count=154),
        "full_offline_regression": bound(
            "full_offline_regression", test_count=675
        ),
        "c0_dry_run": bound(
            "c0_dry_run",
            run_id=c0["run_id"],
            graph_namespace=c0["graph_namespace"],
            live_request_performed=False,
        ),
        "freeze": bound("freeze", payload_sha256=freeze["payload_sha256"]),
        "phase_map": bound(
            "phase_map", payload_sha256=phase_map["payload_sha256"]
        ),
        "live_authorized": False,
    }


def build_qualified_offline_state(
    source: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    _validate_base(source)
    if hashlib.sha256(_canonical_json_bytes(source)).hexdigest() != BASE_STATE_SHA256:
        raise _fail("base_state_hash_mismatch")
    target = deepcopy(dict(source))
    target["next_allowed_action"] = TARGET_NEXT_ACTION
    stage_progress = deepcopy(dict(target["stage_progress"]))
    stage_progress["native_characterization"] = TARGET_PROGRESS
    target["stage_progress"] = stage_progress
    target["native_characterization_offline_qualification"] = _metadata(evidence)
    return target


def _derive(state: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], bool]:
    if "native_characterization_offline_qualification" not in state:
        return build_qualified_offline_state(state, evidence), False

    reconstructed = deepcopy(dict(state))
    observed = reconstructed.pop("native_characterization_offline_qualification")
    reconstructed["next_allowed_action"] = "implement_c1_instrumentation_offline"
    stage_progress = deepcopy(dict(reconstructed.get("stage_progress", {})))
    stage_progress["native_characterization"] = "c1_instrumentation_tdd_pending"
    reconstructed["stage_progress"] = stage_progress
    expected = build_qualified_offline_state(reconstructed, evidence)
    if observed != expected["native_characterization_offline_qualification"]:
        raise _fail("qualified_state_evidence_drift")
    if _canonical_json_bytes(expected) != _canonical_json_bytes(state):
        raise _fail("qualified_state_drift")
    return deepcopy(dict(state)), True


def advance_native_characterization_offline(
    state_path: str | Path,
    *,
    repo_root: str | Path,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Bind offline qualification evidence without granting C0 live access."""

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

    def derive() -> tuple[bytes, dict[str, Any], bool]:
        evidence = _validate_evidence(repo, validation)
        raw = state_file.read_bytes()
        try:
            state = json.loads(raw.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError):
            raise _fail("state_unreadable") from None
        if not isinstance(state, dict) or raw != _canonical_json_bytes(state):
            raise _fail("state_not_canonical")
        target, already = _derive(state, evidence)
        return raw, target, already

    if dry_run:
        return derive()[1]
    with _state_transition_lock(state_file):
        initial, target, already = derive()
        if already:
            return target
        current = state_file.read_bytes()
        if current != initial:
            raise _fail("state_changed_before_commit")
        _atomic_write(state_file, target)
    return target


def _main() -> int:
    validation = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state", type=Path, default=validation / "CURRENT_STATE.json")
    parser.add_argument("--repo-root", type=Path, default=validation.parent)
    args = parser.parse_args()
    try:
        result = advance_native_characterization_offline(
            args.state,
            repo_root=args.repo_root,
            dry_run=not args.apply,
        )
    except NativeCharacterizationQualificationStateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
