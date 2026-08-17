from __future__ import annotations

import asyncio

import pytest

from paper_eval.baseline_suite_live import execute_method_schedule
from paper_eval.s5_native_method_adapters import S5EpisodeRef


def _episodes(count: int) -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(
            source_sequence=index,
            source_sha256=f"{index + 1:064x}",
            native_episode={"source_sequence": index},
        )
        for index in range(count)
    )


@pytest.mark.asyncio
async def test_u0_schedule_is_direct_serial_without_async_caller_returns() -> None:
    active = 0
    peak = 0
    call_order: list[int] = []
    events: list[dict[str, object]] = []

    async def add_episode(episode: object) -> None:
        nonlocal active, peak
        source = int(episode["source_sequence"])
        active += 1
        peak = max(peak, active)
        call_order.append(source)
        await asyncio.sleep(0)
        active -= 1

    async def persist(event: dict[str, object]) -> None:
        events.append(dict(event))

    result = await execute_method_schedule(
        method="U0",
        run_id="bs-canary-20260816-001-u0",
        episodes=_episodes(2),
        native_add_episode=add_episode,
        persist_event=persist,
    )

    assert result["status"] == "PASS"
    assert result["summary"]["configured_worker_count"] == 1
    assert result["summary"]["max_active_calls"] == 1
    assert call_order == [0, 1]
    assert peak == 1
    assert not any(event["event_type"] == "caller_return" for event in events)


@pytest.mark.asyncio
async def test_a0_schedule_reuses_fifo_single_worker_and_durable_caller_returns() -> None:
    call_order: list[int] = []
    events: list[dict[str, object]] = []

    async def add_episode(episode: object) -> None:
        call_order.append(int(episode["source_sequence"]))
        await asyncio.sleep(0)

    async def persist(event: dict[str, object]) -> None:
        events.append(dict(event))

    result = await execute_method_schedule(
        method="A0",
        run_id="bs-canary-20260816-001-a0",
        episodes=_episodes(2),
        native_add_episode=add_episode,
        persist_event=persist,
    )

    assert result["status"] == "PASS"
    assert result["summary"]["configured_worker_count"] == 1
    assert result["summary"]["max_active_calls"] == 1
    assert call_order == [0, 1]
    assert sum(event["event_type"] == "caller_return" for event in events) == 2


@pytest.mark.asyncio
async def test_p_c2_schedule_requires_and_observes_two_whole_update_workers() -> None:
    entered: list[int] = []
    release = asyncio.Event()
    events: list[dict[str, object]] = []

    async def add_episode(episode: object) -> None:
        entered.append(int(episode["source_sequence"]))
        if len(entered) == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=1)

    async def persist(event: dict[str, object]) -> None:
        events.append(dict(event))

    result = await execute_method_schedule(
        method="P(C=2)",
        run_id="bs-canary-20260816-001-p-c2",
        episodes=_episodes(2),
        native_add_episode=add_episode,
        persist_event=persist,
    )

    assert result["status"] == "PASS"
    assert result["summary"]["configured_worker_count"] == 2
    assert result["summary"]["max_active_calls"] == 2
    assert result["summary"]["whole_update_interval_overlap_observed"] is True
    assert sorted(entered) == [0, 1]
    assert sum(event["event_type"] == "publication" for event in events) == 2


@pytest.mark.asyncio
async def test_schedule_rejects_mstar_before_invoking_native_callable() -> None:
    called = False

    async def add_episode(_episode: object) -> None:
        nonlocal called
        called = True

    async def persist(_event: dict[str, object]) -> None:
        return None

    with pytest.raises(ValueError, match="method"):
        await execute_method_schedule(
            method="M*",
            run_id="bs-canary-20260816-001-mstar",
            episodes=_episodes(2),
            native_add_episode=add_episode,
            persist_event=persist,
        )

    assert called is False

