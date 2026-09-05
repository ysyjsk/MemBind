from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..core.contracts import canonical_sha256


def implementation_identity(root: str | Path) -> dict[str, Any]:
    path = Path(root)
    try:
        commit = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "UNAVAILABLE"
    payload = {"git_head": commit, "package": "clean-membind", "schema_version": "clean-membind.identity.v1"}
    return {**payload, "identity_sha256": canonical_sha256(payload)}
