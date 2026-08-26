#!/usr/bin/env python3
"""Run bounded Bailian construction probes as engineering-only evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "saturated_fixed_work_baseline_v1_3/src",
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "membind-validation/src",
    ROOT / "paper-eval-v3/src",
):
    selected = str(source)
    if selected not in sys.path:
        sys.path.insert(0, selected)

from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import (  # noqa: E402
    build_bailian_engineering_artifact,
    load_engineering_provider_freeze,
    run_bailian_construction_probes_async,
)


PROVIDER_FREEZE = (
    ROOT
    / "saturated_fixed_work_baseline_v1_3/v7/BAILIAN_ENGINEERING_PROVIDER_FREEZE_V2.json"
)
DATASET = ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_exclusive(path: Path, value: dict[str, object]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("engineering probe artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_bindings() -> dict[str, str]:
    paths = {
        "saturated_fixed_work_baseline_v1_3/scripts/probe_v7_bailian_engineering.py": Path(
            __file__
        ).resolve(),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/provider_diagnostics.py": ROOT
        / "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/provider_diagnostics.py",
        "graphiti_core/utils/maintenance/node_operations.py": ROOT
        / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/node_operations.py",
        "graphiti_core/utils/maintenance/edge_operations.py": ROOT
        / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/edge_operations.py",
        "graphiti_core/prompts/extract_nodes.py": ROOT
        / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/prompts/extract_nodes.py",
        "graphiti_core/prompts/extract_edges.py": ROOT
        / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/prompts/extract_edges.py",
        "membind-validation/src/graphiti_native.py": ROOT
        / "membind-validation/src/graphiti_native.py",
        "membind-validation/src/structured_output.py": ROOT
        / "membind-validation/src/structured_output.py",
    }
    return {name: _sha256(path) for name, path in sorted(paths.items())}


async def _run(args: argparse.Namespace) -> dict[str, object]:
    import httpx
    from openai import AsyncOpenAI

    from mab_quality_v2_final_qa.mab_main_dataset import (
        build_authority,
        build_episode_inputs,
    )

    provider = load_engineering_provider_freeze(PROVIDER_FREEZE)
    authority = build_authority(DATASET)
    workload = provider["probe_workload"]
    if authority["local_file_sha256"] != workload["dataset_sha256"]:
        raise ValueError("engineering probe dataset differs from provider freeze")
    context_index = int(workload["context_index"])
    source_sequence = int(workload["source_sequence"])
    episodes = tuple(build_episode_inputs(authority["contexts"][context_index]))
    if source_sequence < 0 or source_sequence >= len(episodes):
        raise ValueError("engineering probe source sequence is invalid")

    credential = os.environ.get("DASHSCOPE_API_KEY")
    if not credential:
        raise ValueError("DASHSCOPE_API_KEY is required")
    timeout = httpx.Timeout(
        connect=min(10.0, args.timeout_seconds),
        read=args.timeout_seconds,
        write=args.timeout_seconds,
        pool=args.timeout_seconds,
    )
    http_client = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    client = AsyncOpenAI(
        api_key=credential,
        base_url=provider["base_url"],
        timeout=timeout,
        max_retries=0,
        http_client=http_client,
    )
    budgets = provider["probe_budgets"]
    try:
        chain = await run_bailian_construction_probes_async(
            episode=episodes[source_sequence],
            previous_episodes=episodes[:source_sequence],
            namespace=f"membind-v7-{args.run_id}",
            model=provider["construction_model"],
            minimal_max_tokens=budgets["minimal_json_schema_max_tokens"],
            node_max_tokens=budgets["extract_nodes_extract_message_max_tokens"],
            edge_max_tokens=budgets["extract_edges_edge_max_tokens"],
            completions=client.chat.completions,
            timeout_seconds=args.timeout_seconds,
            structured_output_mode=provider["structured_output_mode"],
            send_max_tokens=provider["output_limit_policy"]["max_tokens_sent"],
        )
    finally:
        await client.close()
        credential = ""
    return build_bailian_engineering_artifact(
        run_id=args.run_id,
        provider_freeze_path=PROVIDER_FREEZE,
        dataset_sha256=authority["local_file_sha256"],
        source_sha256=_source_bindings(),
        timeout_seconds=args.timeout_seconds,
        chain_result=chain,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if not args.run_id or "/" in args.run_id or "\\" in args.run_id:
        parser.error("--run-id must be a non-path identity")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.output.exists():
        parser.error("--output must be fresh")
    result = asyncio.run(_run(args))
    _write_exclusive(args.output, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "classification": result["classification"],
                "run_id": result["run_id"],
                "output": str(args.output.resolve()),
                "formal_r1_r3_eligible": False,
                "gate_outcome": "NOT_EVALUATED",
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
