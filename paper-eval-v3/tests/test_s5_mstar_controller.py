"""Offline TDD for the minimal M* runtime composition and controller."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding
from paper_eval.s5_mstar_controller import (
    S5MStarControllerError,
    S5MStarControllerPaths,
    S5MStarRuntimeComposition,
    build_s5_mstar_runtime_composition,
    execute_s5_mstar_controller,
    inspect_s5_mstar_controller_attempt,
)
from paper_eval.s5_mstar_pipeline import MStarSource
from paper_eval.s5_mstar_production_core_identity import (
    build_s5_mstar_production_core_identity,
)
from paper_eval.s5_native_method_adapters import S5EpisodeRef
from paper_eval.s5_production_runner import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    build_s5_production_identity,
)


RUN_ID = "s5-mstar-20260816-101"
NAMESPACE = f"pev3-{RUN_ID}"


def _core_identity() -> dict[str, object]:
    return build_s5_mstar_production_core_identity(
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_semantic_api_sha256="b" * 64,
        graphiti_semantic_identity_artifact_sha256="4" * 64,
        runtime_factory_entrypoint=(
            "native_characterization_runtime.build_u0_graphiti_from_env"
        ),
        runtime_factory_source_sha256="c" * 64,
        pipeline_source_sha256="d" * 64,
        pipeline_test_source_sha256="e" * 64,
        adapter_source_sha256="5" * 64,
        adapter_test_source_sha256="6" * 64,
        semantic_runtime_source_sha256="7" * 64,
        semantic_runtime_test_source_sha256="8" * 64,
        semantic_binding_source_sha256="9" * 64,
        semantic_binding_test_source_sha256="0" * 64,
        durable_store_source_sha256="f" * 64,
        durable_store_test_source_sha256="1" * 64,
        runtime_config_sha256="2" * 64,
    )


def _identity(
    core: dict[str, object], **overrides: object
) -> dict[str, object]:
    fields: dict[str, object] = {
        "method": "M*",
        "graphiti_version": GRAPHITI_VERSION,
        "graphiti_commit": GRAPHITI_COMMIT,
        "graphiti_native_source_sha256": "a" * 64,
        "graphiti_semantic_api_sha256": core["graphiti_semantic_api_sha256"],
        "runtime_factory_entrypoint": core["runtime_factory_entrypoint"],
        "runtime_factory_source_sha256": core["runtime_factory_source_sha256"],
        "scheduler_source_sha256": core["pipeline_source_sha256"],
        "scheduler_test_source_sha256": core["pipeline_test_source_sha256"],
        "durable_store_source_sha256": core["durable_store_source_sha256"],
        "durable_store_test_source_sha256": core[
            "durable_store_test_source_sha256"
        ],
        "runtime_config_sha256": core["runtime_config_sha256"],
        "fx0_parity_artifact_sha256": "3" * 64,
    }
    fields.update(overrides)
    return build_s5_production_identity(**fields)


def _fx0_qualification(
    identity: dict[str, object], core: dict[str, object]
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": (
            "membind.paper-eval-v3.s5-graphiti-fx0-production-qualification.v1"
        ),
        "verdict": "PRODUCTION_PATH_EXACT_PARITY_PASS",
        "fixture_count": 11,
        "run_id": "s5-mstar-fx0-production-parity-test-001",
        "runtime_config_sha256": core["runtime_config_sha256"],
        "production_core_identity_sha256": core["identity_sha256"],
        "fx0_artifact_payload_sha256": identity["fx0_parity_artifact_sha256"],
        "fx0_fixture_manifest_sha256": "a" * 64,
        "current_stage_pointer_sha256": "b" * 64,
        "full_regression_junit_sha256": "c" * 64,
        "full_regression_summary": {
            "tests": 100,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        },
        "legacy_status_artifact_preserved": True,
        "authority": {
            "model_call_authorized": False,
            "neo4j_read_authorized": False,
            "neo4j_mutation_authorized": False,
            "s5_live_execution_authorized": False,
            "current_stage_pointer_update_authorized": False,
        },
    }
    return {
        "git_commit": "a" * 40,
        "payload": payload,
        "payload_sha256": payload_sha256(payload),
        "protocol_version": "paper-eval-v3",
        "run_id": "s5-mstar-fx0-production-parity-test-001-qualification",
        "status": "finalized",
    }


@dataclass(frozen=True)
class _Episode:
    group_id: str
    source_sequence: int
    source_hash: str


def _episodes(
    *, count: int = 49, namespace: str = NAMESPACE
) -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(
            source_sequence=index,
            source_sha256=f"{index + 1:064x}",
            native_episode=_Episode(
                group_id=namespace,
                source_sequence=index,
                source_hash=f"{index + 1:064x}",
            ),
        )
        for index in range(count)
    )


def _binding() -> S5GraphitiSemanticBinding:
    async def pending(*_args, **_kwargs):
        return None

    def immediate(*_args, **_kwargs):
        return None

    return S5GraphitiSemanticBinding(
        extract_nodes=pending,
        resolve_extracted_nodes=pending,
        extract_attributes_from_nodes=pending,
        extract_edges=pending,
        resolve_extracted_edges=pending,
        resolve_edge_pointers=immediate,
        process_episode_data=pending,
    )


def _paths(tmp_path: Path) -> S5MStarControllerPaths:
    return S5MStarControllerPaths(
        controller_root=tmp_path / "controller",
        attempt_root=tmp_path / "attempt",
    )


def _composition() -> S5MStarRuntimeComposition:
    async def prepare(source: object, _logical_time: int) -> object:
        return source

    async def bind(*_args: object) -> object:
        return None

    async def commit(
        _result: object,
        _logical_time: int,
        source_sequence: int,
        _visible_prefix: tuple[int, ...],
    ) -> str:
        return f"{source_sequence + 100:064x}"

    return S5MStarRuntimeComposition(
        sources=tuple(
            MStarSource(
                source_sequence=index,
                source_sha256=f"{index + 1:064x}",
                opaque_source={"index": index},
                logical_time_ns=1_735_000_000_000_000_000 + index,
            )
            for index in range(49)
        ),
        semantic_prepare=prepare,
        latest_state_bind=bind,
        commit_evidence=commit,
        telemetry_clock_ns=lambda: 1,
    )


class _Runner:
    def __init__(self, trace: list[str], outcome: object) -> None:
        self.trace = trace
        self.outcome = outcome

    async def run(self) -> object:
        self.trace.append("run")
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _execute(
    tmp_path: Path,
    *,
    outcome: object | None = None,
    failure: str | None = None,
    identity_override: dict[str, object] | None = None,
) -> tuple[dict[str, object], list[str], S5MStarControllerPaths, dict[str, object]]:
    paths = _paths(tmp_path)
    core = _core_identity()
    identity = identity_override or _identity(core)
    fx0 = _fx0_qualification(identity, core)
    trace: list[str] = []
    runtime = SimpleNamespace(graphiti=object())

    def runtime_factory() -> object:
        trace.append("runtime")
        if failure == "runtime":
            raise RuntimeError("private runtime detail")
        return runtime

    async def readiness(_runtime: object) -> None:
        trace.append("readiness")
        if failure == "readiness":
            raise RuntimeError("private readiness detail")

    def composition_factory(
        _runtime: object,
        _episodes: tuple[S5EpisodeRef, ...],
        _namespace: str,
    ) -> S5MStarRuntimeComposition:
        trace.append("composition")
        if failure == "composition":
            raise RuntimeError("private composition detail")
        return _composition()

    def runner_factory(**kwargs: object) -> _Runner:
        trace.append("runner")
        assert kwargs["identity"] == identity
        assert kwargs["production_core_identity"] == core
        assert kwargs["fx0_qualification"] == fx0
        assert kwargs["sources"] == _composition().sources
        if failure == "runner_factory":
            raise RuntimeError("private runner factory detail")
        selected_outcome = outcome or {
            "status": "complete",
            "resume_authorized": False,
            "production_identity_sha256": identity["identity_sha256"],
            "production_core_identity_sha256": core["identity_sha256"],
            "payload": {"status": "PASS"},
        }
        if failure == "runner":
            selected_outcome = RuntimeError("private runner detail")
        return _Runner(trace, selected_outcome)

    async def close_runtime(_runtime: object) -> None:
        trace.append("close")
        if failure == "close":
            raise RuntimeError("private close detail")

    result = asyncio.run(
        execute_s5_mstar_controller(
            paths=paths,
            run_id=RUN_ID,
            namespace=NAMESPACE,
            episodes=_episodes(),
            identity=identity,
            production_core_identity=core,
            fx0_qualification=fx0,
            runtime_factory=runtime_factory,
            readiness=readiness,
            composition_factory=composition_factory,
            runner_factory=runner_factory,
            close_runtime=close_runtime,
        )
    )
    return result, trace, paths, core


def test_runtime_composition_binds_exact_workload_and_external_commit_evidence() -> None:
    graphiti = object()
    runtime = SimpleNamespace(graphiti=graphiti)
    ticks = iter([1_735_000_000_000_000_000] * 49)

    def projector(_episode: object) -> dict[str, object]:
        return {}

    class EpisodeNode:
        pass

    async def commit_evidence(*_args: object) -> str:
        return "f" * 64

    composition = build_s5_mstar_runtime_composition(
        runtime=runtime,
        episodes=_episodes(),
        namespace=NAMESPACE,
        semantic_binding=_binding(),
        graphiti_episode_kwargs=projector,
        episodic_node_type=EpisodeNode,
        epoch_clock_ns=lambda: next(ticks),
        commit_evidence=commit_evidence,
        telemetry_clock_ns=lambda: 123,
    )

    assert len(composition.sources) == 49
    assert [source.source_sequence for source in composition.sources] == list(range(49))
    assert [source.source_sha256 for source in composition.sources] == [
        ref.source_sha256 for ref in _episodes()
    ]
    assert [source.logical_time_ns for source in composition.sources] == [
        1_735_000_000_000_000_000 + index for index in range(49)
    ]
    assert composition.commit_evidence is commit_evidence
    assert composition.telemetry_clock_ns() == 123
    assert callable(composition.semantic_prepare)
    assert callable(composition.latest_state_bind)


@pytest.mark.parametrize(
    ("episodes", "namespace", "commit", "code"),
    [
        (_episodes(count=48), NAMESPACE, lambda *_args: "f" * 64, "workload"),
        (_episodes(namespace="wrong"), NAMESPACE, lambda *_args: "f" * 64, "namespace"),
        (_episodes(), NAMESPACE, None, "commit_evidence"),
    ],
)
def test_runtime_composition_rejects_drift_before_adapter_construction(
    episodes: tuple[S5EpisodeRef, ...],
    namespace: str,
    commit: object,
    code: str,
) -> None:
    with pytest.raises(S5MStarControllerError, match=code):
        build_s5_mstar_runtime_composition(
            runtime=SimpleNamespace(graphiti=object()),
            episodes=episodes,
            namespace=namespace,
            semantic_binding=_binding(),
            graphiti_episode_kwargs=lambda _episode: {},
            episodic_node_type=object,
            epoch_clock_ns=lambda: 1_735_000_000_000_000_000,
            commit_evidence=commit,
        )


def test_controller_builds_runtime_and_promotes_only_bound_runner_pass(
    tmp_path: Path,
) -> None:
    result, trace, paths, core = _execute(tmp_path)

    assert trace == ["runtime", "readiness", "composition", "runner", "run", "close"]
    assert result == {
        "status": "controller_complete_evidence_only",
        "attempt_status": "complete",
        "production_core_identity_sha256": core["identity_sha256"],
        "scientific_outcome_candidate": True,
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
        "scientific_pass_authorized": False,
        "next_method_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }
    inspected = inspect_s5_mstar_controller_attempt(paths.controller_root)
    assert [event["event_type"] for event in inspected["events"]] == [
        "controller_started",
        "runtime_constructed",
        "runtime_ready",
        "runtime_composed",
        "mstar_runner_started",
        "runtime_closed",
        "runner_evidence_complete",
    ]
    assert inspected["checkpoint"]["status"] == "controller_complete_evidence_only"


def test_identity_drift_fails_before_runtime_or_controller_artifacts(tmp_path: Path) -> None:
    core = _core_identity()
    drifted = _identity(core, scheduler_source_sha256="f" * 64)
    paths = _paths(tmp_path)
    trace: list[str] = []
    with pytest.raises(S5MStarControllerError, match="binding"):
        asyncio.run(
            execute_s5_mstar_controller(
                paths=paths,
                run_id=RUN_ID,
                namespace=NAMESPACE,
                episodes=_episodes(),
                identity=drifted,
                production_core_identity=core,
                fx0_qualification=_fx0_qualification(drifted, core),
                runtime_factory=lambda: trace.append("runtime"),
                readiness=lambda _runtime: None,
                composition_factory=lambda *_args: _composition(),
                runner_factory=lambda **_kwargs: None,
                close_runtime=lambda _runtime: None,
            )
        )
    assert trace == []
    assert not paths.controller_root.exists()
    assert not paths.attempt_root.exists()


@pytest.mark.parametrize(
    ("failure", "stage", "trace"),
    [
        ("runtime", "runtime_construction", ["runtime"]),
        ("readiness", "runtime_readiness", ["runtime", "readiness", "close"]),
        (
            "composition",
            "runtime_composition",
            ["runtime", "readiness", "composition", "close"],
        ),
        (
            "runner_factory",
            "runner_construction",
            ["runtime", "readiness", "composition", "runner", "close"],
        ),
        (
            "runner",
            "mstar_execution",
            ["runtime", "readiness", "composition", "runner", "run", "close"],
        ),
        (
            "close",
            "runtime_close",
            ["runtime", "readiness", "composition", "runner", "run", "close"],
        ),
    ],
)
def test_post_start_failures_are_sanitized_and_nonmergeable(
    tmp_path: Path, failure: str, stage: str, trace: list[str]
) -> None:
    result, observed, paths, _core = _execute(tmp_path, failure=failure)

    assert observed == trace
    assert result["status"] == "incomplete_non_mergeable"
    assert result["failure_stage"] == stage
    assert result["error_class"] == "builtins.RuntimeError"
    assert result["scientific_outcome_candidate"] is False
    assert result["resume_authorized"] is False
    assert result["namespace_cleanup_authorized"] is False
    inspected = inspect_s5_mstar_controller_attempt(paths.controller_root)
    assert inspected["checkpoint"]["status"] == "incomplete_non_mergeable"
    persisted = paths.controller_root.joinpath("events.jsonl").read_text()
    assert "private" not in persisted


def test_incomplete_runner_is_not_promoted(tmp_path: Path) -> None:
    core = _core_identity()
    identity = _identity(core)
    outcome = {
        "status": "incomplete_non_mergeable",
        "resume_authorized": False,
        "production_identity_sha256": identity["identity_sha256"],
        "production_core_identity_sha256": core["identity_sha256"],
        "payload": {
            "status": "FAIL_CLOSED",
            "events": [{"error_class": "httpx.ConnectError"}],
        },
    }
    result, _trace, paths, _core = _execute(tmp_path, outcome=outcome)

    assert result["status"] == "incomplete_non_mergeable"
    assert result["failure_stage"] == "mstar_execution"
    assert result["error_class"] == "httpx.ConnectError"
    assert result["scientific_outcome_candidate"] is False
    assert inspect_s5_mstar_controller_attempt(paths.controller_root)["checkpoint"][
        "status"
    ] == "incomplete_non_mergeable"


def test_controller_is_single_use_before_runtime(tmp_path: Path) -> None:
    _result, _trace, paths, _core = _execute(tmp_path)
    calls: list[str] = []
    core = _core_identity()
    identity = _identity(core)
    with pytest.raises(S5MStarControllerError, match="single_use"):
        asyncio.run(
            execute_s5_mstar_controller(
                paths=paths,
                run_id=RUN_ID,
                namespace=NAMESPACE,
                episodes=_episodes(),
                identity=identity,
                production_core_identity=core,
                fx0_qualification=_fx0_qualification(identity, core),
                runtime_factory=lambda: calls.append("runtime"),
                readiness=lambda _runtime: None,
                composition_factory=lambda *_args: _composition(),
                runner_factory=lambda **_kwargs: None,
                close_runtime=lambda _runtime: None,
            )
        )
    assert calls == []
