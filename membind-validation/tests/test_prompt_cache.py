import asyncio
import fcntl
import json
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from response_cache import GraphitiPromptCacheLLM, PromptCache, PromptParts, UnexpectedPromptError, compute_prompt_hash  # noqa: E402


class PromptCacheTests(TestCase):
    def test_hash_changes_when_any_protocol_part_changes(self):
        base = PromptParts(
            model_revision="rev",
            decoding_config={"temperature": 0.0, "seed": 20260806},
            structured_output_schema={"type": "object"},
            system_prompt="system",
            user_prompt="user",
        )
        changed = PromptParts(
            model_revision="rev2",
            decoding_config=base.decoding_config,
            structured_output_schema=base.structured_output_schema,
            system_prompt=base.system_prompt,
            user_prompt=base.user_prompt,
        )
        self.assertNotEqual(compute_prompt_hash(base), compute_prompt_hash(changed))

    def test_read_only_cache_miss_is_unexpected_prompt_and_does_not_call_live_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = PromptCache(Path(tmp) / "cache.jsonl", read_only=True)
            calls = []
            with self.assertRaises(UnexpectedPromptError):
                cache.resolve(
                    PromptParts("rev", {}, {}, "system", "new prompt"),
                    live_call=lambda: calls.append("called"),
                )
            self.assertEqual(calls, [])
            self.assertTrue(cache.unexpected_prompt)

    def test_read_only_miss_records_component_hashes_prompt_parts_and_nearest_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"
            cached_parts = PromptParts(
                "rev",
                {"prompt_name": "dedupe_edges.resolve_edge", "temperature": 0.0},
                {"title": "EdgeDuplicate"},
                "system",
                "candidate alpha\ncandidate beta\nnew fact",
            )
            cached_record = PromptCache(path, read_only=False).put(
                cached_parts,
                raw_response="{}",
                parsed_response={},
                token_usage={},
            )
            replay = PromptCache(path, read_only=True)
            requested_parts = PromptParts(
                cached_parts.model_revision,
                cached_parts.decoding_config,
                cached_parts.structured_output_schema,
                cached_parts.system_prompt,
                "candidate beta\ncandidate alpha\nnew fact",
            )

            with self.assertRaises(UnexpectedPromptError) as raised:
                replay.resolve(requested_parts)

            diagnostics = replay.unexpected_prompt_diagnostics
            self.assertEqual(len(diagnostics), 1)
            diagnostic = diagnostics[0]
            self.assertEqual(diagnostic["prompt_name"], "dedupe_edges.resolve_edge")
            self.assertEqual(diagnostic["requested_prompt_parts"]["user_prompt"], requested_parts.user_prompt)
            self.assertEqual(
                set(diagnostic["component_hashes"]),
                {
                    "model_revision",
                    "decoding_config",
                    "structured_output_schema",
                    "system_prompt",
                    "user_prompt",
                },
            )
            self.assertEqual(
                diagnostic["nearest_cache_record"]["prompt_hash"],
                cached_record.prompt_hash,
            )
            self.assertEqual(raised.exception.diagnostic, diagnostic)

    def test_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"
            cache = PromptCache(path, read_only=False)
            parts = PromptParts("rev", {}, {}, "system", "user")
            cache.put(parts, raw_response='{"ok": true}', parsed_response={"ok": True}, token_usage={"input": 1})
            loaded = PromptCache(path, read_only=True)
            rec = loaded.get(parts)
            self.assertIsNotNone(rec)
            self.assertEqual(rec.parsed_response, {"ok": True})
            self.assertEqual(rec.prompt_parts["system_prompt"], "system")
            self.assertEqual(rec.prompt_parts["user_prompt"], "user")

    def test_cache_round_trip_preserves_unicode_line_separators_inside_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"
            cache = PromptCache(path, read_only=False)
            parts = PromptParts(
                "rev",
                {},
                {},
                "system",
                "paragraph one\u2028paragraph two\u0085paragraph three",
            )
            cache.put(
                parts,
                raw_response='{"ok": true}',
                parsed_response={"ok": True},
                token_usage={"input": 1},
            )

            loaded = PromptCache(path, read_only=True)

            self.assertEqual(loaded.get(parts).parsed_response, {"ok": True})

    def test_read_only_loader_waits_for_an_exclusive_record_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl"
            target = root / "cache.jsonl"
            parts = PromptParts("rev", {}, {}, "system", "large user prompt")
            PromptCache(source, read_only=False).put(
                parts,
                raw_response=json.dumps({"value": "x" * 100_000}),
                parsed_response={"ok": True},
                token_usage={"prompt_tokens": 10},
            )
            payload = source.read_text(encoding="utf-8")
            midpoint = len(payload) // 2
            writer_has_partial_record = threading.Event()

            def slow_locked_append():
                with target.open("a", encoding="utf-8") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    handle.write(payload[:midpoint])
                    handle.flush()
                    writer_has_partial_record.set()
                    time.sleep(0.15)
                    handle.write(payload[midpoint:])
                    handle.flush()
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

            writer = threading.Thread(target=slow_locked_append)
            writer.start()
            self.assertTrue(writer_has_partial_record.wait(timeout=1.0))
            try:
                loaded = PromptCache(target, read_only=True)
            finally:
                writer.join(timeout=1.0)

            self.assertFalse(writer.is_alive())
            self.assertEqual(loaded.get(parts).parsed_response, {"ok": True})

    def test_graphiti_llm_wrapper_replay_miss_blocks_live_call(self):
        @dataclass
        class Msg:
            role: str
            content: str

        class Inner:
            async def generate_response(self, *_args, **_kwargs):
                raise AssertionError("live call should not happen")

        with tempfile.TemporaryDirectory() as tmp:
            cache = PromptCache(Path(tmp) / "cache.jsonl", read_only=True)
            wrapper = GraphitiPromptCacheLLM(Inner(), cache, "rev", {"temperature": 0.0})
            with self.assertRaises(UnexpectedPromptError):
                asyncio.run(wrapper.generate_response([Msg("system", "s"), Msg("user", "u")], prompt_name="p"))
            self.assertTrue(cache.unexpected_prompt)

    def test_graphiti_wrapper_persists_raw_response_usage_and_complete_call_config(self):
        @dataclass
        class Msg:
            role: str
            content: str

        class Inner:
            async def generate_response(self, *_args, **_kwargs):
                return {"ok": True}

            def consume_last_call_record(self):
                return {
                    "raw_response": '{"ok":true}',
                    "token_usage": {"prompt_tokens": 11, "completion_tokens": 2},
                }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"
            cache = PromptCache(path, read_only=False)
            wrapper = GraphitiPromptCacheLLM(Inner(), cache, "rev", {"temperature": 0.0})
            parsed = asyncio.run(
                wrapper.generate_response(
                    [Msg("system", "s"), Msg("user", "u")],
                    prompt_name="extract",
                    group_id="g",
                    max_tokens=99,
                    model_size="medium",
                    attribute_extraction=True,
                )
            )

            self.assertEqual(parsed, {"ok": True})
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["raw_response"], '{"ok":true}')
            self.assertEqual(records[0]["token_usage"]["prompt_tokens"], 11)
            config = records[0]["prompt_parts"]["decoding_config"]
            self.assertEqual(config["group_id"], "g")
            self.assertEqual(config["max_tokens"], 99)
            self.assertTrue(config["attribute_extraction"])

    def test_replay_key_changes_for_generation_kwargs_that_change_native_prompt(self):
        @dataclass
        class Msg:
            role: str
            content: str

        class Inner:
            async def generate_response(self, *_args, **_kwargs):
                return {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"
            capture = GraphitiPromptCacheLLM(Inner(), PromptCache(path, False), "rev", {"temperature": 0.0})
            asyncio.run(
                capture.generate_response(
                    [Msg("system", "s"), Msg("user", "u")],
                    group_id="g1",
                    attribute_extraction=False,
                )
            )
            replay = GraphitiPromptCacheLLM(Inner(), PromptCache(path, True), "rev", {"temperature": 0.0})
            with self.assertRaises(UnexpectedPromptError):
                asyncio.run(
                    replay.generate_response(
                        [Msg("system", "s"), Msg("user", "u")],
                        group_id="g2",
                        attribute_extraction=False,
                    )
                )
