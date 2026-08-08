#!/usr/bin/env python3
"""Persist the V1 retained-artifact embedding diagnostic exactly once."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedding_nondeterminism import write_retained_diagnostic  # noqa: E402


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Close V1 from retained artifacts without any live model calls."
    )
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=ROOT / "artifacts",
        help="Root containing the immutable retained evidence.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "artifacts"
        / "diagnostics"
        / "embedding_nondeterminism_source5.json",
        help="Exclusive output path; an existing file is never overwritten.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = write_retained_diagnostic(args.artifacts_root, args.output)
    except FileExistsError:
        print(f"error: output already exists: {args.output}", file=sys.stderr)
        return 2

    summary = {
        "status": "written",
        "output": str(args.output),
        "output_sha256": _sha256_file(args.output),
        "schema_version": result["schema_version"],
        "v1_gate_status": result["v1_gate"]["status"],
        "embedding_hash_changed_counts": {
            "entities": result["source_state"]["entities"][
                "embedding_hash_changed_count"
            ],
            "edges": result["source_state"]["edges"][
                "embedding_hash_changed_count"
            ],
        },
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
