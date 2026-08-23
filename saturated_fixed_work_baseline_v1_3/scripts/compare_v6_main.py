#!/usr/bin/env python3
"""Reduce two counterbalanced V6 full-history pairs into one evidence file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v6.main_comparison import reduce_main_campaign


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-first-control", type=Path, required=True)
    parser.add_argument("--control-first-candidate", type=Path, required=True)
    parser.add_argument("--candidate-first-candidate", type=Path, required=True)
    parser.add_argument("--candidate-first-control", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--history-id", default="6071bd76")
    args = parser.parse_args(argv)
    result = reduce_main_campaign(
        control_first_control=args.control_first_control,
        control_first_candidate=args.control_first_candidate,
        candidate_first_candidate=args.candidate_first_candidate,
        candidate_first_control=args.candidate_first_control,
        history_id=args.history_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "claim_status": result["claim_status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
