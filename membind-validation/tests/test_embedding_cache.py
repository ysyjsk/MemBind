import asyncio
import json
import struct
import sys
import tempfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedding_cache import (  # noqa: E402
    CachingCountingEmbedder,
    EmbeddingCache,
    EmbeddingNamespace,
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


def namespace(dimension=1, **overrides):
    values = {
        "served_model_id": "qwen3-embedding-0.6b",
        "identity_kind": "deployment_fingerprint",
        "identity_value": "a" * 64,
        "dimension": dimension,
        "dtype": "float32",
        "pooling": "last_token",
        "normalization": "l2",
        "instruction_policy": "none",
        "input_transform": "utf8_exact_v1",
    }
    values.update(overrides)
    return EmbeddingNamespace(**values)


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
                persistent_cache=EmbeddingCache(
                    path,
                    read_only=False,
                    namespace=namespace(),
                ),
            )
            expected = await capture.create(["same text"])

            replay_inner = DifferentEmbedder()
            replay = CachingCountingEmbedder(
                replay_inner,
                persistent_cache=EmbeddingCache(
                    path,
                    read_only=True,
                    namespace=namespace(),
                ),
            )
            actual = await replay.create(["same text"])

            self.assertEqual(actual, expected)
            self.assertEqual(replay_inner.single_calls, [])
            self.assertEqual(replay.api_call_count, 0)

    async def test_replay_miss_fails_without_calling_inner_embedder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            capture = EmbeddingCache(
                path,
                read_only=False,
                namespace=namespace(dimension=2),
            )
            capture.put("known", [1.0, 2.0])
            inner = FakeEmbedder()
            replay_cache = EmbeddingCache(
                path,
                read_only=True,
                namespace=namespace(dimension=2),
            )
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
                    namespace=namespace(),
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
            cache = EmbeddingCache(
                path,
                read_only=False,
                namespace=namespace(dimension=2),
            )
            cache.put("alpha\u2028inside", [1.0, 2.0])
            cache.put("beta", [3.0, 4.0])

            raw = path.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertNotIn(b"\r", raw)
            self.assertEqual(len([line for line in raw.split(b"\n") if line]), 3)
            loaded = EmbeddingCache(
                path,
                read_only=True,
                namespace=namespace(dimension=2),
            )
            self.assertEqual(loaded.get("alpha\u2028inside"), [1.0, 2.0])

    async def test_conflicting_vectors_for_same_text_hash_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            cache = EmbeddingCache(
                path,
                read_only=False,
                namespace=namespace(dimension=2),
            )
            cache.put("same", [1.0, 2.0])
            record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
            record["vector"] = [9.0, 9.0]
            record["dimension"] = 2
            record["vector_sha256"] = sha256(
                b"".join(struct.pack(">d", value) for value in record["vector"])
            ).hexdigest()
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

            with self.assertRaisesRegex(ValueError, "conflicting embedding"):
                EmbeddingCache(
                    path,
                    read_only=True,
                    namespace=namespace(dimension=2),
                )

    async def test_namespace_header_is_required_and_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            expected = namespace(dimension=2)
            cache = EmbeddingCache(path, read_only=False, namespace=expected)
            cache.put("sensitive input", [1.0, 2.0])

            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(records[0]["record_type"], "namespace")
            self.assertEqual(records[0]["namespace"], expected.to_dict())
            self.assertNotIn("sensitive input", path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "namespace mismatch"):
                EmbeddingCache(
                    path,
                    read_only=True,
                    namespace=replace(expected, normalization="none"),
                )

    async def test_identity_must_be_reported_revision_or_immutable_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "immutable deployment fingerprint"):
            namespace(identity_value="qwen3-embedding-0.6b")
        with self.assertRaisesRegex(ValueError, "endpoint revision"):
            namespace(
                identity_kind="endpoint_revision",
                identity_value="endpoint-unreported",
            )

        reported = namespace(
            identity_kind="endpoint_revision",
            identity_value="hf-revision-0123456789abcdef",
        )
        self.assertEqual(reported.identity_kind, "endpoint_revision")

    async def test_vector_dimension_and_finite_values_are_hard_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = EmbeddingCache(
                Path(tmp) / "embedding.jsonl",
                read_only=False,
                namespace=namespace(dimension=2),
            )
            for vector, message in (
                ([1.0], "dimension"),
                ([1.0, float("nan")], "finite"),
                ([1.0, float("inf")], "finite"),
            ):
                with self.subTest(vector=vector):
                    with self.assertRaisesRegex(ValueError, message):
                        cache.put("bad", vector)

    async def test_corrupt_jsonl_and_vector_hash_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            cache = EmbeddingCache(
                path,
                read_only=False,
                namespace=namespace(),
            )
            cache.put("same", [1.0])
            lines = path.read_text(encoding="utf-8").splitlines()
            record = json.loads(lines[-1])
            record["vector_sha256"] = "0" * 64
            path.write_text(
                "\n".join([lines[0], json.dumps(record, sort_keys=True)]) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "vector hash"):
                EmbeddingCache(path, read_only=True, namespace=namespace())

            corrupt = Path(tmp) / "corrupt.jsonl"
            corrupt.write_text("{not-json}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid embedding cache JSON"):
                EmbeddingCache(corrupt, read_only=True, namespace=namespace())

    async def test_capture_cache_open_is_exclusive_even_for_valid_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            EmbeddingCache(path, read_only=False, namespace=namespace())

            with self.assertRaises(FileExistsError):
                EmbeddingCache(path, read_only=False, namespace=namespace())

    async def test_persistent_create_rejects_multiple_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            inner = FakeEmbedder()
            cached = CachingCountingEmbedder(
                inner,
                persistent_cache=EmbeddingCache(
                    Path(tmp) / "embedding.jsonl",
                    read_only=False,
                    namespace=namespace(),
                ),
            )

            with self.assertRaisesRegex(ValueError, "one exact string item"):
                await cached.create(["first", "second"])

            self.assertEqual(inner.single_calls, [])

    async def test_invalid_batch_persists_none_of_the_owner_batch(self):
        class PartiallyInvalid(FakeEmbedder):
            async def create_batch(self, values):
                self.batch_calls.append(list(values))
                return [[1.0], [float("nan")]]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            cache = EmbeddingCache(
                path,
                read_only=False,
                namespace=namespace(),
            )
            cached = CachingCountingEmbedder(
                PartiallyInvalid(),
                persistent_cache=cache,
            )

            with self.assertRaisesRegex(ValueError, "finite"):
                await cached.create_batch(["first", "second"])

            self.assertEqual(len(path.read_text(encoding="ascii").splitlines()), 1)
            self.assertIsNone(cache.get("first"))
            self.assertIsNone(cache.get("second"))

    async def test_exact_utf8_keys_do_not_apply_unicode_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            cache = EmbeddingCache(
                path,
                read_only=False,
                namespace=namespace(),
            )
            nfc = "\u00e9"
            nfd = "e\u0301"
            cache.put(nfc, [1.0])
            cache.put(nfd, [2.0])

            self.assertEqual(cache.get(nfc), [1.0])
            self.assertEqual(cache.get(nfd), [2.0])
            self.assertNotEqual(
                sha256(nfc.encode("utf-8")).hexdigest(),
                sha256(nfd.encode("utf-8")).hexdigest(),
            )

    async def test_multibyte_replay_diagnostic_has_codepoint_and_byte_lengths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding.jsonl"
            EmbeddingCache(
                path,
                read_only=False,
                namespace=namespace(),
            )
            replay = EmbeddingCache(
                path,
                read_only=True,
                namespace=namespace(),
            )
            diagnostic = replay.record_unexpected("\u8bb0\u5fc6")

            self.assertEqual(diagnostic["text_length"], 2)
            self.assertEqual(diagnostic["text_byte_length"], 6)
            self.assertNotIn("\u8bb0\u5fc6", json.dumps(diagnostic, ensure_ascii=False))
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                replay.put("forbidden", [1.0])


if __name__ == "__main__":
    import unittest

    unittest.main()
