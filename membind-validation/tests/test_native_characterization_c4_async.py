"""Async execution contracts for the frozen C4/E3 treatments.

These tests use a manually advanced monotonic clock.  They prove concurrency
semantics without sleeping in wall-clock time or touching a live dependency.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c4 as c4  # noqa: E402
import native_characterization_c4_async as c4_async  # noqa: E402


class ManualAsyncClock:
    """Controllable async clock whose sleepers wake at absolute timestamps."""

    def __init__(self, initial_ns: int = 0) -> None:
        self.current_ns = initial_ns
        self._waiters: list[tuple[int, asyncio.Future[None]]] = []
        self.cancelled_sleeps = 0

    def now_ns(self) -> int:
        return self.current_ns

    async def sleep_until_ns(self, timestamp_ns: int) -> None:
        if timestamp_ns <= self.current_ns:
            return
        future = asyncio.get_running_loop().create_future()
        item = (timestamp_ns, future)
        self._waiters.append(item)
        try:
            await future
        except asyncio.CancelledError:
            self.cancelled_sleeps += 1
            raise
        finally:
            if item in self._waiters:
                self._waiters.remove(item)

    def advance_to(self, timestamp_ns: int) -> None:
        if timestamp_ns < self.current_ns:
            raise AssertionError("manual clock moved backwards")
        self.current_ns = timestamp_ns
        for deadline, future in list(self._waiters):
            if deadline <= timestamp_ns and not future.done():
                future.set_result(None)

    @property
    def pending_deadlines(self) -> list[int]:
        return sorted(deadline for deadline, future in self._waiters if not future.done())


async def settle(rounds: int = 8) -> None:
    """Let all tasks made runnable at the current virtual time reach an await."""

    for _ in range(rounds):
        await asyncio.sleep(0)


class FakeAsyncService:
    def __init__(
        self,
        clock: ManualAsyncClock,
        durations_ns: dict[int, int],
        *,
        fail_at: int | None = None,
    ) -> None:
        self.clock = clock
        self.durations_ns = durations_ns
        self.fail_at = fail_at
        self.calls: list[tuple[int, int]] = []

    async def __call__(self, episode: c4.Episode, service_start_ns: int) -> None:
        self.calls.append((episode.source_sequence, service_start_ns))
        if episode.source_sequence == self.fail_at:
            raise RuntimeError("synthetic async U0 failure")
        await self.clock.sleep_until_ns(
            service_start_ns + self.durations_ns[episode.source_sequence]
        )


class FakeAsyncDurableWriter:
    def __init__(
        self,
        clock: ManualAsyncClock,
        *,
        enqueue_delays_ns: dict[int, int] | None = None,
    ) -> None:
        self.clock = clock
        self.enqueue_delays_ns = enqueue_delays_ns or {}
        self.enqueue_calls: list[tuple[int, int]] = []
        self.enqueue_acks: list[tuple[int, int]] = []
        self.publications: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []

    async def persist_enqueue(
        self, episode: c4.Episode, actual_arrival_timestamp_ns: int
    ) -> None:
        self.enqueue_calls.append((episode.source_sequence, actual_arrival_timestamp_ns))
        await self.clock.sleep_until_ns(
            actual_arrival_timestamp_ns
            + self.enqueue_delays_ns.get(episode.source_sequence, 0)
        )
        self.enqueue_acks.append((episode.source_sequence, self.clock.now_ns()))

    async def persist_publication(self, record: dict[str, object]) -> None:
        self.publications.append(dict(record))

    async def persist_failure(self, checkpoint: dict[str, object]) -> None:
        self.failures.append(dict(checkpoint))


def episodes(count: int) -> list[c4.Episode]:
    return [c4.Episode(source_sequence=index, payload={"index": index}) for index in range(count)]


class NativeCharacterizationC4AsyncTests(IsolatedAsyncioTestCase):
    maxDiff = None

    async def test_producer_keeps_absolute_arrivals_while_service_is_awaited(self) -> None:
        for method in (c4.NATIVE_SYNC, c4.NATIVE_ASYNC_SERIAL):
            with self.subTest(method=method):
                clock = ManualAsyncClock()
                service = FakeAsyncService(clock, {0: 30, 1: 10, 2: 5})
                writer = FakeAsyncDurableWriter(clock)
                replay = asyncio.create_task(
                    c4_async.run_async_replay(
                        method, episodes(3), [0, 10, 20], clock, service, writer
                    )
                )

                await settle()
                self.assertEqual(service.calls, [(0, 0)])
                for timestamp in (10, 20):
                    clock.advance_to(timestamp)
                    await settle()

                # Episode zero is still in service, yet all producer arrivals
                # have occurred.  Async also has all three durable queue acks.
                self.assertEqual(service.calls, [(0, 0)])
                if method == c4.NATIVE_ASYNC_SERIAL:
                    self.assertEqual([item[0] for item in writer.enqueue_acks], [0, 1, 2])

                for timestamp in (30, 40, 45):
                    clock.advance_to(timestamp)
                    await settle()
                result = await replay

                self.assertEqual(result["status"], "complete")
                self.assertEqual(service.calls, [(0, 0), (1, 30), (2, 40)])
                self.assertEqual(
                    [item["planned_arrival_timestamp_ns"] for item in result["records"]],
                    [0, 10, 20],
                )
                self.assertEqual(
                    [item["actual_arrival_timestamp_ns"] for item in result["records"]],
                    [0, 10, 20],
                )
                self.assertEqual(
                    [item["schedule_lag_ns"] for item in result["records"]], [0, 0, 0]
                )
                self.assertEqual(
                    [item["publish_timestamp_ns"] for item in result["records"]],
                    [30, 40, 45],
                )
                expected_returns = (
                    [30, 40, 45]
                    if method == c4.NATIVE_SYNC
                    else [0, 10, 20]
                )
                self.assertEqual(
                    [item["caller_return_timestamp_ns"] for item in result["records"]],
                    expected_returns,
                )
                self.assertEqual(
                    [item["source_sequence"] for item in writer.publications], [0, 1, 2]
                )

    async def test_async_caller_returns_only_after_append_fsync_ack(self) -> None:
        clock = ManualAsyncClock()
        service = FakeAsyncService(clock, {0: 10, 1: 2})
        writer = FakeAsyncDurableWriter(clock, enqueue_delays_ns={0: 3, 1: 3})
        replay = asyncio.create_task(
            c4_async.run_async_replay(
                c4.NATIVE_ASYNC_SERIAL,
                episodes(2),
                [0, 5],
                clock,
                service,
                writer,
            )
        )

        await settle()
        self.assertEqual(service.calls, [])
        for timestamp in (3, 5, 8, 13, 15):
            clock.advance_to(timestamp)
            await settle()
        result = await asyncio.wait_for(replay, timeout=0.1)

        self.assertEqual(writer.enqueue_acks, [(0, 3), (1, 8)])
        self.assertEqual(service.calls, [(0, 3), (1, 13)])
        self.assertEqual(
            [item["enqueue_ack_timestamp_ns"] for item in result["records"]], [3, 8]
        )
        self.assertEqual(
            [item["caller_return_timestamp_ns"] for item in result["records"]], [3, 8]
        )
        self.assertEqual(
            [item["queue_wait_ns"] for item in result["episode_metrics"]], [0, 5]
        )

    async def test_slow_durable_ack_never_shifts_absolute_arrival_observation(self) -> None:
        clock = ManualAsyncClock()
        service = FakeAsyncService(clock, {0: 1, 1: 1, 2: 1})
        writer = FakeAsyncDurableWriter(
            clock,
            enqueue_delays_ns={0: 20, 1: 20, 2: 20},
        )
        replay = asyncio.create_task(
            c4_async.run_async_replay(
                c4.NATIVE_ASYNC_SERIAL,
                episodes(3),
                [0, 5, 10],
                clock,
                service,
                writer,
            )
        )

        await settle()
        for timestamp in (5, 10, 20, 21, 25, 26, 30, 31):
            clock.advance_to(timestamp)
            await settle()
        result = await asyncio.wait_for(replay, timeout=0.1)

        self.assertEqual(
            [item["actual_arrival_timestamp_ns"] for item in result["records"]],
            [0, 5, 10],
        )
        self.assertEqual(
            [item["schedule_lag_ns"] for item in result["records"]],
            [0, 0, 0],
        )
        self.assertEqual(writer.enqueue_acks, [(0, 20), (1, 25), (2, 30)])
        self.assertEqual(
            [item["caller_return_timestamp_ns"] for item in result["records"]],
            [20, 25, 30],
        )

    async def test_sync_uses_memory_admission_and_returns_only_at_publish(self) -> None:
        clock = ManualAsyncClock()
        service = FakeAsyncService(clock, {0: 7})
        writer = FakeAsyncDurableWriter(clock, enqueue_delays_ns={0: 100})
        replay = asyncio.create_task(
            c4_async.run_async_replay(
                c4.NATIVE_SYNC, episodes(1), [0], clock, service, writer
            )
        )
        await settle()
        self.assertEqual(writer.enqueue_calls, [])
        clock.advance_to(7)
        await settle()
        result = await replay

        record = result["records"][0]
        self.assertEqual(record["enqueue_ack_timestamp_ns"], 0)
        self.assertEqual(record["caller_return_timestamp_ns"], 7)
        self.assertEqual(record["publish_timestamp_ns"], 7)
        self.assertEqual(result["episode_metrics"][0], c4.compute_episode_metrics(record))

    async def test_late_wakeup_records_actual_arrival_and_schedule_lag(self) -> None:
        clock = ManualAsyncClock()
        service = FakeAsyncService(clock, {0: 1, 1: 1})
        writer = FakeAsyncDurableWriter(clock)
        replay = asyncio.create_task(
            c4_async.run_async_replay(
                c4.NATIVE_ASYNC_SERIAL, episodes(2), [10, 20], clock, service, writer
            )
        )
        await settle()
        clock.advance_to(15)
        await settle()
        clock.advance_to(16)
        await settle()
        clock.advance_to(25)
        await settle()
        clock.advance_to(26)
        await settle()
        result = await replay

        self.assertEqual(
            [item["actual_arrival_timestamp_ns"] for item in result["records"]], [15, 25]
        )
        self.assertEqual([item["schedule_lag_ns"] for item in result["records"]], [5, 5])
        self.assertEqual(
            [item["arrival_timestamp_ns"] for item in result["records"]], [15, 25]
        )

    async def test_service_failure_cancels_producer_checkpoints_and_stops(self) -> None:
        clock = ManualAsyncClock()
        service = FakeAsyncService(clock, {0: 1, 1: 1, 2: 1}, fail_at=0)
        writer = FakeAsyncDurableWriter(clock)
        replay = asyncio.create_task(
            c4_async.run_async_replay(
                c4.NATIVE_ASYNC_SERIAL,
                episodes(3),
                [0, 100, 200],
                clock,
                service,
                writer,
            )
        )
        await settle()
        result = await replay

        self.assertEqual(result["status"], "failed")
        self.assertEqual(service.calls, [(0, 0)])
        self.assertEqual(writer.publications, [])
        self.assertEqual(len(writer.failures), 1)
        checkpoint = writer.failures[0]
        self.assertEqual(checkpoint["failure_stage"], "service")
        self.assertEqual(checkpoint["failed_source_sequence"], 0)
        self.assertEqual(checkpoint["error_class"], "RuntimeError")
        self.assertEqual(checkpoint["admitted_source_sequences"], [0])
        self.assertEqual(checkpoint["durably_enqueued_source_sequences"], [0])
        self.assertEqual(checkpoint["pending_source_sequences"], [0])
        self.assertEqual(checkpoint["not_yet_arrived_source_sequences"], [1, 2])
        self.assertEqual(result["failure_checkpoint"], checkpoint)
        self.assertGreaterEqual(clock.cancelled_sleeps, 1)
        self.assertEqual(clock.pending_deadlines, [])

    async def test_failure_after_backlog_never_starts_a_later_service(self) -> None:
        clock = ManualAsyncClock()
        service = FakeAsyncService(clock, {0: 20, 1: 1, 2: 1}, fail_at=1)
        writer = FakeAsyncDurableWriter(clock)
        replay = asyncio.create_task(
            c4_async.run_async_replay(
                c4.NATIVE_ASYNC_SERIAL, episodes(3), [0, 5, 10], clock, service, writer
            )
        )
        await settle()
        for timestamp in (5, 10, 20):
            clock.advance_to(timestamp)
            await settle()
        result = await replay

        self.assertEqual(service.calls, [(0, 0), (1, 20)])
        self.assertEqual([item["source_sequence"] for item in result["records"]], [0])
        self.assertEqual(len(writer.failures), 1)
        self.assertEqual(
            writer.failures[0]["pending_source_sequences"], [1, 2]
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
