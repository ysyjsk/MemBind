#!/usr/bin/env python3
"""Run v3.1 smoke/block orchestration with an explicit executor factory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.membind_v31.orchestration import (
    load_executor_hooks,
    run_v31_orchestration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument(
        "--executor-factory",
        required=True,
        help="Explicit module:function returning OrchestrationHooks; no live default exists.",
    )
    parser.add_argument(
        "--formal-block-limit",
        type=int,
        choices=(0, 4, 6),
        default=6,
        help=(
            "Run smoke only (0), four main MemBind blocks, or all six after "
            "baseline merge authority."
        ),
    )
    args = parser.parse_args(argv)
    try:
        hooks = load_executor_hooks(args.executor_factory)
        result = run_v31_orchestration(
            control_root=args.control_root,
            attempt_root=args.attempt_root,
            attempt_id=args.attempt_id,
            hooks=hooks,
            formal_block_limit=args.formal_block_limit,
        )
    except ValueError as error:
        print(
            json.dumps(
                {
                    "status": "REJECTED",
                    "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
                    "error_code": str(error),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    print(
        json.dumps(
            {
                "status": result["status"],
                "attempt_id": result["attempt_id"],
                "payload_sha256": result["payload_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
