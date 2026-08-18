#!/usr/bin/env python3
"""Read-only status probe for the exact APC baseline required by MemBind v3.1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.membind_v31.baseline_acceptance import verify_apc_baseline_acceptance


DEFAULT_BASELINE = (
    PROJECT
    / "artifacts/paper_eval/apc_aligned_baseline/runs/apc-baseline-dev-20260817-001"
)
DEFAULT_QUALITY = (
    PROJECT / "artifacts/paper_eval/quality_evaluation_v1/runs/qev1-apc-20260817-001"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--quality-root", type=Path, default=DEFAULT_QUALITY)
    args = parser.parse_args(argv)
    try:
        result = verify_apc_baseline_acceptance(
            args.baseline_root, quality_root=args.quality_root
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
