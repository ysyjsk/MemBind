"""TDD contract tests for request-level global LLM admission."""

from __future__ import annotations

import asyncio

import pytest

from paper_eval.membind_v1.admission import (
    MemBindV1AdmissionError,
    RequestAdmission,
    RuntimeBounds,
)


def test_conservative_runtime_bounds_are_explicit_c1_w1_k2() -> None:
    assert RuntimeBounds.conservative_defaults() == RuntimeBounds(
        compile_concurrency=1,
        prepared_lookahead=1,
        llm_request_limit=2,
    )


def test_async_request_admission_never_exceeds_k_and_reports_observed_max() -> None:
    async def scenario() -> dict[str, object]:
        admission = RequestAdmission(limit=2)
        release = asyncio.Event()
        entered = asyncio.Event()
        entry_count = 0

        async def request(request_id: str) -> None:
            nonlocal entry_count
            async with admission.request(request_id):
                entry_count += 1
                if entry_count == 2:
                    entered.set()
                await release.wait()

        tasks = [asyncio.create_task(request(f"request-{index}")) for index in range(3)]
        await asyncio.wait_for(entered.wait(), timeout=1)
        snapshot = admission.observation()
        assert snapshot["active_request_count"] == 2
        assert snapshot["observed_max_inflight"] == 2
        release.set()
        await asyncio.gather(*tasks)
        return admission.observation()

    snapshot = asyncio.run(scenario())
    assert snapshot["active_request_count"] == 0
    assert snapshot["observed_max_inflight"] == 2
    assert snapshot["completed_request_count"] == 3


def test_request_admission_rejects_concurrent_duplicate_request_identity() -> None:
    async def scenario() -> None:
        admission = RequestAdmission(limit=2)
        release = asyncio.Event()

        async with admission.request("request-1"):
            duplicate = asyncio.create_task(_use_duplicate(admission))
            await asyncio.sleep(0)
            with pytest.raises(MemBindV1AdmissionError, match="request_already_active"):
                await duplicate
            release.set()

    async def _use_duplicate(admission: RequestAdmission) -> None:
        async with admission.request("request-1"):
            pass

    asyncio.run(scenario())
