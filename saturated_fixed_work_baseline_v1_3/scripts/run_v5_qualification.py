#!/usr/bin/env python3
"""Run the read-only V5 P0/P1 qualification gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v5.qualification.certificate_check import build_dependency_certificate
from saturated_fixed_work_baseline_v1_3.membind_v5.qualification.p0_repository import qualify_repository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline-root")
    parser.add_argument("--skip-graphiti", action="store_true")
    args = parser.parse_args()
    report = {"p0": qualify_repository(args.repo_root, baseline_root=args.baseline_root)}
    if not args.skip_graphiti:
        report["p1"] = build_dependency_certificate()
    report["status"] = "PASS"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

