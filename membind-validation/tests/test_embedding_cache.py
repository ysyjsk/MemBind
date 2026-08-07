import asyncio
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedding_cache import CachingCountingEmbedder  # noqa: E402
from graphiti_core.embedder.client import EmbedderClient  # noqa: E402


class FakeEmbedder:
    def __init__(self):
        self.single_calls = []
        self.batch_calls = []

    async def create(self, value):
        self.single_calls.append(value)
        await asyncio.sleep(0)
        return [float(len(str(value)))]

    async def create_batch(self, values):
        self.batch_calls.append(list(values))
        await asyncio.sleep(0)
        return [[float(len(value))] for value in values]


class EmbeddingCacheTests(IsolatedAsyncioTestCase):
    async def test_wrapper_satisfies_graphiti_embedder_runtime_type_contract(self):
        self.assertIsInstance(CachingCountingEmbedder(FakeEmbedder()), EmbedderClient)

    async def test_concurrent_identical_inputs_share_one_remote_call(self):
        inner = FakeEmbedder()
        cached = CachingCountingEmbedder(inner)

        first, second = await asyncio.gather(cached.create("alice"), cached.create("alice"))

        self.assertEqual(first, second)
        self.assertEqual(inner.single_calls, ["alice"])
        self.assertEqual(cached.api_call_count, 1)
        self.assertEqual(cached.text_count, 1)
        self.assertEqual(cached.cache_hit_count, 1)

    async def test_batch_preserves_order_deduplicates_and_reuses_cache(self):
        inner = FakeEmbedder()
        cached = CachingCountingEmbedder(inner)

        result = await cached.create_batch(["bob", "alice", "bob"])
        again = await cached.create_batch(["alice", "bob"])

        self.assertEqual(result, [[3.0], [5.0], [3.0]])
        self.assertEqual(again, [[5.0], [3.0]])
        self.assertEqual(inner.batch_calls, [["bob", "alice"]])
        self.assertEqual(cached.api_call_count, 1)
        self.assertEqual(cached.text_count, 2)
        self.assertEqual(cached.cache_hit_count, 3)

    async def test_failures_are_not_cached(self):
        class Failing(FakeEmbedder):
            async def create(self, value):
                self.single_calls.append(value)
                raise RuntimeError("embedding failed")

        inner = Failing()
        cached = CachingCountingEmbedder(inner)

        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "embedding failed"):
                await cached.create("alice")

        self.assertEqual(inner.single_calls, ["alice", "alice"])


if __name__ == "__main__":
    import unittest

    unittest.main()
