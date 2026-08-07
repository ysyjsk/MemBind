"""Reproducible Graphiti add/search/database-isolation integration gate."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from live_runtime import count_nodes, prepare_clean_graph


SMOKE_GROUP_ID = "integration_smoke"


async def graphiti_integration_smoke(graphiti: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}

    async def warm_up(runtime: Any) -> None:
        added = await runtime.add_episode(
            name="integration-smoke-0000",
            episode_body=(
                "[USER] Alice works at Adidas.\n"
                "[ASSISTANT] I will remember that Alice works at Adidas."
            ),
            source_description="MemBind integration smoke",
            reference_time=datetime(2026, 8, 6, tzinfo=timezone.utc),
            group_id=SMOKE_GROUP_ID,
        )
        result["episode_uuid"] = str(getattr(getattr(added, "episode", None), "uuid", ""))
        result["entity_count"] = len(getattr(added, "nodes", []) or [])
        result["edge_count"] = len(getattr(added, "edges", []) or [])
        found = await runtime.search(
            "Where does Alice work?",
            group_ids=[SMOKE_GROUP_ID],
            num_results=10,
        )
        result["search_count"] = len(found)
        result["search_facts"] = [str(getattr(edge, "fact", "")) for edge in found]
        if not found:
            raise RuntimeError("Graphiti integration search returned no results")

    await prepare_clean_graph(graphiti, warm_up)
    result["node_count_after_clear"] = await count_nodes(graphiti)
    result["ok"] = result["search_count"] > 0 and result["node_count_after_clear"] == 0
    return result
