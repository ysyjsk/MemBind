#!/usr/bin/env python3
"""Reset both LLM prefix caches and apply one symmetric unmeasured warmup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
import sys
from typing import Any


P0_SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0,
    "presence_penalty": 1.5,
}
P1_SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "repetition_penalty": 1.05,
}
P2_SAMPLING = {
    "temperature": 0.6,
    "top_p": 0.95,
    "top_k": 20,
}


def deployment_sampling(policy_id: str) -> dict[str, Any]:
    if policy_id == "P0_QWEN3_8B_AWQ":
        return dict(P0_SAMPLING)
    if policy_id == "P1_QWEN25_7B_AWQ":
        return dict(P1_SAMPLING)
    if policy_id == "P2_QWEN3_14B_AWQ":
        return dict(P2_SAMPLING)
    raise RuntimeError(f"unknown deployment policy: {policy_id}")


def warmup_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    seed: int,
    deployment_policy_id: str = "P0_QWEN3_8B_AWQ",
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        **deployment_sampling(deployment_policy_id),
        "seed": seed,
        "max_tokens": 32,
        "response_format": {"type": "json_schema", "json_schema": schema},
    }
    if deployment_policy_id in {"P0_QWEN3_8B_AWQ", "P2_QWEN3_14B_AWQ"}:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def request_json(url: str, *, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="GET" if payload is None else "POST",
        headers={
            "Authorization": f"Bearer {os.environ['MEMBIND_LOCAL_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"attempt preparation request failed: {url}: {exc}") from exc
    if not raw:
        return None
    return json.loads(raw)


def write_exclusive(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        policy_id = os.environ.get(
            "MEMBIND_DEPLOYMENT_POLICY_ID", "P0_QWEN3_8B_AWQ"
        )
        sampling = deployment_sampling(policy_id)
        endpoints = [
            ("native-replica", os.environ["NATIVE_LLM_BASE_URL"].removesuffix("/v1")),
            ("prepare-replica", os.environ["PREPARE_LLM_BASE_URL"].removesuffix("/v1")),
        ]
        prompt = [
            {"role": "system", "content": "Return valid JSON matching the schema."},
            {"role": "user", "content": "Return status equal to ready."},
        ]
        schema = {
            "name": "membind_symmetric_warmup",
            "schema": {
                "type": "object",
                "properties": {"status": {"type": "string", "const": "ready"}},
                "required": ["status"],
                "additionalProperties": False,
            },
            "strict": True,
        }
        rows = []
        for endpoint_id, root in endpoints:
            models = request_json(f"{root}/v1/models")
            reset = request_json(f"{root}/reset_prefix_cache", payload={})
            started = time.monotonic_ns()
            warmup = request_json(
                f"{root}/v1/chat/completions",
                payload=warmup_payload(
                    model=os.environ["CONSTRUCTION_LLM_MODEL"],
                    messages=prompt,
                    schema=schema,
                    seed=int(os.environ["CONSTRUCTION_SEED"]),
                    deployment_policy_id=policy_id,
                ),
            )
            duration = time.monotonic_ns() - started
            content = warmup["choices"][0]["message"]["content"]
            if json.loads(content) != {"status": "ready"}:
                raise RuntimeError(f"warmup response mismatch: {endpoint_id}")
            rows.append(
                {
                    "endpoint_id": endpoint_id,
                    "served_models": sorted(row["id"] for row in models["data"]),
                    "cache_reset_response": reset,
                    "warmup_duration_ns": duration,
                    "warmup_response_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            )
        evidence = {
            "schema_version": "membind.dual-replica-attempt-preparation.v1",
            "status": "PASS",
            "attempt_id": args.attempt_id,
            "cache_policy": "reset_then_identical_structured_warmup_v1",
            "deployment_policy_id": policy_id,
            "warmup_sampling": sampling,
            "endpoints": rows,
            "completed_unix": time.time(),
        }
        write_exclusive(args.output, evidence)
        print(json.dumps(evidence, sort_keys=True))
        return 0
    except BaseException as exc:
        evidence = {
            "schema_version": "membind.dual-replica-attempt-preparation.v1",
            "status": "FAILED",
            "attempt_id": args.attempt_id,
            "cache_policy": "reset_then_identical_structured_warmup_v1",
            "failure_class": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "error": str(exc)[:1000],
            "content_and_secrets_omitted": True,
            "completed_unix": time.time(),
        }
        write_exclusive(args.output, evidence)
        print(json.dumps(evidence, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
