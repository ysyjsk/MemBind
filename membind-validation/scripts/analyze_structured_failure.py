#!/usr/bin/env python3
"""Persist a safe, read-only diagnosis of retained structured LLM failures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from structured_failure_diagnostics import write_failure_diagnostic  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze retained failure bytes without calling a model or database."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = write_failure_diagnostic(args.source, args.output)
    print(
        json.dumps(
            {
                "classification": result["classification"],
                "output": str(args.output),
                "request_attempt_count": result["retry_analysis"][
                    "request_attempt_count"
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
