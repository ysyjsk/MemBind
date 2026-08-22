"""Read-only P0 repository qualification."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


class QualificationError(RuntimeError):
    pass


EXPECTED_COMMIT = "c4c9577208ab41d1cd148778e0a6eab4daafe6ac"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(*args: str) -> str:
    try:
        return subprocess.run(args, check=False, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Require the audited revision to remain in the current implementation history."""
    try:
        result = subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant),
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def qualify_repository(
    repo_root: str | Path,
    *,
    expected_commit: str = EXPECTED_COMMIT,
    frozen_config_paths: tuple[str | Path, ...] = (
        "saturated_fixed_work_baseline_v1_3/configs/frozen_backend_v1_3.json",
        "saturated_fixed_work_baseline_v1_3/configs/frozen_client_v1_3.json",
        "saturated_fixed_work_baseline_v1_3/configs/resource_policy.json",
    ),
    baseline_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    actual_commit = _run("git", "-C", str(root), "rev-parse", "HEAD")
    if not actual_commit or not _is_ancestor(root, expected_commit, actual_commit):
        raise QualificationError(
            f"repo revision is not a descendant of audited baseline: "
            f"expected ancestor {expected_commit}, got {actual_commit}"
        )
    configs: dict[str, str] = {}
    for raw in frozen_config_paths:
        path = (root / raw).resolve()
        if not path.is_file():
            raise QualificationError(f"missing frozen config: {path}")
        configs[str(path.relative_to(root))] = _sha256(path)
    python = Path(sys.executable).resolve()
    try:
        graphiti_version = importlib.metadata.version("graphiti-core")
    except importlib.metadata.PackageNotFoundError:
        graphiti_version = None
    baseline_status = "NONE"
    baseline_evidence: dict[str, Any] = {}
    if baseline_root is not None:
        candidate = Path(baseline_root).resolve()
        formal_seal = candidate / "formal_run_seal.json"
        block_seals = tuple(candidate.glob("blocks/*/attempt-*/seal.json"))
        process = _run("pgrep", "-af", "run_formal_baseline.py")
        baseline_status = (
            "RUNNING"
            if process
            else "SEALED"
            if formal_seal.is_file() and (candidate / "qualification" / "baseline_results.json").is_file()
            else "FAILED/PARTIAL"
            if block_seals
            else "NONE"
        )
        baseline_evidence = {
            "root": str(candidate),
            "formal_run_seal": formal_seal.exists(),
            "block_seal_count": len(block_seals),
            "producer_process": process,
        }
    report = {
        "schema_version": "membind.v5.p0-repository-qualification.v1",
        "status": "PASS",
        "repo_root": str(root),
        "audit_base_commit": expected_commit,
        "membind_commit": actual_commit,
        "implementation_descends_from_audit_base": True,
        "worktree_status": _run("git", "-C", str(root), "status", "--short"),
        "python": str(python),
        "python_version": sys.version,
        "graphiti_core_version": graphiti_version,
        "config_sha256": configs,
        "live_inventory": {
            "tmux": _run("tmux", "list-sessions"),
            "formal_process": _run("pgrep", "-af", "run_formal_baseline.py"),
            "listening_ports": _run("ss", "-ltnp"),
            "nvidia_smi": _run("nvidia-smi", "--query-gpu=index,name,memory.used,utilization.gpu", "--format=csv,noheader"),
        },
        "baseline": {"status": baseline_status, **baseline_evidence},
    }
    return report
