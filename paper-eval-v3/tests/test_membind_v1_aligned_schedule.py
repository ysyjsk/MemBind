"""TDD contracts for fresh open-loop U0/P(C=2) scheduling.

These schedulers are intentionally independent of Graphiti.  They consume the
same frozen absolute arrival offsets which will later be used by the live
benchmark, while the supplied callback is the only whole-update boundary.
"""

from __future__ import annotations

import asyncio

import pytest

from paper_eval.membind_v1.aligned_schedule import (
    AlignedEpisodeRef,
    AlignedScheduleError,
    run_aligned_baseline,
)


def _episodes(count: int = 4) -> tuple[AlignedEpisodeRef, ...]:
    return tuple(
        AlignedEpisodeRef(
            source_sequence=sequence,
            source_sha256=f"{sequence + 1:064x}",
            native_episode={"private": sequence},
        )
        for sequence in range(count)
    )


def test_u0_uses_frozen_arrival_offsets_but_remains_source_serial() -> None:
    async def scenario() -> tuple[dict[str, object], list[int]]:
        calls: list[int] = []

        async def native(episode: object) -> None:
            calls.append(int(episode["private"]))  # type: ignore[index]
            await asyncio.sleep(0)

        return await run_aligned_baseline(
            method="U0-aligned",
            episodes=_episodes(3),
            arrival_offsets_ns=(0, 0, 0),
            native_add_episode=native,
        ), calls

    result, calls = asyncio.run(scenario())

    assert calls == [0, 1, 2]
    assert result["method"] == "U0-aligned"
    assert result["configured_worker_count"] == 1
    assert result["observed_max_active_updates"] == 1
    assert [row["source_sequence"] for row in result["lifecycle"]] == [0, 1, 2]
    assert all(
        row["arrival_timestamp_ns"] <= row["service_start_timestamp_ns"] <= row["publication_timestamp_ns"]
        for row in result["lifecycle"]
    )


def test_p_c2_uses_no_more_than_two_whole_update_workers_and_records_real_overlap() -> None:
    async def scenario() -> dict[str, object]:
        active = 0
        observed_max = 0
        release = asyncio.Event()
        both_entered = asyncio.Event()

        async def native(_episode: object) -> None:
            nonlocal active, observed_max
            active += 1
            observed_max = max(observed_max, active)
            if active == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            await release.wait()
            active -= 1

        task = asyncio.create_task(
            run_aligned_baseline(
                method="P(C=2)-aligned",
                episodes=_episodes(4),
                arrival_offsets_ns=(0, 0, 0, 0),
                native_add_episode=native,
            )
        )
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        assert observed_max == 2
        release.set()
        return await task

    result = asyncio.run(scenario())

    assert result["configured_worker_count"] == 2
    assert result["observed_max_active_updates"] == 2
    assert result["whole_update_interval_overlap_observed"] is True
    assert sorted(row["source_sequence"] for row in result["lifecycle"]) == [0, 1, 2, 3]


@pytest.mark.parametrize(
    "offsets, expected",
    [
        ((0, 1), "arrival offset count"),
        ((0, 2, 1), "arrival offsets"),
    ],
)
def test_scheduler_rejects_bad_trace_before_the_native_callback(offsets, expected) -> None:
    called = False

    async def native(_episode: object) -> None:
        nonlocal called
        called = True

    with pytest.raises(AlignedScheduleError, match=expected):
        asyncio.run(
            run_aligned_baseline(
                method="U0-aligned",
                episodes=_episodes(3),
                arrival_offsets_ns=offsets,
                native_add_episode=native,
            )
        )
    assert called is False


def test_scheduler_never_leaks_the_opaque_episode_into_public_lifecycle_rows() -> None:
    async def native(_episode: object) -> None:
        return None

    result = asyncio.run(
        run_aligned_baseline(
            method="U0-aligned",
            episodes=_episodes(1),
            arrival_offsets_ns=(0,),
            native_add_episode=native,
        )
    )

    assert "private" not in repr(result["lifecycle"])
    assert set(result["lifecycle"][0]) == {
        "source_sequence",
        "source_sha256",
        "arrival_timestamp_ns",
        "enqueue_timestamp_ns",
        "service_start_timestamp_ns",
        "publication_timestamp_ns",
        "terminal_timestamp_ns",
        "worker_id",
    }


def test_scheduler_emits_each_durable_lifecycle_boundary_in_source_safe_form() -> None:
    async def scenario() -> list[tuple[str, int, int]]:
        observed: list[tuple[str, int, int]] = []

        async def native(_episode: object) -> None:
            return None

        async def observer(event_type: str, source_sequence: int, timestamp_ns: int) -> None:
            observed.append((event_type, source_sequence, timestamp_ns))

        await run_aligned_baseline(
            method="U0-aligned",
            episodes=_episodes(2),
            arrival_offsets_ns=(0, 0),
            native_add_episode=native,
            lifecycle_observer=observer,
        )
        return observed

    observed = asyncio.run(scenario())

    assert [event_type for event_type, _sequence, _timestamp in observed] == [
        "ARRIVAL",
        "ENQUEUED",
        "SERVICE_STARTED",
        "PUBLICATION_DURABLE",
        "ARRIVAL",
        "ENQUEUED",
        "SERVICE_STARTED",
        "PUBLICATION_DURABLE",
    ]
    assert [sequence for _event_type, sequence, _timestamp in observed] == [0, 0, 0, 0, 1, 1, 1, 1]
    by_source: dict[int, list[int]] = {}
    for _event_type, source_sequence, timestamp_ns in observed:
        by_source.setdefault(source_sequence, []).append(timestamp_ns)
    assert all(
        timestamps == sorted(timestamps) for timestamps in by_source.values()
    )


def test_p_c2_cancels_and_awaits_running_sibling_before_propagating_failure() -> None:
    async def scenario() -> None:
        sibling_entered = asyncio.Event()
        sibling_cancelled = asyncio.Event()
        sibling_unwound = asyncio.Event()
        release = asyncio.Event()
        observed: list[tuple[str, int, int]] = []

        async def native(episode: object) -> None:
            sequence = int(episode["private"])  # type: ignore[index]
            if sequence == 0:
                sibling_entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    sibling_cancelled.set()
                    # A cancellation handler may itself yield.  The scheduler
                    # must await it before returning the sibling's failure.
                    await asyncio.sleep(0)
                    raise
                finally:
                    sibling_unwound.set()
                return
            await sibling_entered.wait()
            raise RuntimeError("injected native failure")

        async def observer(event_type: str, source_sequence: int, timestamp_ns: int) -> None:
            observed.append((event_type, source_sequence, timestamp_ns))

        try:
            with pytest.raises(RuntimeError, match="injected native failure"):
                await run_aligned_baseline(
                    method="P(C=2)-aligned",
                    episodes=_episodes(2),
                    arrival_offsets_ns=(0, 0),
                    native_add_episode=native,
                    lifecycle_observer=observer,
                )

            assert sibling_cancelled.is_set()
            assert sibling_unwound.is_set()
            events_after_failure = list(observed)
            await asyncio.sleep(0)
            assert observed == events_after_failure
        finally:
            # Keep the RED implementation from leaking its un-cancelled
            # sibling into the next test, while remaining inert after GREEN.
            release.set()
            await asyncio.wait_for(sibling_unwound.wait(), timeout=1)
            await asyncio.sleep(0)

    asyncio.run(scenario())
