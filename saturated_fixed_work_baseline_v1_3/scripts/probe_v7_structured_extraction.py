#!/usr/bin/env python3
"""Probe one exact V7 Graphiti extraction request without Neo4j or embedding."""

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
    text = str(source)
    if text not in sys.path:
        sys.path.insert(0, text)

from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (  # noqa: E402
    load_protocol_freeze,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import (  # noqa: E402
    build_structured_extraction_probe,
    run_structured_extraction_probe_async,
)


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
                raise OSError("structured probe artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    import httpx
    from openai import AsyncOpenAI
    from mab_quality_v2_final_qa.mab_main_dataset import (
        build_authority,
        build_episode_inputs,
    )

    protocol = load_protocol_freeze(args.protocol)
    authority = build_authority(args.dataset)
    workload = protocol["workload"]
    if authority["local_file_sha256"] != workload["local_file_sha256"]:
        raise ValueError("structured probe dataset hash differs from protocol")
    if args.context_index < 0 or args.context_index >= authority["context_count"]:
        raise ValueError("structured probe context index is invalid")
    episodes = tuple(build_episode_inputs(authority["contexts"][args.context_index]))
    if args.source_sequence < 0 or args.source_sequence >= len(episodes):
        raise ValueError("structured probe source sequence is invalid")

    provider = protocol["provider"]
    base_url = str(provider["base_url"])
    model = str(provider["construction_model"])
    key = os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise ValueError("SILICONFLOW_API_KEY is required")
    probe = build_structured_extraction_probe(
        episode=episodes[args.source_sequence],
        previous_episodes=episodes[: args.source_sequence],
        namespace=f"membind-v7-{args.run_id}",
        model=model,
        max_tokens=args.max_tokens,
    )
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
        api_key=key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
        http_client=http_client,
    )
    try:
        result = await run_structured_extraction_probe_async(
            probe,
            completions=client.chat.completions,
            timeout_seconds=args.timeout_seconds,
        )
    finally:
        await client.close()
    return {
        **result,
        "run_id": args.run_id,
        "provider": "siliconflow-openai-compatible-v1",
        "protocol_path": args.protocol.name,
        "protocol_sha256": _sha256(args.protocol),
        "dataset_sha256": authority["local_file_sha256"],
        "sdk_max_retries": 0,
        "hard_attempt_limit": 1,
        "formal_r1_r3_eligible": False,
        "diagnostic_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "saturated_fixed_work_baseline_v1_3/v7/R1_R3_PROTOCOL_FREEZE.json",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json",
    )
    parser.add_argument("--context-index", type=int, default=0)
    parser.add_argument("--source-sequence", type=int, default=0)
    parser.add_argument("--max-tokens", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_id or "/" in args.run_id or "\\" in args.run_id:
        parser.error("--run-id must be a non-path identity")
    if args.max_tokens <= 0 or args.timeout_seconds <= 0:
        parser.error("max tokens and timeout must be positive")
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
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
