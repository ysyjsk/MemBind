"""TDD contract for the S5 production-path composition layer.

The tests use an in-memory Graphiti callable double and a temporary durable
attempt directory.  They do not authorize a model call, Neo4j access, or a
live method smoke; those actions remain behind the future authority layer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from paper_eval.s5_graphiti_native_binding import S5GraphitiNativeBinding
from paper_eval.s5_production_runner import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    S5ProductionIdentityError,
    S5ProductionRunner,
    build_s5_production_identity,
    verify_s5_production_identity,
)
from paper_eval.s5_native_method_adapters import S5EpisodeRef, S5MethodSpec
from paper_eval.s5_durable_attempt_store import inspect_s5_attempt


SHA = "a" * 64


def _identity(method: str = "A0") -> dict:
    return build_s5_production_identity(
        method=method,
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_native_source_sha256=SHA,
        graphiti_semantic_api_sha256="3" * 64,
        runtime_factory_entrypoint="native_characterization_runtime.build_u0_graphiti_from_env",
        runtime_factory_source_sha256="b" * 64,
        scheduler_source_sha256="c" * 64,
        scheduler_test_source_sha256="f" * 64,
        durable_store_source_sha256="d" * 64,
        durable_store_test_source_sha256="1" * 64,
        runtime_config_sha256="e" * 64,
    )


def _episodes(count: int = 3) -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(i, (f"{i:064x}")[-64:], {"opaque": i})
        for i in range(count)
    )


def _binding(calls: list[object]) -> S5GraphitiNativeBinding:
    async def add_episode(graphiti: object, episode: object) -> None:
        calls.append((graphiti, episode))
        await asyncio.sleep(0)

    def graphiti_episode_kwargs(episode: object) -> dict[str, object]:
        return {"opaque": episode}

    add_episode.__module__ = "graphiti_native"
    add_episode.__qualname__ = "add_episode"
    graphiti_episode_kwargs.__module__ = "graphiti_native"
    graphiti_episode_kwargs.__qualname__ = "graphiti_episode_kwargs"
    return S5GraphitiNativeBinding(
        module_name="graphiti_native",
        add_episode=add_episode,
        graphiti_episode_kwargs=graphiti_episode_kwargs,
    )


def _failing_binding() -> S5GraphitiNativeBinding:
    async def add_episode(_graphiti: object, _episode: object) -> None:
        raise RuntimeError("simulated native failure")

    def graphiti_episode_kwargs(episode: object) -> dict[str, object]:
        return {"opaque": episode}

    add_episode.__module__ = "graphiti_native"
    add_episode.__qualname__ = "add_episode"
    graphiti_episode_kwargs.__module__ = "graphiti_native"
    graphiti_episode_kwargs.__qualname__ = "graphiti_episode_kwargs"
    return S5GraphitiNativeBinding(
        module_name="graphiti_native",
        add_episode=add_episode,
        graphiti_episode_kwargs=graphiti_episode_kwargs,
    )


def test_identity_is_hash_sealed_to_pinned_graphiti_and_method() -> None:
    identity = _identity()
    verified = verify_s5_production_identity(identity)
    assert verified["status"] == "FROZEN"
    assert verified["method"] == "A0"
    assert verified["graphiti_version"] == GRAPHITI_VERSION
    assert verified["graphiti_commit"] == GRAPHITI_COMMIT
    assert len(verified["identity_sha256"]) == 64


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(graphiti_version="0.29.2"),
        lambda value: value.update(graphiti_commit="0" * 40),
        lambda value: value.update(method="M2"),
        lambda value: value.update(identity_sha256="0" * 64),
    ],
)
def test_identity_drift_fails_closed(mutate) -> None:
    identity = _identity()
    mutate(identity)
    with pytest.raises(S5ProductionIdentityError):
        verify_s5_production_identity(identity)


def test_mstar_identity_requires_sealed_fx0_parity_artifact() -> None:
    with pytest.raises(S5ProductionIdentityError, match="fx0"):
        _identity("M*")

    identity = build_s5_production_identity(
        method="M*",
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_native_source_sha256=SHA,
        graphiti_semantic_api_sha256="3" * 64,
        runtime_factory_entrypoint="native_characterization_runtime.build_u0_graphiti_from_env",
        runtime_factory_source_sha256="b" * 64,
        scheduler_source_sha256="c" * 64,
        scheduler_test_source_sha256="f" * 64,
        durable_store_source_sha256="d" * 64,
        durable_store_test_source_sha256="1" * 64,
        runtime_config_sha256="e" * 64,
        fx0_parity_artifact_sha256="2" * 64,
    )
    assert identity["fx0_parity_artifact_sha256"] == "2" * 64


def test_a0_runner_uses_exact_native_binding_and_seals_attempt(tmp_path: Path) -> None:
    calls: list[object] = []
    identity = _identity("A0")
    spec = S5MethodSpec(
        run_id="s5-a0-test-run",
        method="A0",
        native_path_identity_sha256=SHA,
    )
    runner = S5ProductionRunner(
        attempt_root=tmp_path / "a0",
        spec=spec,
        identity=identity,
        graphiti=object(),
        binding=_binding(calls),
        episodes=_episodes(),
    )

    result = asyncio.run(runner.run())

    assert result["status"] == "complete"
    assert result["payload"]["status"] == "PASS"
    assert [episode[1] for episode in calls] == [
        {"opaque": 0},
        {"opaque": 1},
        {"opaque": 2},
    ]
    inspected = inspect_s5_attempt(tmp_path / "a0")
    assert inspected["result"]["status"] == "complete"
    assert inspected["resume_authorized"] is False


def test_runner_refuses_identity_method_or_existing_attempt(tmp_path: Path) -> None:
    spec = S5MethodSpec(
        run_id="s5-a0-test-run",
        method="A0",
        native_path_identity_sha256=SHA,
    )
    with pytest.raises(S5ProductionIdentityError, match="method"):
        S5ProductionRunner(
            attempt_root=tmp_path / "bad",
            spec=spec,
            identity=_identity("P*"),
            graphiti=object(),
            binding=_binding([]),
            episodes=_episodes(),
        )

    first = S5ProductionRunner(
        attempt_root=tmp_path / "same",
        spec=spec,
        identity=_identity("A0"),
        graphiti=object(),
        binding=_binding([]),
        episodes=_episodes(),
    )
    asyncio.run(first.run())
    with pytest.raises(S5ProductionIdentityError, match="attempt"):
        S5ProductionRunner(
            attempt_root=tmp_path / "same",
            spec=spec,
            identity=_identity("A0"),
            graphiti=object(),
            binding=_binding([]),
            episodes=_episodes(),
        )


def test_p_c2_runner_requires_real_two_worker_overlap(tmp_path: Path) -> None:
    calls: list[object] = []
    spec = S5MethodSpec(
        run_id="s5-p-star-test-run",
        method="P*",
        native_path_identity_sha256=SHA,
    )
    runner = S5ProductionRunner(
        attempt_root=tmp_path / "p",
        spec=spec,
        identity=_identity("P*"),
        graphiti=object(),
        binding=_binding(calls),
        episodes=_episodes(4),
    )
    result = asyncio.run(runner.run())
    assert result["status"] == "complete"
    assert result["payload"]["summary"]["configured_worker_count"] == 2
    assert result["payload"]["summary"]["whole_update_interval_overlap_observed"] is True


def test_native_failure_is_persisted_as_incomplete_and_never_resumable(tmp_path: Path) -> None:
    spec = S5MethodSpec(
        run_id="s5-a0-failure-run",
        method="A0",
        native_path_identity_sha256=SHA,
    )
    runner = S5ProductionRunner(
        attempt_root=tmp_path / "failed",
        spec=spec,
        identity=_identity("A0"),
        graphiti=object(),
        binding=_failing_binding(),
        episodes=_episodes(),
    )
    result = asyncio.run(runner.run())
    assert result["status"] == "incomplete_non_mergeable"
    assert result["resume_authorized"] is False
    assert result["payload"]["status"] == "FAIL_CLOSED"
    inspected = inspect_s5_attempt(tmp_path / "failed")
    assert inspected["result"]["status"] == "incomplete_non_mergeable"
    assert inspected["checkpoint"]["resume_authorized"] is False


def test_runner_rejects_private_identity_fields(tmp_path: Path) -> None:
    identity = _identity()
    identity["api_key"] = "must never enter identity"
    with pytest.raises(S5ProductionIdentityError, match="private"):
        verify_s5_production_identity(identity)
