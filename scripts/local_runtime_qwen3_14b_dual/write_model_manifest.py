#!/usr/bin/env python3
"""Seal the P2 Qwen3-14B-AWQ snapshot without modifying the model directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


SOURCE_MODEL = "Qwen/Qwen3-14B-AWQ"
REVISION = "31c69efc29464b6bb0aee1398b5a7b50a99340c3"


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("/data/predator/ly/Mem/models/Qwen3-14B-AWQ"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "/data/predator/ly/Mem/profiles/local-qwen3-14b-awq-dualreplica-v1/"
            "model_snapshot_manifest.json"
        ),
    )
    args = parser.parse_args()
    root = args.model_root.resolve()
    target = args.output.resolve()
    if target.exists():
        raise FileExistsError(f"model manifest already exists: {target}")
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    generation = json.loads(
        (root / "generation_config.json").read_text(encoding="utf-8")
    )
    if config.get("max_position_embeddings") != 40960:
        raise RuntimeError("P2 model must retain its native 40960-token context")
    # generation_config is immutable snapshot metadata.  Qwen's non-thinking
    # deployment override is measured separately and must not be inferred from
    # (or written back into) the downloaded snapshot.
    files = [
        {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.iterdir())
        if path.is_file()
    ]
    weights = [row for row in files if row["path"].endswith(".safetensors")]
    if len(weights) != 2:
        raise RuntimeError("P2 snapshot must contain exactly two safetensor shards")
    payload = {
        "schema_version": "membind.model-snapshot-manifest.v2",
        "source_model": SOURCE_MODEL,
        "revision": REVISION,
        "path": str(root),
        "files": files,
        "file_count": len(files),
        "weight_file_count": len(weights),
        "bytes": sum(int(row["bytes"]) for row in files),
        "immutable_generation_config": {
            key: generation.get(key)
            for key in ("temperature", "top_p", "top_k")
            if key in generation
        },
        "measured_deployment_sampling_override": {
            "enable_thinking": False,
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
        },
    }
    payload["payload_sha256"] = canonical_sha256(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(
        json.dumps(
            {"path": str(target), "payload_sha256": payload["payload_sha256"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
