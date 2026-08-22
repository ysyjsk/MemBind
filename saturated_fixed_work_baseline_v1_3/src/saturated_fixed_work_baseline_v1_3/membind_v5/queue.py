"""Legal P9 queue manifest builder; it never mutates a baseline root."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .campaign import V5_METHOD
from .qualification.p0_repository import qualify_repository


class QueueContractError(ValueError):
    pass


def _formal_process() -> str:
    try:
        return subprocess.run(("pgrep", "-af", "run_formal_baseline.py"), capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def build_queue_manifest(
    *,
    repo_root: str | Path,
    baseline_root: str | Path,
    queue_root: str | Path,
    p8_seal: str | Path | None = None,
    session_name: str | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    queue = Path(queue_root).resolve()
    if queue.exists() and any(queue.iterdir()):
        raise QueueContractError("queue root must be new")
    p0 = qualify_repository(repo_root, baseline_root=baseline_root)
    baseline_process = p0["baseline"].get("producer_process") or _formal_process()
    p8_path = Path(p8_seal).resolve() if p8_seal else None
    p8_valid = bool(p8_path and p8_path.is_file())
    mode = "QUEUED" if p8_valid else "QUEUED_WITH_GATED_MINIMAL"
    if mode == "QUEUED" and not baseline_process:
        # A sealed P8 still requires a final resource/endpoint recheck in the queue.
        mode = "QUEUED"
    manifest = {
        "schema_version": "membind.v5.queue-manifest.v1",
        "status": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": V5_METHOD,
        "repo_root": str(Path(repo_root).resolve()),
        "baseline": {
            "root": str(Path(baseline_root).resolve()),
            "observed_status": p0["baseline"].get("status"),
            "producer_process": baseline_process,
            "formal_seal_required": True,
            "formal_seal_path": str(Path(baseline_root).resolve() / "formal_run_seal.json"),
        },
        "session": {"name": session_name, "pid": os.getpid(), "command": command},
        "gates": [
            {"name": "baseline_formal_seal", "verify": "formal_run_seal.json + qualification/baseline_results.json"},
            {"name": "endpoint_resource_idle_recheck", "verify": "frozen model catalog + Neo4j canary + GPU/resource evidence"},
            {"name": "minimal_v5_live", "required": not p8_valid, "output_root": str(queue / "minimal")},
            {"name": "minimal_p8_seal", "required": not p8_valid, "path": str(p8_path or queue / "minimal" / "seal.json")},
            {"name": "full_v5_campaign", "command": command or "run_v5_campaign.py --baseline-root <sealed-baseline>"},
        ],
        "p8": {"seal_path": str(p8_path) if p8_path else None, "verified": p8_valid},
        "backend_resource_identity": "inherited from frozen v1.3 contracts; recheck immediately before execution",
        "full_must_not_start_before": ["baseline_formal_seal", "endpoint_resource_idle_recheck", "minimal_p8_seal" if not p8_valid else "p8_seal_verified"],
    }
    queue.mkdir(parents=True, exist_ok=False)
    (queue / "queue_manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def validate_queue_manifest(manifest: dict[str, Any]) -> None:
    mode = manifest.get("status")
    if mode not in {"QUEUED", "QUEUED_WITH_GATED_MINIMAL"}:
        raise QueueContractError("invalid queue status")
    if mode == "QUEUED_WITH_GATED_MINIMAL" and manifest.get("p8", {}).get("verified"):
        raise QueueContractError("gated-minimal mode cannot claim verified P8")
    if "baseline_formal_seal" not in {gate.get("name") for gate in manifest.get("gates", [])}:
        raise QueueContractError("baseline seal gate missing")
    if not manifest.get("full_must_not_start_before"):
        raise QueueContractError("full campaign dependency gate missing")


def mark_queue_failure(queue_root: str | Path, *, reason: str, superseded_by: str | None = None) -> Path:
    """Record an invalid/superseded queue without rewriting its manifest."""

    root = Path(queue_root)
    target = root / "failure.json"
    if target.exists():
        raise QueueContractError("queue failure already recorded")
    body = {
        "schema_version": "membind.v5.queue-failure.v1",
        "status": "FAILED_QUEUE",
        "reason": reason,
        "superseded_by": superseded_by,
    }
    target.write_text(json.dumps(body, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def write_session_evidence(queue_root: str | Path, *, session_name: str, pane_pid: int, command: str) -> Path:
    root = Path(queue_root)
    target = root / "session_evidence.json"
    if target.exists():
        raise QueueContractError("session evidence already recorded")
    body = {
        "schema_version": "membind.v5.queue-session-evidence.v1",
        "status": "QUEUE_SESSION_RUNNING",
        "session_name": session_name,
        "pane_pid": int(pane_pid),
        "command": command,
        "full_start_gate": "minimal_p8_seal",
    }
    target.write_text(json.dumps(body, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
