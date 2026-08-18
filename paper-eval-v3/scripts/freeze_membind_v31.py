#!/usr/bin/env python3
"""Freeze or verify the offline MemBind v3.1 pre-live qualification artifacts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.membind_v31.freezer import (  # noqa: E402
    V31FreezePaths,
    freeze_v31_qualification,
    verify_v31_qualification_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "verify"))
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--compile-workers", type=int, default=2)
    parser.add_argument("--lookahead", type=int, default=2)
    parser.add_argument("--global-llm-admission-k", type=int, default=2)
    args = parser.parse_args()
    paths = V31FreezePaths.from_repository(
        args.repository_root,
        output_dir=args.output_dir,
    )
    if args.command == "freeze":
        documents = asyncio.run(
            freeze_v31_qualification(
                paths,
                compile_workers=args.compile_workers,
                lookahead=args.lookahead,
                global_llm_admission_k=args.global_llm_admission_k,
            )
        )
    else:
        documents = verify_v31_qualification_artifacts(paths)
    print(
        json.dumps(
            {
                "status": "PASS",
                "command": args.command,
                "artifact_payload_sha256s": {
                    name: document["payload_sha256"]
                    for name, document in documents.items()
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
