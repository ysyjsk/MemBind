from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.three_way_campaign import (
    CampaignRunnerError,
    run_provider_free_block,
)


WORKLOAD = [
    {"context_id": "ctx", "source_sequence": 0, "episode_id": "e0", "body": "a", "reference_time": "t0", "arrival_offset_s": 0.0},
    {"context_id": "ctx", "source_sequence": 1, "episode_id": "e1", "body": "b", "reference_time": "t1", "arrival_offset_s": 0.0},
]


def test_provider_free_two_session_triad_uses_shared_durable_boundary() -> None:
    async def prepare(sequence: int, method: str) -> dict:
        if method == "B1" and sequence == 0:
            await asyncio.sleep(0.01)
        return {"sequence": sequence, "method": method}

    async def publish(sequence: int, prepared: dict, method: str) -> None:
        await asyncio.sleep(0)

    outputs = {}
    for method in ("B0", "B1", "V6"):
        outputs[method] = asyncio.run(
            run_provider_free_block(
                WORKLOAD,
                method=method,
                prepare=prepare,
                publish=publish,
            )
        )
    assert {result["workload_hash"] for result in outputs.values()}.__len__() == 1
    assert all(result["lifecycle_validation"]["contract_status"] == "PASS" for result in outputs.values())
    assert outputs["B0"]["order_validation"]["order_contract_status"] == "PASS"
    assert outputs["V6"]["order_validation"]["order_contract_status"] == "PASS"
    assert outputs["B1"]["order_validation"]["order_contract_status"] == "NOT_REQUIRED"
    assert outputs["V6"]["refinement_validation"]["refinement_status"] == "PASS"
    assert all(result["t_build_ns"] >= 0 for result in outputs.values())


def test_provider_free_runner_rejects_nonzero_arrival_and_unknown_method() -> None:
    with pytest.raises(CampaignRunnerError, match="method"):
        asyncio.run(run_provider_free_block(WORKLOAD, method="unknown", prepare=lambda *_: None, publish=lambda *_: None))
    with pytest.raises(CampaignRunnerError, match="arrival"):
        asyncio.run(
            run_provider_free_block(
                [dict(WORKLOAD[0], arrival_offset_s=1.0)],
                method="B0",
                prepare=lambda *_: None,
                publish=lambda *_: None,
            )
        )
