"""Offline TDD for the real A0 controller production composition."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import finalize_envelope
from paper_eval.s5_a0_controller import (
    S5A0ControllerError,
    S5A0ProductionDependencies,
    S5A0ProductionPaths,
    close_s5_a0_runtime,
    ensure_s5_a0_runtime_ready,
    execute_s5_a0_production,
    execute_s5_a0_controller,
    main,
)
from paper_eval.s5_native_method_adapters import S5EpisodeRef
from tests.test_s5_a0_controller import (
    NAMESPACE,
    SOURCE_SHA256S,
    _Runner,
    _chain,
    _dependencies,
    _write_json,
)


@dataclass(frozen=True)
class _FrozenEpisode:
    group_id: str
    source_sequence: int


class _Driver:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self._init_task = self._ready()

    async def _ready(self) -> None:
        self.trace.append("driver_ready")


class _Graphiti:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.driver = _Driver(trace)

    async def close(self) -> None:
        self.trace.append("close")


def _production_paths(tmp_path: Path) -> tuple[S5A0ProductionPaths, tuple]:
    controller, _episodes = _chain(tmp_path)
    return (
        S5A0ProductionPaths(
            controller=controller,
            runtime_config=tmp_path / "runtime-config.json",
            identity_materialization=tmp_path / "materialization.json",
            env_file=tmp_path / ".env",
            materialization_inputs=None,
        ),
        _episodes,
    )


def _source_refs() -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(
            source_sequence=index,
            source_sha256=digest,
            native_episode=_FrozenEpisode("frozen-original-group", index),
        )
        for index, digest in enumerate(SOURCE_SHA256S)
    )


def test_production_entry_consumes_before_env_and_rebinds_exact_workload(
    tmp_path: Path,
) -> None:
    paths, _episodes = _production_paths(tmp_path)
    trace: list[str] = []
    captured: dict[str, object] = {}

    def workload_loader(_paths):
        trace.append("workload")
        assert not paths.controller.consumption.exists()
        return _source_refs()

    def env_loader(path: Path, _legacy_src: Path):
        trace.append("env")
        assert path == paths.env_file
        assert paths.controller.consumption.is_file()
        return {"private": "not persisted"}

    def runtime_builder(**kwargs):
        trace.append("runtime")
        assert paths.controller.consumption.is_file()
        kwargs["authorization_checker"](kwargs["live_action"])
        assert kwargs["env_loader"]() is None
        return SimpleNamespace(graphiti=_Graphiti(trace))

    def binding_loader():
        trace.append("binding")
        return object()

    def runner_factory(**kwargs):
        trace.append("runner")
        captured["episodes"] = kwargs["episodes"]
        return _Runner(
            trace,
            {
                "status": "complete",
                "resume_authorized": False,
                "payload": {"status": "PASS"},
            },
        )

    result = asyncio.run(
        execute_s5_a0_production(
            paths=paths,
            git_commit="deadbeef",
            dependencies=S5A0ProductionDependencies(
                workload_loader=workload_loader,
                env_file_loader=env_loader,
                runtime_builder=runtime_builder,
                binding_loader=binding_loader,
                runner_factory=runner_factory,
            ),
        )
    )

    assert result["status"] == "controller_complete_evidence_only"
    assert trace == [
        "workload",
        "env",
        "runtime",
        "driver_ready",
        "binding",
        "runner",
        "native",
        "close",
    ]
    rebound = captured["episodes"]
    assert [item.source_sequence for item in rebound] == list(range(49))
    assert [item.source_sha256 for item in rebound] == list(SOURCE_SHA256S)
    assert all(item.native_episode.group_id == NAMESPACE for item in rebound)


def test_production_env_failure_is_after_consumption_and_sanitized(
    tmp_path: Path,
) -> None:
    paths, _episodes = _production_paths(tmp_path)

    def env_loader(_path: Path, _legacy_src: Path):
        assert paths.controller.consumption.is_file()
        raise RuntimeError("private credential detail")

    result = asyncio.run(
        execute_s5_a0_production(
            paths=paths,
            git_commit="deadbeef",
            dependencies=S5A0ProductionDependencies(
                workload_loader=lambda _paths: _source_refs(),
                env_file_loader=env_loader,
            ),
        )
    )

    assert result["status"] == "incomplete_non_mergeable"
    assert result["error_class"] == "builtins.RuntimeError"
    assert paths.controller.consumption.is_file()
    assert "private credential" not in paths.controller.controller_root.joinpath(
        "events.jsonl"
    ).read_text(encoding="utf-8")


def test_runtime_readiness_and_close_use_graphiti_lifecycle() -> None:
    trace: list[str] = []
    runtime = SimpleNamespace(graphiti=_Graphiti(trace))

    asyncio.run(ensure_s5_a0_runtime_ready(runtime))
    asyncio.run(close_s5_a0_runtime(runtime))

    assert trace == ["driver_ready", "close"]


def test_invalid_runtime_and_missing_close_fail_closed() -> None:
    with pytest.raises(ValueError, match="runtime_graphiti"):
        asyncio.run(ensure_s5_a0_runtime_ready(object()))
    with pytest.raises(ValueError, match="runtime_close"):
        asyncio.run(
            close_s5_a0_runtime(
                SimpleNamespace(graphiti=SimpleNamespace(driver=object()))
            )
        )


def test_result_verifier_source_drift_stops_before_consumption(tmp_path: Path) -> None:
    paths, episodes = _chain(tmp_path)
    authority = json.loads(paths.authority.read_text(encoding="utf-8"))
    authority["payload"]["source_sha256"]["result_verifier"] = "0" * 64
    authority = finalize_envelope(
        payload=authority["payload"],
        protocol_version="paper-eval-v3",
        git_commit="deadbeef",
        run_id=authority["run_id"],
    )
    _write_json(paths.authority, authority)
    trace: list[str] = []

    with pytest.raises(S5A0ControllerError, match="result_verifier_source"):
        asyncio.run(
            execute_s5_a0_controller(
                paths=paths,
                episodes=episodes,
                git_commit="deadbeef",
                **_dependencies(trace),
            )
        )

    assert trace == []
    assert not paths.consumption.exists()


def test_runtime_close_failure_is_single_call_and_has_its_own_stage(
    tmp_path: Path,
) -> None:
    paths, episodes = _chain(tmp_path)
    trace: list[str] = []
    dependencies = _dependencies(trace)

    async def broken_close(_runtime):
        trace.append("close")
        raise RuntimeError("private close detail")

    dependencies["close_runtime"] = broken_close
    result = asyncio.run(
        execute_s5_a0_controller(
            paths=paths,
            episodes=episodes,
            git_commit="deadbeef",
            **dependencies,
        )
    )

    assert result["status"] == "incomplete_non_mergeable"
    assert result["failure_stage"] == "runtime_close"
    assert result["error_class"] == "builtins.RuntimeError"
    assert trace.count("close") == 1
    assert "private close detail" not in paths.controller_root.joinpath(
        "events.jsonl"
    ).read_text(encoding="utf-8")


def test_cli_builds_run_scoped_paths_without_executing_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    captured: dict[str, object] = {}

    async def fake_execute(*, paths, git_commit, dependencies=None):
        captured.update(paths=paths, git_commit=git_commit, dependencies=dependencies)
        return {
            "status": "controller_complete_evidence_only",
            "scientific_pass_authorized": False,
        }

    monkeypatch.setattr(
        "paper_eval.s5_a0_controller.execute_s5_a0_production", fake_execute
    )
    run_root = tmp_path / "s5-a0-20260816-101"
    code = main(
        [
            "--production-identity",
            str(tmp_path / "identity.json"),
            "--production-identity-qualification",
            str(tmp_path / "qualification.json"),
            "--preflight",
            str(tmp_path / "preflight.json"),
            "--authority",
            str(tmp_path / "authority.json"),
            "--runtime-config",
            str(tmp_path / "runtime.json"),
            "--identity-materialization",
            str(tmp_path / "materialization.json"),
            "--run-root",
            str(run_root),
            "--git-commit",
            "deadbeef",
        ]
    )

    assert code == 0
    assert captured["git_commit"] == "deadbeef"
    production = captured["paths"]
    assert production.controller.consumption == run_root / "authority_consumption.json"
    assert production.controller.controller_root == run_root / "controller"
    assert production.controller.attempt_root == run_root / "attempt"
    assert "controller_complete_evidence_only" in capsys.readouterr().out


def test_cli_returns_nonzero_for_incomplete_controller_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    async def fake_execute(**_kwargs):
        return {
            "status": "incomplete_non_mergeable",
            "failure_stage": "native_execution",
            "error_class": "httpx.ConnectError",
        }

    monkeypatch.setattr(
        "paper_eval.s5_a0_controller.execute_s5_a0_production", fake_execute
    )

    code = main(
        [
            "--production-identity",
            str(tmp_path / "identity.json"),
            "--production-identity-qualification",
            str(tmp_path / "qualification.json"),
            "--preflight",
            str(tmp_path / "preflight.json"),
            "--authority",
            str(tmp_path / "authority.json"),
            "--runtime-config",
            str(tmp_path / "runtime.json"),
            "--identity-materialization",
            str(tmp_path / "materialization.json"),
            "--run-root",
            str(tmp_path / "run"),
            "--git-commit",
            "deadbeef",
        ]
    )

    assert code != 0
    assert "incomplete_non_mergeable" in capsys.readouterr().out


def test_cli_does_not_print_unknown_exception_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    async def fake_execute(**_kwargs):
        raise RuntimeError("private credential and endpoint detail")

    monkeypatch.setattr(
        "paper_eval.s5_a0_controller.execute_s5_a0_production", fake_execute
    )

    code = main(
        [
            "--production-identity",
            str(tmp_path / "identity.json"),
            "--production-identity-qualification",
            str(tmp_path / "qualification.json"),
            "--preflight",
            str(tmp_path / "preflight.json"),
            "--authority",
            str(tmp_path / "authority.json"),
            "--runtime-config",
            str(tmp_path / "runtime.json"),
            "--identity-materialization",
            str(tmp_path / "materialization.json"),
            "--run-root",
            str(tmp_path / "run"),
            "--git-commit",
            "deadbeef",
        ]
    )

    captured = capsys.readouterr()
    assert code != 0
    assert "RuntimeError" in captured.err
    assert "private credential" not in captured.err
    assert "endpoint detail" not in captured.err
