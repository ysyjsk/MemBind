"""Tests for the GPT-5.5 lane's local BGE-M3 embedding adapter."""

from __future__ import annotations

import asyncio
import math
import sys
from argparse import Namespace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(ROOT / "src"))


class _FakeCuda:
    @staticmethod
    def is_available() -> bool:
        return True

    @staticmethod
    def get_device_name(index: int) -> str:
        return "NVIDIA GeForce RTX 3090"

    @staticmethod
    def get_device_properties(index: int):
        return type("Properties", (), {"total_memory": 24 * 1024**3})()


class _FakeTorch:
    __version__ = "2.8.0+cu128"
    float16 = "torch.float16"
    cuda = _FakeCuda()
    version = type("Version", (), {"cuda": "12.8"})()


class _FakeSentenceTransformer:
    package_version = "5.1.0"
    last_init: dict | None = None
    last_encode: dict | None = None

    def __init__(self, model: str, **kwargs):
        type(self).last_init = {"model": model, **kwargs}

    def parameters(self):
        yield type("Parameter", (), {"dtype": "torch.float16"})()

    def encode(self, texts, **kwargs):
        type(self).last_encode = {"texts": list(texts), **kwargs}
        vectors = []
        for ordinal, _text in enumerate(texts, start=1):
            vector = [0.0] * 1024
            vector[0] = float(ordinal)
            if ordinal > 1:
                vector[1] = 1.0
            vectors.append(vector)
        return vectors


class LocalBgeM3EmbedderTests(TestCase):
    """Pin the offline local embedding contract used by temporary Graphiti probes."""

    def test_create_uses_frozen_local_bge_m3_settings_and_returns_normalized_1024_vector(self):
        import local_embedding_adapter as adapter

        embedder = adapter.LocalBgeM3Embedder(
            sentence_transformer_cls=_FakeSentenceTransformer,
            torch_module=_FakeTorch(),
            batch_size=3,
        )

        vector = asyncio.run(embedder.create("alpha project deadline"))

        self.assertEqual(len(vector), adapter.BGE_M3_DIMENSION)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in vector)), 1.0, places=7)
        self.assertEqual(
            _FakeSentenceTransformer.last_init,
            {
                "model": "BAAI/bge-m3",
                "cache_folder": "/data/predator/ly/Mem/cache/huggingface/hub",
                "revision": "5617a9f61b028005a4858fdac845db406aefb181",
                "device": "cuda",
                "trust_remote_code": False,
                "local_files_only": True,
                "model_kwargs": {"torch_dtype": "torch.float16", "use_safetensors": False},
                "tokenizer_kwargs": {"local_files_only": True},
                "config_kwargs": {"local_files_only": True},
            },
        )
        self.assertEqual(_FakeSentenceTransformer.last_encode["texts"], ["alpha project deadline"])
        self.assertEqual(_FakeSentenceTransformer.last_encode["batch_size"], 3)
        self.assertIs(_FakeSentenceTransformer.last_encode["normalize_embeddings"], True)
        self.assertIs(_FakeSentenceTransformer.last_encode["convert_to_numpy"], True)
        self.assertIs(_FakeSentenceTransformer.last_encode["show_progress_bar"], False)

    def test_create_batch_uses_one_encode_call_and_preserves_order(self):
        import local_embedding_adapter as adapter

        embedder = adapter.LocalBgeM3Embedder(
            sentence_transformer_cls=_FakeSentenceTransformer,
            torch_module=_FakeTorch(),
            batch_size=8,
        )

        vectors = asyncio.run(embedder.create_batch(["first", "second"]))

        self.assertEqual([len(vector) for vector in vectors], [1024, 1024])
        self.assertEqual(_FakeSentenceTransformer.last_encode["texts"], ["first", "second"])
        self.assertEqual(_FakeSentenceTransformer.last_encode["batch_size"], 8)
        self.assertGreater(vectors[0][0], vectors[1][0])
        self.assertLess(vectors[0][1], vectors[1][1])

    def test_probe_factory_defaults_to_local_embedding_not_remote_openai_embedding(self):
        import gpt55_temporary_graphiti_probe as probe

        args = Namespace(
            embedding_provider="local_bge_m3",
            local_embedding_model="BAAI/bge-m3",
            local_embedding_revision="5617a9f61b028005a4858fdac845db406aefb181",
            local_embedding_cache_folder="/data/predator/ly/Mem/cache/huggingface/hub",
            local_embedding_dim=1024,
            local_embedding_batch_size=11,
        )

        with patch.object(
            probe,
            "_build_openai_compatible_embedder",
            side_effect=AssertionError("remote embedding path must not be used by default"),
        ):
            with patch("local_embedding_adapter.LocalBgeM3Embedder") as fake_local:
                fake_local.return_value = type("Inner", (), {"config": object()})()
                embedder = probe._build_temporary_embedder(args, embedding_cache="cache")

        self.assertIs(embedder.inner, fake_local.return_value)
        self.assertEqual(embedder.persistent_cache, "cache")
        fake_local.assert_called_once_with(
            model="BAAI/bge-m3",
            revision="5617a9f61b028005a4858fdac845db406aefb181",
            cache_folder=Path("/data/predator/ly/Mem/cache/huggingface/hub"),
            dimension=1024,
            batch_size=11,
        )

    def test_parser_defaults_to_local_embedding_provider(self):
        import gpt55_temporary_graphiti_probe as probe

        args = probe._parser().parse_args(
            ["--data", "dataset.json", "--question-id", "q0", "--attempt", "tmp"]
        )

        self.assertEqual(args.embedding_provider, "local_bge_m3")
        self.assertEqual(args.local_embedding_model, "BAAI/bge-m3")
        self.assertEqual(args.local_embedding_dim, 1024)

    def test_local_preflight_records_runtime_identity_and_normalization(self):
        import local_embedding_adapter as adapter

        result = adapter.probe_local_embedding(
            torch_module=_FakeTorch(),
            sentence_transformer_cls=_FakeSentenceTransformer,
            require_cuda=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["device"], "cuda")
        self.assertEqual(result["device_name"], "NVIDIA GeForce RTX 3090")
        self.assertEqual(result["model_parameter_dtype"], "torch.float16")
        self.assertEqual(result["pooling_policy"], "model_defined")
        self.assertEqual(
            result["normalization_policy"],
            "encode(normalize_embeddings=True)+adapter_defensive_l2",
        )
        self.assertEqual(len(result["vector_norms"]), 2)
        for norm in result["vector_norms"]:
            self.assertAlmostEqual(norm, 1.0, places=7)

    def test_local_preflight_can_run_inside_existing_event_loop(self):
        import local_embedding_adapter as adapter

        async def run_inside_loop():
            return adapter.probe_local_embedding(
                torch_module=_FakeTorch(),
                sentence_transformer_cls=_FakeSentenceTransformer,
                require_cuda=True,
            )

        result = asyncio.run(run_inside_loop())

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(result["vector_norms"]), 2)

    def test_embedding_preflight_failure_blocks_before_live_graphiti(self):
        import gpt55_temporary_graphiti_probe as probe

        async def must_not_run(*args, **kwargs):
            raise AssertionError("run_experiment must not run when local embedding preflight fails")

        with patch.object(
            probe,
            "_default_embedding_preflight",
            return_value={"ok": False, "status": "blocked", "reason": "sentence-transformers missing"},
        ):
            result = asyncio.run(
                probe.run_temporary_probe(
                    Namespace(
                        data="dataset.json",
                        question_id="q0",
                        attempt="gpt55_tmp_local_embed_blocked",
                        arrival_interval_ms=0,
                        artifacts="gpt55_temporary/artifacts",
                        base_url="https://api.labforge.test/v1",
                        api_key="secret",
                        model="gpt-5.5",
                        embedding_provider="local_bge_m3",
                        local_embedding_model="BAAI/bge-m3",
                        local_embedding_revision="5617a9f61b028005a4858fdac845db406aefb181",
                        local_embedding_cache_folder="/data/predator/ly/Mem/cache/huggingface/hub",
                        local_embedding_dim=1024,
                        local_embedding_batch_size=11,
                    ),
                    preflight_fn=lambda **kwargs: {"ok": True, "artifact": "preflight.json"},
                    run_experiment_fn=must_not_run,
                    load_instance_fn=lambda path, qid: {"question_id": qid},
                    force_embedding_preflight=True,
                )
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked_embedding_preflight")
        self.assertIn("sentence-transformers missing", result["reason"])
