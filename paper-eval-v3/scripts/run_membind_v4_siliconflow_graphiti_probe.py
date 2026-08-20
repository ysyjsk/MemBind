#!/usr/bin/env python3
"""Run one Graphiti Qwen client request against SiliconFlow.

This is a provider-compatibility probe only. It does not instantiate Graphiti,
touch Neo4j, run a candidate, or claim vLLM performance parity.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx
from openai import AsyncOpenAI

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))
LEGACY_SRC = PROJECT.parent / "membind-validation" / "src"
if str(LEGACY_SRC) not in sys.path:
    sys.path.append(str(LEGACY_SRC))
MAB_SRC = PROJECT.parent / "mab_quality_v2_final_qa" / "src"
if str(MAB_SRC) not in sys.path:
    sys.path.append(str(MAB_SRC))

from paper_eval.artifacts import atomic_write_json, payload_sha256  # noqa: E402
from paper_eval.membind_v4.siliconflow_probe import (  # noqa: E402
    SILICONFLOW_BASE_URL,
    SILICONFLOW_CHAT_MODEL,
)


async def _run(api_key: str, timeout_seconds: float) -> dict[str, object]:
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.prompts.dedupe_nodes import NodeResolutions
    from graphiti_core.prompts.models import Message
    from graphiti_native import QwenVLLMClient
    from mab_quality_v2_final_qa.live_adapters import SiliconFlowOpenAITransport

    timeout = httpx.Timeout(
        connect=10.0,
        read=timeout_seconds,
        write=timeout_seconds,
        pool=timeout_seconds,
    )
    http_client = httpx.AsyncClient(timeout=timeout, trust_env=False)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=SILICONFLOW_BASE_URL,
        timeout=timeout,
        max_retries=0,
        http_client=http_client,
    )
    try:
        config = LLMConfig(
            api_key=api_key,
            model=SILICONFLOW_CHAT_MODEL,
            small_model=SILICONFLOW_CHAT_MODEL,
            base_url=SILICONFLOW_BASE_URL,
            temperature=0.0,
            max_tokens=256,
        )
        llm = QwenVLLMClient(
            config=config,
            max_tokens=256,
            structured_output_mode="json_schema",
            vllm_options_enabled=False,
            client=client,
        )
        llm.client = SiliconFlowOpenAITransport(llm.client)
        result = await llm.generate_response(
            [
                Message(role="system", content="Return the requested JSON only."),
                Message(
                    role="user",
                    content=(
                        "Resolve one entity named Alice. Return id 0, name Alice, "
                        "duplicate_candidate_id -1."
                    ),
                ),
            ],
            response_model=NodeResolutions,
            max_tokens=128,
            prompt_name="membind_v4_provider_probe",
        )
        public = {
            "schema_version": "membind.paper-eval-v4.siliconflow-graphiti-probe.v1",
            "status": "PASS",
            "provider": "SILICONFLOW_QWEN",
            "model": SILICONFLOW_CHAT_MODEL,
            "structured_output_mode": "json_schema",
            "structured_backend_observed": "provider-managed/unknown",
            "vllm_options_enabled": False,
            "http_max_retries": 0,
            "http_trust_env": False,
            "call_count": int(getattr(llm, "call_count", 0)),
            "structured_request_count": int(getattr(llm, "structured_request_count", 0)),
            "entity_resolution_count": len(result.get("entity_resolutions", [])),
            "usage": dict(getattr(llm, "usage_totals", {}) or {}),
            "result_sha256": hashlib.sha256(
                json.dumps(result, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            "formal_main_table_eligible": False,
            "development_only": True,
            "mutations_performed": False,
            "credentials_recorded": False,
            "api_key_source": "PROCESS_ENVIRONMENT_ONLY",
        }
        public["payload_sha256"] = payload_sha256(public)
        return public
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not key:
        print("SILICONFLOW_GRAPHITI_PROBE_FAILED:api_key_missing", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(_run(key, args.timeout))
    except Exception as error:
        print(
            f"SILICONFLOW_GRAPHITI_PROBE_FAILED:{type(error).__name__}:{error}",
            file=sys.stderr,
        )
        return 2
    args.output_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output_root / "SILICONFLOW_GRAPHITI_PROBE.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "artifact": str(
                    (args.output_root / "SILICONFLOW_GRAPHITI_PROBE.json").resolve()
                ),
                "formal_main_table_eligible": result["formal_main_table_eligible"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
