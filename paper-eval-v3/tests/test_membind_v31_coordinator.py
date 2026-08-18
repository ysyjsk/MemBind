"""Offline event-driven coordinator tests for the v3.1 semantic path."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from paper_eval.membind_v1.source_log import SourceLog, SourceRecord
from paper_eval.membind_v31.coordinator import (
    MemBindV31CoordinatorError,
    run_membind_v31_stream,
)


def _source(sequence: int) -> SourceRecord:
    return SourceRecord.create(
        source_sequence=sequence,
        episode_uuid=f"episode-{sequence}",
        group_id="history-a",
        reference_time_ns=100 + sequence,
        source_filter="message",
        episode_projection={"name": f"episode-{sequence}", "body": f"private-{sequence}"},
    )


class _RequestClient:
    def __init__(self) -> None:
        self.regions: list[tuple[str, int]] = []

    @asynccontextmanager
    async def frontier_bind_region(self, stream_id: str, source_sequence: int):
        self.regions.append((stream_id, source_sequence))
        yield

    def observation(self) -> dict[str, int]:
        return {"active_count": 0, "waiting_count": 0}


class _OutOfOrderAdapter:
    def __init__(self) -> None:
        self.release = {sequence: asyncio.Event() for sequence in range(3)}
        self.compile_started: list[int] = []
        self.compile_finished: list[int] = []
        self.bind_order: list[int] = []
        self.evidence: dict[int, tuple[int, ...]] = {}

    async def prepare(self, compile_input):
        sequence = compile_input.source.source_sequence
        self.compile_started.append(sequence)
        self.evidence[sequence] = compile_input.evidence.evidence_source_sequences
        await self.release[sequence].wait()
        self.compile_finished.append(sequence)
        return {"sequence": sequence}

    async def bind(self, compile_input, artifact, *, logical_time_ns: int):
        sequence = compile_input.source.source_sequence
        assert artifact == {"sequence": sequence}
        assert logical_time_ns >= 0
        self.bind_order.append(sequence)
        return {"bound": sequence}


def test_out_of_order_compile_enters_rob_but_bind_and_publish_remain_source_ordered() -> None:
    async def scenario() -> None:
        adapter = _OutOfOrderAdapter()
        client = _RequestClient()
        events: list[dict[str, object]] = []
        publications: list[int] = []
        task = asyncio.create_task(
            run_membind_v31_stream(
                stream_id="history-a",
                source_log=SourceLog.create([_source(0), _source(1), _source(2)]),
                arrival_offsets_ns=(0, 0, 0),
                adapter=adapter,
                request_client=client,
                compile_workers=3,
                lookahead=2,
                observer=events.append,
                publication_probe=lambda sequence, _result: publications.append(sequence) or True,
            )
        )
        for _ in range(10):
            await asyncio.sleep(0)
            if len(adapter.compile_started) == 3:
                break
        assert sorted(adapter.compile_started) == [0, 1, 2]

        adapter.release[2].set()
        adapter.release[1].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert adapter.bind_order == []
        adapter.release[0].set()
        result = await task

        assert adapter.compile_finished[:2] == [2, 1]
        assert adapter.bind_order == [0, 1, 2]
        assert publications == [0, 1, 2]
        assert client.regions == [("history-a", 0), ("history-a", 1), ("history-a", 2)]
        assert adapter.evidence == {0: (), 1: (0,), 2: (0, 1)}
        assert result["publication_source_sequences"] == [0, 1, 2]
        assert result["direct_violation_count"] == 0
        assert result["rob_observation"]["observed_max_active_compiles"] == 3
        assert [
            event["source_sequence"]
            for event in events
            if event["event_type"] == "publication_durable"
        ] == [0, 1, 2]
        assert "private-" not in repr(events)

    asyncio.run(scenario())


def test_compile_failure_stops_stream_without_binding_later_artifacts() -> None:
    class FailingAdapter:
        async def prepare(self, compile_input):
            if compile_input.source.source_sequence == 1:
                raise RuntimeError("private source fragment")
            await asyncio.sleep(0)
            return {"sequence": compile_input.source.source_sequence}

        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            return {"bound": compile_input.source.source_sequence}

    events: list[dict[str, object]] = []
    with pytest.raises(MemBindV31CoordinatorError, match="compile_failed"):
        asyncio.run(
            run_membind_v31_stream(
                stream_id="history-a",
                source_log=SourceLog.create([_source(0), _source(1), _source(2)]),
                arrival_offsets_ns=(0, 0, 0),
                adapter=FailingAdapter(),
                request_client=_RequestClient(),
                compile_workers=2,
                lookahead=2,
                observer=events.append,
                publication_probe=lambda _sequence, _result: True,
            )
        )
    assert "private source fragment" not in repr(events)
    assert any(event["event_type"] == "compile_failure" for event in events)


def test_publication_visibility_failure_is_a_direct_lost_publish_violation() -> None:
    class Adapter:
        async def prepare(self, compile_input):
            return {"sequence": compile_input.source.source_sequence}

        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            return artifact

    with pytest.raises(MemBindV31CoordinatorError, match="publication_visibility_failed"):
        asyncio.run(
            run_membind_v31_stream(
                stream_id="history-a",
                source_log=SourceLog.create([_source(0)]),
                arrival_offsets_ns=(0,),
                adapter=Adapter(),
                request_client=_RequestClient(),
                compile_workers=1,
                lookahead=0,
                observer=lambda _event: None,
                publication_probe=lambda _sequence, _result: False,
            )
        )


def test_scheduler_snapshots_distinguish_ready_reserved_active_and_window_blocked() -> None:
    class BlockingAdapter:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def prepare(self, compile_input):
            await self.release.wait()
            return {"sequence": compile_input.source.source_sequence}

        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            return artifact

    async def run_with_window(lookahead: int) -> list[dict[str, object]]:
        adapter = BlockingAdapter()
        events: list[dict[str, object]] = []
        scheduler_events: list[dict[str, object]] = []
        task = asyncio.create_task(
            run_membind_v31_stream(
                stream_id="history-a",
                source_log=SourceLog.create([_source(index) for index in range(5)]),
                arrival_offsets_ns=(0, 0, 0, 0, 0),
                adapter=adapter,
                request_client=_RequestClient(),
                compile_workers=1,
                lookahead=lookahead,
                observer=events.append,
                scheduler_observer=scheduler_events.append,
                publication_probe=lambda _sequence, _result: True,
            )
        )
        for _ in range(100):
            await asyncio.sleep(0)
            snapshots = list(scheduler_events)
            if any(
                snapshot["reason"] == "ARRIVAL"
                and snapshot["trigger_source_sequence"] == 4
                for snapshot in snapshots
            ) and any(snapshot["reason"] == "DISPATCH" for snapshot in snapshots):
                break
        snapshots = list(scheduler_events)
        assert any(
            snapshot["reason"] == "DISPATCH"
            and snapshot["reserved_compile_count"] == 1
            and snapshot["active_compile_count"] == 0
            and snapshot["compile_slot_occupancy"] == 1
            for snapshot in snapshots
        )
        adapter.release.set()
        await task
        # Include post-release transitions so the fixture observes the
        # reserved -> active ROB boundary as well as the arrival snapshot.
        return list(scheduler_events)

    async def scenario() -> None:
        w2 = await run_with_window(2)
        w4 = await run_with_window(4)

        w2_last_arrival = next(
            snapshot
            for snapshot in w2
            if snapshot["reason"] == "ARRIVAL"
            and snapshot["trigger_source_sequence"] == 4
        )
        w4_last_arrival = next(
            snapshot
            for snapshot in w4
            if snapshot["reason"] == "ARRIVAL"
            and snapshot["trigger_source_sequence"] == 4
        )
        assert w2_last_arrival["arrived_beyond_lookahead_count"] == 2
        # The coordinator's frozen legality rule is
        # ``sequence <= frontier + lookahead``.  With frontier=0 this
        # exposes 0..2 (three candidates), not two total slots.
        assert w2_last_arrival["legal_ready_compile_count"] == 3
        assert w4_last_arrival["arrived_beyond_lookahead_count"] == 0
        assert w4_last_arrival["legal_ready_compile_count"] == 5
        assert any(
            snapshot["active_compile_count"] == 1
            and snapshot["reserved_compile_count"] == 0
            for snapshot in w2
        )

    asyncio.run(scenario())


def test_scheduler_snapshot_marks_bind_dispatched_and_is_monotonic_content_safe() -> None:
    class Adapter:
        async def prepare(self, compile_input):
            return {
                "sequence": compile_input.source.source_sequence,
                "private": "private prepared content",
            }

        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            return {"bound": artifact["sequence"]}

    events: list[dict[str, object]] = []
    scheduler_events: list[dict[str, object]] = []
    asyncio.run(
        run_membind_v31_stream(
            stream_id="history-a",
            source_log=SourceLog.create([_source(0)]),
            arrival_offsets_ns=(0,),
            adapter=Adapter(),
            request_client=_RequestClient(),
            compile_workers=1,
            lookahead=0,
            observer=events.append,
            scheduler_observer=scheduler_events.append,
            publication_probe=lambda _sequence, _result: True,
        )
    )

    snapshots = scheduler_events
    assert any(
        snapshot["frontier_phase"] == "BIND_DISPATCHED"
        and snapshot["bind_task_reserved"] is True
        for snapshot in snapshots
    )
    assert [snapshot["event_sequence"] for snapshot in snapshots] == list(
        range(len(snapshots))
    )
    assert [snapshot["timestamp_ns"] for snapshot in snapshots] == sorted(
        snapshot["timestamp_ns"] for snapshot in snapshots
    )
    assert "private prepared content" not in repr(snapshots)
    assert "private-0" not in repr(snapshots)


def test_scheduler_observer_exposes_ready_pool_and_w2_window_pressure() -> None:
    class BlockingAdapter:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def prepare(self, compile_input):
            await self.release.wait()
            return {"sequence": compile_input.source.source_sequence}

        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            assert artifact["sequence"] == compile_input.source.source_sequence
            return artifact

    async def scenario() -> None:
        adapter = BlockingAdapter()
        scheduler_events: list[dict[str, object]] = []
        task = asyncio.create_task(
            run_membind_v31_stream(
                stream_id="history-a",
                source_log=SourceLog.create([_source(sequence) for sequence in range(5)]),
                arrival_offsets_ns=(0, 0, 0, 0, 0),
                adapter=adapter,
                request_client=_RequestClient(),
                compile_workers=2,
                lookahead=2,
                observer=lambda _event: None,
                scheduler_observer=scheduler_events.append,
                publication_probe=lambda _sequence, _result: True,
            )
        )
        for _ in range(30):
            await asyncio.sleep(0)
            if any(
                event["arrived_beyond_lookahead_count"] >= 2
                for event in scheduler_events
            ):
                break

        pressure = max(
            scheduler_events,
            key=lambda event: int(event["arrived_beyond_lookahead_count"]),
        )
        assert pressure["schema_version"] == (
            "membind.paper-eval-v3.membind-v31-scheduler-state.v1"
        )
        assert pressure["lookahead"] == 2
        assert pressure["frontier_source_sequence"] == 0
        assert pressure["arrived_beyond_lookahead_count"] == 2
        assert pressure["legal_ready_compile_count"] <= 3
        assert pressure["llm_active_count"] == 0
        assert pressure["llm_waiting_count"] == 0

        adapter.release.set()
        result = await task

        assert result["publication_source_sequences"] == [0, 1, 2, 3, 4]
        assert result["scheduler_observation"]["event_count"] == len(scheduler_events)
        assert result["scheduler_observation"]["max_arrived_beyond_lookahead_count"] == 2
        assert result["scheduler_observation"]["max_prepared_rob_occupancy"] <= 3
        assert "private-" not in repr(scheduler_events)

    asyncio.run(scenario())


def test_arrival_task_failure_is_reported_instead_of_deadlocking() -> None:
    class Adapter:
        async def prepare(self, compile_input):
            return {"sequence": compile_input.source.source_sequence}

        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            return artifact

    # Eleven same-timestamp predecessors exceed the default last-N fence and
    # must fail closed; the coordinator must wake its main loop and surface the
    # failure instead of waiting forever on the condition.
    records = [
        SourceRecord.create(
            source_sequence=index,
            episode_uuid=f"episode-{index}",
            group_id="history-a",
            reference_time_ns=100,
            source_filter="message",
            episode_projection={"name": f"episode-{index}", "body": f"private-{index}"},
        )
        for index in range(12)
    ]
    events: list[dict[str, object]] = []
    with pytest.raises(MemBindV31CoordinatorError, match="arrival_failed"):
        asyncio.run(
            asyncio.wait_for(
                run_membind_v31_stream(
                    stream_id="history-a",
                    source_log=SourceLog.create(records),
                    arrival_offsets_ns=(0,) * len(records),
                    adapter=Adapter(),
                    request_client=_RequestClient(),
                    compile_workers=2,
                    lookahead=4,
                    observer=events.append,
                    publication_probe=lambda _sequence, _result: True,
                ),
                timeout=1.0,
            )
        )
    assert any(event["event_type"] == "arrival_failure" for event in events)
