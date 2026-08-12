"""Focused contracts for the frozen C4/E3 scheduling and analysis core."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c4 as c4  # noqa: E402


class FakeClock:
    """Monotonic virtual clock that records every absolute wake-up."""

    def __init__(self, initial_ns: int = 0) -> None:
        self.current_ns = initial_ns
        self.wakeups: list[int] = []

    def now_ns(self) -> int:
        return self.current_ns

    def sleep_until_ns(self, timestamp_ns: int) -> None:
        if timestamp_ns < self.current_ns:
            raise AssertionError("clock moved backwards")
        self.current_ns = timestamp_ns
        self.wakeups.append(timestamp_ns)


class FakeU0:
    """Deterministic duration/error provider for the shared native service path."""

    def __init__(self, durations_ns: dict[int, int], fail_at: int | None = None) -> None:
        self.durations_ns = durations_ns
        self.fail_at = fail_at
        self.calls: list[tuple[int, int]] = []

    def __call__(self, episode: c4.Episode, service_start_ns: int) -> int:
        self.calls.append((episode.source_sequence, service_start_ns))
        if episode.source_sequence == self.fail_at:
            raise RuntimeError("synthetic U0 failure")
        return self.durations_ns[episode.source_sequence]


class FakeDurableWriter:
    """In-memory durable boundary with an injectable enqueue acknowledgement delay."""

    def __init__(self, enqueue_ack_delay_ns: int = 0) -> None:
        self.enqueue_ack_delay_ns = enqueue_ack_delay_ns
        self.enqueues: list[tuple[int, int, int]] = []
        self.publications: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []

    def persist_enqueue(self, episode: c4.Episode, arrival_timestamp_ns: int) -> int:
        ack = arrival_timestamp_ns + self.enqueue_ack_delay_ns
        self.enqueues.append((episode.source_sequence, arrival_timestamp_ns, ack))
        return ack

    def persist_publication(self, record: dict[str, object]) -> None:
        self.publications.append(dict(record))

    def persist_failure(self, checkpoint: dict[str, object]) -> None:
        self.failures.append(dict(checkpoint))


def episodes(count: int) -> list[c4.Episode]:
    return [c4.Episode(source_sequence=index, payload={"index": index}) for index in range(count)]


class NativeCharacterizationC4Tests(TestCase):
    maxDiff = None

    def test_method_ids_match_the_frozen_e3_artifact(self) -> None:
        self.assertEqual(c4.NATIVE_SYNC, "Native-Sync")
        self.assertEqual(c4.NATIVE_ASYNC_SERIAL, "Native-Async-Serial")

    def test_absolute_arrival_builder_has_no_cumulative_drift(self) -> None:
        self.assertEqual(
            c4.build_absolute_arrivals(start_ns=17, interarrival_ns=10, count=4),
            [17, 27, 37, 47],
        )
        with self.assertRaises(c4.NativeCharacterizationC4Error):
            c4.build_absolute_arrivals(start_ns=0, interarrival_ns=0, count=2)

    def test_both_treatments_share_fifo_single_worker_and_absolute_arrivals(self) -> None:
        arrivals = [100, 110, 120]
        expected_starts = [100, 130, 140]
        expected_publishes = [130, 140, 145]
        results: dict[str, dict[str, object]] = {}

        for method in (c4.NATIVE_SYNC, c4.NATIVE_ASYNC_SERIAL):
            clock = FakeClock()
            u0 = FakeU0({0: 30, 1: 10, 2: 5})
            writer = FakeDurableWriter()
            result = c4.run_replay(method, episodes(3), arrivals, clock, u0, writer)
            results[method] = result

            self.assertEqual(u0.calls, list(zip(range(3), expected_starts)))
            expected_enqueues = (
                [] if method == c4.NATIVE_SYNC else [0, 1, 2]
            )
            self.assertEqual([item[0] for item in writer.enqueues], expected_enqueues)
            self.assertEqual(
                [item["source_sequence"] for item in writer.publications],
                [0, 1, 2],
            )
            self.assertEqual(
                [item["service_start_timestamp_ns"] for item in result["records"]],
                expected_starts,
            )
            self.assertEqual(
                [item["publish_timestamp_ns"] for item in result["records"]],
                expected_publishes,
            )

        sync_records = results[c4.NATIVE_SYNC]["records"]
        async_records = results[c4.NATIVE_ASYNC_SERIAL]["records"]
        self.assertEqual(
            [item["caller_return_timestamp_ns"] for item in sync_records],
            expected_publishes,
        )
        self.assertEqual(
            [item["caller_return_timestamp_ns"] for item in async_records],
            arrivals,
        )

    def test_async_return_is_exact_durable_ack_and_worker_waits_for_ack(self) -> None:
        clock = FakeClock()
        writer = FakeDurableWriter(enqueue_ack_delay_ns=2)
        result = c4.run_replay(
            c4.NATIVE_ASYNC_SERIAL,
            episodes(2),
            [100, 110],
            clock,
            FakeU0({0: 10, 1: 3}),
            writer,
        )

        self.assertEqual(
            [item["enqueue_ack_timestamp_ns"] for item in result["records"]],
            [102, 112],
        )
        self.assertEqual(
            [item["caller_return_timestamp_ns"] for item in result["records"]],
            [102, 112],
        )
        self.assertEqual(
            [item["service_start_timestamp_ns"] for item in result["records"]],
            [102, 112],
        )

    def test_timestamp_metrics_are_signed_clamped_and_boundary_exact(self) -> None:
        async_result = c4.run_replay(
            c4.NATIVE_ASYNC_SERIAL,
            episodes(3),
            [100, 110, 120],
            FakeClock(),
            FakeU0({0: 30, 1: 10, 2: 5}),
            FakeDurableWriter(),
        )
        async_metrics = async_result["episode_metrics"]

        self.assertEqual([item["caller_return_latency_ns"] for item in async_metrics], [0, 0, 0])
        self.assertEqual([item["construction_service_time_ns"] for item in async_metrics], [30, 10, 5])
        self.assertEqual([item["queue_wait_ns"] for item in async_metrics], [0, 20, 20])
        self.assertEqual([item["arrival_to_visible_ns"] for item in async_metrics], [30, 30, 25])
        self.assertEqual([item["signed_publish_after_return_ns"] for item in async_metrics], [30, 30, 25])
        self.assertEqual([item["post_return_stale_window_ns"] for item in async_metrics], [30, 30, 25])

        sync_result = c4.run_replay(
            c4.NATIVE_SYNC,
            episodes(3),
            [100, 110, 120],
            FakeClock(),
            FakeU0({0: 30, 1: 10, 2: 5}),
            FakeDurableWriter(),
        )
        sync_metrics = sync_result["episode_metrics"]
        self.assertEqual([item["caller_return_latency_ns"] for item in sync_metrics], [30, 30, 25])
        self.assertEqual([item["signed_publish_after_return_ns"] for item in sync_metrics], [0, 0, 0])
        self.assertEqual([item["post_return_stale_window_ns"] for item in sync_metrics], [0, 0, 0])

        early_return = dict(sync_result["records"][0], caller_return_timestamp_ns=131)
        metric = c4.compute_episode_metrics(early_return)
        self.assertEqual(metric["signed_publish_after_return_ns"], -1)
        self.assertEqual(metric["post_return_stale_window_ns"], 0)

    def test_backlog_series_auc_final_arrival_drain_and_throughput(self) -> None:
        result = c4.run_replay(
            c4.NATIVE_ASYNC_SERIAL,
            episodes(3),
            [100, 110, 120],
            FakeClock(),
            FakeU0({0: 30, 1: 10, 2: 5}),
            FakeDurableWriter(),
        )
        aggregate = result["aggregate"]

        self.assertEqual(
            aggregate["backlog_time_series"],
            [
                {"timestamp_ns": 100, "backlog": 1},
                {"timestamp_ns": 110, "backlog": 2},
                {"timestamp_ns": 120, "backlog": 3},
                {"timestamp_ns": 130, "backlog": 2},
                {"timestamp_ns": 140, "backlog": 1},
                {"timestamp_ns": 145, "backlog": 0},
            ],
        )
        self.assertEqual(aggregate["backlog_auc_episode_ns"], 85)
        self.assertEqual(aggregate["maximum_backlog"], 3)
        self.assertEqual(aggregate["backlog_at_final_arrival"], 3)
        self.assertEqual(aggregate["drain_time_ns"], 25)
        self.assertAlmostEqual(aggregate["throughput_episodes_per_second"], 3 * 1e9 / 45)
        self.assertEqual(aggregate["final_backlog"], 0)

    def test_backlog_ties_apply_arrival_before_publication(self) -> None:
        records = [
            {"publish_timestamp_ns": 10},
            {"publish_timestamp_ns": 20},
        ]

        aggregate = c4.analyze_backlog([0, 10], records)

        self.assertEqual(aggregate["backlog_auc_episode_ns"], 20)
        self.assertEqual(aggregate["maximum_backlog"], 2)
        self.assertEqual(aggregate["backlog_at_final_arrival"], 2)
        self.assertEqual(aggregate["final_backlog"], 0)
        self.assertEqual(
            aggregate["backlog_time_series"],
            [
                {"timestamp_ns": 0, "backlog": 1},
                {"timestamp_ns": 10, "backlog": 2},
                {"timestamp_ns": 10, "backlog": 1},
                {"timestamp_ns": 20, "backlog": 0},
            ],
        )

    def test_exactly_once_validator_rejects_loss_duplicate_and_reordering(self) -> None:
        requested = episodes(3)
        records = [
            {"source_sequence": 0},
            {"source_sequence": 1},
            {"source_sequence": 2},
        ]
        self.assertEqual(
            c4.validate_exactly_once(requested, records),
            {"requested": 3, "published": 3, "loss_count": 0, "duplicate_count": 0},
        )
        for invalid in (records[:-1], records + [records[-1]], [records[1], records[0], records[2]]):
            with self.assertRaises(c4.NativeCharacterizationC4Error):
                c4.validate_exactly_once(requested, invalid)

    def test_failure_is_checkpointed_once_and_stops_without_later_service(self) -> None:
        clock = FakeClock()
        u0 = FakeU0({0: 10, 1: 1, 2: 1}, fail_at=1)
        writer = FakeDurableWriter()
        result = c4.run_replay(
            c4.NATIVE_ASYNC_SERIAL,
            episodes(3),
            [0, 5, 10],
            clock,
            u0,
            writer,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(u0.calls, [(0, 0), (1, 10)])
        self.assertEqual([item["source_sequence"] for item in result["records"]], [0])
        self.assertEqual(len(writer.failures), 1)
        checkpoint = writer.failures[0]
        self.assertEqual(checkpoint["failure_timestamp_ns"], 10)
        self.assertEqual(checkpoint["failed_source_sequence"], 1)
        self.assertEqual(checkpoint["error_class"], "RuntimeError")
        self.assertEqual(checkpoint["completed_source_sequences"], [0])
        self.assertEqual(checkpoint["durably_enqueued_source_sequences"], [0, 1, 2])
        self.assertEqual(checkpoint["pending_source_sequences"], [1, 2])
        self.assertEqual(result["failure_checkpoint"], checkpoint)

    def test_invalid_method_schedule_and_timestamp_envelopes_fail_closed(self) -> None:
        with self.assertRaises(c4.NativeCharacterizationC4Error):
            c4.run_replay(
                "unknown",
                episodes(1),
                [0],
                FakeClock(),
                FakeU0({0: 1}),
                FakeDurableWriter(),
            )
        with self.assertRaises(c4.NativeCharacterizationC4Error):
            c4.run_replay(
                c4.NATIVE_SYNC,
                episodes(2),
                [10, 9],
                FakeClock(),
                FakeU0({0: 1, 1: 1}),
                FakeDurableWriter(),
            )
        with self.assertRaises(c4.NativeCharacterizationC4Error):
            c4.compute_episode_metrics(
                {
                    "arrival_timestamp_ns": 10,
                    "enqueue_ack_timestamp_ns": 9,
                    "service_start_timestamp_ns": 10,
                    "publish_timestamp_ns": 11,
                    "caller_return_timestamp_ns": 11,
                }
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
