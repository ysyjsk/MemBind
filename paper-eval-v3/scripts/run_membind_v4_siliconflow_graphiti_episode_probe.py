#!/usr/bin/env python3
"""Run one isolated Graphiti episode through SiliconFlow and Neo4j.

This is a development-only compatibility smoke. It deliberately uses one
fresh group id, never invokes the v4 A1 runner, and records only public counts
and hashes. The process environment is the only credential source.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
for selected in (
    PROJECT / "src",
    PROJECT.parent / "membind-validation" / "src",
    PROJECT.parent / "mab_quality_v2_final_qa" / "src",
):
    if str(selected) not in sys.path:
        sys.path.append(str(selected))

from paper_eval.artifacts import atomic_write_json, payload_sha256  # noqa: E402
from mab_quality_v2_final_qa.live_workflow import _overlay_siliconflow_env  # noqa: E402
from mab_quality_v2_final_qa.runtime_gate import (  # noqa: E402
    SILICONFLOW_BASE_URL,
    SILICONFLOW_CHAT_MODEL,
    SILICONFLOW_EMBEDDING_MODEL,
)


def _env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip("\"'")
    return result


async def _run(*, api_key: str, group_id: str, env_path: Path) -> dict[str, object]:
    from graphiti_core.nodes import EpisodeType
    from mab_quality_v2_final_qa.siliconflow_runtime import build_siliconflow_u0_runtime

    source_env = _env_file(env_path)
    env = _overlay_siliconflow_env(source_env, {"SILICONFLOW_API_KEY": api_key})
    runtime = build_siliconflow_u0_runtime(
        env=env,
        request_id_prefix=f"membind-v4-sf-compat-{group_id}",
    )
    graphiti = runtime.graphiti
    driver = getattr(graphiti, "driver", None)
    init_task = getattr(driver, "_init_task", None)
    if init_task is not None:
        await init_task
    try:
        async def counts() -> tuple[int, int, int]:
            snapshot = await driver.execute_query(
                """
                CALL { MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count }
                CALL { MATCH ()-[r]->() WHERE r.group_id = $group_id RETURN count(r) AS relationship_count }
                CALL { MATCH (n:Episodic) WHERE n.group_id = $group_id RETURN count(n) AS episode_count }
                RETURN node_count, relationship_count, episode_count
                """,
                params={"group_id": group_id},
            )
            record = snapshot.records[0]
            return (
                int(record.get("node_count") or 0),
                int(record.get("relationship_count") or 0),
                int(record.get("episode_count") or 0),
            )

        initial_counts = await counts()
        if initial_counts != (0, 0, 0):
            raise RuntimeError("GROUP_NOT_FRESH")
        await graphiti.add_episode(
            name=f"sf-compat-probe-{group_id}",
            episode_body="Alice joined the team in 2026.",
            source_description="MemBind v4 provider compatibility probe",
            reference_time=datetime(2026, 8, 20, tzinfo=timezone.utc),
            source=EpisodeType.message,
            group_id=group_id,
        )
        node_count, relationship_count, episode_count = await counts()
        body: dict[str, object] = {
            "schema_version": "membind.paper-eval-v4.siliconflow-graphiti-episode-probe.v1",
            "status": "PASS",
            "provider": "SILICONFLOW_QWEN",
            "construction_endpoint": SILICONFLOW_BASE_URL,
            "construction_model": SILICONFLOW_CHAT_MODEL,
            "embedding_model": SILICONFLOW_EMBEDDING_MODEL,
            "group_id": group_id,
            "initial_node_count": initial_counts[0],
            "initial_relationship_count": initial_counts[1],
            "initial_episode_count": initial_counts[2],
            "node_count": node_count,
            "relationship_count": relationship_count,
            "episode_count": episode_count,
            "runtime_identity_sha256": hashlib.sha256(
                json.dumps(runtime.public_identity, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "formal_main_table_eligible": False,
            "development_only": True,
            "credentials_recorded": False,
            "api_key_source": "PROCESS_ENVIRONMENT_ONLY",
            "mutations_performed": True,
            "mutation_scope": "ONE_FRESH_NEO4J_GROUP_ONLY",
        }
        body["payload_sha256"] = payload_sha256(body)
        return body
    finally:
        close = getattr(graphiti, "close", None)
        if callable(close):
            value = close()
            if hasattr(value, "__await__"):
                await value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=PROJECT.parent / "membind-validation/.env")
    parser.add_argument("--group-id", default=None)
    args = parser.parse_args(argv)
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        print("SILICONFLOW_GRAPHITI_EPISODE_PROBE_FAILED:api_key_missing", file=sys.stderr)
        return 2
    group_id = args.group_id or f"membind-v4-sf-compat-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    try:
        result = asyncio.run(_run(api_key=api_key, group_id=group_id, env_path=args.env_file))
    except Exception as error:
        print(f"SILICONFLOW_GRAPHITI_EPISODE_PROBE_FAILED:{type(error).__name__}:{error}", file=sys.stderr)
        return 2
    args.output_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output_root / "SILICONFLOW_GRAPHITI_EPISODE_PROBE.json", result)
    print(json.dumps({"status": result["status"], "artifact": str((args.output_root / "SILICONFLOW_GRAPHITI_EPISODE_PROBE.json").resolve()), "formal_main_table_eligible": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
