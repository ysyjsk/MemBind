"""TDD for the isolated 3-5 episode MemBind-v1 live smoke stage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.membind_v1.aligned_live import AlignedLiveHooks
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.delta import PreparedNodeArtifact
from paper_eval.membind_v1.graphiti_adapter import NodeArtifactIdentity
from paper_eval.membind_v1.smoke import (
    MemBindV1SmokeError,
    inspect_membind_v1_smoke,
    run_membind_v1_smoke,
)


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
    def __init__(self) -> None:
        self.namespaces: dict[str, list[str]] = {}
        self.prepare_calls: list[int] = []
        self.bind_calls: list[int] = []
        self.fail_bind_sequence: int | None = None
        self.closed = 0


def _identity() -> NodeArtifactIdentity:
    return NodeArtifactIdentity(
        operation_identity_sha256="1" * 64,
        model_identity_sha256="2" * 64,
        prompt_identity_sha256="3" * 64,
        schema_identity_sha256="4" * 64,
        config_identity_sha256="5" * 64,
    )


def _formal_plan() -> dict[str, object]:
    return verify_aligned_development_plan(
        build_aligned_development_plan(
            aligned_run_id="aligned-formal-smoke-source-001",
            history_source_sha256s={
                history_id: [
                    f"{history_index + 1:032x}{sequence + 1:032x}"
                    for sequence in range(6)
                ]
                for history_index, history_id in enumerate(
                    ALIGNED_DEVELOPMENT_HISTORIES
                )
            },
            interarrival_ns=0,
            shared_execution_envelope_sha256="d" * 64,
        )
    )


def _episodes(plan: dict[str, object], *, history_id: str) -> tuple[_Episode, ...]:
    hashes = plan["history_source_sha256s"][history_id]
    return tuple(
        _Episode(
            source_sequence=sequence,
            source_hash=str(source_hash),
            reference_time=f"2026-01-{sequence + 1:02d}T00:00:00+00:00",
            body=f"private smoke episode {sequence}",
        )
        for sequence, source_hash in enumerate(hashes)
    )


def _hooks(state: _State, *, identity: NodeArtifactIdentity) -> AlignedLiveHooks:
    def runtime_builder(*, env, admission, request_id_prefix):
        assert env == {"public": "smoke-test"}
        assert admission.limit == 2
        assert request_id_prefix.startswith("aligned-smoke-")
        return _Runtime(
            execution_envelope_sha256="d" * 64,
            graphiti=SimpleNamespace(driver=SimpleNamespace()),
        )

    async def runtime_ready(_runtime: _Runtime) -> None:
        return None

    async def namespace_probe(
        _runtime: _Runtime, namespace: str
    ) -> dict[str, object]:
        names = list(state.namespaces.get(namespace, ()))
        return {
            "node_count": len(names),
            "relationship_count": 0,
            "episode_names": names,
        }

    def namespace_episode(episode: _Episode, namespace: str) -> _Episode:
        return replace(episode, group_id=namespace)

    async def native_add_episode(*_args: object) -> None:
        raise AssertionError("smoke must execute only MemBind-v1")

    class _Adapter:
        async def prepare(self, compile_input):
            state.prepare_calls.append(compile_input.source.source_sequence)
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
            del artifact, logical_time_ns
            sequence = compile_input.source.source_sequence
            state.bind_calls.append(sequence)
            if state.fail_bind_sequence == sequence:
                raise RuntimeError("fake smoke bind failure")
            state.namespaces.setdefault(compile_input.source.group_id, []).append(
                str(compile_input.source.episode_projection["name"])
            )
            return {"ok": True}

    def membind_adapter_factory(_runtime, source_log, artifact_identity):
        assert source_log.source_count in {3, 4, 5}
        assert artifact_identity == identity
        return _Adapter()

    async def close_runtime(_runtime: _Runtime) -> None:
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
async def test_smoke_runs_only_first_three_sources_in_a_new_namespace_and_persists_result(
    tmp_path: Path,
) -> None:
    formal_plan = _formal_plan()
    state = _State()
    root = tmp_path / "smoke-attempt"

    result = await run_membind_v1_smoke(
        root,
        smoke_run_id="smoke-test-001",
        formal_verified_plan=formal_plan,
        history_id="07741c45",
        episodes=_episodes(formal_plan, history_id="07741c45"),
        sample_count=3,
        env={"public": "smoke-test"},
        execution_identity_sha256="e" * 64,
        membind_artifact_identity=_identity(),
        hooks=_hooks(state, identity=_identity()),
    )

    inspected = inspect_membind_v1_smoke(root)
    assert result["status"] == "PASS"
    assert result["source_count"] == 3
    assert result["method"] == "MemBind-v1 node-only"
    assert result["execution_identity_sha256"] == "e" * 64
    assert result["membind_artifact_identity_sha256"] == inspected["manifest"][
        "membind_artifact_identity_sha256"
    ]
    assert result["namespace"].startswith("pev3-aligned-smoke-test-001-mv1-")
    assert inspected["checkpoint"]["status"] == "COMPLETED"
    assert inspected["checkpoint"]["resume_status"] == "NOT_NEEDED_COMPLETE"
    assert inspected["result"] == result
    assert inspected["smoke_plan"]["history_source_sha256s"]["07741c45"] == (
        formal_plan["history_source_sha256s"]["07741c45"][:3]
    )
    assert state.prepare_calls == [0, 1, 2]
    assert state.bind_calls == [0, 1, 2]
    assert state.closed == 1
    assert (root / "aligned-block" / "checkpoint.json").is_file()
    assert (root / "SMOKE_RESULT.json").is_file()


@pytest.mark.asyncio
async def test_failed_live_smoke_is_durable_non_reusable_and_cannot_retry_in_place(
    tmp_path: Path,
) -> None:
    formal_plan = _formal_plan()
    state = _State()
    state.fail_bind_sequence = 1
    root = tmp_path / "failed-smoke"
    kwargs = {
        "smoke_run_id": "smoke-test-002",
        "formal_verified_plan": formal_plan,
        "history_id": "07741c45",
        "episodes": _episodes(formal_plan, history_id="07741c45"),
        "sample_count": 3,
        "env": {"public": "smoke-test"},
        "execution_identity_sha256": "e" * 64,
        "membind_artifact_identity": _identity(),
        "hooks": _hooks(state, identity=_identity()),
    }

    with pytest.raises(MemBindV1SmokeError, match="live execution failed"):
        await run_membind_v1_smoke(root, **kwargs)

    inspected = inspect_membind_v1_smoke(root)
    assert inspected["checkpoint"]["status"] == "FAILED_NON_REUSABLE"
    assert inspected["checkpoint"]["resume_status"] == "DO_NOT_REUSE_CREATE_NEW_ATTEMPT"
    assert inspected["result"] is None
    assert not (root / "SMOKE_RESULT.json").exists()
    with pytest.raises(MemBindV1SmokeError, match="already exists"):
        await run_membind_v1_smoke(root, **kwargs)


@pytest.mark.parametrize("sample_count", [0, 2, 6])
def test_smoke_rejects_out_of_scope_sample_count_before_creating_attempt(
    tmp_path: Path, sample_count: int
) -> None:
    formal_plan = _formal_plan()
    state = _State()
    root = tmp_path / f"bad-{sample_count}"

    with pytest.raises(MemBindV1SmokeError, match="3-5"):
        asyncio.run(
            run_membind_v1_smoke(
                root,
                smoke_run_id="smoke-test-003",
                formal_verified_plan=formal_plan,
                history_id="07741c45",
                episodes=_episodes(formal_plan, history_id="07741c45"),
                sample_count=sample_count,
                env={"public": "smoke-test"},
                execution_identity_sha256="e" * 64,
                membind_artifact_identity=_identity(),
                hooks=_hooks(state, identity=_identity()),
            )
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_smoke_source_identity_drift_becomes_a_non_reusable_attempt(
    tmp_path: Path,
) -> None:
    formal_plan = _formal_plan()
    episodes = list(_episodes(formal_plan, history_id="07741c45"))
    episodes[1] = replace(episodes[1], source_hash="f" * 64)
    root = tmp_path / "drifted-smoke"

    with pytest.raises(MemBindV1SmokeError, match="live execution failed"):
        await run_membind_v1_smoke(
            root,
            smoke_run_id="smoke-test-004",
            formal_verified_plan=formal_plan,
            history_id="07741c45",
            episodes=episodes,
            sample_count=3,
            env={"public": "smoke-test"},
            execution_identity_sha256="e" * 64,
            membind_artifact_identity=_identity(),
            hooks=_hooks(_State(), identity=_identity()),
        )

    assert inspect_membind_v1_smoke(root)["checkpoint"]["status"] == (
        "FAILED_NON_REUSABLE"
    )
