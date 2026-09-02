#!/usr/bin/env python3
"""Run the one-shot deterministic xgrammar/guidance qualification.

This is provider-only evidence: no Graphiti runner, database, embedding, retry,
or formal workload is involved.  The request fixtures and wire parameters are
constructed once and reused byte-for-byte for each backend endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError


class Edge(BaseModel):
    source_entity_name: str
    target_entity_name: str
    relation_type: str
    fact: str
    valid_at: str | None = None
    invalid_at: str | None = None
    episode_indices: list[int] = Field(default_factory=lambda: [0])


class ExtractedEdges(BaseModel):
    edges: list[Edge]


ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "saturated_fixed_work_baseline_v1_3/src", ROOT / "mab_quality_v2_final_qa/src"):
    sys.path.insert(0, str(source))


def _exact_requests() -> list[tuple[str, dict[str, Any]]]:
    """Build Graphiti's exact edge prompt/schema requests for fixed fixtures."""
    from mab_quality_v2_final_qa.mab_main_dataset import build_authority, build_episode_inputs
    from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import build_structured_edge_extraction_probe

    authority = build_authority(ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json")
    official = tuple(build_episode_inputs(authority["contexts"][0]))
    dense_body = " ".join(
        f"Entity-{index:02d} is explicitly connected to Entity-{(index + 1) % 12:02d}."
        for index in range(12)
    )
    dense = SimpleNamespace(
        context_id="l1-dense-edge-fixture", source_sequence=0,
        body=dense_body, reference_time="2026-01-01T00:00:00Z",
    )
    fixtures: list[tuple[str, Any, list[str]]] = [("dense_edge", dense, [f"Entity-{i:02d}" for i in range(12)])]
    for index in range(3):
        fixtures.append((f"primary_path_{index}", official[index], ["USER", "Miami", "Delta SkyMiles", "JetBlue", "Boston", "American Airlines"]))
    requests: list[tuple[str, dict[str, Any]]] = []
    for name, episode, entity_names in fixtures:
        probe = build_structured_edge_extraction_probe(
            episode=episode, previous_episodes=official[: int(getattr(episode, "source_sequence", 0))],
            namespace="membind-l1-guidance-fixed-fixture", model="qwen3-8b-awq",
            max_tokens=16_384, entity_names=entity_names,
        )
        requests.append((name, probe.request))
    return requests


async def _call(client: AsyncOpenAI, request: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic_ns()
    try:
        response = await client.chat.completions.create(**request)
        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage.model_dump() if hasattr(response.usage, "model_dump") else None
        parsed_ok = False
        pydantic_ok = False
        item_count = None
        error = None
        try:
            value = json.loads(content)
            parsed_ok = True
            parsed = ExtractedEdges.model_validate(value)
            pydantic_ok = True
            item_count = len(parsed.edges)
        except (json.JSONDecodeError, ValidationError) as exc:
            error = type(exc).__name__
        return {
            "status": "PASS" if choice.finish_reason == "stop" and parsed_ok and pydantic_ok else "FAIL",
            "finish_reason": choice.finish_reason,
            "usage": usage,
            "response_characters": len(content),
            "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "json_valid": parsed_ok,
            "pydantic_valid": pydantic_ok,
            "edge_count": item_count,
            "error_type": error,
            "duration_ns": time.monotonic_ns() - started,
            "raw_response": content,
        }
    except Exception as exc:  # provider rejection/transport remains diagnostic evidence
        return {
            "status": "FAIL",
            "finish_reason": None,
            "usage": None,
            "response_characters": None,
            "response_sha256": None,
            "json_valid": False,
            "pydantic_valid": False,
            "edge_count": None,
            "error_type": type(exc).__name__,
            "error_message_sha256": hashlib.sha256(str(exc).encode()).hexdigest(),
            "duration_ns": time.monotonic_ns() - started,
            "raw_response": None,
        }


async def _run(args: argparse.Namespace) -> None:
    fixtures = _exact_requests()
    requests = [request for _, request in fixtures]
    request_hash = hashlib.sha256(json.dumps(requests, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    timeout = httpx.Timeout(connect=10.0, read=args.timeout, write=args.timeout, pool=args.timeout)
    client = AsyncOpenAI(api_key=args.api_key, base_url=args.base_url, timeout=timeout, max_retries=0, http_client=httpx.AsyncClient(timeout=timeout, trust_env=False))
    try:
        rows = []
        for kind, request in fixtures:
            first = await _call(client, request)
            repeat = await _call(client, request)
            rows.append({"fixture": kind, "request_hash": request_hash, "first": first, "repeat": repeat, "canonical_stable": first.get("raw_response") == repeat.get("raw_response")})
    finally:
        await client.close()
    output = {
        "schema_version": "membind.l1-structured-backend-qualification.v1",
        "backend": args.backend,
        "base_url": args.base_url,
        "model": args.model,
        "max_tokens": args.max_tokens,
        "seed": 20260806,
        "temperature": 0.0,
        "top_p": 1.0,
        "request_hash": request_hash,
        "graphiti_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        "native_runner_patch": False,
        "formal_workload": False,
        "fixtures": [{key: value for key, value in row.items() if key != "first" and key != "repeat"} | {"first": {k: v for k, v in row["first"].items() if k != "raw_response"}, "repeat": {k: v for k, v in row["repeat"].items() if k != "raw_response"}} for row in rows],
        "raw_responses": [{"fixture": row["fixture"], "first": row["first"].get("raw_response"), "repeat": row["repeat"].get("raw_response")} for row in rows],
    }
    output["status"] = "PASS" if all(
        row["first"]["status"] == "PASS" and row["repeat"]["status"] == "PASS" and row["canonical_stable"] and row["first"]["finish_reason"] == "stop" for row in rows
    ) else "L1_REJECTED"
    args.output.write_text(json.dumps(output, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["status"], "backend": args.backend, "output": str(args.output)}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="qwen3-8b-awq")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--api-key", default="membind-local")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"output already exists: {args.output}")
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
