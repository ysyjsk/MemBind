"""Seal a successful temporary-provider V7 development NULL result."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .observer_campaign import verify_observer_manifest


class DevelopmentNullError(RuntimeError):
    """Development evidence cannot authorize a sealed NULL conclusion."""


_DIGEST = re.compile(r"[0-9a-f]{64}")
_SCIENTIFIC_METHOD_SELECTION_SHA256 = (
    "0a2958aeeedc2b7b8762247d5d6cf15252e2b6b737b5807a51a127461a632c53"
)


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DevelopmentNullError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise DevelopmentNullError(f"{label} is invalid")
    return value


def _attempt_rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
        rows = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DevelopmentNullError("development attempt journal is unreadable") from error
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise DevelopmentNullError("development attempt journal is invalid")
    return rows


def _number(value: Any, *, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DevelopmentNullError(f"development NULL metric is invalid: {label}")
    return value


def build_development_null_terminal(
    *,
    campaign_root: str | Path,
    attempt_journal_path: str | Path,
    scientific_method_selection_path: str | Path,
) -> dict[str, Any]:
    """Build a content-free terminal summary from one valid development campaign."""

    root = Path(campaign_root).resolve()
    try:
        verification = verify_observer_manifest(root)
    except Exception as error:
        raise DevelopmentNullError("development campaign seal is invalid") from error
    identity = verification.get("campaign_identity")
    if (
        not isinstance(identity, Mapping)
        or identity.get("schema_version")
        != "membind.v7.development-campaign-identity.v2"
        or identity.get("campaign_scope") != "TEMPORARY_PROVIDER_DEVELOPMENT"
        or identity.get("formal_r1_r3_eligible") is not False
        or identity.get("live_treatment_authorized") is not False
        or identity.get("provider_swap_requires_new_formal_campaign") is not True
        or identity.get("treatment_calls") != 0
        or identity.get("response_replay_calls") != 0
    ):
        raise DevelopmentNullError("development campaign identity is invalid")
    manifest_sha256 = str(verification.get("manifest_sha256"))
    if _DIGEST.fullmatch(manifest_sha256) is None:
        raise DevelopmentNullError("development campaign manifest digest is invalid")

    attempt_path = Path(attempt_journal_path).resolve()
    rows = _attempt_rows(attempt_path)
    terminal = rows[-1]
    run_id = identity.get("run_id")
    if (
        terminal.get("event") != "ATTEMPT_SUCCESS"
        or terminal.get("run_id") != run_id
        or terminal.get("completed_block_count") != 3
        or terminal.get("manifest_sha256") != manifest_sha256
        or terminal.get("treatment_calls") != 0
        or terminal.get("response_replay_calls") != 0
        or any(row.get("event") == "ATTEMPT_FAILURE" for row in rows)
    ):
        raise DevelopmentNullError("development attempt did not terminate successfully")

    selection_path = root / "DEVELOPMENT_METHOD_SELECTION.json"
    selection = _object(selection_path, label="development method selection")
    gates = selection.get("gates")
    reasons = selection.get("reasons")
    if (
        selection.get("schema_version")
        != "membind.v7.development-method-selection.v1"
        or selection.get("status") != "DEVELOPMENT_NULL"
        or selection.get("selected_method") != "NULL"
        or selection.get("implementation_authorized") is not False
        or selection.get("live_treatment_authorized") is not False
        or selection.get("formal_r1_r3_eligible") is not False
        or selection.get("provider_swap_requires_new_formal_campaign") is not True
        or not isinstance(gates, Mapping)
        or set(gates) != set("ABCDE")
        or gates.get("A") is not True
        or any(gates.get(name) is not False for name in "BCDE")
        or not isinstance(reasons, list)
        or not reasons
        or (root / "METHOD_SELECTION.json").exists()
    ):
        raise DevelopmentNullError("development selection is not a legal NULL")

    method_path = Path(scientific_method_selection_path).resolve()
    scientific_digest = _sha256(method_path)
    if scientific_digest != _SCIENTIFIC_METHOD_SELECTION_SHA256:
        raise DevelopmentNullError("scientific method selection changed")
    decision = _object(root / "R3_DECISION_INPUT.json", label="R3 decision input")
    metrics = {
        "false_stable_count": _number(
            decision.get("false_stable_count"), label="false_stable_count"
        ),
        "false_unaffected_count": _number(
            decision.get("false_unaffected_count"), label="false_unaffected_count"
        ),
        "stable_prediction_count": _number(
            decision.get("stable_prediction_count"), label="stable_prediction_count"
        ),
        "csp": _number(decision.get("csp"), label="csp"),
        "csp_preregistered_min": _number(
            decision.get("csp_preregistered_min"), label="csp_preregistered_min"
        ),
        "gross_saved_cp_lb_ns": _number(
            decision.get("gross_saved_cp_lb_ns"), label="gross_saved_cp_lb_ns"
        ),
        "certificate_cost_ub_ns": _number(
            decision.get("certificate_cost_ub_ns"), label="certificate_cost_ub_ns"
        ),
        "repair_cost_ub_ns": _number(
            decision.get("repair_cost_ub_ns"), label="repair_cost_ub_ns"
        ),
        "required_online_headroom_ns": _number(
            decision.get("required_online_headroom_ns"),
            label="required_online_headroom_ns",
        ),
        "offline_opportunity_margin_ns": _number(
            selection.get("offline_opportunity_margin_ns"),
            label="offline_opportunity_margin_ns",
        ),
        "early_memory_specific": decision.get("early_memory_specific") is True,
        "sca_within_bound": decision.get("sca_within_bound") is True,
        "meaningful_reconvergence": decision.get("meaningful_reconvergence") is True,
        "replay_allowed": decision.get("replay_allowed") is True,
    }
    return {
        "schema_version": "membind.v7.development-null-terminal.v1",
        "status": "V7_DEVELOPMENT_NULL_NO_MEMORY_SPECIFIC_METHOD",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "run_id": run_id,
        "selected_method": "NULL",
        "method_implementation_authorized": False,
        "live_treatment_authorized": False,
        "formal_r1_r3_eligible": False,
        "provider_swap_requires_new_formal_campaign": True,
        "gate_result": dict(gates),
        "reasons": [str(reason) for reason in reasons],
        "metrics": metrics,
        "m0_ceiling": "UNAVAILABLE_REPLAY_ALLOWED_FALSE",
        "m1_status": "NOT_AUTHORIZED_BY_OPPORTUNITY_GATE",
        "m2_status": "NOT_AUTHORIZED_BY_OPPORTUNITY_GATE",
        "skipped_as_unauthorized": [
            "METHOD_IMPLEMENTATION",
            "R4_SELECTED_METHOD_FREEZE",
            "R5_ADVERSARIAL_DIFFERENTIAL",
            "R6A_ONLINE_ECONOMICS",
            "R6B_SIX_TO_TWELVE_SOURCE",
            "R7_DEVELOPMENT_QUALIFICATION",
            "R8_HELD_OUT_PUBLICATION",
        ],
        "next_action": "NEW_FORMAL_R1_R3_CAMPAIGN_REQUIRED_AFTER_PROVIDER_SWAP",
        "campaign_manifest_sha256": manifest_sha256,
        "development_method_selection_sha256": _sha256(selection_path),
        "attempt_journal_sha256": _sha256(attempt_path),
        "scientific_method_selection_sha256": scientific_digest,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "raw_embedding_persisted": False,
        "response_hash_persisted": False,
        "credentials_recorded": False,
    }


def write_development_null_terminal(
    path: str | Path,
    *,
    campaign_root: str | Path,
    attempt_journal_path: str | Path,
    scientific_method_selection_path: str | Path,
) -> dict[str, Any]:
    """Write one exclusive private terminal artifact."""

    target = Path(path)
    value = build_development_null_terminal(
        campaign_root=campaign_root,
        attempt_journal_path=attempt_journal_path,
        scientific_method_selection_path=scientific_method_selection_path,
    )
    payload = (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise DevelopmentNullError("development NULL terminal already exists") from error
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("development NULL terminal write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value


__all__ = [
    "DevelopmentNullError",
    "build_development_null_terminal",
    "write_development_null_terminal",
]
