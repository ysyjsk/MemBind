#!/usr/bin/env python3
"""Freeze the 45-block publication plan without making live calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.campaign_orchestrator import build_campaign_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    authority = json.loads(args.authority.read_text(encoding="utf-8"))
    preflight = json.loads(args.preflight.read_text(encoding="utf-8"))
    plan = build_campaign_plan(authority, context_indices=(0, 1, 2, 3, 4), scope="FORMAL", repeats=3)
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "campaign_plan.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    ledger_path = root / "campaign_ledger.jsonl"
    with ledger_path.open("w", encoding="utf-8") as handle:
        for block in plan["blocks"]:
            handle.write(json.dumps({"event": "BLOCK_PLANNED", **block}, ensure_ascii=False, sort_keys=True) + "\n")
    status = {
        "schema_version": "membind.v1.3.formal-campaign-status.v1",
        "status": "READY" if preflight.get("status") == "READY_FOR_A3" else "BLOCKED_EXTERNAL_PROVIDER",
        "planned_block_count": len(plan["blocks"]),
        "planned_qa_result_rows": 45 * 60,
        "preflight_status": preflight.get("status"),
        "blocked_gates": preflight.get("blocked_gates", []),
        "claim_boundary": "planned only; no live construction or QA result is implied",
    }
    (root / "campaign_status.json").write_text(json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
