#!/usr/bin/env python3
"""Persist a sanitized embedding endpoint identity probe."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedding_identity import assess_endpoint_identity, write_identity_probe  # noqa: E402
from graphiti_native import load_env_file  # noqa: E402


def _json_request(url: str, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + api_key},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "environment" / "embedding_identity_probe.json",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    load_env_file()
    base_url = os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/")
    model = os.environ.get("EMBEDDING_MODEL", "")
    api_key = os.environ.get("EMBEDDING_API_KEY") or os.environ.get("VLLM_API_KEY")
    if not base_url or not model or not api_key:
        print("error: embedding endpoint configuration is incomplete", file=sys.stderr)
        return 2
    origin_parts = urlsplit(base_url)
    origin = urlunsplit(
        (origin_parts.scheme, origin_parts.netloc, "", "", "")
    )
    try:
        models_payload = _json_request(base_url + "/models", api_key)
        version_payload = _json_request(origin + "/version", api_key)
        result = assess_endpoint_identity(
            models_payload,
            version_payload,
            expected_model=model,
        )
        written = write_identity_probe(result, args.output)
    except FileExistsError:
        print(f"error: output already exists: {args.output}", file=sys.stderr)
        return 2
    summary = {
        "status": written["status"],
        "served_model_id": written["served_model_id"],
        "vllm_version": written["vllm_version"],
        "endpoint_reported_revision": written["endpoint_reported_revision"],
        "blocks_v2_live_integration": written["blocks_v2_live_integration"],
        "output": str(args.output),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
