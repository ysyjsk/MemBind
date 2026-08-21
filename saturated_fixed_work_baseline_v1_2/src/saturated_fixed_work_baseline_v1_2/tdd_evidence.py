"""Append-only RED/GREEN evidence verification and hash-bound amendments."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


class TddEvidenceError(ValueError):
    """The TDD journal is malformed, incomplete, or internally inconsistent."""


SCHEMA_VERSION = "membind.saturated-fixed-work.tdd-evidence.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise TddEvidenceError(f"{field.upper()}_INVALID")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise TddEvidenceError(f"{field.upper()}_INVALID") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TddEvidenceError(f"{field.upper()}_INVALID")
    return parsed


def _load_lines(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if path.is_symlink() or not path.is_file():
        raise TddEvidenceError("TDD_EVIDENCE_UNREADABLE")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise TddEvidenceError("TDD_EVIDENCE_UNREADABLE") from None
    if not raw or not raw.endswith("\n"):
        raise TddEvidenceError("TDD_EVIDENCE_TRUNCATED")
    lines = raw.splitlines()
    try:
        rows = [json.loads(line) for line in lines]
    except json.JSONDecodeError:
        raise TddEvidenceError("TDD_EVIDENCE_JSON_INVALID") from None
    if any(not isinstance(row, dict) for row in rows):
        raise TddEvidenceError("TDD_EVIDENCE_ROW_INVALID")
    return lines, rows


def _validate_observation(row: dict[str, Any]) -> datetime:
    if row.get("schema_version") != SCHEMA_VERSION:
        raise TddEvidenceError("TDD_EVIDENCE_SCHEMA_INVALID")
    stage = row.get("stage")
    command = row.get("command")
    summary = row.get("output_summary")
    exit_code = row.get("exit_code")
    if (
        not isinstance(stage, str)
        or not stage
        or not isinstance(command, str)
        or not command
        or not isinstance(summary, str)
        or not summary
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
    ):
        raise TddEvidenceError("TDD_EVIDENCE_OBSERVATION_INVALID")
    event = row.get("event")
    if event == "RED" and exit_code == 0:
        raise TddEvidenceError("RED_EXIT_CODE_INVALID")
    expected_blocked = (
        event == "GREEN"
        and stage.startswith("L0_")
        and summary.startswith("expected blocked terminal state")
    )
    if event == "GREEN" and exit_code != 0 and not expected_blocked:
        raise TddEvidenceError("GREEN_EXIT_CODE_INVALID")
    if event not in {"RED", "GREEN"}:
        raise TddEvidenceError("TDD_EVIDENCE_EVENT_INVALID")
    return _timestamp(row.get("observed_at"), field="observed_at")


def _validate_amendments(
    lines: Sequence[str], rows: Sequence[dict[str, Any]]
) -> tuple[set[tuple[str, str]], list[str]]:
    row_hashes = [hashlib.sha256(line.encode("utf-8")).hexdigest() for line in lines]
    index_by_hash: dict[str, int] = {}
    for index, digest in enumerate(row_hashes):
        if digest in index_by_hash:
            raise TddEvidenceError("TDD_EVIDENCE_DUPLICATE_LINE")
        index_by_hash[digest] = index
    corrections: set[tuple[str, str]] = set()
    amended_stages: set[str] = set()
    amended_targets: set[str] = set()
    for amendment_index, row in enumerate(rows):
        if row.get("event") != "AMENDMENT":
            continue
        if (
            row.get("schema_version") != SCHEMA_VERSION
            or row.get("amendment_type") != "OBSERVATION_ORDER_CORRECTION"
            or row.get("corrected_relation") != "BEFORE"
        ):
            raise TddEvidenceError("AMENDMENT_CONTRACT_INVALID")
        target = row.get("target_line_sha256")
        related = row.get("related_line_sha256")
        reason = row.get("reason")
        if (
            not isinstance(target, str)
            or _SHA256.fullmatch(target) is None
            or not isinstance(related, str)
            or _SHA256.fullmatch(related) is None
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise TddEvidenceError("AMENDMENT_CONTRACT_INVALID")
        _timestamp(row.get("amended_at"), field="amended_at")
        candidate = dict(row)
        observed_payload = candidate.pop("payload_sha256", None)
        if observed_payload != _payload_hash(candidate):
            raise TddEvidenceError("AMENDMENT_PAYLOAD_HASH_INVALID")
        if target in amended_targets:
            raise TddEvidenceError("AMENDMENT_TARGET_DUPLICATE")
        amended_targets.add(target)
        target_index = index_by_hash.get(target)
        related_index = index_by_hash.get(related)
        if target_index is None or target_index >= amendment_index:
            raise TddEvidenceError("AMENDMENT_TARGET_ROW_UNKNOWN")
        if related_index is None or related_index >= amendment_index:
            raise TddEvidenceError("AMENDMENT_RELATED_ROW_UNKNOWN")
        target_row = rows[target_index]
        related_row = rows[related_index]
        if (
            target_index >= related_index
            or target_row.get("event") != "RED"
            or related_row.get("event") != "GREEN"
            or target_row.get("stage") != related_row.get("stage")
            or row.get("stage") != target_row.get("stage")
        ):
            raise TddEvidenceError("AMENDMENT_RELATION_INVALID")
        corrections.add((target, related))
        amended_stages.add(str(target_row["stage"]))
    return corrections, sorted(amended_stages)


def verify_tdd_evidence(
    path: Path, *, required_red_green_stages: Sequence[str] = ()
) -> dict[str, Any]:
    """Verify immutable observations and any later hash-bound order corrections."""

    lines, rows = _load_lines(path)
    line_hashes = [hashlib.sha256(line.encode("utf-8")).hexdigest() for line in lines]
    observations: dict[str, list[tuple[int, dict[str, Any], datetime, str]]] = {}
    for index, (row, digest) in enumerate(zip(rows, line_hashes)):
        if row.get("event") == "AMENDMENT":
            continue
        observed_at = _validate_observation(row)
        observations.setdefault(str(row["stage"]), []).append(
            (index, row, observed_at, digest)
        )
    corrections, amended_stages = _validate_amendments(lines, rows)
    verified_stages: list[str] = []
    green_only_stages: list[str] = []
    unresolved_red_stages: list[str] = []
    for stage, events in sorted(observations.items()):
        reds = [event for event in events if event[1]["event"] == "RED"]
        greens = [event for event in events if event[1]["event"] == "GREEN"]
        if not reds:
            green_only_stages.append(stage)
            continue
        if not greens:
            unresolved_red_stages.append(stage)
            continue
        valid_pair = False
        saw_line_order_pair = False
        for red in reds:
            for green in greens:
                if red[0] >= green[0]:
                    continue
                saw_line_order_pair = True
                if red[2] < green[2] or (red[3], green[3]) in corrections:
                    valid_pair = True
                    break
            if valid_pair:
                break
        if not saw_line_order_pair:
            raise TddEvidenceError("RED_GREEN_JOURNAL_ORDER_INVALID")
        if not valid_pair:
            raise TddEvidenceError("OBSERVATION_ORDER_INVALID")
        verified_stages.append(stage)
    required = tuple(required_red_green_stages)
    if (
        any(not isinstance(stage, str) or not stage for stage in required)
        or len(set(required)) != len(required)
        or any(stage not in verified_stages for stage in required)
    ):
        raise TddEvidenceError("REQUIRED_RED_GREEN_STAGE_INCOMPLETE")
    return {
        "schema_version": "membind.saturated-fixed-work.tdd-verification.v1",
        "verified": True,
        "journal_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "observation_count": sum(
            row.get("event") in {"RED", "GREEN"} for row in rows
        ),
        "amendment_count": sum(row.get("event") == "AMENDMENT" for row in rows),
        "verified_red_green_stages": verified_stages,
        "green_only_stages": green_only_stages,
        "unresolved_red_stages": unresolved_red_stages,
        "amended_stage_pairs": amended_stages,
    }


def append_observation_order_amendment(
    path: Path,
    *,
    target_line_sha256: str,
    related_line_sha256: str,
    amended_at: str,
    reason: str,
) -> dict[str, Any]:
    """Append an order-only correction while preserving every original byte."""

    if path.is_symlink() or not path.is_file():
        raise TddEvidenceError("TDD_EVIDENCE_UNREADABLE")
    if (
        _SHA256.fullmatch(target_line_sha256) is None
        or _SHA256.fullmatch(related_line_sha256) is None
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise TddEvidenceError("AMENDMENT_CONTRACT_INVALID")
    _timestamp(amended_at, field="amended_at")
    lines, rows = _load_lines(path)
    hashes = [hashlib.sha256(line.encode("utf-8")).hexdigest() for line in lines]
    try:
        target_index = hashes.index(target_line_sha256)
    except ValueError:
        raise TddEvidenceError("AMENDMENT_TARGET_ROW_UNKNOWN") from None
    target = rows[target_index]
    if target.get("event") != "RED" or not isinstance(target.get("stage"), str):
        raise TddEvidenceError("AMENDMENT_TARGET_ROW_INVALID")
    body = {
        "schema_version": SCHEMA_VERSION,
        "stage": target["stage"],
        "event": "AMENDMENT",
        "amendment_type": "OBSERVATION_ORDER_CORRECTION",
        "target_line_sha256": target_line_sha256,
        "related_line_sha256": related_line_sha256,
        "corrected_relation": "BEFORE",
        "amended_at": amended_at,
        "reason": reason.strip(),
    }
    body["payload_sha256"] = _payload_hash(body)
    payload = _canonical_bytes(body) + b"\n"
    descriptor = os.open(path, os.O_APPEND | os.O_WRONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    return body


__all__ = [
    "SCHEMA_VERSION",
    "TddEvidenceError",
    "append_observation_order_amendment",
    "verify_tdd_evidence",
]
