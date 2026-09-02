#!/usr/bin/env python3
"""Capture diagnostic-only strict Graphiti edge responses for L0 auditing.

The raw payloads are written outside the repository under the local run root and
are never consumed by construction.  This script performs one node request to
obtain the entity set, followed by exactly one 16K and one 32K edge request.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "saturated_fixed_work_baseline_v1_3/src", ROOT / "mab_quality_v2_final_qa/src"):
    sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab_main_dataset import build_authority, build_episode_inputs
from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import (
    build_structured_edge_extraction_probe,
    build_structured_extraction_probe,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import load_protocol_freeze


async def main() -> None:
    import httpx
    from openai import AsyncOpenAI
    from graphiti_core.prompts.extract_nodes import ExtractedEntities

    run_root = Path(os.environ["L0_RUN_ROOT"]).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    authority = build_authority(ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json")
    episodes = tuple(build_episode_inputs(authority["contexts"][0]))
    episode = episodes[0]
    namespace = "membind-root-cause-diagnostic-20260902"
    model = "qwen3-8b-awq"
    timeout = httpx.Timeout(connect=10.0, read=3600.0, write=3600.0, pool=3600.0)
    client = AsyncOpenAI(
        api_key=os.environ.get("MEMBIND_LOCAL_API_KEY", "membind-local"),
        base_url="http://127.0.0.1:18200/v1",
        timeout=timeout,
        max_retries=0,
        http_client=httpx.AsyncClient(timeout=timeout, trust_env=False),
    )
    try:
        node_probe = build_structured_extraction_probe(
            episode=episode,
            previous_episodes=[],
            namespace=namespace,
            model=model,
            max_tokens=32768,
        )
        node_response = await client.chat.completions.create(**node_probe.request)
        node_content = node_response.choices[0].message.content
        (run_root / "node_response.json").write_text(node_content, encoding="utf-8")
        parsed_nodes = ExtractedEntities.model_validate(json.loads(node_content))
        names = []
        for item in parsed_nodes.extracted_entities:
            name = str(item.name).strip()
            if name and name not in names:
                names.append(name)
        if len(names) < 2:
            raise RuntimeError(f"node probe returned fewer than two entities: {names!r}")
        metadata = {
            "node_finish_reason": node_response.choices[0].finish_reason,
            "node_usage": node_response.usage.model_dump() if hasattr(node_response.usage, "model_dump") else str(node_response.usage),
            "entity_names": names,
        }
        for budget in (16384, 32768):
            probe = build_structured_edge_extraction_probe(
                episode=episode,
                previous_episodes=[],
                namespace=namespace,
                model=model,
                max_tokens=budget,
                entity_names=names,
            )
            response = await client.chat.completions.create(**probe.request)
            content = response.choices[0].message.content or ""
            (run_root / f"edge_{budget}.txt").write_text(content, encoding="utf-8")
            usage = response.usage.model_dump() if hasattr(response.usage, "model_dump") else str(response.usage)
            metadata[f"edge_{budget}"] = {
                "finish_reason": response.choices[0].finish_reason,
                "usage": usage,
                "characters": len(content),
                "messages_sha256": __import__("hashlib").sha256(json.dumps(probe.request["messages"], default=str, sort_keys=True).encode()).hexdigest(),
            }
        (run_root / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
