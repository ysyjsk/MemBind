#!/usr/bin/env python3
"""Reduce sealed v3.1 development artifacts without any live service access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.membind_v31.reducer import (
    load_development_inputs,
    reduce_development_results,
    write_development_outputs,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-run-id", required=True)
    parser.add_argument("--baseline-acceptance", type=Path, required=True)
    parser.add_argument("--baseline-run-root", type=Path, required=True)
    parser.add_argument("--method-plan", type=Path, required=True)
    parser.add_argument("--method-run-root", type=Path, required=True)
    parser.add_argument("--quality-root", type=Path, required=True)
    parser.add_argument("--workload-complexity", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = load_development_inputs(
        baseline_acceptance_path=args.baseline_acceptance,
        baseline_run_root=args.baseline_run_root,
        method_plan_path=args.method_plan,
        method_run_root=args.method_run_root,
        quality_root=args.quality_root,
        workload_complexity_path=args.workload_complexity,
    )
    outputs = reduce_development_results(table_run_id=args.table_run_id, **inputs)
    write_development_outputs(args.output_root, outputs)
    table = outputs["DEVELOPMENT_MAIN_TABLE.json"]
    print(
        json.dumps(
            {
                "status": table["status"],
                "table_run_id": table["table_run_id"],
                "payload_sha256": table["payload_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
