import asyncio
import json
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedding_cache import (  # noqa: E402
    CachingCountingEmbedder,
    EmbeddingCache,
    UnexpectedEmbeddingError,
)
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

    async def test_capture_then_replay_across_instances_returns_identical_vector(self):
        class DifferentEmbedder(FakeEmbedder):
            async def create(self, value):
                self.single_calls.append(value)
                return [999.0, 998.0]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            capture_inner = FakeEmbedder()
            capture = CachingCountingEmbedder(
                capture_inner,
                persistent_cache=EmbeddingCache(path, read_only=False),
            )
            expected = await capture.create(["same text"])

            replay_inner = DifferentEmbedder()
            replay = CachingCountingEmbedder(
                replay_inner,
                persistent_cache=EmbeddingCache(path, read_only=True),
            )
            actual = await replay.create(["same text"])

            self.assertEqual(actual, expected)
            self.assertEqual(replay_inner.single_calls, [])
            self.assertEqual(replay.api_call_count, 0)

    async def test_replay_miss_fails_without_calling_inner_embedder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            capture = EmbeddingCache(path, read_only=False)
            capture.put("known", [1.0, 2.0])
            inner = FakeEmbedder()
            replay_cache = EmbeddingCache(path, read_only=True)
            replay = CachingCountingEmbedder(inner, persistent_cache=replay_cache)

            with self.assertRaises(UnexpectedEmbeddingError) as raised:
                await replay.create(["missing"])

            self.assertEqual(inner.single_calls, [])
            self.assertEqual(inner.batch_calls, [])
            self.assertEqual(replay.api_call_count, 0)
            self.assertEqual(
                raised.exception.text_sha256,
                sha256("missing".encode("utf-8")).hexdigest(),
            )
            self.assertEqual(replay_cache.unexpected_embedding_diagnostics[0]["text_length"], 7)
            self.assertNotIn(
                "missing",
                json.dumps(replay_cache.unexpected_embedding_diagnostics),
            )

    async def test_single_element_create_and_batch_share_exact_text_cache_key(self):
        inner = FakeEmbedder()
        cached = CachingCountingEmbedder(inner)

        first = await cached.create(["shared"])
        second = await cached.create_batch(["shared"])

        self.assertEqual(first, second[0])
        self.assertEqual(inner.single_calls, [["shared"]])
        self.assertEqual(inner.batch_calls, [])
        self.assertEqual(cached.api_call_count, 1)
        self.assertEqual(cached.cache_hit_count, 1)

    async def test_concurrent_single_and_batch_requests_share_one_live_call(self):
        inner = FakeEmbedder()
        with tempfile.TemporaryDirectory() as tmp:
            cached = CachingCountingEmbedder(
                inner,
                persistent_cache=EmbeddingCache(
                    Path(tmp) / "embedding.jsonl",
                    read_only=False,
                ),
            )

            single, batch = await asyncio.gather(
                cached.create(["shared"]),
                cached.create_batch(["shared"]),
            )

            self.assertEqual(single, batch[0])
            self.assertEqual(len(inner.single_calls) + len(inner.batch_calls), 1)
            self.assertEqual(cached.api_call_count, 1)

    async def test_persistent_cache_uses_ascii_lf_jsonl_framing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            cache = EmbeddingCache(path, read_only=False)
            cache.put("alpha\u2028inside", [1.0, 2.0])
            cache.put("beta", [3.0, 4.0])

            raw = path.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertNotIn(b"\r", raw)
            self.assertEqual(len([line for line in raw.split(b"\n") if line]), 2)
            loaded = EmbeddingCache(path, read_only=True)
            self.assertEqual(loaded.get("alpha\u2028inside"), [1.0, 2.0])

    async def test_conflicting_vectors_for_same_text_hash_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            cache = EmbeddingCache(path, read_only=False)
            cache.put("same", [1.0, 2.0])
            record = json.loads(path.read_text(encoding="utf-8").strip())
            record["vector"] = [9.0, 9.0]
            record["dimension"] = 2
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

            with self.assertRaisesRegex(ValueError, "conflicting embedding"):
                EmbeddingCache(path, read_only=True)


if __name__ == "__main__":
    import unittest

    unittest.main()
