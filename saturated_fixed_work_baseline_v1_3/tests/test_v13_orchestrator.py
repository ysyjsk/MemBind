from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.campaign_orchestrator import (
    CampaignOrchestrationError,
    build_campaign_plan,
    run_campaign,
)


def _authority() -> dict:
    return {
        "revision": "7ea066982b140a19337e17e60d45d4076e042faf",
        "source_filter": "longmemeval_s*",
        "context_count": 5,
        "context_ids": [f"ctx{i}" for i in range(5)],
        "session_counts": [111, 107, 116, 111, 110],
        "local_file_sha256": "a" * 64,
        "authority_sha256": "h" * 64,
    }


def test_formal_plan_rejects_prefix_and_incomplete_context_subset() -> None:
    with pytest.raises(CampaignOrchestrationError, match="prefix"):
        build_campaign_plan(_authority(), context_indices=(0,), scope="FORMAL", repeats=1)
    with pytest.raises(CampaignOrchestrationError, match="0..4"):
        build_campaign_plan(_authority(), context_indices=(0, 1, 2, 3), scope="FORMAL", repeats=1)
    plan = build_campaign_plan(_authority(), context_indices=(0,), scope="ENGINEERING_DIAGNOSTIC", repeats=1, session_limit=8)
    assert plan["scope"] == "ENGINEERING_DIAGNOSTIC"
    assert len(plan["blocks"]) == 3


def test_campaign_ledger_keeps_failed_attempt_and_uses_fresh_namespaces(tmp_path: Path) -> None:
    plan = build_campaign_plan(_authority(), context_indices=(0,), scope="ENGINEERING_DIAGNOSTIC", repeats=1, session_limit=2)
    seen: list[str] = []

    async def runner(block: dict) -> dict:
        seen.append(block["namespace"])
        if block["method"] == "B1":
            raise RuntimeError("provider unavailable")
        return {"status": "PASS", "method": block["method"], "t_build_ns": 10}

    result = asyncio.run(run_campaign(plan, output_root=tmp_path / "campaign", block_runner=runner))
    assert result["completed_block_count"] == 2
    assert result["failed_block_count"] == 1
    assert len(set(seen)) == 3
    rows = [json.loads(line) for line in (tmp_path / "campaign" / "campaign_ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 6  # append-only start and terminal row per attempt
    failures = [row for row in rows if row["event"] == "ATTEMPT_FAILURE"]
    assert failures[0]["failure_class"] == "RuntimeError"
