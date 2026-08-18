#!/usr/bin/env python3
"""Accept the APC lane and transactionally publish the v3.1 six-block plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import sha256_file
from paper_eval.membind_v31.baseline_acceptance import verify_apc_baseline_acceptance
from paper_eval.membind_v31.materialization import materialize_membind_v31_control


DEFAULT_BASELINE = (
    PROJECT
    / "artifacts/paper_eval/apc_aligned_baseline/runs/apc-baseline-dev-20260817-001"
)
DEFAULT_QUALITY = (
    PROJECT / "artifacts/paper_eval/quality_evaluation_v1/runs/qev1-apc-20260817-001"
)
DEFAULT_OUTPUT = PROJECT / "artifacts/paper_eval/membind_v31"
DEFAULT_METHODOLOGY = ROOT / "MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md"
DEFAULT_WORKPLAN = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3.1_METHODOLOGY_ALIGNED.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--quality-root", type=Path, default=DEFAULT_QUALITY)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-id", default="membind-v31-dev-20260817-001")
    parser.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)
    parser.add_argument("--workplan", type=Path, default=DEFAULT_WORKPLAN)
    args = parser.parse_args(argv)
    try:
        # Probe first: while APC/quality is active, do not read control docs or
        # create the output root merely to report NOT_TERMINAL.
        status = verify_apc_baseline_acceptance(
            args.baseline_root, quality_root=args.quality_root
        )
        if status.get("status") != "PASS":
            print(json.dumps(status, sort_keys=True), flush=True)
            return 0
        result = materialize_membind_v31_control(
            baseline_root=args.baseline_root,
            quality_root=args.quality_root,
            output_root=args.output_root,
            run_id=args.run_id,
            methodology_sha256=sha256_file(args.methodology),
            workplan_sha256=sha256_file(args.workplan),
        )
    except ValueError as error:
        result = {
            "status": "REJECTED",
            "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
            "message": str(error)[:500],
        }
        print(json.dumps(result, sort_keys=True), flush=True)
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
