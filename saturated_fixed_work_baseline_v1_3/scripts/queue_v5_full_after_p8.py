#!/usr/bin/env python3
"""Append the legal P9 full-V5 queue record after a verified P8 seal."""

from __future__ import annotations

import argparse
import json

from saturated_fixed_work_baseline_v1_3.membind_v5.queue import promote_queue_after_p8


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-root", required=True)
    parser.add_argument("--p8-seal", required=True)
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--command", default="run_v5_campaign.py --baseline-root <sealed-baseline>")
    parser.add_argument("--output-name", default="p9_full_queue.json")
    parser.add_argument("--readiness-name", default="p8_ready.json")
    args = parser.parse_args()
    path = promote_queue_after_p8(
        queue_root=args.queue_root,
        p8_seal=args.p8_seal,
        baseline_root=args.baseline_root,
        command=args.command,
        output_name=args.output_name,
        readiness_name=args.readiness_name,
    )
    print(json.dumps({"status": "QUEUED_P9_FULL_AFTER_P8", "evidence": str(path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
