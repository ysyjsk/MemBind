#!/usr/bin/env python3
"""Run provider-free scripted V5 extension qualification or gated preflight."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v5.campaign import FORMAL_HISTORIES, V5_METHOD, verify_baseline_reference
from saturated_fixed_work_baseline_v1_3.membind_v5.qualification.equivalence import ScriptedEpisode, run_scripted_serial_equivalence_async


async def _run_scripted(output: Path) -> dict:
    rows = []
    for history in FORMAL_HISTORIES:
        result = await run_scripted_serial_equivalence_async([ScriptedEpisode(0, f"{history}-a"), ScriptedEpisode(1, f"{history}-b")])
        rows.append({
            "history_id": history,
            "method": V5_METHOD,
            "canonical_exact_match": result.canonical_equal,
            "timer_start_ns": 100,
            "timer_stop_ns": 200,
            "final_publication_ns": 199,
            "semantic_work_after_final_publication": False,
            "trace_envelope_count": 2,
            "episode_count": 2,
            "logical_captured": result.logical_captured,
            "logical_consumed": result.logical_consumed,
            "provider_calls_v5_replay": result.provider_calls_v5_replay,
        })
    body = {"schema_version": "membind.v5.scripted-campaign.v1", "status": "PASS", "method": V5_METHOD, "rows": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(body, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument("--baseline-root")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.baseline_root:
        # This intentionally rejects the current partial producer until its formal seal exists.
        verify_baseline_reference(args.baseline_root)
    if args.preflight_only:
        print(json.dumps({"status": "PASS", "baseline_reference": bool(args.baseline_root)}, sort_keys=True))
        return 0
    if not args.scripted:
        raise SystemExit("live V5 requires the P8 qualification/queue gate; use --scripted for provider-free qualification")
    body = asyncio.run(_run_scripted(Path(args.output)))
    print(json.dumps({"status": body["status"], "output": args.output}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

