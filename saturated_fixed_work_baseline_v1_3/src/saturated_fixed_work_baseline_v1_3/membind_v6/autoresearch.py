"""Append-only V6 autoresearch ledger and resumable campaign state."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class V6LedgerError(ValueError):
    """The ledger/state contract cannot be satisfied."""


_LEDGER_FIELDS = (
    "iteration",
    "observation",
    "hypothesis",
    "null_hypothesis",
    "predicted_critical_path_change",
    "test_written_before_code",
    "single_change",
    "endpoint_identity",
    "artifact_root",
    "raw_artifact_hashes",
    "result_and_uncertainty",
    "strongest_counterexample",
    "paper_or_source_used",
    "hypothesis_revision",
    "next_cheapest_discriminating_test",
)

_STATE_FIELDS = {
    "schema_version",
    "status",
    "campaign_root",
    "iteration",
    "active_hypothesis",
    "active_phase",
    "git_commit",
    "git_diff_hash",
    "own_tmux_sessions",
    "running_attempt",
    "last_sealed_attempt",
    "next_action",
    "updated_at",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(dict(value), ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def create_campaign_root(root: str | Path) -> Path:
    target = Path(root).resolve()
    if target.exists():
        raise V6LedgerError("campaign root must be fresh")
    target.mkdir(parents=True)
    for name in ("environment", "papers", "method", "tests", "micro", "prefix", "main", "report", "logs"):
        (target / name).mkdir()
    state = {
        "schema_version": "membind.v6.run-state.v1",
        "status": "INITIALIZED",
        "campaign_root": str(target),
        "iteration": "R00",
        "active_hypothesis": None,
        "active_phase": None,
        "git_commit": None,
        "git_diff_hash": None,
        "own_tmux_sessions": [],
        "running_attempt": None,
        "last_sealed_attempt": None,
        "next_action": "complete environment preflight and L0 reducer",
        "updated_at": _now(),
    }
    _write_exclusive(target / "RUN_STATE.json", state)
    ledger_path = target / "V6_AUTORESEARCH_LEDGER.jsonl"
    descriptor = os.open(ledger_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    bootstrap = {
        "iteration": "R00",
        "observation": "campaign initialized",
        "hypothesis": "V5 native frontier is the correct measurement boundary",
        "null_hypothesis": "no V6 change improves the native critical path",
        "predicted_critical_path_change": "none before L0 reduction",
        "test_written_before_code": "bootstrap",
        "single_change": "create V6 campaign root",
        "endpoint_identity": "NOT_YET_PROBED",
        "artifact_root": str(target),
        "raw_artifact_hashes": {},
        "result_and_uncertainty": "pending",
        "strongest_counterexample": "pending",
        "paper_or_source_used": "MemBind_V6_Graphiti_Autoresearch_Workplan.md",
        "hypothesis_revision": "pending",
        "next_cheapest_discriminating_test": "L0 critical-path reducer",
    }
    append_ledger_entry(target, bootstrap)
    return target


def append_ledger_entry(root: str | Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in _LEDGER_FIELDS if field not in entry]
    if missing:
        raise V6LedgerError(f"required ledger field missing: {missing[0]}")
    row = {
        "schema_version": "membind.v6.autoresearch-entry.v1",
        "observed_at": _now(),
        **{field: entry[field] for field in _LEDGER_FIELDS},
    }
    path = Path(root) / "V6_AUTORESEARCH_LEDGER.jsonl"
    if not path.is_file():
        raise V6LedgerError("ledger file is missing")
    payload = (json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_APPEND | os.O_WRONLY)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return row


def update_run_state(root: str | Path, **updates: Any) -> dict[str, Any]:
    unknown = sorted(set(updates) - _STATE_FIELDS)
    if unknown:
        raise V6LedgerError(f"unknown run-state field: {unknown[0]}")
    path = Path(root) / "RUN_STATE.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V6LedgerError("RUN_STATE.json is unreadable") from exc
    if not isinstance(state, dict) or state.get("schema_version") != "membind.v6.run-state.v1":
        raise V6LedgerError("RUN_STATE.json schema is invalid")
    state.update(updates)
    state["updated_at"] = _now()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="RUN_STATE.", suffix=".tmp", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return state


__all__ = ["V6LedgerError", "append_ledger_entry", "create_campaign_root", "update_run_state"]
