#!/usr/bin/env python3
"""Seal the pinned P1 local snapshot with a complete byte-level catalog."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


SOURCE_MODEL = "Qwen/Qwen2.5-7B-Instruct-AWQ"
REVISION = "b25037543e9394b818fdfca67ab2a00ecc7dd641"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def main() -> int:
    root = Path(
        os.environ.get(
            "MEMBIND_LLM_MODEL_DIR",
            "/data/predator/ly/Mem/models/Qwen2.5-7B-Instruct-AWQ",
        )
    ).resolve()
    target = root / ".membind-model-manifest.json"
    if target.exists():
        raise FileExistsError(f"model manifest already exists: {target}")
    files = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name == target.name:
            continue
        files.append(
            {
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    weight_files = [row for row in files if row["path"].endswith(".safetensors")]
    if len(weight_files) != 2:
        raise RuntimeError("P1 snapshot must contain exactly two safetensor shards")
    payload = {
        "schema_version": "membind.model-snapshot-manifest.v2",
        "source_model": SOURCE_MODEL,
        "revision": REVISION,
        "path": str(root),
        "files": files,
        "file_count": len(files),
        "weight_file_count": len(weight_files),
        "bytes": sum(int(row["bytes"]) for row in files),
    }
    payload["payload_sha256"] = canonical_sha256(payload)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"path": str(target), "payload_sha256": payload["payload_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
