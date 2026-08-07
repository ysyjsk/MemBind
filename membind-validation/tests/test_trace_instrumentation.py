import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from instrumentation import (  # noqa: E402
    apply_episode_metrics,
    current_episode_key,
    episode_scope,
    install_driver_instrumentation,
)
from tracing import EpisodeTrace  # noqa: E402


class TraceInstrumentationTests(TestCase):
    def test_episode_scope_is_inherited_by_child_tasks_and_resets(self):
        async def check():
            with episode_scope("run", 3):
                inherited = await asyncio.create_task(_current())
            return inherited, current_episode_key()

        inherited, after = asyncio.run(check())
        self.assertEqual(inherited, ("run", 3))
        self.assertIsNone(after)

    def test_apply_episode_metrics_aggregates_only_matching_context(self):
        llm = SimpleNamespace(
            call_events=[
                {"episode_key": ("run", 1), "token_usage": {"prompt_tokens": 7, "completion_tokens": 2}},
                {"episode_key": ("other", 1), "token_usage": {"prompt_tokens": 99, "completion_tokens": 99}},
                {"episode_key": ("run", 1), "token_usage": {"prompt_tokens": 3, "completion_tokens": 1}},
            ]
        )
        embedder = SimpleNamespace(
            call_events=[
                {"episode_key": ("run", 1), "text_count": 4},
                {"episode_key": ("run", 2), "text_count": 8},
            ]
        )
        graphiti = SimpleNamespace(llm_client=llm, embedder=embedder)
        trace = EpisodeTrace("run", "q", "M0", 0, 1, 1)

        apply_episode_metrics(graphiti, trace)

        self.assertEqual(trace.llm_call_count, 2)
        self.assertEqual(trace.llm_input_tokens, 10)
        self.assertEqual(trace.llm_output_tokens, 3)
        self.assertEqual(trace.embedding_call_count, 1)
        self.assertEqual(trace.extra["embedding_text_count"], 4)

    def test_driver_queries_are_attributed_as_reads_and_writes(self):
        class Driver:
            def __init__(self):
                self.queries = []

            async def execute_query(self, cypher_query_, **kwargs):
                self.queries.append((cypher_query_, kwargs))
                return None

        driver = Driver()
        graphiti = SimpleNamespace(
            driver=driver,
            llm_client=SimpleNamespace(call_events=[]),
            embedder=SimpleNamespace(call_events=[]),
        )
        install_driver_instrumentation(graphiti)
        async def exercise():
            with episode_scope("run", 4):
                await driver.execute_query("MATCH (n) RETURN n")
                await driver.execute_query(
                    "MATCH (n) SET n.value = 1 RETURN n",
                    query="semantic search text",
                )
        asyncio.run(exercise())

        trace = EpisodeTrace("run", "q", "M0", 0, 4, 1)
        apply_episode_metrics(graphiti, trace)
        self.assertEqual(trace.db_query_count, 1)
        self.assertEqual(trace.db_write_count, 1)
        self.assertEqual(driver.queries[1][1]["query"], "semantic search text")

    def test_session_transactions_are_attributed_as_reads_and_writes(self):
        class Session:
            def __init__(self):
                self.calls = []

            async def execute_write(self, *args, **kwargs):
                self.calls.append(("execute_write", args, kwargs))
                return "write"

            async def execute_read(self, *args, **kwargs):
                self.calls.append(("execute_read", args, kwargs))
                return "read"

            async def run(self, cypher_query_, **kwargs):
                self.calls.append(("run", cypher_query_, kwargs))
                return "run"

            async def close(self):
                self.calls.append(("close",))

        class Driver:
            def __init__(self):
                self.created_session = Session()

            async def execute_query(self, cypher_query_, **kwargs):
                return None

            def session(self, **_kwargs):
                return self.created_session

        driver = Driver()
        graphiti = SimpleNamespace(
            driver=driver,
            llm_client=SimpleNamespace(call_events=[]),
            embedder=SimpleNamespace(call_events=[]),
        )
        install_driver_instrumentation(graphiti)

        async def exercise():
            with episode_scope("run", 5):
                session = driver.session(database="neo4j")
                await session.execute_write(lambda *_: None)
                await session.execute_read(lambda *_: None)
                await session.run("CREATE (n:Entity)")
                await session.close()

        asyncio.run(exercise())
        trace = EpisodeTrace("run", "q", "M0", 0, 5, 1)
        apply_episode_metrics(graphiti, trace)
        self.assertEqual(trace.db_query_count, 1)
        self.assertEqual(trace.db_write_count, 2)


async def _current():
    return current_episode_key()


if __name__ == "__main__":
    import unittest

    unittest.main()
