from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from live_runtime import (  # noqa: E402
    GraphNotEmptyError,
    clear_database,
    close,
    count_nodes,
    prepare_clean_graph,
)


class FakeGraphOps:
    def __init__(self, events: list[str], *, clear_succeeds: bool = True):
        self.events = events
        self.clear_succeeds = clear_succeeds
        self.received_driver = None

    async def clear_data(self, driver) -> None:
        self.events.append("clear")
        self.received_driver = driver
        if self.clear_succeeds:
            driver.node_count = 0


class FakeDriver:
    def __init__(self, events: list[str], *, node_count: int = 3, clear_succeeds: bool = True):
        self.events = events
        self.node_count = node_count
        self.graph_ops = FakeGraphOps(events, clear_succeeds=clear_succeeds)

    async def execute_query(self, query: str):
        if "count(n)" not in query:
            raise AssertionError(f"unexpected query: {query}")
        self.events.append("verify_empty")
        return ([{"node_count": self.node_count}], object(), ["node_count"])


class FakeEagerResult:
    def __init__(self, node_count: int):
        self.records = [{"node_count": node_count}]


class FakeGraphiti:
    def __init__(self, *, node_count: int = 3, clear_succeeds: bool = True):
        self.events: list[str] = []
        self.driver = FakeDriver(
            self.events,
            node_count=node_count,
            clear_succeeds=clear_succeeds,
        )

    async def build_indices_and_constraints(self) -> None:
        self.events.append("build_indexes")

    async def close(self) -> None:
        self.events.append("close")


class LiveRuntimeTests(IsolatedAsyncioTestCase):
    async def test_clear_database_uses_graph_ops_with_the_driver(self) -> None:
        graphiti = FakeGraphiti()

        await clear_database(graphiti)

        self.assertEqual(graphiti.events, ["clear"])
        self.assertIs(graphiti.driver.graph_ops.received_driver, graphiti.driver)

    async def test_count_nodes_reads_graphiti_driver_eager_result(self) -> None:
        graphiti = FakeGraphiti(node_count=7)

        self.assertEqual(await count_nodes(graphiti), 7)
        self.assertEqual(graphiti.events, ["verify_empty"])

    async def test_count_nodes_accepts_records_attribute(self) -> None:
        graphiti = FakeGraphiti()

        async def eager_query(_query: str) -> FakeEagerResult:
            return FakeEagerResult(9)

        graphiti.driver.execute_query = eager_query
        self.assertEqual(await count_nodes(graphiti), 9)

    async def test_prepare_clean_graph_uses_strict_lifecycle_order(self) -> None:
        graphiti = FakeGraphiti()

        async def warm_up(instance: FakeGraphiti) -> None:
            instance.events.append("warm_up")
            instance.driver.node_count = 2

        await prepare_clean_graph(graphiti, warm_up)

        self.assertEqual(
            graphiti.events,
            [
                "clear",
                "build_indexes",
                "verify_empty",
                "warm_up",
                "clear",
                "verify_empty",
            ],
        )

    async def test_prepare_awaits_graphiti_auto_index_task_without_duplicate_build(self) -> None:
        graphiti = FakeGraphiti()

        async def auto_build() -> None:
            graphiti.events.append("await_auto_indexes")

        graphiti.driver._init_task = asyncio.create_task(auto_build())

        async def warm_up(instance: FakeGraphiti) -> None:
            instance.events.append("warm_up")

        await prepare_clean_graph(graphiti, warm_up)

        self.assertEqual(
            graphiti.events,
            [
                "clear",
                "await_auto_indexes",
                "verify_empty",
                "warm_up",
                "clear",
                "verify_empty",
            ],
        )

    async def test_prepare_clean_graph_stops_if_initial_clear_is_incomplete(self) -> None:
        graphiti = FakeGraphiti(clear_succeeds=False)
        warm_up_called = False

        async def warm_up(_instance: FakeGraphiti) -> None:
            nonlocal warm_up_called
            warm_up_called = True

        with self.assertRaisesRegex(GraphNotEmptyError, "after initial clear"):
            await prepare_clean_graph(graphiti, warm_up)

        self.assertFalse(warm_up_called)
        self.assertEqual(graphiti.events, ["clear", "build_indexes", "verify_empty"])

    async def test_warm_up_failure_still_clears_and_caller_can_close(self) -> None:
        graphiti = FakeGraphiti()

        async def failing_warm_up(instance: FakeGraphiti) -> None:
            instance.events.append("warm_up")
            instance.driver.node_count = 1
            raise RuntimeError("warm-up failed")

        try:
            with self.assertRaisesRegex(RuntimeError, "warm-up failed"):
                await prepare_clean_graph(graphiti, failing_warm_up)
        finally:
            await close(graphiti)

        self.assertEqual(
            graphiti.events,
            [
                "clear",
                "build_indexes",
                "verify_empty",
                "warm_up",
                "clear",
                "verify_empty",
                "close",
            ],
        )

    async def test_close_accepts_none_for_partially_constructed_runtime(self) -> None:
        await close(None)

    async def test_close_falls_back_to_async_driver_close(self) -> None:
        events: list[str] = []

        class Driver:
            async def close(self) -> None:
                events.append("driver_close")

        class Runtime:
            driver = Driver()

        await close(Runtime())
        self.assertEqual(events, ["driver_close"])


if __name__ == "__main__":
    import unittest

    unittest.main()
