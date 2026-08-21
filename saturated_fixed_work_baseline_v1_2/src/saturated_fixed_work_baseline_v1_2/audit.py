"""Fail-closed repository identity collection for the remote experiment host."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Mapping


EXPECTED_HEAD = "22017eb2e9772898b11d2519968005d7d243868c"
EXPECTED_ORIGINS = {
    "git@github.com:ysyjsk/MemBind.git",
    "https://github.com/ysyjsk/MemBind.git",
}
REMOTE_ROOT_PREFIX = Path("/data/predator/ly")


class AuditError(ValueError):
    """The checkout cannot be proven to be the frozen remote repository."""


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AuditError(f"GIT_AUDIT_FAILED:{args[0]}")
    return completed.stdout.strip()


def collect_repository_audit(repository_root: Path) -> dict[str, Any]:
    root = repository_root.resolve()
    try:
        root.relative_to(REMOTE_ROOT_PREFIX)
    except ValueError:
        execution_location = "CONTROL_HOST"
    else:
        execution_location = "REMOTE_EXPERIMENT_HOST"
    dirty = _git(root, "status", "--porcelain=v1").splitlines()
    return {
        "schema_version": "membind.saturated-fixed-work.audit.v1",
        "repository_root": str(root),
        "origin_url": _git(root, "remote", "get-url", "origin"),
        "head": _git(root, "rev-parse", "HEAD"),
        "head_subject": _git(root, "log", "-1", "--format=%s"),
        "execution_location": execution_location,
        "dirty_paths": dirty,
    }


def validate_repository_identity(audit: Mapping[str, Any]) -> None:
    if audit.get("origin_url") not in EXPECTED_ORIGINS:
        raise AuditError("REMOTE_ORIGIN_MISMATCH")
    if audit.get("head") != EXPECTED_HEAD:
        raise AuditError("HEAD_MISMATCH")
    if audit.get("execution_location") != "REMOTE_EXPERIMENT_HOST":
        raise AuditError("CONTROL_HOST_EXECUTION_FORBIDDEN")


__all__ = [
    "EXPECTED_HEAD",
    "AuditError",
    "collect_repository_audit",
    "validate_repository_identity",
]

