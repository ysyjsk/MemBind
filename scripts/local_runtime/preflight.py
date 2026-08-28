#!/usr/bin/env python3
"""Read-only local service preflight for the MemBind Qwen profile."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx


def check_models(client: httpx.Client, base_url: str, expected: str) -> dict[str, object]:
    response = client.get(f"{base_url.rstrip('/')}/models")
    response.raise_for_status()
    payload = response.json()
    ids = [str(row.get("id")) for row in payload.get("data", [])]
    return {"expected": expected, "ids": ids, "ok": expected in ids}


def check_chat(client: httpx.Client, base_url: str, model: str) -> dict[str, object]:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    response = client.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Return {ok:true}."}],
            "temperature": 0,
            "max_tokens": 32,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "probe", "schema": schema, "strict": True}},
        },
    )
    response.raise_for_status()
    body = response.json()
    message = body["choices"][0]["message"]
    parsed = json.loads(message["content"])
    finish_reason = body["choices"][0].get("finish_reason")
    return {"ok": parsed == {"ok": True} and finish_reason == "stop", "finish_reason": finish_reason, "content": parsed}


async def check_chat_concurrency(
    base_url: str,
    model: str,
    api_key: str,
    timeout: float,
    request_count: int = 8,
) -> dict[str, object]:
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    async def send_one(client: httpx.AsyncClient, index: int) -> bool:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": f"Probe {index}: return {{ok:true}}."}],
                "temperature": 0,
                "max_tokens": 32,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": f"probe_{index}", "schema": schema, "strict": True},
                },
            },
        )
        response.raise_for_status()
        body = response.json()
        parsed = json.loads(body["choices"][0]["message"]["content"])
        return parsed == {"ok": True} and body["choices"][0].get("finish_reason") == "stop"

    async with httpx.AsyncClient(
        timeout=timeout,
        trust_env=False,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        results = await asyncio.gather(*(send_one(client, index) for index in range(request_count)))
    succeeded = sum(results)
    return {"ok": succeeded == request_count, "requests": request_count, "succeeded": succeeded}


def check_embedding(client: httpx.Client, base_url: str, model: str, dimension: int) -> dict[str, object]:
    response = client.post(f"{base_url.rstrip('/')}/embeddings", json={"model": model, "input": ["MemBind local embedding probe"]})
    response.raise_for_status()
    vector = response.json()["data"][0]["embedding"]
    return {"ok": len(vector) == dimension, "dimension": len(vector), "expected_dimension": dimension}


def check_embedding_batch(
    client: httpx.Client,
    base_url: str,
    model: str,
    dimension: int,
    input_count: int = 128,
) -> dict[str, object]:
    inputs = [f"MemBind embedding concurrency probe {index}" for index in range(input_count)]
    response = client.post(f"{base_url.rstrip('/')}/embeddings", json={"model": model, "input": inputs})
    response.raise_for_status()
    rows = response.json()["data"]
    dimensions = [len(row["embedding"]) for row in rows]
    ok = len(rows) == input_count and all(value == dimension for value in dimensions)
    return {"ok": ok, "inputs": input_count, "vectors": len(rows), "dimension": dimension}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-base-url", default="http://127.0.0.1:18100/v1")
    parser.add_argument("--llm-model", default="qwen3-14b-awq")
    parser.add_argument("--embedding-base-url", default="http://127.0.0.1:18101/v1")
    parser.add_argument("--embedding-model", default="qwen3-embedding-0.6b")
    parser.add_argument("--embedding-dimension", type=int, default=1024)
    parser.add_argument("--api-key", default="membind-local")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    started = time.monotonic()
    result: dict[str, object] = {"ok": False, "started_unix": time.time(), "checks": {}}
    try:
        with httpx.Client(
            timeout=args.timeout,
            trust_env=False,
            headers={"Authorization": f"Bearer {args.api_key}"},
        ) as client:
            checks = {
                "llm_models": check_models(client, args.llm_base_url, args.llm_model),
                "llm_structured_json": check_chat(client, args.llm_base_url, args.llm_model),
                "llm_concurrency_8": asyncio.run(
                    check_chat_concurrency(
                        args.llm_base_url,
                        args.llm_model,
                        args.api_key,
                        args.timeout,
                    )
                ),
                "embedding_models": check_models(client, args.embedding_base_url, args.embedding_model),
                "embedding_dimension": check_embedding(client, args.embedding_base_url, args.embedding_model, args.embedding_dimension),
                "embedding_batch_128": check_embedding_batch(
                    client,
                    args.embedding_base_url,
                    args.embedding_model,
                    args.embedding_dimension,
                ),
            }
        result["checks"] = checks
        result["ok"] = all(bool(value.get("ok")) for value in checks.values())
    except Exception as exc:  # preflight output must remain machine-readable
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_seconds"] = time.monotonic() - started
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
