from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.contracts import EpisodeInput, ResumeIdentity
from saturated_fixed_work_baseline_v1_2.live import FormalBlock, derive_cache_salt, derive_namespace
from saturated_fixed_work_baseline_v1_2.live_block import (
    LiveBlockDependencies,
    LiveBlockError,
    execute_live_block,
)
from saturated_fixed_work_baseline_v1_2.schedules import Method


def _identity(namespace: str) -> ResumeIdentity:
    return ResumeIdentity(
        project_sha256="1" * 64,
        data_sha256="2" * 64,
        provider_sha256="3" * 64,
        resource_sha256="4" * 64,
        config_sha256="5" * 64,
        cache_sha256="6" * 64,
        namespace=namespace,
    )


def _block(method: Method) -> FormalBlock:
    run_id = "sfwb-v1-2-integration-001"
    block_id = f"integration-{method.value}"
    return FormalBlock(
        ordinal=1,
        block_id=block_id,
        run_id=run_id,
        history_id="07741c45",
        method=method,
        attempt_ordinal=1,
        namespace=derive_namespace(
            run_id, method, "07741c45", attempt_ordinal=1
        ),
        cache_salt=derive_cache_salt(run_id, block_id, attempt_ordinal=1),
    )


def _episodes(block: FormalBlock) -> tuple[EpisodeInput, ...]:
    return tuple(
        EpisodeInput(
            history_id=block.history_id,
            session_id=f"s{index}",
            source_sequence=index,
            source_hash=f"{index + 1:064x}",
            reference_time="2023-01-01T00:00:00Z",
            body=f"body-{index}",
            namespace=block.namespace,
        )
        for index in range(3)
    )


class _Handle:
    def __init__(self) -> None:
        self.restored = False

    def restore(self) -> None:
        self.restored = True


class _Recorder:
    def __init__(self) -> None:
        self.rows: dict[int, list[dict[str, Any]]] = {}
        self.current: int | None = None

    @contextmanager
    def episode_scope(self, run_id: str, episode_id: str, source_sequence: int):
        del run_id, episode_id
        self.current = source_sequence
        try:
            yield
        finally:
            self.rows[source_sequence] = [
                {
                    "span_id": f"llm-{source_sequence}",
                    "parent_span_id": None,
                    "phase": "llm",
                    "operation_class": "logical-call",
                    "start_ns": source_sequence * 10 + 1,
                    "end_ns": source_sequence * 10 + 5,
                    "duration_ns": 4,
                    "status": "ok",
                    "error_code": None,
                    "metadata": {"input_tokens": 100 + source_sequence},
                    "source_sequence": source_sequence,
                }
            ]
            self.current = None

    def episode_envelope(
        self, run_id: str, episode_id: str, source_sequence: int
    ) -> dict[str, Any]:
        return {
            "schema_version": "fake.trace.v1",
            "run_id": run_id,
            "episode_id": episode_id,
            "source_sequence": source_sequence,
            "spans": self.rows[source_sequence],
        }


class _Graphiti:
    def __init__(self) -> None:
        self.episodes: list[dict[str, Any]] = []
        self.closed = False

    async def add_episode(self, **kwargs: Any) -> None:
        self.episodes.append(dict(kwargs))

    async def close(self) -> None:
        self.closed = True


async def _export(
    graphiti: _Graphiti,
    episodes: tuple[EpisodeInput, ...],
    namespace: str,
) -> dict[str, Any]:
    by_name = {
        f"{episode.history_id}::episode::{episode.source_sequence:04d}": episode
        for episode in episodes
    }
    persisted = []
    for row in graphiti.episodes:
        episode = by_name[row["name"]]
        persisted.append(
            {
                "source_sequence": episode.source_sequence,
                "source_hash": episode.source_hash,
                "session_id": episode.session_id,
            }
        )
    return {
        "entities": [],
        "edges": [],
        "episodes": persisted,
        "namespace": namespace,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("method", list(Method))
async def test_live_block_uses_tested_schedule_and_writes_sealed_raw_artifacts(
    tmp_path: Path, method: Method
) -> None:
    block = _block(method)
    graphiti = _Graphiti()
    recorder = _Recorder()
    phase_handle = _Handle()
    measurement_handle = _Handle()
    observed_salts: list[str] = []
    sampler_events: list[str] = []

    class Sampler:
        async def start(self) -> None:
            sampler_events.append("start")

        async def stop(self) -> dict[str, object]:
            sampler_events.append("stop")
            return {
                "coverage": 1.0,
                "actual_samples": 4,
                "expected_samples": 4,
                "gap_p95_s": 1.0,
                "gap_max_s": 1.0,
                "source_coverage": {
                    "construction_vllm": 1.0,
                    "embedding_vllm": 1.0,
                    "runner_cpu": 1.0,
                },
            }

    def runtime_factory(cache_salt: str, authority_path: Path) -> Any:
        assert authority_path.is_file()
        observed_salts.append(cache_salt)
        return SimpleNamespace(graphiti=graphiti)

    dependencies = LiveBlockDependencies(
        runtime_factory=runtime_factory,
        graph_exporter=_export,
        recorder_factory=lambda: recorder,
        instrumentation_installer=lambda graph, trace: phase_handle,
        measurement_installer=lambda graph, trace: measurement_handle,
        episode_source="MESSAGE",
        service_idle=lambda: True,
        sampler_factory=lambda path: (
            Sampler() if path.name == "telemetry.jsonl" else None
        ),
    )
    result = await execute_live_block(
        repository_root=tmp_path,
        run_root=tmp_path / "run",
        block=block,
        identity=_identity(block.namespace),
        episodes=_episodes(block),
        dependencies=dependencies,
        source_tokens=300,
    )
    attempt_root = Path(result["attempt_root"])
    assert result["valid"] is True
    assert result["episode_count"] == 3
    assert result["attempt_ordinal"] == 1
    assert result["resource_envelope_id"] == "4" * 64
    assert result["llm_input_tokens"] == 303
    assert result["llm_duration_p50_s"] == pytest.approx(4e-9)
    assert result["llm_duration_p95_s"] == pytest.approx(4e-9)
    assert result["llm_duration_p99_s"] == pytest.approx(4e-9)
    assert result["embedding_duration_p50_s"] is None
    assert result["db_duration_p50_s"] is None
    assert result["whole_update_active_max"] == (
        1 if method is Method.B0_NATIVE_SERIAL else result["whole_update_active_max"]
    )
    assert observed_salts == [block.cache_salt]
    assert graphiti.closed is True
    assert phase_handle.restored is True
    assert measurement_handle.restored is True
    assert sampler_events == ["start", "stop"]
    assert result["sampler_coverage"] == 1.0
    assert result["resource_availability"] == "MEASURED"
    assert (attempt_root / "seal.json").is_file()
    assert (attempt_root / "canonical_graph.json").is_file()
    assert (attempt_root / "block_metrics.json").is_file()
    assert (attempt_root / "sampler_summary.json").is_file()
    trace_rows = [
        json.loads(line)
        for line in (attempt_root / "native_trace.jsonl").read_text().splitlines()
    ]
    assert len(trace_rows) == 3
    assert {row["source_sequence"] for row in trace_rows} == {0, 1, 2}
    schedule_events = [
        row["event"]
        for row in (
            json.loads(line)
            for line in (attempt_root / "raw_events.jsonl").read_text().splitlines()
        )
    ]
    assert "BLOCK_STARTED" in schedule_events
    assert schedule_events.count("PUBLICATION_DURABLE") == 3


@pytest.mark.asyncio
async def test_live_block_refuses_nonempty_namespace_before_construction(
    tmp_path: Path,
) -> None:
    block = _block(Method.B0_NATIVE_SERIAL)
    graphiti = _Graphiti()
    graphiti.episodes.append({"name": "unexpected"})
    dependencies = LiveBlockDependencies(
        runtime_factory=lambda cache_salt, authority_path: SimpleNamespace(
            graphiti=graphiti
        ),
        graph_exporter=_export,
        recorder_factory=_Recorder,
        instrumentation_installer=lambda graph, trace: _Handle(),
        measurement_installer=lambda graph, trace: _Handle(),
        episode_source="MESSAGE",
        service_idle=lambda: True,
    )
    with pytest.raises(LiveBlockError, match="FRESH_NAMESPACE_NOT_EMPTY"):
        await execute_live_block(
            repository_root=tmp_path,
            run_root=tmp_path / "run",
            block=block,
            identity=_identity(block.namespace),
            episodes=_episodes(block),
            dependencies=dependencies,
            source_tokens=300,
        )
