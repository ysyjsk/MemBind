#!/usr/bin/env python3
"""Publish the source-bound v3.1 live plan without claiming baseline acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.artifacts import sha256_file  # noqa: E402
from paper_eval.membind_v31.materialization import (  # noqa: E402
    materialize_membind_v31_live_plan,
)


DEFAULT_BASELINE = (
    PROJECT
    / "artifacts/paper_eval/apc_aligned_baseline/runs/apc-baseline-dev-20260817-001"
)
DEFAULT_OUTPUT = PROJECT / "artifacts/paper_eval/membind_v31"
DEFAULT_METHODOLOGY = ROOT / "MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md"
DEFAULT_WORKPLAN = (
    ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3.1_METHODOLOGY_ALIGNED.md"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-id", default="membind-v31-dev-20260818-001")
    parser.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)
    parser.add_argument("--workplan", type=Path, default=DEFAULT_WORKPLAN)
    args = parser.parse_args(argv)
    try:
        plan = materialize_membind_v31_live_plan(
            baseline_root=args.baseline_root,
            output_root=args.output_root,
            run_id=args.run_id,
            methodology_sha256=sha256_file(args.methodology),
            workplan_sha256=sha256_file(args.workplan),
        )
    except ValueError as error:
        print(
            json.dumps(
                {
                    "status": "REJECTED",
                    "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
                    "message": str(error)[:500],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "authorization_scope": plan["authorization_scope"],
                "run_id": plan["run_id"],
                "payload_sha256": plan["payload_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
