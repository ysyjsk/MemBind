from __future__ import annotations

import asyncio
import json
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.artifact_seals import verify_seal
from saturated_fixed_work_baseline_v1_3.membind_v6_1.mab import (
    run_mab_v61_construction_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.executor import (
    STAGED_EXECUTION_STRATEGY,
)
import saturated_fixed_work_baseline_v1_3.membind_v6_1.mab as v61_mab
from saturated_fixed_work_baseline_v1_3.membind_v6_1.policy import V61Policy
from saturated_fixed_work_baseline_v1_3.workload_contract import (
    EpisodeInput,
    WorkloadManifest,
)


class _Delegate:
    def __init__(self) -> None:
        self.calls = 0
        self._membind_extraction_diagnostics: list[dict[str, object]] = []

    async def generate_response(
        self,
        messages,
        response_model=None,
        max_tokens=None,
        model_size=None,
        group_id=None,
        prompt_name=None,
        *,
        attribute_extraction=False,
    ):
        self.calls += 1
        return {"messages": messages, "prompt_name": prompt_name, "call": self.calls}


def _provider_kwargs(prompt_name: str, group_id: str):
    return {
        "response_model": {"type": "object"},
        "max_tokens": 32,
        "model_size": "medium",
        "group_id": group_id,
        "prompt_name": prompt_name,
    }


class _Driver:
    async def execute_query(self, *_args, **_kwargs):
        return SimpleNamespace(records=[])


class _Graph:
    def __init__(self, delegate: _Delegate) -> None:
        self.llm_client = delegate
        self.clients = SimpleNamespace(llm_client=delegate)
        self.driver = _Driver()
        self.max_coroutines = 2

    async def add_episode(self, **kwargs):
        messages = [{"role": "user", "content": kwargs["name"]}]
        await self.llm_client.generate_response(
            messages,
            **_provider_kwargs("extract_nodes.extract_message", kwargs["group_id"]),
        )
        await self.llm_client.generate_response(
            messages,
            **_provider_kwargs("extract_edges.edge", kwargs["group_id"]),
        )
        return {"name": kwargs["name"]}

    async def close(self):
        return None


class _Recorder:
    def __init__(self) -> None:
        self.records = []

    class Scope:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def restore(self):
            return None

    def episode_scope(self, *_args):
        return self.Scope()

    def episode_envelope(self, run_id, episode_id, source_sequence):
        return {
            "schema_version": "test.trace",
            "run_id": run_id,
            "episode_id": episode_id,
            "source_sequence": source_sequence,
            "spans": [],
        }


def test_v61_mab_provider_free_composition_uses_one_arbiter_and_seals(
    tmp_path, monkeypatch
) -> None:
    async def fake_extract_nodes(clients, episode, *_args):
        messages = [{"role": "user", "content": episode.name}]
        await clients.llm_client.generate_response(
            messages,
            **_provider_kwargs("extract_nodes.extract_message", episode.group_id),
        )
        return [SimpleNamespace(name="node")], {0: 0}

    async def fake_extract_edges(clients, episode, *_args):
        messages = [{"role": "user", "content": episode.name}]
        await clients.llm_client.generate_response(
            messages,
            **_provider_kwargs("extract_edges.edge", episode.group_id),
        )
        return [SimpleNamespace(name="edge")]

    modules = {
        name: ModuleType(name)
        for name in (
            "graphiti_core",
            "graphiti_core.utils",
            "graphiti_core.utils.maintenance",
            "graphiti_core.utils.maintenance.edge_operations",
            "graphiti_core.utils.maintenance.node_operations",
        )
    }
    modules["graphiti_core.utils.maintenance.node_operations"].extract_nodes = fake_extract_nodes
    modules["graphiti_core.utils.maintenance.edge_operations"].extract_edges = fake_extract_edges
    async def fake_resolve_extracted_edge(*_args, **_kwargs):
        return None

    modules[
        "graphiti_core.utils.maintenance.edge_operations"
    ].resolve_extracted_edge = fake_resolve_extracted_edge
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        v61_mab,
        "_episode_node",
        lambda episode, *, namespace, uuid_value=None: SimpleNamespace(
            name=episode.name, group_id=namespace, uuid=uuid_value
        ),
    )
    monkeypatch.setattr(v61_mab, "_native_previous_window", lambda *_args: [])
    episodes = tuple(
        EpisodeInput(
            context_id="ctx",
            source_sequence=index,
            episode_id=f"episode-{index}",
            reference_time=f"2026-01-0{index + 1}T00:00:00Z",
            body=f"message {index}",
        )
        for index in range(3)
    )
    manifest = WorkloadManifest.from_episodes(
        context_id="ctx",
        episodes=episodes,
        dataset_revision="revision",
        dataset_file_sha256="a" * 64,
        expected_episode_count=3,
    )
    delegate = _Delegate()
    graph = _Graph(delegate)

    async def exporter(_graph, selected, namespace):
        return {
            "namespace": namespace,
            "episodes": [item.source_sequence for item in selected],
            "canonical_graph_hash": "b" * 64,
        }

    result = asyncio.run(
        run_mab_v61_construction_async(
            run_id="test-v61",
            context_id="ctx",
            namespace="local-qwen3-14b-awq-v1-test-v61",
            episodes=episodes,
            policy=V61Policy(lookahead=1, future_cap=1, native_future_quota=0),
            runtime_builder=lambda: SimpleNamespace(
                graphiti=graph,
                llm_client=delegate,
                config=SimpleNamespace(max_coroutines=2),
            ),
            instrumentation_installer=lambda *_args: _Recorder.Scope(),
            recorder_factory=_Recorder,
            graph_exporter=exporter,
            output_root=tmp_path / "block",
            authority={"authority_sha256": "c" * 64},
            workload_manifest=manifest,
            frozen_config={"profile_id": "local-qwen3-14b-awq-v1"},
            environment={"profile_id": "local-qwen3-14b-awq-v1"},
            preflight={"status": "PASS"},
        )
    )
    assert result["status"] == "PASS"
    assert result["t_build_ns"] > 0
    assert result["refinement_validation"]["proof"]["provider"]["admission_count"] == 6
    assert result["refinement_validation"]["proof"]["shared_arbiter"]["status"] == "PASS"
    assert result["shadow_db_proof"]["status"] == "PASS"
    assert result["execution_strategy"] == STAGED_EXECUTION_STRATEGY
    assert result["scheduler_evidence"]["execution_strategy"] == STAGED_EXECUTION_STRATEGY
    assert result["scheduler_evidence"]["preparation_stage_barrier"]["status"] == "PASS"
    frontier_events = result["frontier_events"]
    barrier_index = next(
        index
        for index, row in enumerate(frontier_events)
        if row["event"] == "PREPARATION_STAGE_DURABLE"
    )
    assert all(
        row["event"] != "NATIVE_START" for row in frontier_events[:barrier_index]
    )
    assert all(
        row["event"] != "PREPARE_START" for row in frontier_events[barrier_index + 1 :]
    )
    assert len(result["bindings"]) == 6
    assert len(result["context_selection"]) == 12
    assert result["work_inventory"]["certified_context_selection_capture_events"] == 6
    assert result["work_inventory"]["certified_previous_context_chars_removed"] == 0
    assert all(row["prepared_response_hash"] == row["native_response_hash"] for row in result["bindings"])
    assert delegate.calls == 6
    assert verify_seal(tmp_path / "block")["status"] == "PASS"


def test_v61_source_publication_faults_are_atomic_and_exact_once(tmp_path, monkeypatch):
    """Exercise the real V6.1 construction seam with before/after commit faults."""

    async def fake_extract_nodes(clients, episode, *_args):
        await clients.llm_client.generate_response(
            [{"role": "user", "content": episode.name}],
            **_provider_kwargs("extract_nodes.extract_message", episode.group_id),
        )
        return [SimpleNamespace(name="node")], {0: 0}

    async def fake_extract_edges(clients, episode, *_args):
        await clients.llm_client.generate_response(
            [{"role": "user", "content": episode.name}],
            **_provider_kwargs("extract_edges.edge", episode.group_id),
        )
        return [SimpleNamespace(name="edge")]

    modules = {
        name: ModuleType(name)
        for name in (
            "graphiti_core",
            "graphiti_core.utils",
            "graphiti_core.utils.maintenance",
            "graphiti_core.utils.maintenance.edge_operations",
            "graphiti_core.utils.maintenance.node_operations",
        )
    }
    modules["graphiti_core.utils.maintenance.node_operations"].extract_nodes = fake_extract_nodes
    modules["graphiti_core.utils.maintenance.edge_operations"].extract_edges = fake_extract_edges
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        v61_mab,
        "_episode_node",
        lambda episode, *, namespace, uuid_value=None: SimpleNamespace(
            name=episode.name, group_id=namespace, uuid=uuid_value
        ),
    )
    monkeypatch.setattr(v61_mab, "_native_previous_window", lambda *_args: [])

    episodes = (
        EpisodeInput(
            context_id="ctx",
            source_sequence=0,
            episode_id="episode-0",
            reference_time="2026-01-01T00:00:00Z",
            body="message 0",
        ),
    )
    manifest = WorkloadManifest.from_episodes(
        context_id="ctx",
        episodes=episodes,
        dataset_revision="revision",
        dataset_file_sha256="a" * 64,
        expected_episode_count=1,
    )

    class Graph(_Graph):
        def __init__(self, delegate):
            super().__init__(delegate)
            self.publications = 0

        async def add_episode(self, **kwargs):
            self.publications += 1
            return await super().add_episode(**kwargs)

    delegate = _Delegate()
    graph = Graph(delegate)

    def common_kwargs(root):
        return dict(
            run_id="fault-test",
            context_id="ctx",
            namespace="local-qwen3-14b-awq-v1-fault",
            episodes=episodes,
            policy=V61Policy(lookahead=1, future_cap=1, native_future_quota=0),
            runtime_builder=lambda: SimpleNamespace(
                graphiti=graph,
                llm_client=delegate,
                config=SimpleNamespace(max_coroutines=2),
            ),
            instrumentation_installer=lambda *_args: _Recorder.Scope(),
            recorder_factory=_Recorder,
            graph_exporter=lambda *_args: {
                "status": "PASS",
                "canonical_graph_hash": "b" * 64,
            },
            output_root=root,
            authority={"authority_sha256": "c" * 64},
            workload_manifest=manifest,
            frozen_config={"profile_id": "local-qwen3-14b-awq-v1"},
            environment={"profile_id": "local-qwen3-14b-awq-v1"},
            preflight={"status": "PASS"},
        )

    def before_db_write(stage, _sequence, _kwargs):
        if stage == "before_db_write":
            raise RuntimeError("injected before db write")

    with pytest.raises(RuntimeError, match="injected before db write"):
        asyncio.run(
            v61_mab.run_mab_v61_construction_async(
                **common_kwargs(tmp_path / "before"),
                publication_fault_injector=before_db_write,
            )
        )
    assert graph.publications == 0
    begin_journal = tmp_path / ".before.v61_live_events.jsonl"
    begin_events = [json.loads(line) for line in begin_journal.read_text().splitlines()]
    assert any(row.get("event") == "PUBLICATION_BEGIN" for row in begin_events)
    assert not any(row.get("event") == "PUBLICATION_COMMITTED" for row in begin_events)

    def after_commit(stage, _sequence, _kwargs):
        if stage == "after_commit":
            raise RuntimeError("injected crash after commit")

    root = tmp_path / "after"
    with pytest.raises(RuntimeError, match="injected crash after commit"):
        asyncio.run(
            v61_mab.run_mab_v61_construction_async(
                **common_kwargs(root),
                publication_fault_injector=after_commit,
            )
        )
    assert graph.publications == 1
    journal_path = tmp_path / ".after.v61_live_events.jsonl"
    committed = [
        json.loads(line)
        for line in journal_path.read_text().splitlines()
        if json.loads(line).get("event") == "PUBLICATION_COMMITTED"
    ]
    assert len(committed) == 1
    committed_uuid = committed[0]["idempotency_key"]

    result = asyncio.run(v61_mab.run_mab_v61_construction_async(**common_kwargs(root)))
    assert result["status"] == "PASS"
    assert graph.publications == 1
    reused = [
        json.loads(line)
        for line in journal_path.read_text().splitlines()
        if json.loads(line).get("event") == "PUBLICATION_REUSED"
    ]
    assert reused and reused[-1]["idempotency_key"] == committed_uuid
