"""TDD tests for durable M* runner composition."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from paper_eval.s5_durable_attempt_store import inspect_s5_attempt
from paper_eval.s5_mstar_pipeline import MStarSource, MStarSpec
from paper_eval.s5_mstar_production_runner import (
    S5MStarProductionRunner,
    S5MStarProductionRunnerError,
)
from paper_eval.s5_production_runner import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    S5ProductionIdentityError,
    build_s5_production_identity,
)


def _identity() -> dict[str, object]:
    return build_s5_production_identity(
        method="M*",
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_native_source_sha256="a" * 64,
        graphiti_semantic_api_sha256="b" * 64,
        runtime_factory_entrypoint="native_characterization_runtime.build_u0_graphiti_from_env",
        runtime_factory_source_sha256="c" * 64,
        scheduler_source_sha256="d" * 64,
        scheduler_test_source_sha256="e" * 64,
        durable_store_source_sha256="f" * 64,
        durable_store_test_source_sha256="1" * 64,
        runtime_config_sha256="2" * 64,
        fx0_parity_artifact_sha256="3" * 64,
    )


class StepClock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _sources(count: int = 3) -> tuple[MStarSource, ...]:
    return tuple(
        MStarSource(i, f"{i + 10:064x}", {"source": i})
        for i in range(count)
    )


def _spec(identity: dict[str, object]) -> MStarSpec:
    return MStarSpec(
        run_id="s5-mstar-production-test",
        production_core_identity_sha256=identity["identity_sha256"],
        prepare_concurrency=2,
    )


def test_mstar_runner_seals_successful_durable_attempt(tmp_path: Path) -> None:
    identity = _identity()
    bound: list[int] = []
    started = 0
    release = asyncio.Event()

    async def prepare(source: object, logical_time: int) -> object:
        nonlocal started
        started += 1
        if started == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=1)
        return {"source": source["source"], "logical_time": logical_time}

    async def bind(
        prepared: object,
        logical_time: int,
        source_sequence: int,
        visible_prefix: tuple[int, ...],
    ) -> None:
        assert prepared["source"] == source_sequence
        assert prepared["logical_time"] == logical_time
        assert visible_prefix == tuple(range(source_sequence))
        bound.append(source_sequence)

    runner = S5MStarProductionRunner(
        attempt_root=tmp_path / "mstar",
        spec=_spec(identity),
        identity=identity,
        sources=_sources(),
        semantic_prepare=prepare,
        latest_state_bind=bind,
        clock_ns=StepClock(),
    )
    result = asyncio.run(runner.run())
    assert result["status"] == "complete"
    assert result["payload"]["status"] == "PASS"
    assert bound == [0, 1, 2]
    inspected = inspect_s5_attempt(tmp_path / "mstar")
    assert inspected["result"]["status"] == "complete"
    assert inspected["resume_authorized"] is False


def test_mstar_bind_failure_is_incomplete_and_keeps_published_prefix(tmp_path: Path) -> None:
    identity = _identity()

    async def prepare(source: object, _logical_time: int) -> object:
        return source["source"]

    async def bind(
        prepared: object,
        _logical_time: int,
        _source_sequence: int,
        _visible_prefix: tuple[int, ...],
    ) -> None:
        if prepared == 1:
            raise RuntimeError("simulated bind failure")

    runner = S5MStarProductionRunner(
        attempt_root=tmp_path / "failed",
        spec=_spec(identity),
        identity=identity,
        sources=_sources(),
        semantic_prepare=prepare,
        latest_state_bind=bind,
        clock_ns=StepClock(),
    )
    result = asyncio.run(runner.run())
    assert result["status"] == "incomplete_non_mergeable"
    assert result["payload"]["failure_code"] == "LATEST_STATE_BIND_FAILED"
    assert result["payload"]["summary"]["published_source_sequences"] == [0]
    assert result["resume_authorized"] is False


def test_mstar_runner_rejects_missing_fx0_identity_or_single_source(tmp_path: Path) -> None:
    with pytest.raises(S5ProductionIdentityError, match="fx0"):
        identity = build_s5_production_identity(
            method="M*",
            graphiti_version=GRAPHITI_VERSION,
            graphiti_commit=GRAPHITI_COMMIT,
            graphiti_native_source_sha256="a" * 64,
            graphiti_semantic_api_sha256="b" * 64,
            runtime_factory_entrypoint="native_characterization_runtime.build_u0_graphiti_from_env",
            runtime_factory_source_sha256="c" * 64,
            scheduler_source_sha256="d" * 64,
            scheduler_test_source_sha256="e" * 64,
            durable_store_source_sha256="f" * 64,
            durable_store_test_source_sha256="1" * 64,
            runtime_config_sha256="2" * 64,
        )
        del identity  # build itself must fail closed before runner construction

    identity = _identity()
    with pytest.raises(S5MStarProductionRunnerError, match="sources"):
        S5MStarProductionRunner(
            attempt_root=tmp_path / "one",
            spec=_spec(identity),
            identity=identity,
            sources=_sources(1),
            semantic_prepare=lambda *_args: None,
            latest_state_bind=lambda *_args: None,
            clock_ns=StepClock(),
        )
