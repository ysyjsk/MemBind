#!/usr/bin/env python3
"""Invalidate and exactly clean the pre-oracle S4 capture attempt."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s1_live import S1LiveAdapter
from paper_eval.s4_authority import _write_exclusive
from paper_eval.s4_preflight_production import load_s4_preflight_env


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
RUN_DIR = (
    PROJECT
    / "artifacts/paper_eval/native/runs/s4-d0-capture-20260814-001"
)
NAMESPACE = "pev3-s4-u0-capture-20260814-001"
OUTPUT = RUN_DIR / "INVALIDATION.json"


def _checkpoint() -> dict:
    path = RUN_DIR / "checkpoint.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    body = dict(value)
    stored = body.pop("payload_sha256", None)
    if stored != payload_sha256(body):
        raise RuntimeError("invalid attempt checkpoint hash drift")
    if value.get("namespace") != NAMESPACE:
        raise RuntimeError("invalid attempt namespace drift")
    return value


async def _run(driver: object, checkpoint: dict) -> dict:
    from graphiti_core.utils.maintenance.graph_data_operations import clear_data

    adapter = S1LiveAdapter(NAMESPACE)
    before = await adapter.namespace_state(driver)
    await clear_data(driver, group_ids=[NAMESPACE])
    after = await adapter.namespace_state(driver)
    if int(after["node_count"]) or int(after["relationship_count"]):
        raise RuntimeError("exact invalid-attempt cleanup left live state")
    payload = {
        "schema_version": "membind.paper-eval-v3.s4-attempt-invalidation.v1",
        "stage": "S4",
        "run_id": "s4-d0-capture-20260814-001",
        "namespace": NAMESPACE,
        "status": "INCOMPLETE_INVALID_NON_MERGEABLE",
        "reason": "CAPTURE_ORACLES_BYPASSED_BY_RETAINED_GRAPHITI_CLIENT_BUNDLE",
        "completed_checkpoint_prefix": len(
            checkpoint.get("completed_source_sequences", [])
        ),
        "checkpoint_file_sha256": sha256_file(RUN_DIR / "checkpoint.json"),
        "events_file_sha256": sha256_file(RUN_DIR / "events.jsonl"),
        "private_cache_evidence": {
            "prompt_cache_file_sha256": sha256_file(
                PROJECT
                / "runtime/private/s4-d0-07741c45-20260814-001/prompt.jsonl"
            ),
            "embedding_cache_file_sha256": sha256_file(
                PROJECT
                / "runtime/private/s4-d0-07741c45-20260814-001/embedding.jsonl"
            ),
        },
        "pre_cleanup": {
            "node_count": int(before["node_count"]),
            "relationship_count": int(before["relationship_count"]),
        },
        "cleanup": {
            "scope": "EXACT_GROUP_ID_ONLY",
            "group_ids": [NAMESPACE],
            "global_cleanup_used": False,
            "post_cleanup_node_count": int(after["node_count"]),
            "post_cleanup_relationship_count": int(after["relationship_count"]),
        },
        "reuse_authorized": False,
        "mergeable": False,
    }
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id="s4-capture-invalidation-20260814-001",
    )
    _write_exclusive(OUTPUT, artifact)
    return artifact


def main() -> None:
    checkpoint = _checkpoint()
    source = str(LEGACY / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    env = load_s4_preflight_env(LEGACY / ".env")
    driver = Neo4jDriver(
        env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"]
    )

    async def execute() -> dict:
        try:
            return await _run(driver, checkpoint)
        finally:
            await driver.close()

    artifact = asyncio.run(execute())
    print(
        json.dumps(
            {
                "status": artifact["payload"]["status"],
                "pre_cleanup": artifact["payload"]["pre_cleanup"],
                "cleanup": artifact["payload"]["cleanup"],
                "artifact": str(OUTPUT),
                "artifact_file_sha256": sha256_file(OUTPUT),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
