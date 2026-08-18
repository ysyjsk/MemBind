#!/usr/bin/env python3
"""Persist a read-only v3.1 LLM-trace diagnostic artifact.

The command is intentionally separate from the formal reducer.  A diagnostic
may explain an incomplete or failed attempt, but it can never make that
attempt mergeable or alter a frozen result.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from paper_eval.artifacts import atomic_write_json
from paper_eval.membind_v31.diagnostics import analyze_llm_trace_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="path to an immutable llm.jsonl")
    parser.add_argument("output", type=Path, help="diagnostic JSON destination")
    parser.add_argument("--capacity", type=int, default=2)
    args = parser.parse_args()
    result = analyze_llm_trace_file(args.trace, admission_capacity=args.capacity)
    atomic_write_json(args.output, result)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
