"""Fail-closed terminal-state evidence for an uncompleted real R1-R3 campaign."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .observer_campaign import (
    ObserverArtifactError,
    verify_observer_manifest,
    write_observer_artifacts,
)


_DIGEST = re.compile(r"[0-9a-f]{64}")


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ObserverArtifactError(f"{label} digest is invalid")
    return value


def validate_blocked_attempt_chain(
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Require the exact invalid-attempt chain before declaring system blocked."""

    if len(attempts) != 3 or any(not isinstance(row, Mapping) for row in attempts):
        raise ObserverArtifactError("blocked terminal requires a three-attempt replacement chain")
    rows = [dict(row) for row in attempts]
    run_ids = [row.get("run_id") for row in rows]
    if any(not isinstance(run_id, str) or not run_id for run_id in run_ids):
        raise ObserverArtifactError("blocked attempt run identity is invalid")
    if len(set(run_ids)) != 3:
        raise ObserverArtifactError("blocked attempt run identity is not unique")
    if (
        rows[0].get("replacement_of") is not None
        or rows[1].get("replacement_of") != run_ids[0]
        or rows[2].get("replacement_of") != run_ids[1]
    ):
        raise ObserverArtifactError("blocked attempt replacement chain is invalid")
    for row in rows:
        if (
            row.get("attempt_validity") != "INVALID_FOR_R1_R3_GATES"
            or row.get("gate_outcome") != "NOT_EVALUATED"
            or row.get("selected_method") is not None
            or row.get("completed_block_count") != 0
            or row.get("treatment_calls") != 0
            or row.get("response_replay_calls") != 0
        ):
            raise ObserverArtifactError("blocked attempt contains gate or treatment evidence")
        _digest(row.get("error_message_sha256"), label="blocked attempt error")
    timeout_rows = (rows[0], rows[2])
    if any(
        row.get("failure_class") != "INFRASTRUCTURE_PROVIDER_TIMEOUT"
        or row.get("error_type") != "openai.APITimeoutError"
        for row in timeout_rows
    ):
        raise ObserverArtifactError("blocked terminal requires repeated provider timeout")
    if timeout_rows[0]["error_message_sha256"] != timeout_rows[1]["error_message_sha256"]:
        raise ObserverArtifactError("blocked timeout signature differs across replacement")
    if (
        rows[1].get("failure_class") != "OBSERVER_RUNTIME_FAILURE"
        or rows[1].get("error_type") != "graphiti_core.errors.NodeNotFoundError"
    ):
        raise ObserverArtifactError("blocked replacement chain does not identify the harness failure")
    return {
        "terminal_state": "V7_THEORY_OR_SYSTEM_BLOCKED",
        "blocker": "SILICONFLOW_STRUCTURED_EXTRACTION_TIMEOUT",
        "gate_a_e_evaluated": False,
        "selected_method": None,
        "attempt_count": 3,
        "timeout_attempt_count": 2,
        "timeout_error_message_sha256": timeout_rows[0]["error_message_sha256"],
        "harness_failure_run_id": rows[1]["run_id"],
        "post_fix_timeout_run_id": rows[2]["run_id"],
        "treatment_calls": 0,
        "response_replay_calls": 0,
    }


def seal_system_blocked_terminal(
    root: str | Path,
    *,
    protocol_sha256: str,
    attempts: Sequence[Mapping[str, Any]],
    evidence_files: Sequence[Mapping[str, Any]],
    harness_source_sha256: str,
) -> dict[str, Any]:
    """Seal a blocker without fabricating R1-R3 or a NULL opportunity result."""

    protocol_digest = _digest(protocol_sha256, label="protocol")
    harness_digest = _digest(harness_source_sha256, label="harness source")
    terminal_input = validate_blocked_attempt_chain(attempts)
    files: list[dict[str, str]] = []
    for item in evidence_files:
        if not isinstance(item, Mapping):
            raise ObserverArtifactError("blocked evidence file is invalid")
        name = item.get("path")
        if not isinstance(name, str) or Path(name).name != name:
            raise ObserverArtifactError("blocked evidence file path is invalid")
        files.append({"path": name, "sha256": _digest(item.get("sha256"), label="evidence file")})
    if len(files) < 2 or len({item["path"] for item in files}) != len(files):
        raise ObserverArtifactError("blocked evidence inventory is incomplete")
    blocker = {
        "schema_version": "membind.v7.system-blocker-evidence.v1",
        "status": "SEALED_SYSTEM_BLOCKER",
        "protocol_sha256": protocol_digest,
        "harness_source_sha256": harness_digest,
        "attempts": [dict(row) for row in attempts],
        "evidence_files": files,
        **terminal_input,
    }
    method = {
        "schema_version": "membind.v7.method-selection.v3",
        "status": "NOT_EVALUATED_SYSTEM_BLOCKED",
        "authorized": False,
        "treatment_authorized": False,
        "selected_method": None,
        "selected_operator": None,
        "selected_seam": None,
        "gates": {name: "NOT_EVALUATED" for name in "ABCDE"},
        "reason_codes": ["REAL_R1_INCOMPLETE", terminal_input["blocker"]],
        "treatment_calls": 0,
    }
    terminal = {
        "schema_version": "membind.v7.terminal-state.v2",
        "state": "V7_THEORY_OR_SYSTEM_BLOCKED",
        "blocker": terminal_input["blocker"],
        "real_r1_completed": False,
        "real_r2_completed": False,
        "real_r3_completed": False,
        "gate_a_e_evaluated": False,
        "selected_method": None,
        "treatment_runtime_implemented": False,
        "live_treatment_authorized": False,
        "retry_policy": "BOUNDED_REPLACEMENT_EXHAUSTED",
        "next_legal_action": "new preregistered campaign after provider infrastructure changes",
        "treatment_calls": 0,
    }
    campaign_identity = {
        "schema_version": "membind.v7.system-blocked-campaign-identity.v1",
        "protocol_sha256": protocol_digest,
        "attempt_run_ids": [str(row["run_id"]) for row in attempts],
        "terminal_state": terminal["state"],
        "treatment_calls": 0,
        "response_replay_calls": 0,
    }
    sealed = write_observer_artifacts(
        root,
        {
            "BLOCKER_EVIDENCE.json": blocker,
            "METHOD_SELECTION.json": method,
            "V7_TERMINAL_STATE.json": terminal,
        },
        campaign_identity=campaign_identity,
    )
    return {**sealed, "verification": verify_observer_manifest(root), "terminal": terminal}


__all__ = ["seal_system_blocked_terminal", "validate_blocked_attempt_chain"]
