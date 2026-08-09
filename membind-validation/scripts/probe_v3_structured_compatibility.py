#!/usr/bin/env python3
"""Run one exact V3 extraction request without Graphiti, embedding, or Neo4j."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graphiti_native import load_env_file  # noqa: E402
from v3_structured_compatibility_probe import write_compatibility_probe  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe the exact source-1 Graphiti extraction request only."
    )
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--question-id", required=True)
    parser.add_argument("--source-sequence", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    load_env_file()
    result = await write_compatibility_probe(
        args.data,
        args.question_id,
        args.output,
        source_sequence=args.source_sequence,
    )
    print(
        json.dumps(
            {
                "classification": result["classification"],
                "ok": result["ok"],
                "output": str(args.output),
                "prompt_token_count_matches_history": result[
                    "prompt_token_count_matches_history"
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
