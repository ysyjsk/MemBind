#!/usr/bin/env python3
"""Pause one legacy Native runner at its durable pre-quality boundary.

This one-shot guard exists only to migrate an already-running construction
attempt to the frozen Reader-v2 adapter.  It validates the exact PID command
line and checkpoint identity, then sends SIGSTOP only after the full episode
prefix is durable and before any quality/result artifact exists.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

from paper_eval.native_baseline_runner import should_pause_before_quality


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("checkpoint_not_object")
    return value


def _validate_target(pid: int, run_id: str) -> None:
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    if "scripts/run_native_baseline.py" not in cmdline or run_id not in cmdline:
        raise RuntimeError("target_pid_identity_mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--quality", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    if args.pid <= 1:
        raise SystemExit("--pid must identify the exact Native runner")
    _validate_target(args.pid, args.run_id)

    while True:
        _validate_target(args.pid, args.run_id)
        checkpoint = _load(args.checkpoint)
        if should_pause_before_quality(
            checkpoint,
            quality_exists=args.quality.exists(),
            result_exists=args.result.exists(),
        ):
            os.kill(args.pid, signal.SIGSTOP)
            print(
                json.dumps(
                    {
                        "status": "PAUSED_AT_PRE_QUALITY_BOUNDARY",
                        "pid": args.pid,
                        "run_id": args.run_id,
                        "completed": len(checkpoint["completed_sequences"]),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0
        if checkpoint.get("status") == "incomplete_non_mergeable":
            print(json.dumps({"status": "ATTEMPT_INCOMPLETE"}), flush=True)
            return 2
        if args.quality.exists() or args.result.exists():
            print(json.dumps({"status": "QUALITY_BOUNDARY_ALREADY_PASSED"}), flush=True)
            return 3
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
