#!/usr/bin/env python3
"""Monitor baseline completion for a gated V5 queue; never bypass P8."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def _process() -> str:
    return subprocess.run(("pgrep", "-af", "run_formal_baseline.py"), capture_output=True, text=True, check=False).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--queue-root", required=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    baseline = Path(args.baseline_root).resolve()
    queue = Path(args.queue_root).resolve()
    queue.mkdir(parents=True, exist_ok=True)
    while True:
        seal = baseline / "formal_run_seal.json"
        results = baseline / "qualification" / "baseline_results.json"
        process = _process()
        if seal.is_file() and results.is_file():
            evidence = {
                "schema_version": "membind.v5.queue-baseline-ready.v1",
                "status": "BASELINE_SEAL_READY_P8_REQUIRED",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "formal_run_seal_sha256": hashlib.sha256(seal.read_bytes()).hexdigest(),
                "baseline_results_sha256": hashlib.sha256(results.read_bytes()).hexdigest(),
                "next_gate": "recheck resources -> run minimal V5 -> verify P8 seal -> full",
            }
            (queue / "baseline_ready.json").write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return 0
        if not process:
            failure = {
                "schema_version": "membind.v5.queue-failure.v1",
                "status": "BASELINE_EXITED_WITHOUT_FORMAL_SEAL",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "baseline_root": str(baseline),
                "action": "stop queue; preserve partial baseline; do not run V5",
            }
            (queue / "failure.json").write_text(json.dumps(failure, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return 2
        time.sleep(max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())

