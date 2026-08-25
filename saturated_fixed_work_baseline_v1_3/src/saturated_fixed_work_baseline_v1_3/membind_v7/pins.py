"""Pin and sealed-source verification helpers for R0."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Mapping


MEMBIND_PIN = "2832d94b56db72fcf993154bde47e16b31ade724"
GRAPHITI_PIN = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
GRAPHITI_VERSION = "0.29.3"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_membind_pin(repo: str | Path, *, expected: str = MEMBIND_PIN) -> dict[str, str | bool]:
    root = Path(repo)
    actual = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return {"expected": expected, "actual": actual, "match": actual == expected}


def verify_source_hashes(paths: Mapping[str, str | Path], expected: Mapping[str, str]) -> dict[str, object]:
    actual = {name: sha256_file(path) for name, path in paths.items()}
    mismatches = {name: {"expected": expected.get(name), "actual": value} for name, value in actual.items() if expected.get(name) != value}
    return {"match": not mismatches, "actual": actual, "mismatches": mismatches}


__all__ = ["GRAPHITI_PIN", "GRAPHITI_VERSION", "MEMBIND_PIN", "sha256_file", "verify_membind_pin", "verify_source_hashes"]
