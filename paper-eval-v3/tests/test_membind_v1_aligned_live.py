"""TDD contracts for isolated MemBind-v1 aligned live composition.

The tests use only local fakes.  They prove the composition consumes a
verified aligned block, keeps all three methods in fresh namespaces, records
the common durable lifecycle, and dispatches exactly one native or node-only
execution path without contacting Graphiti, Neo4j, or either model service.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.membind_v1.aligned_artifacts import inspect_aligned_block_artifacts
from paper_eval.membind_v1.aligned_live import (
    AlignedLiveBlockError,
    AlignedLiveHooks,
    execute_aligned_live_block,
)
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.delta import PreparedNodeArtifact
from paper_eval.membind_v1.graphiti_adapter import NodeArtifactIdentity
from paper_eval.membind_v1.store import inspect_membind_v1_attempt


@dataclass(frozen=True)
class _Episode:
    source_sequence: int
    source_hash: str
    reference_time: str
    body: str
    group_id: str = "unscoped"

    @property
    def name(self) -> str:
        return f"episode::{self.source_sequence:04d}"


@dataclass
class _Runtime:
    execution_envelope_sha256: str
    graphiti: object


class _State:
    def __init__(self, *, envelope: str) -> None:
        self.envelope = envelope
        self.namespaces: dict[str, list[str]] = {}
        self.native_calls: list[tuple[int, str, str]] = []
        self.membind_prepare_calls: list[int] = []
        self.membind_bind_calls: list[tuple[int, int]] = []
        self.closed = 0
        self.ready = 0
        self.fail_native_sequence: int | None = None


def _plan() -> dict[str, object]:
    sources = {
        history_id: [
            f"{history_index + 1:032x}{source_index + 1:032x}"
            for source_index in range(2)
        ]
        for history_index, history_id in enumerate(ALIGNED_DEVELOPMENT_HISTORIES)
    }
    return verify_aligned_development_plan(
        build_aligned_development_plan(
            aligned_run_id="aligned-live-test-001",
            history_source_sha256s=sources,
            interarrival_ns=0,
            shared_execution_envelope_sha256="d" * 64,
        )
    )


def _episodes(plan: dict[str, object], *, history_id: str) -> tuple[_Episode, ...]:
    raw = plan["history_source_sha256s"]
    assert isinstance(raw, dict)
    hashes = raw[history_id]
    assert isinstance(hashes, list)
    return tuple(
        _Episode(
            source_sequence=index,
            source_hash=str(source_hash),
            reference_time=f"2026-01-0{index + 1}T00:00:00+00:00",
            body=f"private episode {index}",
        )
        for index, source_hash in enumerate(hashes)
    )


def _identity() -> NodeArtifactIdentity:
    return NodeArtifactIdentity(
        operation_identity_sha256="1" * 64,
        model_identity_sha256="2" * 64,
        prompt_identity_sha256="3" * 64,
        schema_identity_sha256="4" * 64,
        config_identity_sha256="5" * 64,
    )


def _hooks(state: _State, *, identity: NodeArtifactIdentity) -> AlignedLiveHooks:
    def runtime_builder(*, env, admission, request_id_prefix):
        assert env == {"public": "test"}
        assert admission.limit == 2
        assert request_id_prefix.startswith("aligned-live-test-001:")
        return _Runtime(
            execution_envelope_sha256=state.envelope,
            graphiti=SimpleNamespace(driver=SimpleNamespace()),
        )

    async def runtime_ready(runtime: _Runtime) -> None:
        assert runtime.execution_envelope_sha256 == state.envelope
        state.ready += 1

    async def namespace_probe(runtime: _Runtime, namespace: str) -> dict[str, object]:
        del runtime
        names = list(state.namespaces.get(namespace, ()))
        return {
            "node_count": len(names),
            "relationship_count": 0,
            "episode_names": names,
        }

    def namespace_episode(episode: _Episode, namespace: str) -> _Episode:
        return replace(episode, group_id=namespace)

    async def native_add_episode(
        runtime: _Runtime, episode: _Episode, source: object
    ) -> None:
        del runtime
        assert episode.group_id
        assert episode.group_id == getattr(source, "group_id")
        state.native_calls.append(
            (episode.source_sequence, episode.group_id, str(getattr(source, "episode_uuid")))
        )
        if state.fail_native_sequence == episode.source_sequence:
            raise RuntimeError("fake native failure")
        await asyncio.sleep(0)
        state.namespaces.setdefault(episode.group_id, []).append(episode.name)

    class _Adapter:
        async def prepare(self, compile_input):
            state.membind_prepare_calls.append(compile_input.source.source_sequence)
            return PreparedNodeArtifact.create(
                source_sequence=compile_input.source.source_sequence,
                source_sha256=compile_input.source.source_sha256,
                evidence_prefix_sha256=compile_input.evidence.evidence_prefix_sha256,
                episode_projection_sha256=compile_input.source.episode_projection_sha256,
                operation_identity_sha256=identity.operation_identity_sha256,
                model_identity_sha256=identity.model_identity_sha256,
                prompt_identity_sha256=identity.prompt_identity_sha256,
                schema_identity_sha256=identity.schema_identity_sha256,
                config_identity_sha256=identity.config_identity_sha256,
                extracted_nodes=[],
                node_episode_index_map={},
            )

        async def bind(self, compile_input, artifact, *, logical_time_ns):
            assert artifact.source_sequence == compile_input.source.source_sequence
            state.membind_bind_calls.append((compile_input.source.source_sequence, logical_time_ns))
            state.namespaces.setdefault(compile_input.source.group_id, []).append(
                str(compile_input.source.episode_projection["name"])
            )
            return {"ok": True}

    def membind_adapter_factory(runtime, source_log, artifact_identity):
        assert runtime.execution_envelope_sha256 == state.envelope
        assert source_log.source_count == 2
        assert artifact_identity == identity
        return _Adapter()

    async def close_runtime(runtime: _Runtime) -> None:
        assert runtime.execution_envelope_sha256 == state.envelope
        state.closed += 1

    return AlignedLiveHooks(
        runtime_builder=runtime_builder,
        runtime_ready=runtime_ready,
        namespace_probe=namespace_probe,
        namespace_episode=namespace_episode,
        native_add_episode=native_add_episode,
        reference_time_to_ns=lambda value: int(value[8:10]) * 1_000_000_000,
        membind_adapter_factory=membind_adapter_factory,
        close_runtime=close_runtime,
    )


@pytest.mark.asyncio
async def test_aligned_u0_creates_only_a_fresh_block_and_keeps_source_record_identity_internal(
    tmp_path: Path,
) -> None:
    plan = _plan()
    state = _State(envelope="d" * 64)
    result = await execute_aligned_live_block(
        verified_plan=plan,
        block_index=0,
        episodes=_episodes(plan, history_id="07741c45"),
        env={"public": "test"},
        block_root=tmp_path / "u0",
        execution_identity_sha256="e" * 64,
        hooks=_hooks(state, identity=_identity()),
    )

    inspected = inspect_aligned_block_artifacts(tmp_path / "u0")
    assert result["status"] == "PASS"
    assert result["method"] == "U0-aligned"
    assert result["source_count"] == 2
    assert result["admission_observation"]["configured_request_limit"] == 2
    assert [event["event_type"] for event in inspected["events"]] == [
        "ARRIVAL",
        "ENQUEUED",
        "SERVICE_STARTED",
        "PUBLICATION_DURABLE",
    ] * 2
    assert inspected["checkpoint"]["complete_coverage"] is True
    assert [item[0] for item in state.native_calls] == [0, 1]
    assert all(item[1] == result["namespace"] for item in state.native_calls)
    # The source UUID remains an internal immutable-artifact identity.  Native
    # Graphiti receives no UUID because add_episode(uuid=...) is a lookup,
    # rather than a fresh-node creation API in pinned Graphiti 0.29.3.
    assert len({item[2] for item in state.native_calls}) == 2
    assert state.ready == 1
    assert state.closed == 1


@pytest.mark.asyncio
async def test_aligned_p_c2_uses_the_same_frozen_arrival_trace_and_durable_lifecycle(
    tmp_path: Path,
) -> None:
    plan = _plan()
    state = _State(envelope="d" * 64)
    result = await execute_aligned_live_block(
        verified_plan=plan,
        block_index=1,
        episodes=_episodes(plan, history_id="07741c45"),
        env={"public": "test"},
        block_root=tmp_path / "pc2",
        execution_identity_sha256="e" * 64,
        hooks=_hooks(state, identity=_identity()),
    )

    inspected = inspect_aligned_block_artifacts(tmp_path / "pc2")
    assert result["method"] == "P(C=2)-aligned"
    assert result["schedule"]["arrival_offsets_ns"] == [0, 0]
    assert result["schedule"]["configured_worker_count"] == 2
    assert inspected["checkpoint"]["complete_coverage"] is True
    assert len(state.native_calls) == 2


@pytest.mark.asyncio
async def test_aligned_membind_creates_a_separate_durable_attempt_and_shared_public_lifecycle(
    tmp_path: Path,
) -> None:
    plan = _plan()
    state = _State(envelope="d" * 64)
    result = await execute_aligned_live_block(
        verified_plan=plan,
        block_index=2,
        episodes=_episodes(plan, history_id="07741c45"),
        env={"public": "test"},
        block_root=tmp_path / "mv1",
        execution_identity_sha256="e" * 64,
        hooks=_hooks(state, identity=_identity()),
        membind_artifact_identity=_identity(),
    )

    inspected = inspect_aligned_block_artifacts(tmp_path / "mv1")
    attempt = inspect_membind_v1_attempt(Path(str(result["membind_attempt_root"])))
    assert result["method"] == "MemBind-v1 node-only"
    assert result["runner"]["status"] == "PASS"
    assert attempt["checkpoint"]["status"] == "complete"
    assert inspected["checkpoint"]["complete_coverage"] is True
    assert state.membind_prepare_calls == [0, 1]
    assert [item[0] for item in state.membind_bind_calls] == [0, 1]
    assert all(item[1] > 0 for item in state.membind_bind_calls)
    assert state.native_calls == []


@pytest.mark.asyncio
async def test_live_composition_rejects_envelope_or_fresh_namespace_drift_before_creating_artifacts(
    tmp_path: Path,
) -> None:
    plan = _plan()
    identity = _identity()
    wrong_envelope = _State(envelope="f" * 64)
    with pytest.raises(AlignedLiveBlockError, match="execution envelope"):
        await execute_aligned_live_block(
            verified_plan=plan,
            block_index=0,
            episodes=_episodes(plan, history_id="07741c45"),
            env={"public": "test"},
            block_root=tmp_path / "wrong-envelope",
            execution_identity_sha256="e" * 64,
            hooks=_hooks(wrong_envelope, identity=identity),
        )
    assert not (tmp_path / "wrong-envelope").exists()
    assert wrong_envelope.closed == 1

    nonempty = _State(envelope="d" * 64)
    nonempty.namespaces["pev3-aligned-live-test-001-u0-07741c45-a001"] = ["stale"]
    with pytest.raises(AlignedLiveBlockError, match="fresh namespace"):
        await execute_aligned_live_block(
            verified_plan=plan,
            block_index=0,
            episodes=_episodes(plan, history_id="07741c45"),
            env={"public": "test"},
            block_root=tmp_path / "nonempty",
            execution_identity_sha256="e" * 64,
            hooks=_hooks(nonempty, identity=identity),
        )
    assert not (tmp_path / "nonempty").exists()
    assert nonempty.closed == 1


@pytest.mark.asyncio
async def test_live_u0_failure_is_durably_sealed_non_mergeable_without_retrying_the_namespace(
    tmp_path: Path,
) -> None:
    plan = _plan()
    state = _State(envelope="d" * 64)
    state.fail_native_sequence = 0

    with pytest.raises(AlignedLiveBlockError, match="execution failed"):
        await execute_aligned_live_block(
            verified_plan=plan,
            block_index=0,
            episodes=_episodes(plan, history_id="07741c45"),
            env={"public": "test"},
            block_root=tmp_path / "failed-u0",
            execution_identity_sha256="e" * 64,
            hooks=_hooks(state, identity=_identity()),
        )

    inspected = inspect_aligned_block_artifacts(tmp_path / "failed-u0")
    assert [event["event_type"] for event in inspected["events"]] == [
        "ARRIVAL",
        "ENQUEUED",
        "SERVICE_STARTED",
        "TERMINAL_FAILURE",
    ]
    assert inspected["checkpoint"]["terminal_status"] == "INCOMPLETE_NON_MERGEABLE"
    assert inspected["checkpoint"]["resume_status"] == "TERMINAL_FAILURE_NON_MERGEABLE"
    assert state.closed == 1
