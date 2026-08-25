#!/usr/bin/env python3
"""Run the provider-free A2 prefix triad against the frozen MAB manifest."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from saturated_fixed_work_baseline_v1_3.campaign_orchestrator import (
    build_campaign_plan,
    run_campaign,
)
from saturated_fixed_work_baseline_v1_3.three_way_campaign import run_provider_free_block


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    frozen = args.frozen_root.resolve()
    authority = _read_json(frozen / "dataset_authority.json")
    context_root = frozen / "contexts" / "context-0"
    workload = _rows(context_root / "workload_manifest.jsonl")[: args.session_limit]
    plan = build_campaign_plan(
        authority,
        context_indices=(0,),
        scope="ENGINEERING_DIAGNOSTIC",
        repeats=1,
        session_limit=args.session_limit,
    )
    output_root = args.output_root.resolve()
    block_root = output_root / "blocks"

    async def runner(block: dict[str, Any]) -> dict[str, Any]:
        method = str(block["method"])

        async def prepare(sequence: int, selected_method: str) -> dict[str, Any]:
            # The delay creates an observable relaxed-order witness for B1,
            # while remaining provider-free and deterministic.
            if selected_method == "B1" and sequence == 0:
                await asyncio.sleep(0.005)
            return {"source_sequence": sequence, "method": selected_method, "body_sha256": __import__("hashlib").sha256(str(workload[sequence]["body"]).encode()).hexdigest()}

        async def publish(sequence: int, prepared: Any, selected_method: str) -> None:
            del sequence, prepared, selected_method
            await asyncio.sleep(0)

        result = await run_provider_free_block(workload, method=method, prepare=prepare, publish=publish)
        result.update({
            "block_id": block["block_id"],
            "namespace": block["namespace"],
            "attempt_id": block["attempt_id"],
            "context_index": block["context_index"],
            "repeat": block["repeat"],
            "scope": block["scope"],
            "status": "PASS",
        })
        block_path = block_root / f"{block['block_id']}.json"
        block_path.parent.mkdir(parents=True, exist_ok=True)
        block_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return result

    summary = await run_campaign(plan, output_root=output_root, block_runner=runner)
    (output_root / "diagnostic_plan.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": summary["status"], "summary": summary, "output_root": str(output_root)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--session-limit", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main_async(args)), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
