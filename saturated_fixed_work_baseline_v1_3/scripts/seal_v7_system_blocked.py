#!/usr/bin/env python3
"""Seal V7's fail-closed terminal state from preserved invalid attempts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (
    ObserverArtifactError,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.terminal import (
    seal_system_blocked_terminal,
)


_FAILURE_NAME = re.compile(r"\.(v7-real-observer-[a-z0-9-]+)\.failure\.json")


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ObserverArtifactError(f"blocked attempt artifact is unreadable: {path.name}") from None
    if not isinstance(value, dict):
        raise ObserverArtifactError(f"blocked attempt artifact is not an object: {path.name}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_failure(path: Path, protocol_sha256: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    match = _FAILURE_NAME.fullmatch(path.name)
    if match is None:
        raise ObserverArtifactError("blocked attempt failure filename is invalid")
    run_id = match.group(1)
    value = _object(path)
    if value.get("provider_key_recorded") is not False:
        raise ObserverArtifactError("blocked attempt does not prove secret redaction")
    if value.get("protocol_sha256") not in {None, protocol_sha256}:
        raise ObserverArtifactError("blocked attempt protocol digest differs")
    error_type = value.get("error_type")
    failure_class = value.get("failure_class")
    if failure_class is None and error_type == "openai.APITimeoutError":
        failure_class = "INFRASTRUCTURE_PROVIDER_TIMEOUT"
    attempt = {
        "run_id": value.get("run_id") or run_id,
        "replacement_of": value.get("replacement_of"),
        "failure_class": failure_class,
        "attempt_validity": value.get("attempt_validity") or "INVALID_FOR_R1_R3_GATES",
        "gate_outcome": value.get("gate_outcome") or "NOT_EVALUATED",
        "selected_method": value.get("selected_method"),
        "error_type": error_type,
        "error_message_sha256": value.get("error_message_sha256"),
        "completed_block_count": value.get("completed_block_count", 0),
        "treatment_calls": value.get("treatment_calls", 0),
        "response_replay_calls": value.get("response_replay_calls", 0),
    }
    evidence = [{"path": path.name, "sha256": _sha256(path)}]
    journal_name = value.get("attempt_journal")
    journal_digest = value.get("attempt_journal_sha256")
    if journal_name is not None:
        if not isinstance(journal_name, str) or Path(journal_name).name != journal_name:
            raise ObserverArtifactError("blocked attempt journal path is invalid")
        journal = path.parent / journal_name
        if not journal.is_file() or _sha256(journal) != journal_digest:
            raise ObserverArtifactError("blocked attempt journal digest mismatch")
        rows = [json.loads(line) for line in journal.read_text(encoding="ascii").splitlines()]
        if not rows or rows[-1].get("event") != "ATTEMPT_FAILURE":
            raise ObserverArtifactError("blocked attempt journal is incomplete")
        for key in (
            "run_id",
            "replacement_of",
            "failure_class",
            "attempt_validity",
            "gate_outcome",
            "selected_method",
            "error_type",
            "error_message_sha256",
            "completed_block_count",
            "treatment_calls",
            "response_replay_calls",
        ):
            if rows[-1].get(key) != attempt.get(key):
                raise ObserverArtifactError("blocked failure and journal disagree")
        evidence.append({"path": journal.name, "sha256": journal_digest})
    if (path.parent / run_id).exists():
        raise ObserverArtifactError("invalid blocked attempt unexpectedly has a sealed output root")
    return attempt, evidence


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    v7 = repository / "saturated_fixed_work_baseline_v1_3/v7"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=v7 / "R1_R3_PROTOCOL_FREEZE.json",
    )
    parser.add_argument("--failure", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    protocol_sha256 = _sha256(args.protocol)
    attempts: list[dict[str, Any]] = []
    evidence: list[dict[str, str]] = []
    for path in args.failure:
        attempt, members = _normalize_failure(path.resolve(), protocol_sha256)
        attempts.append(attempt)
        evidence.extend(members)
    harness = (
        repository
        / "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/observer_campaign.py"
    )
    sealed = seal_system_blocked_terminal(
        args.output,
        protocol_sha256=protocol_sha256,
        attempts=attempts,
        evidence_files=evidence,
        harness_source_sha256=_sha256(harness),
    )
    print(
        json.dumps(
            {
                "status": sealed["status"],
                "terminal_state": sealed["terminal"]["state"],
                "manifest_sha256": sealed["manifest_sha256"],
                "treatment_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
