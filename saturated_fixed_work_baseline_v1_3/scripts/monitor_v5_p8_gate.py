#!/usr/bin/env python3
"""Keep a legal queue alive while the explicit P8 seal is absent."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-root", required=True)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    args = parser.parse_args()
    queue = Path(args.queue_root).resolve()
    while True:
        candidates = [queue / "minimal" / "seal.json", *sorted(queue.glob("minimal-*/seal.json"))]
        p8 = next((path for path in candidates if path.is_file()), None)
        if p8 is not None:
            try:
                seal = json.loads(p8.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                seal = {}
            if seal.get("status") != "P8_LIVE_SEALED":
                time.sleep(max(1.0, args.poll_seconds))
                continue
            ready = queue / "p8_ready.json"
            if ready.exists():
                return 0
            ready.write_text(json.dumps({"schema_version": "membind.v5.p8-ready.v1", "status": "P8_SEAL_READY", "observed_at": datetime.now(timezone.utc).isoformat(), "seal_path": str(p8), "source_count": seal.get("source_count"), "method": seal.get("method")}, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return 0
        time.sleep(max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
