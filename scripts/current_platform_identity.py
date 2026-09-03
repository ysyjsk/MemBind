"""Authenticate the active immutable local 8B platform identity."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


PROFILE_ID = "local-qwen3-8b-awq-dualreplica-v1"
DEFAULT_PROFILE_ROOT = Path(
    "/data/predator/ly/Mem/profiles/local-qwen3-8b-awq-dualreplica-v1"
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"platform JSON object required: {path}")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_current_platform_identity(
    profile_root: Path | None = None,
) -> dict[str, str]:
    """Return the current pointer only after authenticating its immutable manifest."""

    root = (
        profile_root
        or Path(os.environ.get("MEMBIND_PROFILE_ROOT", str(DEFAULT_PROFILE_ROOT)))
    ).resolve()
    pointer_path = root / "latest.json"
    pointer = _json(pointer_path)
    manifest_path = Path(str(pointer.get("manifest_path", ""))).resolve()
    if pointer.get("profile_id") != PROFILE_ID:
        raise RuntimeError("active platform pointer has the wrong profile")
    if root not in manifest_path.parents or not manifest_path.is_file():
        raise RuntimeError("active platform manifest is outside the profile root")

    manifest_bytes = manifest_path.read_bytes()
    manifest = _json(manifest_path)
    payload = {key: value for key, value in manifest.items() if key != "payload_sha256"}
    payload_sha256 = hashlib.sha256(_canonical(payload)).hexdigest()
    file_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        manifest.get("profile_id") != PROFILE_ID
        or manifest.get("platform_status") != "LIVE_VALIDATED_RESOURCE_MATCHED"
        or manifest.get("platform_formal_eligible") is not True
        or manifest.get("payload_sha256") != payload_sha256
        or pointer.get("payload_sha256") != payload_sha256
        or pointer.get("file_sha256") != file_sha256
    ):
        raise RuntimeError("active platform manifest authentication failed")
    return {
        "profile_id": PROFILE_ID,
        "path": str(manifest_path),
        "pointer_path": str(pointer_path),
        "payload_sha256": payload_sha256,
        "file_sha256": file_sha256,
    }
