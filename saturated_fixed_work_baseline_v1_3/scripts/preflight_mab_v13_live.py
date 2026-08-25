#!/usr/bin/env python3
"""A3 read-only health probe for the frozen MAB v1.3 live campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
MAB_SRC = REPO / "mab_quality_v2_final_qa" / "src"
if str(MAB_SRC) not in sys.path:
    sys.path.insert(0, str(MAB_SRC))
from mab_quality_v2_final_qa.mab_main_dataset import build_authority  # noqa: E402


def _models(url: str, expected: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as response:
            body = response.read()
        parsed = json.loads(body)
        ids = sorted(str(item.get("id")) for item in parsed.get("data", []) if isinstance(item, dict) and item.get("id"))
        return {"status": "PASS" if expected in ids else "FAIL", "url": url, "expected_model": expected, "model_ids": ids, "body_sha256": hashlib.sha256(body).hexdigest()}
    except Exception as exc:
        return {"status": "FAIL", "url": url, "expected_model": expected, "error_class": type(exc).__name__, "error": str(exc)[:240]}


def _neo4j(host: str, port: int) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=5):
            return {"status": "TCP_REACHABLE", "host": host, "port": port}
    except OSError as exc:
        return {"status": "FAIL", "host": host, "port": port, "error_class": type(exc).__name__, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence: dict[str, Any] = {"schema_version": "membind.v1.3.live-preflight.v1", "scope": "A3_REAL_CONTEXT0_1_2_8_PROBE", "frozen_root": str(args.frozen_root.resolve())}
    try:
        authority = build_authority(REPO / "mab_quality_v2_final_qa" / "data" / "official_5_contexts.json")
        evidence["authority"] = {key: value for key, value in authority.items() if key != "contexts"}
        evidence["authority_status"] = "PASS"
    except Exception as exc:
        evidence["authority_status"] = "FAIL"
        evidence["authority_error"] = f"{type(exc).__name__}:{str(exc)}"
    evidence["construction_endpoint"] = _models("http://10.87.5.247:8000/v1/models", "qwen3-32b-fp8")
    evidence["embedding_endpoint"] = _models("http://10.87.5.247:8001/v1/models", "qwen3-embedding-0.6b")
    evidence["neo4j"] = _neo4j("localhost", 7687)
    live_gates = {
        "authority": evidence.get("authority_status") == "PASS",
        "construction_endpoint": evidence["construction_endpoint"].get("status") == "PASS",
        "embedding_endpoint": evidence["embedding_endpoint"].get("status") == "PASS",
        "neo4j": evidence["neo4j"].get("status") == "TCP_REACHABLE",
    }
    evidence["gates"] = live_gates
    evidence["status"] = "READY_FOR_A3" if all(live_gates.values()) else "BLOCKED_EXTERNAL_PROVIDER"
    evidence["blocked_gates"] = [key for key, value in live_gates.items() if not value]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "blocked_gates": evidence["blocked_gates"], "output": str(args.output)}, ensure_ascii=False, sort_keys=True))
    return 0 if evidence["status"] in {"READY_FOR_A3", "BLOCKED_EXTERNAL_PROVIDER"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
