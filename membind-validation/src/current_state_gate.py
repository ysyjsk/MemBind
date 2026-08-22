"""Fail-closed authorization for MemBind operations that can perform live I/O.

The gate intentionally has no environment-variable or command-line bypass. Tests may
inject a checker into higher-level functions, while production defaults always read
``CURRENT_STATE.json`` before loading secrets, creating artifacts, or opening clients.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / "CURRENT_STATE.json"
EXPECTED_PROTOCOL_VERSION = "current-validation-v1.3"


class LiveAction(str, Enum):
    H0_CANDIDATE = "h0_candidate"
    V2_R = "v2_r"
    V3_R = "v3_r"
    CALIBRATION = "calibration"
    FORMAL = "formal"
    ENVIRONMENT_GATE = "environment_gate"
    MODEL_METADATA = "model_metadata"
    EMBEDDING_IDENTITY = "embedding_identity"
    STRUCTURED_COMPATIBILITY = "structured_compatibility"
    NEO4J_INTEGRATION = "neo4j_integration"
    SERVICE_STATUS = "service_status"
    SERVICE_ADMIN = "service_admin"
    NATIVE_CHARACTERIZATION_C0 = "native_characterization_c0"
    NATIVE_CHARACTERIZATION_C2 = "native_characterization_c2"
    NATIVE_CHARACTERIZATION_C4 = "native_characterization_c4"
    NATIVE_CHARACTERIZATION_C5 = "native_characterization_c5"
    MEMBIND_V5 = "membind_v5"


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str
    action: str


class LiveActionDenied(RuntimeError):
    """A sanitized denial that never includes state contents or credentials."""

    def __init__(self, reason: str, *, action: str | None = None) -> None:
        message = "live action denied"
        if action is not None:
            message += f": action={action}"
        message += f" reason={reason}"
        super().__init__(message)
        self.reason = reason
        self.action = action


_EXACT_STAGE_SCOPES: dict[LiveAction, set[tuple[str, str]]] = {
    LiveAction.V2_R: {("V2-R", "v2_r_live_only")},
    LiveAction.V3_R: {("V3-R", "v3_r_live_only")},
    LiveAction.CALIBRATION: {
        ("V4", "v4_calibration_live_only"),
        ("V5", "v5_calibration_live_only"),
    },
    LiveAction.FORMAL: {("V6", "v6_formal_live_only")},
    LiveAction.ENVIRONMENT_GATE: {("H0", "h0_environment_gate_live_only")},
    LiveAction.MODEL_METADATA: {("H0", "h0_model_metadata_live_only")},
    LiveAction.EMBEDDING_IDENTITY: {("H0", "h0_embedding_identity_live_only")},
    LiveAction.STRUCTURED_COMPATIBILITY: {
        ("H0", "h0_structured_compatibility_live_only")
    },
    LiveAction.NEO4J_INTEGRATION: {
        ("H0", "h0_neo4j_integration_live_only"),
        ("V2-R", "v2_r_live_only"),
        ("V3-R", "v3_r_live_only"),
    },
    LiveAction.SERVICE_STATUS: {("H0", "service_status_live_only")},
    LiveAction.SERVICE_ADMIN: {("H0", "service_admin_live_only")},
    LiveAction.NATIVE_CHARACTERIZATION_C0: {
        ("NATIVE_CHARACTERIZATION", "native_characterization_c0_live_only")
    },
    LiveAction.NATIVE_CHARACTERIZATION_C2: {
        ("NATIVE_CHARACTERIZATION", "native_characterization_c2_live_only")
    },
    LiveAction.NATIVE_CHARACTERIZATION_C4: {
        ("NATIVE_CHARACTERIZATION", "native_characterization_c4_live_only")
    },
    LiveAction.NATIVE_CHARACTERIZATION_C5: {
        ("NATIVE_CHARACTERIZATION", "native_characterization_c5_live_only")
    },
    LiveAction.MEMBIND_V5: {
        ("V5", "membind_v5_live_only")
    },
}


def _coerce_action(action: LiveAction | str) -> LiveAction | None:
    if isinstance(action, LiveAction):
        return action
    try:
        return LiveAction(str(action))
    except ValueError:
        return None


def evaluate_live_action(
    state: Mapping[str, Any],
    action: LiveAction | str,
    *,
    candidate_id: str | None = None,
) -> GateDecision:
    """Purely evaluate one live action against a parsed current-state mapping."""

    normalized = _coerce_action(action)
    action_name = normalized.value if normalized is not None else str(action)
    if normalized is None:
        return GateDecision(False, "unknown_live_action", action_name)
    if state.get("protocol_version") != EXPECTED_PROTOCOL_VERSION:
        return GateDecision(False, "protocol_version_mismatch", action_name)

    live_h0 = state.get("live_h0_candidate_authorized")
    if not isinstance(live_h0, bool):
        return GateDecision(False, "h0_authorization_not_boolean", action_name)
    actions = state.get("authorized_live_actions")
    if not isinstance(actions, list) or any(not isinstance(item, str) for item in actions):
        return GateDecision(False, "authorized_live_actions_invalid", action_name)
    if normalized.value not in actions:
        return GateDecision(False, "action_not_authorized", action_name)

    stage = state.get("current_stage")
    scope = state.get("current_action_scope")
    if not isinstance(stage, str) or not isinstance(scope, str):
        return GateDecision(False, "stage_or_scope_invalid", action_name)

    if normalized is LiveAction.H0_CANDIDATE:
        if not live_h0:
            return GateDecision(False, "h0_candidate_not_authorized", action_name)
        if stage != "H0":
            return GateDecision(False, "h0_stage_mismatch", action_name)
        if candidate_id not in {"Q1", "Q2", "Q3"}:
            return GateDecision(False, "h0_candidate_id_invalid", action_name)
        if state.get("authorized_h0_candidate_id") != candidate_id:
            return GateDecision(False, "h0_candidate_id_mismatch", action_name)
        expected_prefix = f"h0_{candidate_id.casefold()}_"
        if not scope.startswith(expected_prefix) or not scope.endswith("_live_only"):
            return GateDecision(False, "h0_scope_mismatch", action_name)
        return GateDecision(True, "authorized", action_name)

    allowed_stage_scopes = _EXACT_STAGE_SCOPES.get(normalized)
    if allowed_stage_scopes is None or (stage, scope) not in allowed_stage_scopes:
        return GateDecision(False, "stage_scope_mismatch", action_name)
    if normalized is LiveAction.SERVICE_ADMIN and state.get(
        "service_admin_authorized"
    ) is not True:
        return GateDecision(False, "service_admin_not_authorized", action_name)
    return GateDecision(True, "authorized", action_name)


def require_live_action(
    action: LiveAction | str,
    *,
    state_path: str | Path = DEFAULT_STATE_PATH,
    candidate_id: str | None = None,
) -> GateDecision:
    """Read current state and raise a sanitized error unless the action is allowed."""

    normalized = _coerce_action(action)
    action_name = normalized.value if normalized is not None else str(action)
    path = Path(state_path)
    try:
        encoded = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        reason = "state_missing" if isinstance(exc, FileNotFoundError) else "state_unreadable"
        raise LiveActionDenied(reason, action=action_name) from None
    try:
        state = json.loads(encoded)
    except json.JSONDecodeError:
        raise LiveActionDenied("state_invalid_json", action=action_name) from None
    if not isinstance(state, dict):
        raise LiveActionDenied("state_not_object", action=action_name)
    decision = evaluate_live_action(state, action, candidate_id=candidate_id)
    if not decision.allowed:
        raise LiveActionDenied(decision.reason, action=decision.action)
    return decision


def _main() -> int:
    parser = argparse.ArgumentParser(description="Check MemBind live-I/O authorization")
    subparsers = parser.add_subparsers(dest="command", required=True)
    require = subparsers.add_parser("require")
    require.add_argument("--action", required=True)
    require.add_argument("--candidate-id")
    require.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    args = parser.parse_args()
    try:
        require_live_action(
            args.action,
            state_path=args.state,
            candidate_id=args.candidate_id,
        )
    except LiveActionDenied as exc:
        print(str(exc))
        return 2
    print(f"live action authorized: action={args.action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
