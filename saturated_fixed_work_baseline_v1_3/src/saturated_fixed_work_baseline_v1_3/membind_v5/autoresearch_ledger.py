"""Append-only research ledger for the V5 Observe -> Test -> Reflect loop."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def append_entry(path: str | Path, *, symptom: str, observed_evidence: str, hypothesis: str, relevant_prior_implementation: str, minimal_reproduction: str, root_cause: str, change: str, validation: str, what_was_learned: str, next_action: str) -> dict[str, Any]:
    fields = {
        "symptom": symptom,
        "observed_evidence": observed_evidence,
        "hypothesis": hypothesis,
        "relevant_prior_implementation": relevant_prior_implementation,
        "minimal_reproduction": minimal_reproduction,
        "root_cause": root_cause,
        "change": change,
        "validation": validation,
        "what_was_learned": what_was_learned,
        "next_action": next_action,
    }
    entry = {
        "schema_version": "membind.v5.autoresearch-entry.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        payload = json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return entry

