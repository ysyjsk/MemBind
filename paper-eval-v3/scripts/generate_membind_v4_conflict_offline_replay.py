#!/usr/bin/env python3
"""Generate the sealed v4 conflict-aware replay from frozen local evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json  # noqa: E402
from paper_eval.membind_v4.conflict_replay import (  # noqa: E402
    build_conflict_offline_replay,
)


ARTIFACTS = PROJECT / "artifacts/paper_eval"
V31_BLOCK = (
    ARTIFACTS
    / "membind_v31/feasibility/membind-v31-feasibility-20260819-004/block-00"
)
V4_ROOT = ARTIFACTS / "membind_v4"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=V4_ROOT / "V4_CONFLICT_OFFLINE_REPLAY.json",
    )
    args = parser.parse_args(argv)
    replay = build_conflict_offline_replay(
        audit_path=(
            V4_ROOT
            / "protocol_amendment_a1/V4_OPPORTUNITY_AUDIT_A1.json"
        ),
        block_manifest_path=V31_BLOCK / "manifest.json",
        events_path=V31_BLOCK / "events.jsonl",
        prepared_dir=V31_BLOCK / "private/prepared",
        baseline_binding_path=V4_ROOT / "BASELINE_BINDING.json",
        old_c01_candidate_dir=(
            V4_ROOT
            / "autoresearch/membind-v4-ar-20260819-c01-6-live/candidates/c01"
        ),
        source_count=12,
    )
    gate = replay["gate"]
    if not isinstance(gate, dict) or gate != {
        "decision": "STOP_CONFLICT_AWARE_NODE_RESOLVE",
        "final_outcome": "STOP_V4_NODE_RESOLVE",
        "live_authorized": False,
        "reason": "low_conflict_opportunities_zero",
    }:
        raise ValueError("registered_offline_gate_result_drift")
    atomic_write_json(args.output.resolve(), replay)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "payload_sha256": replay["payload_sha256"],
                "final_outcome": gate["final_outcome"],
                "live_authorized": gate["live_authorized"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
