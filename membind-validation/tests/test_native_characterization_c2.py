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
from native_characterization_instrumentation import PatchHandle  # noqa: E402
from native_characterization_tracing import SpanRecord  # noqa: E402


FORBIDDEN_TEXT = (
    "api_key",
    "authorization",
    "body",
    "content",
    "cypher",
    "messages",
    "raw_prompt",
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

    def transaction(self):
        class Transaction:
            async def run(self, query, **_kwargs):
                return query

        class Context:
            async def __aenter__(self):
                return Transaction()

            async def __aexit__(self, *_args):
                return False

        return Context()


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

    async def retrieve_episodes(*_args, **_kwargs):
        return []

    async def process_episode_data(*_args, **_kwargs):
        async with graphiti.driver.transaction() as transaction:
            await transaction.run("MERGE (n) RETURN n")

    async def add_episode(episode):
        await graphiti.retrieve_episodes(episode)
        await graphiti.embedder.create("redacted")
        await phase_module.extract_nodes(episode)
        await phase_module.resolve_extracted_nodes(episode)
        await phase_module.extract_edges(episode)
        await phase_module.resolve_extracted_edges(episode)
        await phase_module.extract_attributes_from_nodes(episode)
        await graphiti._process_episode_data(episode)
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
    graphiti.retrieve_episodes = retrieve_episodes
    graphiti._process_episode_data = process_episode_data
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


def _complete_measurement_installer(graphiti, recorder, *, phase_module=None):
    original = phase_module.resolve_extracted_nodes

    async def measured(*args, **kwargs):
        with recorder.span(
            "candidate-embedding",
            operation_class="fixture",
            metadata={"text_count": 1},
        ):
            pass
        with recorder.span(
            "candidate-search",
            operation_class="fixture",
            metadata={"candidate_count": 2, "candidate_query_count": 1},
        ):
            pass
        with recorder.span(
            "invalidation-update",
            operation_class="fixture",
            metadata={
                "invalidation_candidate_count": 0,
                "invalidated_count": 0,
                "new_edge_expired_count": 0,
                "timing_scope": "fixture",
            },
        ):
            pass
        return await original(*args, **kwargs)

    phase_module.resolve_extracted_nodes = measured
    handle = PatchHandle()
    handle.add(lambda: setattr(phase_module, "resolve_extracted_nodes", original))
    return handle


async def _graph_prefix_collector(_driver, _graph_namespace):
    return {
        "graph_prefix_node_count": 0,
        "graph_prefix_relationship_count": 0,
    }


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
                        freeze_path=freeze_path.relative_to(root).as_posix(),
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
                    freeze_path=freeze_path.relative_to(root).as_posix(),
                    run_id="c2-offline-green",
                    authorization_checker=authorize,
                    runtime_factory=_fake_runtime_factory,
                    measurement_installer=_complete_measurement_installer,
                    graph_prefix_collector=_graph_prefix_collector,
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

    def test_progress_is_emitted_only_after_durable_episode_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            freeze_path = _write_freeze(root)
            run_id = "c2-offline-progress-order"
            run_root = (
                root
                / "artifacts"
                / "native_characterization"
                / "runs"
                / run_id
            )
            observed = []

            def progress_sink(event):
                checkpoint = json.loads(
                    (run_root / "checkpoint.json").read_text(encoding="ascii")
                )
                observed.append((dict(event), checkpoint["status"]))

            asyncio.run(
                execute_c2(
                    validation_root=root,
                    freeze_path=freeze_path.relative_to(root).as_posix(),
                    run_id=run_id,
                    authorization_checker=lambda _action: None,
                    runtime_factory=_fake_runtime_factory,
                    measurement_installer=_complete_measurement_installer,
                    graph_prefix_collector=_graph_prefix_collector,
                    progress_sink=progress_sink,
                )
            )

            episode_events = [
                (event, checkpoint_status)
                for event, checkpoint_status in observed
                if event["event_type"] == "episode_completed"
            ]
            self.assertEqual(len(episode_events), 5)
            self.assertTrue(
                all(status == "episode_completed" for _event, status in episode_events)
            )
            self.assertTrue(
                all(event["run_id"] == run_id for event, _status in episode_events)
            )
            self.assertTrue(
                all(event["service_latency_ns"] > 0 for event, _status in episode_events)
            )
            self.assertTrue(
                all(
                    event["telemetry_completeness"] == "complete"
                    for event, _status in episode_events
                )
            )
            self.assertTrue(
                all(
                    event["work_volume"]["llm_logical_call_count"] > 0
                    and event["work_volume"]["embedding_call_count"] > 0
                    and event["work_volume"]["db_transaction_count"] > 0
                    for event, _status in episode_events
                )
            )
            self.assertTrue(
                all(
                    "add-episode" in event["phase_interval_union_ns"]
                    for event, _status in episode_events
                )
            )

    def test_execute_c2_checkpoints_each_episode_before_block_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            freeze_path = _write_freeze(root)

            with self.assertRaises(RuntimeError):
                asyncio.run(
                    execute_c2(
                        validation_root=root,
                        freeze_path=freeze_path.relative_to(root).as_posix(),
                        run_id="c2-offline-episode-checkpoint",
                        authorization_checker=lambda _action: None,
                        runtime_factory=lambda: _failing_runtime_factory("h-alpha:1"),
                        measurement_installer=_complete_measurement_installer,
                        graph_prefix_collector=_graph_prefix_collector,
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
            self.assertEqual(len(trace_lines), 2)
            failed_envelope = json.loads(trace_lines[-1])
            failed_root = next(
                span
                for span in failed_envelope["spans"]
                if span["phase"] == "add-episode"
            )
            self.assertEqual(failed_envelope["episode_id"], "h-alpha:1")
            self.assertEqual(failed_root["status"], "error")

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

    def test_analyzer_reports_accounting_distributions_and_failure_work_volume(self) -> None:
        block = E1E2Block(
            block_index=0,
            history_id="h",
            graph_namespace="nc-e1e2-0000000000000000",
            episode_count=2,
            episodes=(
                {
                    "episode_id": "h:0",
                    "source_sequence": 0,
                    "episode_source_sha256": "a" * 64,
                    "prefix_sha256": "b" * 64,
                },
                {
                    "episode_id": "h:1",
                    "source_sequence": 1,
                    "episode_source_sha256": "c" * 64,
                    "prefix_sha256": "d" * 64,
                },
            ),
        )
        records = [
            SpanRecord(0, "r0", None, "run", "h:0", 0, "add-episode", None, 0, 100, "ok", None),
            SpanRecord(1, "l0", "r0", "run", "h:0", 0, "llm", "logical-call", 10, 60, "ok", None, {"input_tokens": 2, "output_tokens": 3, "retry_count": 1, "prompt_name": "nodes"}),
            SpanRecord(2, "p0", "r0", "run", "h:0", 0, "publication", None, 80, 100, "ok", None),
            SpanRecord(3, "r1", None, "run", "h:1", 1, "add-episode", None, 200, 500, "ok", None),
            SpanRecord(4, "l1", "r1", "run", "h:1", 1, "llm", "logical-call", 220, 420, "error", "builtins.ValueError", {"input_tokens": 5, "output_tokens": 7, "retry_count": 2, "prompt_name": "edges"}),
            SpanRecord(5, "p1", "r1", "run", "h:1", 1, "publication", None, 450, 500, "ok", None),
        ]

        breakdown = analyze_e1_breakdown(
            run_id="run",
            blocks=[(block, records)],
            freeze_sha256="0" * 64,
        )

        summary = breakdown["blocks"][0]
        self.assertEqual(summary["observed_episode_count"], 2)
        self.assertEqual(summary["episode_metrics"][0]["service_latency_ns"], 100)
        self.assertEqual(summary["episode_metrics"][1]["service_latency_ns"], 300)
        self.assertEqual(
            summary["distributions"]["service_latency_ns"],
            {"count": 2, "median": 200.0, "p95": 300},
        )
        self.assertEqual(summary["accounting"]["inclusive_ns"], 400)
        self.assertEqual(summary["accounting"]["interval_union_ns"], 400)
        self.assertEqual(summary["accounting"]["sum_of_work_ns"], 720)
        self.assertEqual(summary["accounting"]["critical_path_ns"], 400)
        self.assertEqual(summary["work_volume"]["llm_retry_count"], 3)
        self.assertEqual(summary["work_volume"]["llm_error_count"], 1)
        self.assertEqual(summary["work_volume"]["prompt_names"], {"edges": 1, "nodes": 1})
        self.assertEqual(
            summary["telemetry_completeness"]["status"],
            "incomplete_missing_required_fields",
        )

    def test_analyzer_fails_closed_when_episode_root_is_missing(self) -> None:
        block = E1E2Block(
            block_index=0,
            history_id="h",
            graph_namespace="nc-e1e2-0000000000000000",
            episode_count=1,
            episodes=({"episode_id": "h:0", "source_sequence": 0},),
        )
        with self.assertRaisesRegex(Exception, "measurement_contract"):
            analyze_e1_breakdown(
                run_id="run",
                blocks=[
                    (
                        block,
                        [
                            SpanRecord(0, "child", None, "run", "h:0", 0, "llm", "logical-call", 0, 1, "ok", None),
                        ],
                    )
                ],
                freeze_sha256="0" * 64,
            )

    def test_analyzer_counts_transport_error_recovered_by_logical_call(self) -> None:
        block = E1E2Block(
            block_index=0,
            history_id="h",
            graph_namespace="nc-e1e2-0000000000000000",
            episode_count=1,
            episodes=(
                {
                    "episode_id": "h:0",
                    "source_sequence": 0,
                    "episode_source_sha256": "a" * 64,
                    "prefix_sha256": "b" * 64,
                },
            ),
        )
        records = [
            SpanRecord(0, "root", None, "run", "h:0", 0, "add-episode", None, 0, 100, "ok", None),
            SpanRecord(1, "logical", "root", "run", "h:0", 0, "llm", "logical-call", 10, 80, "ok", None, {"prompt_name": "nodes", "retry_count": 1, "input_tokens": 9, "output_tokens": 4}),
            SpanRecord(2, "attempt-0", "logical", "run", "h:0", 0, "llm-transport", "request-attempt", 20, 30, "error", "builtins.ConnectionError", {"attempt_index": 0}),
            SpanRecord(3, "attempt-1", "logical", "run", "h:0", 0, "llm-transport", "request-attempt", 40, 70, "ok", None, {"attempt_index": 1, "input_tokens": 9, "output_tokens": 4}),
        ]

        breakdown = analyze_e1_breakdown(
            run_id="run",
            blocks=[(block, records)],
            freeze_sha256="0" * 64,
        )

        episode = breakdown["blocks"][0]["episode_metrics"][0]["work_volume"]
        block_work = breakdown["blocks"][0]["work_volume"]
        aggregate_work = breakdown["aggregate"]["work_volume"]
        for work in (episode, block_work, aggregate_work):
            self.assertEqual(work["llm_transport_error_count"], 1)
            self.assertEqual(work["llm_transport_statuses"], {"error": 1, "ok": 1})


if __name__ == "__main__":
    import unittest

    unittest.main()
