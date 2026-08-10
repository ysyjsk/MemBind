"""Fail-closed machine-state progression for the repaired Q1 H0 stages.

All builders are pure and offline.  They keep repair evidence binding, live
authorization, and phase advancement as separate operator-controlled actions;
no runner imports this module to advance itself automatically.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import re
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from h0_completion import validate_h0_prior_phase_terminal_completion
from h0_full_history_completion import validate_h0_b_terminal_completion
from h0_repair_admission import verify_h0_repair_decision
from h0_state_transition import (
    _atomic_write,
    _assert_safe_state_value,
    _load_canonical_state_snapshot,
    _state_target,
    _state_transition_lock,
    _validate_manifest_verification,
    _validate_tdd_evidence,
)
from h0_live_preflight import load_authorized_h0_runtime_identity
from h0_runtime import H0ManifestError, H0StateGateError, authorize_h0_live_entry, canonical_json_bytes


PROTOCOL_VERSION = "current-validation-v1.3"
REPAIR_BOUND_STATUS = "h0_protocol_repair_verified_not_live_authorized"
REPAIR_BOUND_SCOPE = "h0_protocol_repair_verified_only"
_BINDING_FIELDS = {
    "resolved_manifest_index_path",
    "resolved_manifest_index_sha256",
    "resolved_candidate_manifest_path",
    "resolved_candidate_manifest_sha256",
    "resolved_shared_base_manifest_path",
    "resolved_shared_base_manifest_sha256",
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class H0PhaseStateError(RuntimeError):
    """A sanitized denial of repair or phase state progression."""


def _fail(reason: str) -> H0PhaseStateError:
    return H0PhaseStateError(f"H0 phase state transition denied: {reason}")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label}_not_object")
    return value


def _validated_revoked_source(source: Mapping[str, Any]) -> Mapping[str, Any]:
    _assert_safe_state_value(source, location="source_state")
    progress = source.get("stage_progress")
    invalidation = source.get("h0_live_authorization_invalidation")
    exact = (
        source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("current_stage") == "H0"
        and source.get("status") == "h0_live_authorization_revoked"
        and source.get("current_action_scope") == "h0_live_forbidden"
        and source.get("current_blocker") == "h0_protocol_gate_order_violation"
        and source.get("live_h0_candidate_authorized") is False
        and source.get("authorized_live_actions") == []
        and source.get("authorized_h0_candidate_id") is None
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == "forbidden"
        and isinstance(invalidation, Mapping)
        and invalidation.get("schema_version")
        == "membind.h0.live-authorization-invalidation.v1"
        and invalidation.get("protocol_version") == PROTOCOL_VERSION
        and invalidation.get("status")
        == "invalidated_no_rerun_or_advance_authorized"
        and invalidation.get("reason") == "protocol_gate_order_violation"
        and invalidation.get("candidate_id") == "Q1"
        and invalidation.get("phase") == "H0-A"
        and invalidation.get("candidate_rerun_authorized") is False
        and invalidation.get("candidate_advance_authorized") is False
        and invalidation.get("live_transition_authorized") is False
        and source.get("live_h0_authorization") is None
    )
    if not exact:
        raise _fail("source_not_exact_revoked_state")
    return invalidation


def build_h0_repair_bound_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
    repair_decision_path: str,
    repair_decision_sha256: str,
    manifest_validator: Callable[..., Any] = _validate_manifest_verification,
    tdd_validator: Callable[..., Any] = _validate_tdd_evidence,
    repair_decision_verifier: Callable[..., Any] = verify_h0_repair_decision,
) -> dict[str, Any]:
    """Bind r2, TDD, and the disclosed decision while retaining a closed gate."""

    source = _mapping(source_state, "source_state")
    invalidation = _validated_revoked_source(source)
    root_path = Path(root).resolve()
    try:
        bindings, verified_manifest = manifest_validator(
            root_path, _mapping(manifest_verification, "manifest_verification")
        )
        verified_tdd = tdd_validator(
            root_path, _mapping(tdd_evidence, "tdd_evidence")
        )
        admission = repair_decision_verifier(
            root=root_path,
            decision_path=repair_decision_path,
            decision_sha256=repair_decision_sha256,
            manifest_verification=manifest_verification,
        )
    except H0PhaseStateError:
        raise
    except Exception as exc:
        raise _fail("offline_repair_evidence_validation_failed") from exc
    bindings = dict(_mapping(bindings, "artifact_bindings"))
    admission = dict(_mapping(admission, "repair_admission"))
    if set(bindings) != _BINDING_FIELDS:
        raise _fail("artifact_bindings_incomplete")
    if not (
        admission.get("schema_version") == "membind.h0.repair-admission.v1"
        and admission.get("protocol_version") == PROTOCOL_VERSION
        and admission.get("candidate_id") == "Q1"
        and admission.get("phase") == "H0-A"
        and admission.get("decision_path") == repair_decision_path
        and admission.get("decision_sha256") == repair_decision_sha256
        and admission.get("decision_result_blind") is False
        and admission.get("one_shot_replacement") is True
        and admission.get("invalidated_stage_attempt_id")
        == invalidation.get("stage_attempt_id")
        and admission.get("invalidated_checkpoint_index_sha256")
        == invalidation.get("checkpoint_index_sha256")
        and admission.get("old_attempt_qualification_reusable") is False
        and admission.get("old_and_new_trial_counts_mergeable") is False
        and admission.get("repaired_manifest_index_sha256")
        == bindings["resolved_manifest_index_sha256"]
        and admission.get("secrets_persisted") is False
    ):
        raise _fail("repair_admission_mismatch")

    state = deepcopy(dict(source))
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": "forbidden",
            "h0_candidate_progression": "repair_verified_replacement_not_authorized",
            "h0_offline_manifest_binding": "v1_3_harness_r2_verified",
        }
    )
    state.update(
        {
            "current_stage": "H0",
            "status": REPAIR_BOUND_STATUS,
            "current_action_scope": REPAIR_BOUND_SCOPE,
            "current_blocker": "replacement_h0_a_not_yet_authorized",
            "stage_progress": progress,
            "live_h0_candidate_authorized": False,
            "authorized_live_actions": [],
            "authorized_h0_candidate_id": None,
            "next_allowed_action": "explicit_q1_h0_a_replacement_transition",
            "h0_repair_live_prerequisites": {
                "schema_version": "membind.h0.repair-live-prerequisites.v1",
                "protocol_version": PROTOCOL_VERSION,
                "status": "verified_not_live_authorized",
                "candidate_id": "Q1",
                "phase": "H0-A",
                "artifact_bindings": bindings,
                "manifest_verification": deepcopy(dict(verified_manifest)),
                "tdd_evidence": deepcopy(dict(verified_tdd)),
                "repair_admission": admission,
                "live_transition_performed": False,
            },
        }
    )
    state.pop("live_h0_authorization", None)
    return state


def _validated_repair_bound(
    source: Mapping[str, Any],
    *,
    root: Path,
    manifest_validator: Callable[..., Any],
    tdd_validator: Callable[..., Any],
    repair_decision_verifier: Callable[..., Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _assert_safe_state_value(source, location="source_state")
    progress = source.get("stage_progress")
    prerequisites = source.get("h0_repair_live_prerequisites")
    if not (
        source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("current_stage") == "H0"
        and source.get("status") == REPAIR_BOUND_STATUS
        and source.get("current_action_scope") == REPAIR_BOUND_SCOPE
        and source.get("live_h0_candidate_authorized") is False
        and source.get("authorized_live_actions") == []
        and source.get("authorized_h0_candidate_id") is None
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == "forbidden"
        and isinstance(prerequisites, Mapping)
        and prerequisites.get("schema_version")
        == "membind.h0.repair-live-prerequisites.v1"
        and prerequisites.get("protocol_version") == PROTOCOL_VERSION
        and prerequisites.get("status") == "verified_not_live_authorized"
        and prerequisites.get("candidate_id") == "Q1"
        and prerequisites.get("phase") == "H0-A"
        and prerequisites.get("live_transition_performed") is False
        and source.get("live_h0_authorization") is None
    ):
        raise _fail("source_not_exact_repair_bound_state")
    try:
        bindings, _ = manifest_validator(
            root,
            _mapping(prerequisites.get("manifest_verification"), "manifest_verification"),
        )
        tdd_validator(root, _mapping(prerequisites.get("tdd_evidence"), "tdd_evidence"))
        recorded_admission = dict(
            _mapping(prerequisites.get("repair_admission"), "repair_admission")
        )
        observed_admission = repair_decision_verifier(
            root=root,
            decision_path=recorded_admission.get("decision_path"),
            decision_sha256=recorded_admission.get("decision_sha256"),
            manifest_verification=prerequisites.get("manifest_verification"),
        )
    except Exception as exc:
        raise _fail("repair_prerequisites_revalidation_failed") from exc
    if dict(bindings) != dict(prerequisites.get("artifact_bindings") or {}):
        raise _fail("repair_artifact_bindings_changed")
    if dict(observed_admission) != recorded_admission:
        raise _fail("repair_admission_changed")
    return dict(bindings), recorded_admission


def build_h0_replacement_live_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_validator: Callable[..., Any] = _validate_manifest_verification,
    tdd_validator: Callable[..., Any] = _validate_tdd_evidence,
    repair_decision_verifier: Callable[..., Any] = verify_h0_repair_decision,
) -> dict[str, Any]:
    """Consume the one-shot admission into one exact Q1/H0-A live grant."""

    source = _mapping(source_state, "source_state")
    bindings, admission = _validated_repair_bound(
        source,
        root=Path(root).resolve(),
        manifest_validator=manifest_validator,
        tdd_validator=tdd_validator,
        repair_decision_verifier=repair_decision_verifier,
    )
    scope = "h0_q1_a_live_only"
    state = deepcopy(dict(source))
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": scope,
            "h0_candidate_progression": "replacement_h0_a_authorized_once",
        }
    )
    state.update(
        {
            "current_stage": "H0",
            "status": scope,
            "current_action_scope": scope,
            "current_blocker": None,
            "stage_progress": progress,
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "next_allowed_action": "run_exact_q1_h0_a_replacement",
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": "H0-A",
                **bindings,
                "authorized_stage_attempt_id": admission[
                    "replacement_attempt_id"
                ],
                "repair_admission": admission,
            },
        }
    )
    state["h0_repair_live_prerequisites"]["live_transition_performed"] = True
    return state


def _validated_live_source(
    source: Mapping[str, Any], *, completed_phase: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = completed_phase.removeprefix("H0-").casefold()
    scope = f"h0_q1_{suffix}_live_only"
    progress = source.get("stage_progress")
    authorization = source.get("live_h0_authorization")
    if not (
        completed_phase in {"H0-A", "H0-B"}
        and source.get("protocol_version") == PROTOCOL_VERSION
        and source.get("current_stage") == "H0"
        and source.get("status") == scope
        and source.get("current_action_scope") == scope
        and source.get("live_h0_candidate_authorized") is True
        and source.get("authorized_live_actions") == ["h0_candidate"]
        and source.get("authorized_h0_candidate_id") == "Q1"
        and isinstance(progress, Mapping)
        and progress.get("h0_live_gate") == scope
        and isinstance(authorization, Mapping)
        and authorization.get("candidate_id") == "Q1"
        and authorization.get("phase") == completed_phase
    ):
        raise _fail("source_not_exact_live_phase")
    bindings = {field: authorization.get(field) for field in _BINDING_FIELDS}
    if any(
        not isinstance(value, str) or not value
        for value in bindings.values()
    ):
        raise _fail("live_manifest_bindings_invalid")
    return dict(authorization), bindings


def build_h0_successor_phase_live_state(
    source_state: Mapping[str, Any],
    *,
    root: str | Path,
    completed_phase: str,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    runtime_definition_sha256: str,
    completion_validator: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Authorize B after qualified A, or C after qualified B, never both."""

    source = _mapping(source_state, "source_state")
    _assert_safe_state_value(source, location="source_state")
    authorization, bindings = _validated_live_source(
        source, completed_phase=completed_phase
    )
    if completion_validator is None:
        completion_validator = (
            validate_h0_prior_phase_terminal_completion
            if completed_phase == "H0-A"
            else validate_h0_b_terminal_completion
        )
    arguments = {
        "root": Path(root).resolve(),
        "stage_attempt_id": stage_attempt_id,
        "checkpoint_index_path": checkpoint_index_path,
        "checkpoint_index_sha256": checkpoint_index_sha256,
        "candidate_id": "Q1",
        "runtime_definition_sha256": runtime_definition_sha256,
    }
    if completed_phase == "H0-A":
        arguments["phase"] = "H0-A"
    try:
        completion = completion_validator(**arguments)
    except Exception as exc:
        raise _fail("terminal_completion_validation_failed") from exc
    completion = dict(_mapping(completion, "terminal_completion"))
    expected_replacement = authorization.get("authorized_stage_attempt_id")
    repair_admission = authorization.get("repair_admission")
    if expected_replacement is None and completed_phase == "H0-A" and isinstance(
        repair_admission, Mapping
    ):
        expected_replacement = repair_admission.get("replacement_attempt_id")
    if not (
        completion.get("qualified") is True
        and completion.get("candidate_id") == "Q1"
        and completion.get("phase") == completed_phase
        and completion.get("stage_attempt_id") == stage_attempt_id
        and completion.get("checkpoint_index_path") == checkpoint_index_path
        and completion.get("checkpoint_index_sha256") == checkpoint_index_sha256
        and completion.get("runtime_definition_sha256")
        == runtime_definition_sha256
        and completion.get("secrets_persisted") is False
        and stage_attempt_id == expected_replacement
    ):
        raise _fail("terminal_completion_binding_mismatch")

    next_phase = "H0-B" if completed_phase == "H0-A" else "H0-C"
    next_suffix = next_phase.removeprefix("H0-").casefold()
    scope = f"h0_q1_{next_suffix}_live_only"
    prior_reference = {
        "stage_attempt_id": stage_attempt_id,
        "checkpoint_index_path": checkpoint_index_path,
        "checkpoint_index_sha256": checkpoint_index_sha256,
        "runtime_definition_sha256": runtime_definition_sha256,
        "terminal_result_sha256": completion.get("terminal_result_sha256"),
    }
    if _SHA256_RE.fullmatch(str(prior_reference["terminal_result_sha256"] or "")) is None:
        raise _fail("terminal_result_sha256_invalid")
    state = deepcopy(dict(source))
    progress = deepcopy(dict(_mapping(state.get("stage_progress"), "stage_progress")))
    progress.update(
        {
            "h0_live_gate": scope,
            "h0_candidate_progression": f"{completed_phase.casefold()}_qualified_{next_phase.casefold()}_authorized",
        }
    )
    completions = deepcopy(dict(state.get("h0_phase_completions") or {}))
    if completed_phase in completions:
        raise _fail("phase_completion_already_recorded")
    completions[completed_phase] = completion
    state.update(
        {
            "status": scope,
            "current_action_scope": scope,
            "current_blocker": None,
            "stage_progress": progress,
            "live_h0_candidate_authorized": True,
            "authorized_live_actions": ["h0_candidate"],
            "authorized_h0_candidate_id": "Q1",
            "next_allowed_action": f"run_q1_{next_phase.casefold()}",
            "live_h0_authorization": {
                "candidate_id": "Q1",
                "phase": next_phase,
                **bindings,
                "prior_phase_completion": prior_reference,
            },
            "h0_phase_completions": completions,
        }
    )
    return state


def _validate_phase_preview(
    state: Mapping[str, Any],
    *,
    root: Path,
    candidate_id: str,
    phase: str,
) -> None:
    preview: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=root,
            prefix=".h0-phase-preview.",
            suffix=".json",
            delete=False,
        ) as handle:
            preview = Path(handle.name)
            handle.write(canonical_json_bytes(state))
            handle.flush()
            os.fsync(handle.fileno())
        authorization = authorize_h0_live_entry(
            state_path=preview,
            candidate_id=candidate_id,
            phase=phase,
        )
        load_authorized_h0_runtime_identity(authorization, root=root)
    except (H0StateGateError, H0ManifestError, OSError) as exc:
        raise _fail("generated_live_state_failed_runtime_validation") from exc
    finally:
        if preview is not None:
            preview.unlink(missing_ok=True)


def transition_h0_repair_bound(
    state_path: str | Path,
    *,
    root: str | Path,
    manifest_verification: Mapping[str, Any],
    tdd_evidence: Mapping[str, Any],
    repair_decision_path: str,
    repair_decision_sha256: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate and optionally commit the still-live-forbidden repair binding."""

    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root_path = Path(root).resolve()
    target = _state_target(state_path, root_path)

    def derive() -> dict[str, Any]:
        _, source = _load_canonical_state_snapshot(target)
        return build_h0_repair_bound_state(
            source,
            root=root_path,
            manifest_verification=manifest_verification,
            tdd_evidence=tdd_evidence,
            repair_decision_path=repair_decision_path,
            repair_decision_sha256=repair_decision_sha256,
        )

    if dry_run:
        return derive()
    with _state_transition_lock(target):
        state = derive()
        _atomic_write(target, state)
    return state


def transition_h0_replacement_live(
    state_path: str | Path,
    *,
    root: str | Path,
    dry_run: bool = True,
    state_builder: Callable[..., Any] = build_h0_replacement_live_state,
    preview_validator: Callable[..., Any] = _validate_phase_preview,
) -> dict[str, Any]:
    """Validate and optionally consume the one-shot replacement live grant."""

    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root_path = Path(root).resolve()
    target = _state_target(state_path, root_path)

    def derive() -> dict[str, Any]:
        _, source = _load_canonical_state_snapshot(target)
        state = state_builder(source, root=root_path)
        if not isinstance(state, Mapping):
            raise _fail("replacement_state_builder_invalid")
        state = deepcopy(dict(state))
        preview_validator(
            state,
            root=root_path,
            candidate_id="Q1",
            phase="H0-A",
        )
        return state

    if dry_run:
        return derive()
    with _state_transition_lock(target):
        state = derive()
        _atomic_write(target, state)
    return state


def transition_h0_successor_phase_live(
    state_path: str | Path,
    *,
    root: str | Path,
    completed_phase: str,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    runtime_definition_sha256: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate a terminal checkpoint and optionally commit the next phase grant."""

    if not isinstance(dry_run, bool):
        raise _fail("dry_run_not_boolean")
    root_path = Path(root).resolve()
    target = _state_target(state_path, root_path)
    next_phase = "H0-B" if completed_phase == "H0-A" else "H0-C"

    def derive() -> dict[str, Any]:
        _, source = _load_canonical_state_snapshot(target)
        state = build_h0_successor_phase_live_state(
            source,
            root=root_path,
            completed_phase=completed_phase,
            stage_attempt_id=stage_attempt_id,
            checkpoint_index_path=checkpoint_index_path,
            checkpoint_index_sha256=checkpoint_index_sha256,
            runtime_definition_sha256=runtime_definition_sha256,
        )
        _validate_phase_preview(
            state,
            root=root_path,
            candidate_id="Q1",
            phase=next_phase,
        )
        return state

    if dry_run:
        return derive()
    with _state_transition_lock(target):
        state = derive()
        _atomic_write(target, state)
    return state
