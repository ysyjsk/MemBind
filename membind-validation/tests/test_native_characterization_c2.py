"""Offline TDD contracts for the C2/E1 Native characterization runner."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from current_state_gate import LiveAction, LiveActionDenied  # noqa: E402
from native_characterization_c2 import (  # noqa: E402
    E1E2Block,
    analyze_e1_breakdown,
    execute_c2,
    load_e1_e2_blocks,
)
from native_characterization_tracing import SpanRecord  # noqa: E402


FORBIDDEN_TEXT = (
    "api_key",
    "authorization",
    "body",
    "content",
    "cypher",
    "messages",
    "prompt",
    "query_text",
    "response",
    "session_id",
)


def _write_freeze(root: Path) -> Path:
    freeze = {
        "schema_version": "membind.native-characterization-freeze.v1",
        "screening": {
            "e1_e2": {
                "shared_native_trace": True,
                "block_order": [
                    {
                        "block_index": index,
                        "history_id": history_id,
                        "graph_namespace": f"nc-e1e2-{index:016x}",
                    }
                    for index, history_id in enumerate(
                        ["h-alpha", "h-beta", "h-gamma", "h-delta"]
                    )
                ],
            }
        },
        "dataset": {
            "calibration_histories": [
                {
                    "history_id": "h-alpha",
                    "episode_count": 2,
                    "episodes": [
                        {
                            "source_sequence": 0,
                            "episode_source_sha256": "a" * 64,
                            "prefix_sha256": "b" * 64,
                        },
                        {
                            "source_sequence": 1,
                            "episode_source_sha256": "c" * 64,
                            "prefix_sha256": "d" * 64,
                        },
                    ],
                },
                {
                    "history_id": "h-beta",
                    "episode_count": 1,
                    "episodes": [
                        {
                            "source_sequence": 0,
                            "episode_source_sha256": "e" * 64,
                            "prefix_sha256": "f" * 64,
                        }
                    ],
                },
                {
                    "history_id": "h-gamma",
                    "episode_count": 1,
                    "episodes": [
                        {
                            "source_sequence": 0,
                            "episode_source_sha256": "1" * 64,
                            "prefix_sha256": "2" * 64,
                        }
                    ],
                },
                {
                    "history_id": "h-delta",
                    "episode_count": 1,
                    "episodes": [
                        {
                            "source_sequence": 0,
                            "episode_source_sha256": "3" * 64,
                            "prefix_sha256": "4" * 64,
                        }
                    ],
                },
            ]
        },
    }
    path = root / "artifacts" / "native_characterization" / "freeze.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(freeze, sort_keys=True), encoding="utf-8")
    return path


class _Usage:
    prompt_tokens = 7
    completion_tokens = 3


class _Transport:
    async def create(self, *_args, **_kwargs):
        return SimpleNamespace(usage=_Usage())


class _LLMClient:
    def __init__(self) -> None:
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Transport())
        )

    async def generate_response(self, *_args, **_kwargs):
        await self.client.chat.completions.create()
        return "ok"


class _Embedder:
    async def create(self, *_args, **_kwargs):
        return [0.0] * 4

    async def create_batch(self, input_data_list):
        return [[0.0] * 4 for _ in input_data_list]


class _Driver:
    async def execute_query(self, cypher_query_, **_kwargs):
        return cypher_query_


def _fake_runtime_factory():
    phase_module = SimpleNamespace()
    graphiti = SimpleNamespace(
        llm_client=_LLMClient(),
        embedder=_Embedder(),
        driver=_Driver(),
    )

    async def extract_nodes(*_args, **_kwargs):
        await graphiti.llm_client.generate_response(prompt_name="node")

    async def resolve_extracted_nodes(*_args, **_kwargs):
        await graphiti.driver.execute_query("MATCH (n) RETURN n")

    async def extract_edges(*_args, **_kwargs):
        await graphiti.llm_client.generate_response(prompt_name="edge")

    async def resolve_extracted_edges(*_args, **_kwargs):
        await graphiti.driver.execute_query("MERGE (n) RETURN n")

    async def extract_attributes_from_nodes(*_args, **_kwargs):
        await graphiti.embedder.create_batch(["redacted", "redacted"])

    async def add_episode(episode):
        await graphiti.embedder.create("redacted")
        await phase_module.extract_nodes(episode)
        await phase_module.resolve_extracted_nodes(episode)
        await phase_module.extract_edges(episode)
        await phase_module.resolve_extracted_edges(episode)
        await phase_module.extract_attributes_from_nodes(episode)
        return {"episode_id": episode["episode_id"]}

    for name, value in {
        "extract_nodes": extract_nodes,
        "resolve_extracted_nodes": resolve_extracted_nodes,
        "extract_edges": extract_edges,
        "resolve_extracted_edges": resolve_extracted_edges,
        "extract_attributes_from_nodes": extract_attributes_from_nodes,
    }.items():
        setattr(phase_module, name, value)
    graphiti.add_episode = add_episode
    return SimpleNamespace(graphiti=graphiti, phase_module=phase_module)


def _failing_runtime_factory(fail_on_episode_id: str):
    runtime = _fake_runtime_factory()
    original = runtime.graphiti.add_episode

    async def add_episode(episode):
        if episode["episode_id"] == fail_on_episode_id:
            raise RuntimeError("synthetic_vllm_disconnect")
        return await original(episode)

    runtime.graphiti.add_episode = add_episode
    return runtime


class NativeCharacterizationC2Tests(TestCase):
    def test_loads_exactly_four_frozen_e1_e2_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            freeze_path = _write_freeze(Path(tmp))
            blocks = load_e1_e2_blocks(freeze_path)

        self.assertEqual(len(blocks), 4)
        self.assertEqual([block.block_index for block in blocks], [0, 1, 2, 3])
        self.assertEqual([block.episode_count for block in blocks], [2, 1, 1, 1])
        self.assertEqual(blocks[0].episodes[1]["episode_id"], "h-alpha:1")

    def test_gate_denial_prevents_runtime_construction_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            freeze_path = _write_freeze(root)

            def denied(_action):
                raise LiveActionDenied("test_denied", action="native_characterization_c2")

            def forbidden_runtime_factory():
                raise AssertionError("runtime factory must not be called before C2 gate")

            with self.assertRaises(LiveActionDenied):
                asyncio.run(
                    execute_c2(
                        validation_root=root,
                        freeze_path=freeze_path,
                        run_id="c2-denied",
                        authorization_checker=denied,
                        runtime_factory=forbidden_runtime_factory,
                    )
                )

            self.assertFalse((root / "artifacts" / "native_characterization" / "runs").exists())

    def test_execute_c2_writes_checkpoint_after_each_block_and_breakdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            freeze_path = _write_freeze(root)
            calls: list[LiveAction] = []

            def authorize(action):
                calls.append(action)

            result = asyncio.run(
                execute_c2(
                    validation_root=root,
                    freeze_path=freeze_path,
                    run_id="c2-offline-green",
                    authorization_checker=authorize,
                    runtime_factory=_fake_runtime_factory,
                )
            )

            self.assertEqual(calls, [LiveAction.NATIVE_CHARACTERIZATION_C2])
            self.assertEqual(result["status"], "completed")
            run_root = root / "artifacts" / "native_characterization" / "runs" / "c2-offline-green"
            checkpoint = json.loads((run_root / "checkpoint.json").read_text())
            self.assertEqual(checkpoint["status"], "completed")
            self.assertEqual(checkpoint["completed_block_indices"], [0, 1, 2, 3])
            block_events = [
                event
                for event in checkpoint["checkpoint_history"]
                if event["event_type"] == "block_completed"
            ]
            self.assertEqual(len(block_events), 4)
            self.assertEqual(
                len(checkpoint["completed_episode_ids"]),
                sum([2, 1, 1, 1]),
            )
            for index, history_id in enumerate(["h-alpha", "h-beta", "h-gamma", "h-delta"]):
                block_dir = run_root / "blocks" / f"{index:03d}_{history_id}"
                self.assertTrue((block_dir / "trace.jsonl").exists())
                self.assertTrue((block_dir / "block_summary.json").exists())
                self.assertTrue((block_dir / "checkpoint.json").exists())

            breakdown = json.loads((run_root / "e1_breakdown.json").read_text())
            self.assertEqual(breakdown["schema_version"], "membind.native-characterization-e1-breakdown.v1")
            self.assertEqual(len(breakdown["blocks"]), 4)
            self.assertGreater(breakdown["aggregate"]["llm_logical_call_count"], 0)
            self.assertGreater(breakdown["aggregate"]["embedding_call_count"], 0)
            self.assertGreater(breakdown["aggregate"]["db_query_count"], 0)
            self.assertGreater(breakdown["aggregate"]["db_write_count"], 0)
            self.assertIn("payload_sha256", breakdown)

            serialized = json.dumps(breakdown, sort_keys=True).casefold()
            for forbidden in FORBIDDEN_TEXT:
                self.assertNotIn(forbidden, serialized)

    def test_execute_c2_checkpoints_each_episode_before_block_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            freeze_path = _write_freeze(root)

            with self.assertRaises(RuntimeError):
                asyncio.run(
                    execute_c2(
                        validation_root=root,
                        freeze_path=freeze_path,
                        run_id="c2-offline-episode-checkpoint",
                        authorization_checker=lambda _action: None,
                        runtime_factory=lambda: _failing_runtime_factory("h-alpha:1"),
                    )
                )

            run_root = (
                root
                / "artifacts"
                / "native_characterization"
                / "runs"
                / "c2-offline-episode-checkpoint"
            )
            checkpoint = json.loads((run_root / "checkpoint.json").read_text())
            self.assertEqual(checkpoint["status"], "error")
            self.assertEqual(checkpoint["completed_block_indices"], [])
            self.assertEqual(checkpoint["completed_episode_ids"], ["h-alpha:0"])
            self.assertEqual(
                checkpoint["checkpoint_history"][0]["event_type"],
                "episode_completed",
            )
            self.assertEqual(
                checkpoint["checkpoint_history"][0]["episode_id"],
                "h-alpha:0",
            )
            trace_lines = (
                run_root
                / "blocks"
                / "000_h-alpha"
                / "trace.jsonl"
            ).read_text().splitlines()
            self.assertEqual(len(trace_lines), 1)

    def test_analyzer_uses_interval_union_for_phase_occupancy(self) -> None:
        block = E1E2Block(
            block_index=0,
            history_id="h",
            graph_namespace="nc-e1e2-0000000000000000",
            episode_count=1,
            episodes=({"episode_id": "h:0", "source_sequence": 0},),
        )
        records = [
            SpanRecord(0, "root", None, "run", "h:0", 0, "add-episode", None, 0, 100, "ok", None),
            SpanRecord(1, "a", "root", "run", "h:0", 0, "llm", "logical-call", 10, 60, "ok", None, {"input_tokens": 1, "output_tokens": 1}),
            SpanRecord(2, "b", "root", "run", "h:0", 0, "llm", "logical-call", 40, 80, "ok", None, {"input_tokens": 2, "output_tokens": 3}),
        ]

        breakdown = analyze_e1_breakdown(
            run_id="run",
            blocks=[(block, records)],
            freeze_sha256="0" * 64,
        )
        llm = breakdown["blocks"][0]["phase_occupancy"]["llm"]

        self.assertEqual(llm["union_ns"], 70)
        self.assertAlmostEqual(llm["occupancy_fraction"], 0.7)
        self.assertEqual(breakdown["aggregate"]["llm_input_tokens"], 3)
        self.assertEqual(breakdown["aggregate"]["llm_output_tokens"], 4)


if __name__ == "__main__":
    import unittest

    unittest.main()
