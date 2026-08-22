#!/usr/bin/env python3
"""Create a dedicated V5 state-transition artifact from CURRENT_STATE.json.

The generated state is intentionally separate from the native-characterization
state and can only authorize ``LiveAction.MEMBIND_V5`` at the exact V5 scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source-state", default="membind-validation/CURRENT_STATE.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    source_path = root / args.source_state
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    if not isinstance(source, dict):
        raise SystemExit("source CURRENT_STATE must be an object")
    state = dict(source)
    state.update(
        {
            "current_stage": "V5",
            "current_action_scope": "membind_v5_live_only",
            "authorized_live_actions": ["membind_v5"],
            "current_blocker": None,
            "status": "membind_v5_live_only",
            "next_allowed_action": "membind_v5",
            "v5_transition": {
                "schema_version": "membind.v5.state-transition.v1",
                "run_id": args.run_id,
                "source_state": str(source_path),
                "source_state_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "performed_at": datetime.now(timezone.utc).isoformat(),
                "native_characterization_c5_reused": False,
                "explicit_v5_action": "membind_v5",
                "explicit_v5_scope": "membind_v5_live_only",
            },
        }
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SystemExit(f"output state already exists: {target}")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    print(json.dumps({"status": "V5_STATE_AUTHORIZED", "state": str(target), "run_id": args.run_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

