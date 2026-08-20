#!/usr/bin/env python3
"""Run the offline v4 candidate or sealed final reducer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.membind_v4.reducer import (  # noqa: E402
    reduce_candidate,
    reduce_v4_final,
    write_v4_final_outputs,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    lane = parser.add_mutually_exclusive_group(required=True)
    lane.add_argument("--candidate-root", type=Path)
    lane.add_argument("--frozen-method", type=Path)
    parser.add_argument(
        "--reference",
        type=Path,
        default=PROJECT / "artifacts/paper_eval/membind_v4/PREFIX_REFERENCE.json",
    )
    parser.add_argument("--a1-audit", type=Path, default=None)
    parser.add_argument("--a1-amendment", type=Path, default=None)
    parser.add_argument("--full-run-result", type=Path)
    parser.add_argument(
        "--baseline-binding",
        type=Path,
        default=PROJECT / "artifacts/paper_eval/membind_v4/BASELINE_BINDING.json",
    )
    parser.add_argument("--v31-result", type=Path)
    parser.add_argument("--quality-overlay", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    if args.candidate_root is not None:
        if any(
            value is not None
            for value in (
                args.full_run_result,
                args.v31_result,
                args.quality_overlay,
                args.output_root,
            )
        ):
            parser.error("formal reducer arguments cannot be used with --candidate-root")
        result = reduce_candidate(
            candidate_root=args.candidate_root,
            reference_path=args.reference,
            a1_audit_path=args.a1_audit,
            a1_amendment_path=args.a1_amendment,
        )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "decision": result["decision"],
                    "path": str(args.candidate_root / "reduction.json"),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.full_run_result is None or args.output_root is None:
        parser.error("--full-run-result and --output-root are required with --frozen-method")
    outputs = reduce_v4_final(
        frozen_method_path=args.frozen_method,
        full_run_result_path=args.full_run_result,
        baseline_binding_path=args.baseline_binding,
        prefix_reference_path=args.reference,
        v31_result_path=args.v31_result,
        quality_overlay_path=args.quality_overlay,
    )
    write_v4_final_outputs(args.output_root, outputs)
    final = outputs["V4_FULL_RESULT.json"]
    print(
        json.dumps(
            {
                "status": final["status"],
                "formal_main_table_eligible": final["formal_main_table_eligible"],
                "output_root": str(args.output_root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
