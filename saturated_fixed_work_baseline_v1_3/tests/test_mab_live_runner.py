from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.mab_live_runner import (
    _mab_graphiti_kwargs,
    _mab_publication_idempotency_key,
    episode_from_input,
    resolve_runtime_builder,
    run_mab_construction_async,
)
from saturated_fixed_work_baseline_v1_3.workload_contract import EpisodeInput, WorkloadManifest
from saturated_fixed_work_baseline_v1_3.membind_v6_1.structured_output_recovery import reliability_identity


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
    assert {
        key: b0[key] for key in reliability_identity()
    } == reliability_identity()
    assert {
        key: b1[key] for key in reliability_identity()
    } == reliability_identity()


def test_fresh_graphiti_uuid_lookup_and_v61_publication_identity_are_separate() -> None:
    """RED proof for Graphiti's fresh UUID lookup semantics and V6.1 keys."""

    from graphiti_core.errors import NodeNotFoundError
    from graphiti_core.nodes import EpisodicNode

    class FreshDriver:
        graph_operations_interface = None
        provider = None

        async def execute_query(self, *_args, **_kwargs):
            return [], None, None

    fresh_uuid = "00000000-0000-4000-8000-000000000001"
    with pytest.raises(NodeNotFoundError):
        asyncio.run(EpisodicNode.get_by_uuid(FreshDriver(), fresh_uuid))

    episode = EpisodeInput(
        context_id="uuid-proof",
        source_sequence=0,
        episode_id="uuid-proof-0",
        reference_time="2026-01-01T00:00:00Z",
        body="proof",
    )
    projected = episode_from_input(episode)
    strict_kwargs = _mab_graphiti_kwargs(projected, namespace="uuid-proof", include_uuid=False)
    v61_kwargs = _mab_graphiti_kwargs(projected, namespace="uuid-proof", include_uuid=False)
    graphiti_uuid = _mab_graphiti_kwargs(projected, namespace="uuid-proof", include_uuid=True)["uuid"]
    idempotency_key = _mab_publication_idempotency_key(projected, namespace="uuid-proof")

    assert "uuid" not in strict_kwargs
    assert "uuid" not in v61_kwargs
    assert idempotency_key != graphiti_uuid
    assert idempotency_key.startswith("membind-idempotency:")


def test_canonical_native_arms_omit_uuid_and_disable_resume(tmp_path: Path) -> None:
    episodes, manifest = _workload(tmp_path, count=1)

    class RecordingGraph(_FakeGraph):
        def __init__(self) -> None:
            super().__init__()
            self.kwargs: list[dict[str, object]] = []

        async def add_episode(self, **kwargs):
            self.kwargs.append(dict(kwargs))
            return await super().add_episode(**kwargs)

    for method in ("GRAPHITI_UPSTREAM_SERIAL", "RELAXED_ORDER_PARALLEL"):
        graph = RecordingGraph()
        root = tmp_path / method
        result = asyncio.run(
            run_mab_construction_async(
                method=method,
                run_id="canonical-native",
                context_id="ctx-0",
                namespace=f"ns-{method.lower()}",
                episodes=episodes,
                runtime_builder=lambda graph=graph: SimpleNamespace(graphiti=graph, llm_client=object()),
                instrumentation_installer=lambda *_args: _FakeRecorder._Scope(),
                recorder_factory=_FakeRecorder,
                graph_exporter=lambda *_args: {"status": "PASS", "canonical_graph_hash": "d" * 64},
                output_root=root,
                authority={"authority_sha256": "b" * 64},
                workload_manifest=manifest,
                frozen_config={"config_sha256": "c" * 64},
            )
        )
        assert graph.kwargs and "uuid" not in graph.kwargs[0]
        assert result["publication_guarantee"] == "UPSTREAM_GRAPHITI_NO_RESUME"
        assert result["method"] == method


def test_legacy_b0_and_b1_resume_only_after_durable_local_commit(tmp_path: Path) -> None:
    episodes, manifest = _workload(tmp_path, count=1)

    class FaultGraph(_FakeGraph):
        def __init__(self) -> None:
            super().__init__()
            self.publications = 0

        async def add_episode(self, **kwargs):
            self.publications += 1
            return await super().add_episode(**kwargs)

    def run(method: str, root: Path, graph: FaultGraph, injector=None):
        return asyncio.run(
            run_mab_construction_async(
                method=method,
                run_id="fault-run",
                context_id="ctx-0",
                namespace=f"fault-{method.lower()}",
                episodes=episodes,
                runtime_builder=lambda: SimpleNamespace(graphiti=graph, llm_client=object()),
                instrumentation_installer=lambda *_args: _FakeRecorder._Scope(),
                recorder_factory=_FakeRecorder,
                graph_exporter=lambda *_args: {"status": "PASS", "canonical_graph_hash": "d" * 64},
                output_root=root,
                authority={"authority_sha256": "b" * 64},
                workload_manifest=manifest,
                frozen_config={"config_sha256": "c" * 64},
                publication_fault_injector=injector,
            )
        )

    for method in ("B0", "B1"):
        before_graph = FaultGraph()

        def before(stage, _sequence, _kwargs):
            if stage == "before_db_write":
                raise RuntimeError("before-write")

        with pytest.raises(RuntimeError, match="before-write"):
            run(method, tmp_path / f"{method}-before", before_graph, before)
        assert before_graph.publications == 0

        after_graph = FaultGraph()

        def after(stage, _sequence, _kwargs):
            if stage == "after_commit":
                raise RuntimeError("after-commit")

        root = tmp_path / f"{method}-after"
        with pytest.raises(RuntimeError, match="after-commit"):
            run(method, root, after_graph, after)
        assert after_graph.publications == 1
        result = run(method, root, after_graph)
        assert result["status"] == "PASS"
        assert after_graph.publications == 1


def test_database_commit_before_journal_is_at_least_once_with_stable_key(tmp_path: Path) -> None:
    episodes, manifest = _workload(tmp_path, count=1)

    class CommitGraph(_FakeGraph):
        def __init__(self) -> None:
            super().__init__()
            self.publications = 0

        async def add_episode(self, **kwargs):
            self.publications += 1
            return await super().add_episode(**kwargs)

    graph = CommitGraph()

    def crash_between_db_and_journal(stage, _sequence, _kwargs):
        if stage == "after_db_commit_before_journal":
            raise RuntimeError("database committed before journal")

    def run(root: Path):
        return asyncio.run(
            run_mab_construction_async(
                method="B0",
                run_id="db-journal-window",
                context_id="ctx-0",
                namespace="db-journal-window",
                episodes=episodes,
                runtime_builder=lambda: SimpleNamespace(graphiti=graph, llm_client=object()),
                instrumentation_installer=lambda *_args: _FakeRecorder._Scope(),
                recorder_factory=_FakeRecorder,
                graph_exporter=lambda *_args: {"status": "PASS", "canonical_graph_hash": "d" * 64},
                output_root=root,
                authority={"authority_sha256": "b" * 64},
                workload_manifest=manifest,
                frozen_config={"config_sha256": "c" * 64},
            )
        )

    with pytest.raises(RuntimeError, match="database committed"):
        asyncio.run(
            run_mab_construction_async(
                method="B0",
                run_id="db-journal-window",
                context_id="ctx-0",
                namespace="db-journal-window",
                episodes=episodes,
                runtime_builder=lambda: SimpleNamespace(graphiti=graph, llm_client=object()),
                instrumentation_installer=lambda *_args: _FakeRecorder._Scope(),
                recorder_factory=_FakeRecorder,
                graph_exporter=lambda *_args: {"status": "PASS", "canonical_graph_hash": "d" * 64},
                output_root=tmp_path / "db-before-journal",
                authority={"authority_sha256": "b" * 64},
                workload_manifest=manifest,
                frozen_config={"config_sha256": "c" * 64},
                publication_fault_injector=crash_between_db_and_journal,
            )
        )
    result = run(tmp_path / "db-before-journal")
    assert result["publication_guarantee"] == "AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY"
    assert graph.publications == 2


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
