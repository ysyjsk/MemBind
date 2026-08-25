from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from saturated_fixed_work_baseline_v1_3.mab_live_runner import (
    episode_from_input,
    resolve_runtime_builder,
    run_mab_construction_async,
)
from saturated_fixed_work_baseline_v1_3.workload_contract import EpisodeInput, WorkloadManifest


@dataclass
class _FakeRecorder:
    def __init__(self) -> None:
        self.records = []

    class _Scope:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def restore(self):
            return None

    def episode_scope(self, *_args):
        return self._Scope()

    def episode_envelope(self, run_id, episode_id, source_sequence):
        return {
            "schema_version": "test.trace",
            "run_id": run_id,
            "episode_id": episode_id,
            "source_sequence": source_sequence,
            "spans": [],
        }


class _FakeGraph:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.llm_client = object()
        self.clients = SimpleNamespace(llm_client=self.llm_client)

    async def add_episode(self, **kwargs):
        sequence = int(kwargs["name"].split("::")[-1])
        self.calls.append(sequence)
        await asyncio.sleep(0 if sequence % 2 else 0.002)
        return {"sequence": sequence}

    async def close(self):
        return None


def _workload(tmp_path: Path, count: int = 3):
    episodes = tuple(
        EpisodeInput(
            context_id="ctx-0",
            source_sequence=index,
            episode_id=f"episode-{index}",
            reference_time=f"2026-01-0{index + 1}T00:00:00Z",
            body=f"[USER]\nmessage {index}",
        )
        for index in range(count)
    )
    manifest = WorkloadManifest.from_episodes(
        context_id="ctx-0",
        episodes=episodes,
        dataset_revision="revision",
        dataset_file_sha256="a" * 64,
        expected_episode_count=count,
    )
    return episodes, manifest


def _run(tmp_path: Path, method: str):
    episodes, manifest = _workload(tmp_path)
    graph = _FakeGraph()

    async def exporter(_graph, selected, namespace):
        return {"namespace": namespace, "episodes": [{"source_sequence": e.source_sequence} for e in selected]}

    result = asyncio.run(
        run_mab_construction_async(
            method=method,
            run_id="test-run",
            context_id="ctx-0",
            namespace=f"ns-{method.lower()}",
            episodes=episodes,
            runtime_builder=lambda: SimpleNamespace(graphiti=graph, llm_client=object()),
            instrumentation_installer=lambda *_args: _FakeRecorder._Scope(),
            recorder_factory=_FakeRecorder,
            graph_exporter=exporter,
            output_root=tmp_path / method,
            authority={"authority_sha256": "b" * 64},
            workload_manifest=manifest,
            frozen_config={"config_sha256": "c" * 64},
        )
    )
    return result, graph


def test_live_runner_b0_and_b1_emit_complete_shared_contract(tmp_path: Path) -> None:
    b0, b0_graph = _run(tmp_path, "B0")
    b1, b1_graph = _run(tmp_path, "B1")
    assert b0["lifecycle_validation"]["contract_status"] == "PASS"
    assert b1["lifecycle_validation"]["contract_status"] == "PASS"
    assert b0["order_validation"]["order_contract_status"] == "PASS"
    assert b1["order_validation"]["order_contract_status"] == "NOT_REQUIRED"
    assert b0["workload_hash"] == b1["workload_hash"]
    assert b0_graph.calls == [0, 1, 2]
    assert sorted(b1_graph.calls) == [0, 1, 2]
    assert json.loads((tmp_path / "B0" / "construction_seal.json").read_text())["status"] == "CONSTRUCTION_SEALED"


def test_episode_projection_does_not_require_private_qa_fields() -> None:
    item = EpisodeInput(
        context_id="ctx",
        source_sequence=0,
        episode_id="episode-0",
        reference_time="2026-01-01T00:00:00Z",
        body="[USER]\npublic",
    )
    projected = episode_from_input(item)
    assert projected.name == "ctx::episode::0000"
    assert not hasattr(projected, "question")
    assert not hasattr(projected, "reference_answers")


def test_runtime_builder_adapter_accepts_sync_and_async_builders() -> None:
    runtime = object()

    async def async_builder() -> object:
        return runtime

    assert asyncio.run(resolve_runtime_builder(lambda: runtime)) is runtime
    assert asyncio.run(resolve_runtime_builder(async_builder)) is runtime
