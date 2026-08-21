from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_2.contracts import EpisodeInput
from saturated_fixed_work_baseline_v1_3.membind_adapter import (
    MEMBIND_COMPILE_WORKERS,
    MEMBIND_GLOBAL_LLM_ADMISSION_K,
    MEMBIND_LOOKAHEAD,
    MemBindAdapterError,
    MemBindExecutionDependencies,
    build_membind_block_spec,
    execute_membind_block,
    normalize_membind_stream_result,
    validate_membind_episodes,
)


def _episodes(namespace: str = "membind-fresh") -> tuple[EpisodeInput, ...]:
    return tuple(
        EpisodeInput(
            history_id="07741c45",
            session_id=f"session-{index}",
            source_sequence=index,
            source_hash=f"{index + 1:064x}",
            reference_time=f"2025-01-01T00:00:{index:02d}+00:00",
            body=f"episode {index}",
            namespace=namespace,
        )
        for index in range(12)
    )


def test_block_spec_freezes_saturated_v31_policy_and_exact_sources() -> None:
    episodes = _episodes()
    spec = build_membind_block_spec(
        run_id="sfwb-v1-3-test-001",
        namespace="membind-fresh",
        cache_salt="fresh-cache-salt",
        source_sha256s=[episode.source_hash for episode in episodes],
    )

    assert spec.method == "MEMBIND_V31"
    assert spec.policy == "FRONTIER_FIRST_CACHE_AFFINITY"
    assert spec.arrival_policy == "SATURATED_ALL_AVAILABLE_AT_T0"
    assert spec.arrival_offsets_ns == (0,) * 12
    assert spec.compile_workers == MEMBIND_COMPILE_WORKERS == 2
    assert spec.lookahead == MEMBIND_LOOKAHEAD == 2
    assert spec.bind_workers == 1
    assert spec.global_llm_admission_k == MEMBIND_GLOBAL_LLM_ADMISSION_K == 2
    assert validate_membind_episodes(episodes, spec) == episodes


@pytest.mark.parametrize("mutation", ["reorder", "source_hash", "namespace"])
def test_episode_identity_drift_is_rejected_before_runtime(mutation: str) -> None:
    episodes = _episodes()
    spec = build_membind_block_spec(
        run_id="sfwb-v1-3-test-001",
        namespace="membind-fresh",
        cache_salt="fresh-cache-salt",
        source_sha256s=[episode.source_hash for episode in episodes],
    )
    selected = list(episodes)
    if mutation == "reorder":
        selected[0], selected[1] = selected[1], selected[0]
    elif mutation == "source_hash":
        selected[0] = replace(selected[0], source_hash="f" * 64)
    else:
        selected[0] = replace(selected[0], namespace="reused-namespace")

    with pytest.raises(MemBindAdapterError, match="MEMBIND_EPISODE_IDENTITY_MISMATCH"):
        validate_membind_episodes(tuple(selected), spec)


def test_stream_result_requires_complete_ordered_publication_and_no_direct_violation() -> None:
    normalized = normalize_membind_stream_result(
        {
            "status": "PASS",
            "source_count": 12,
            "publication_source_sequences": list(range(12)),
            "direct_violation_count": 0,
            "direct_violations": [],
            "scheduler_observation": {
                "max_reserved_compile_count": 2,
                "max_prepared_rob_occupancy": 2,
            },
        },
        source_count=12,
    )

    assert normalized["complete_publication_coverage"] is True
    assert normalized["publication_source_sequences"] == list(range(12))
    assert normalized["direct_violation_count"] == 0

    with pytest.raises(MemBindAdapterError, match="MEMBIND_PUBLICATION_COVERAGE_INVALID"):
        normalize_membind_stream_result(
            {
                "status": "PASS",
                "source_count": 12,
                "publication_source_sequences": list(range(11)),
                "direct_violation_count": 0,
                "direct_violations": [],
            },
            source_count=12,
        )


def test_direct_semantic_violation_is_accounted_without_falsifying_completion() -> None:
    normalized = normalize_membind_stream_result(
        {
            "status": "PASS",
            "source_count": 12,
            "publication_source_sequences": list(range(12)),
            "direct_violation_count": 1,
            "direct_violations": [{"violation": "stale_predecessor_write"}],
        },
        source_count=12,
    )

    assert normalized["complete_publication_coverage"] is True
    assert normalized["direct_violation_count"] == 1
    assert normalized["direct_violations"] == [
        {"violation": "stale_predecessor_write"}
    ]


class _FakeArtifact:
    def __init__(self, source_sequence: int) -> None:
        self.source_sequence = source_sequence

    def to_document(self) -> dict[str, object]:
        return {"source_sequence": self.source_sequence}


class _FakeAdapter:
    def __init__(self, calls: list[tuple[str, int]]) -> None:
        self.calls = calls

    async def prepare(self, compile_input) -> _FakeArtifact:
        sequence = compile_input.source.source_sequence
        self.calls.append(("prepare", sequence))
        return _FakeArtifact(sequence)

    async def bind(self, compile_input, _artifact, *, logical_time_ns: int) -> object:
        assert logical_time_ns >= 0
        sequence = compile_input.source.source_sequence
        self.calls.append(("bind", sequence))
        return {"source_sequence": sequence}


class _FakeRequestClient:
    @asynccontextmanager
    async def frontier_bind_region(self, _stream_id: str, _sequence: int):
        yield

    def observation(self) -> dict[str, object]:
        return {
            "active_count": 0,
            "waiting_count": 0,
            "configured_limit": 2,
            "observed_max_inflight": 2,
            "policy": "FRONTIER_FIRST_CACHE_AFFINITY",
        }


class _FakeRecorder:
    def __init__(self) -> None:
        self.scopes: list[tuple[str, int]] = []

    @contextmanager
    def episode_scope(self, _namespace: str, episode_id: str, sequence: int):
        self.scopes.append((episode_id, sequence))
        yield

    def episode_envelope(
        self, namespace: str, episode_id: str, sequence: int
    ) -> dict[str, object]:
        return {
            "schema_version": "test.trace.v1",
            "run_id": namespace,
            "episode_id": episode_id,
            "source_sequence": sequence,
            "spans": [],
        }


class _FakeHandle:
    def __init__(self) -> None:
        self.restored = False

    def restore(self) -> None:
        self.restored = True


def test_execute_membind_block_reuses_v31_coordinator_with_sfwb_contract(
    tmp_path: Path,
) -> None:
    from paper_eval.membind_v1.graphiti_factories import build_source_log_from_episodes
    from paper_eval.membind_v31.coordinator import run_membind_v31_stream
    from saturated_fixed_work_baseline_v1_3.simple_campaign import (
        _SimpleAttemptStore,
        build_execution_identity,
    )

    episodes = _episodes()
    spec = build_membind_block_spec(
        run_id="sfwb-v1-3-test-001-qualification",
        namespace="membind-fresh",
        cache_salt="fresh-cache-salt",
        source_sha256s=[episode.source_hash for episode in episodes],
    )
    identity = build_execution_identity(
        run_id="sfwb-v1-3-test-001",
        repository_root=tmp_path,
        workload_sha256="a" * 64,
        namespace=spec.namespace,
    )
    calls: list[tuple[str, int]] = []
    closed: list[bool] = []
    published: list[int] = []
    recorder = _FakeRecorder()
    phase_handle = _FakeHandle()
    measurement_handle = _FakeHandle()
    request_client = _FakeRequestClient()
    runtime = SimpleNamespace(graphiti=object(), admitted_llm=request_client)

    def runtime_builder(**kwargs):
        assert kwargs["env"]["CONSTRUCTION_CACHE_SALT"] == spec.cache_salt
        assert spec.policy == "FRONTIER_FIRST_CACHE_AFFINITY"
        assert kwargs["policy"].name == "CACHE_AFFINE"
        return runtime

    async def visibility(_runtime, source) -> bool:
        published.append(source.source_sequence)
        return True

    async def graph_exporter(_graphiti, selected, namespace):
        if not published:
            return {"entities": [], "edges": [], "episodes": []}
        return {
            "entities": [],
            "edges": [],
            "episodes": [
                {
                    "source_sequence": episode.source_sequence,
                    "source_hash": episode.source_hash,
                    "session_id": episode.session_id,
                    "group_id": namespace,
                }
                for episode in selected
            ],
        }

    hooks = SimpleNamespace(
        runtime_builder=runtime_builder,
        runtime_ready=lambda _runtime: asyncio.sleep(0),
        namespace_probe=lambda _runtime, _namespace: asyncio.sleep(
            0,
            result={
                "node_count": 0,
                "relationship_count": 0,
                "episode_names": [],
            },
        ),
        reference_time_to_ns=lambda value: int(value[17:19]) * 1_000_000_000,
        adapter_factory=lambda _runtime, _certification: _FakeAdapter(calls),
        source_visibility_probe=visibility,
        close_runtime=lambda _runtime: asyncio.sleep(0, result=closed.append(True)),
    )
    certification = SimpleNamespace(
        certification_sha256="b" * 64,
        verify=lambda: certification,
    )
    live_dependencies = SimpleNamespace(
        graph_exporter=graph_exporter,
        recorder_factory=lambda: recorder,
        instrumentation_installer=lambda _graphiti, _recorder: phase_handle,
        measurement_installer=lambda _graphiti, _recorder: measurement_handle,
        service_idle=lambda: True,
        sampler_factory=None,
    )
    dependencies = MemBindExecutionDependencies(
        hooks=hooks,
        certification=certification,
        live_dependencies=live_dependencies,
        source_log_builder=build_source_log_from_episodes,
        coordinator=run_membind_v31_stream,
        attempt_store_factory=_SimpleAttemptStore.create,
    )

    result = asyncio.run(
        execute_membind_block(
            repository_root=tmp_path,
            run_root=tmp_path / "qualification",
            spec=spec,
            identity=identity,
            episodes=episodes,
            source_tokens=24610,
            env={"MEMBIND_V31_TRACE_HMAC_KEY": "c" * 64},
            dependencies=dependencies,
        )
    )

    assert result["valid"] is True
    assert result["method"] == "MEMBIND_V31"
    assert result["publication_source_sequences"] == list(range(12))
    assert result["episode_count"] == 12
    assert result["source_tokens"] == 24610
    assert "feeder_workload_await_count" not in result
    assert calls.count(("prepare", 0)) == 1
    assert calls.count(("bind", 11)) == 1
    assert len(recorder.scopes) == 24
    assert phase_handle.restored is True
    assert measurement_handle.restored is True
    assert closed == [True]

    attempt_root = Path(result["attempt_root"])
    assert (attempt_root / "canonical_graph.json").is_file()
    assert (attempt_root / "block_metrics.json").is_file()
    assert (attempt_root / "seal.json").is_file()
    manifest = json.loads((attempt_root / "manifest.json").read_text())
    assert manifest["arrival_offsets_ns"] == [0] * 12
    assert manifest["source_sha256s"] == [episode.source_hash for episode in episodes]
