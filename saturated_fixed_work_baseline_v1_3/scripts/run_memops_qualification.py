#!/usr/bin/env python3
"""Freeze and qualify the minimal official MemOps baseline workload."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.memops_qualification import (
    b0_eligibility,
    compare_b0_b1,
    freeze_memops_selection,
    load_qualified_b0_result,
    run_memops_live,
)


def _next_root(artifacts: Path) -> Path:
    prefix = f"sfwb-v1-3-memops-qualification-{datetime.now(timezone.utc):%Y%m%d}"
    index = 1
    while True:
        candidate = artifacts / f"{prefix}-{index:03d}"
        if not candidate.exists():
            return candidate
        index += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "b0", "b1"), required=True)
    parser.add_argument("--qualification-root", type=Path)
    parser.add_argument("--memops-root", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[2]
    artifacts = repository_root / "saturated_fixed_work_baseline_v1_3" / "artifacts"
    memops_root = args.memops_root or Path("/data/predator/ly/third_party/MemOps")
    if args.mode == "offline":
        root = args.qualification_root or _next_root(artifacts)
        result = freeze_memops_selection(root, memops_root=memops_root, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.qualification_root is None:
        parser.error("--qualification-root is required for live modes")
    b0_result = None
    if args.mode == "b1":
        b0_result = load_qualified_b0_result(args.qualification_root)
    methods = (
        ("B0_NATIVE_SERIAL",)
        if args.mode == "b0"
        else ("B1_NAIVE_WHOLE_UPDATE_ASYNC",)
    )
    result = asyncio.run(
        run_memops_live(
            qualification_root=args.qualification_root,
            method_names=methods,
            repository_root=repository_root,
        )
    )
    if args.mode == "b0":
        gate = b0_eligibility(result)
        result["b0_gate"] = gate
        from saturated_fixed_work_baseline_v1_3.memops_qualification import _write_new_json

        _write_new_json(args.qualification_root / "b0_result.json", result)
        _write_new_json(args.qualification_root / "b0_gate.json", gate)
    else:
        assert b0_result is not None
        paired = {
            "status": "LIVE_COMPLETE",
            "methods": ["B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC"],
            "sample_ids": result["sample_ids"],
            "outputs": list(b0_result["outputs"]) + list(result["outputs"]),
        }
        gate = compare_b0_b1(paired)
        result["final_gate"] = gate
        from saturated_fixed_work_baseline_v1_3.memops_qualification import _write_new_json

        _write_new_json(args.qualification_root / "b1_result.json", result)
        _write_new_json(args.qualification_root / "final_gate.json", gate)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
