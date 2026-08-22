#!/usr/bin/env python3
"""Create a gated V5 queue without touching active baseline/services."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v5.queue import build_queue_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--queue-root", required=True)
    parser.add_argument("--p8-seal")
    parser.add_argument("--session-name")
    parser.add_argument("--command")
    args = parser.parse_args()
    manifest = build_queue_manifest(repo_root=args.repo_root, baseline_root=args.baseline_root, queue_root=args.queue_root, p8_seal=args.p8_seal, session_name=args.session_name, command=args.command)
    print(json.dumps({"status": manifest["status"], "queue_root": args.queue_root}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

