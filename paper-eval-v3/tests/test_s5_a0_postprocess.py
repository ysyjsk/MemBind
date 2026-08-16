"""Offline TDD for the authority-bound S5 A0 postprocess lifecycle."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import finalize_envelope, sha256_file
from paper_eval.s5_a0_controller import execute_s5_a0_controller
from paper_eval.s5_a0_postprocess import (
    S5A0PostprocessError,
    build_parser,
    execute_s5_a0_postprocess,
    inspect_s5_a0_postprocess_checkpoint,
    main,
)
from paper_eval.s5_a0_result_finalizer import (
    S5A0FinalizerPaths,
    verify_s5_a0_result,
)
from paper_eval.s5_native_post_observation import verify_s5_native_post_observation
from tests.test_s5_a0_controller import _chain, _dependencies, _write_json
from tests.test_s5_a0_result_finalizer import (
    FINALIZER_SOURCE,
    _DurableA0Runner,
    _rows,
)
from tests.test_s5_native_post_observation import QueryExecutor


class _Driver:
    def __init__(self, trace: list[str], *, close_error: BaseException | None = None):
        self.trace = trace
        self.close_error = close_error

    async def close(self) -> None:
        self.trace.append("close")
        if self.close_error is not None:
            raise self.close_error


def _ready_chain(tmp_path: Path) -> S5A0FinalizerPaths:
    controller_paths, episodes = _chain(tmp_path)
    authority = json.loads(controller_paths.authority.read_text(encoding="utf-8"))
    authority["payload"]["source_sha256"]["result_verifier"] = sha256_file(
        FINALIZER_SOURCE
    )
    authority = finalize_envelope(
        payload=authority["payload"],
        protocol_version="paper-eval-v3",
        git_commit="deadbeef",
        run_id=authority["run_id"],
    )
    _write_json(controller_paths.authority, authority)

    trace: list[str] = []
    dependencies = _dependencies(trace)
    dependencies["runner_factory"] = lambda **kwargs: _DurableA0Runner(**kwargs)
    result = asyncio.run(
        execute_s5_a0_controller(
            paths=controller_paths,
            episodes=episodes,
            git_commit="deadbeef",
            **dependencies,
        )
    )
    assert result["status"] == "controller_complete_evidence_only"
    run_root = controller_paths.controller_root.parent
    return S5A0FinalizerPaths(
        production_identity=controller_paths.production_identity,
        production_identity_qualification=(
            controller_paths.production_identity_qualification
        ),
        current_stage_pointer=controller_paths.current_stage_pointer,
        preflight=controller_paths.preflight,
        authority=controller_paths.authority,
        consumption=controller_paths.consumption,
        controller_root=controller_paths.controller_root,
        attempt_root=controller_paths.attempt_root,
        post_observation=run_root / "post_observation.json",
        result=run_root / "S5_A0_RESULT.json",
    )


def _execute(
    paths: S5A0FinalizerPaths,
    *,
    rows=None,
    query_executor=None,
    driver_error: BaseException | None = None,
    close_error: BaseException | None = None,
    finalizer=None,
):
    trace: list[str] = []

    def env_loader():
        trace.append("env")
        return {"private": "not persisted"}

    def driver_factory(_env):
        trace.append("driver")
        if driver_error is not None:
            raise driver_error
        return _Driver(trace, close_error=close_error)

    selected_query = query_executor or QueryExecutor(rows)
    kwargs = {}
    if finalizer is not None:
        kwargs["finalizer"] = finalizer
    result = asyncio.run(
        execute_s5_a0_postprocess(
            paths=paths,
            git_commit="deadbeef",
            env_loader=env_loader,
            driver_factory=driver_factory,
            query_executor=selected_query,
            **kwargs,
        )
    )
    return result, trace


def test_complete_durable_chain_observes_closes_and_finalizes_pass(
    tmp_path: Path,
) -> None:
    paths = _ready_chain(tmp_path)
    authority = json.loads(paths.authority.read_text(encoding="utf-8"))
    run = authority["payload"]["run"]
    sources = [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(
            json.loads((paths.attempt_root / "manifest.json").read_text())["source_sha256s"]
        )
    ]

    result, trace = _execute(
        paths,
        rows=_rows(run["namespace"], sources, violation=False),
    )

    assert result == {
        "status": "PASS",
        "method": "A0",
        "post_observation_status": "PASS",
        "final_result_status": "PASS",
        "published_count": 49,
        "last_published_source_sequence": 48,
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }
    assert trace == ["env", "driver", "close"]
    assert verify_s5_native_post_observation(
        json.loads(paths.post_observation.read_text(encoding="ascii"))
    )["status"] == "PASS"
    assert verify_s5_a0_result(
        json.loads(paths.result.read_text(encoding="ascii"))
    )["payload"]["verdict"] == "PASS"
    checkpoint = inspect_s5_a0_postprocess_checkpoint(
        paths.controller_root.parent / "postprocess/checkpoint.json"
    )
    assert checkpoint["status"] == "complete"
    assert checkpoint["resume_authorized"] is False
    rendered = repr(result) + repr(checkpoint)
    assert "pev3-" not in rendered
    assert "s5-a0-20260816-101" not in rendered


def test_incomplete_controller_or_attempt_blocks_before_private_env(
    tmp_path: Path,
) -> None:
    controller_paths, _episodes = _chain(tmp_path)
    paths = S5A0FinalizerPaths(
        production_identity=controller_paths.production_identity,
        production_identity_qualification=(
            controller_paths.production_identity_qualification
        ),
        current_stage_pointer=controller_paths.current_stage_pointer,
        preflight=controller_paths.preflight,
        authority=controller_paths.authority,
        consumption=controller_paths.consumption,
        controller_root=controller_paths.controller_root,
        attempt_root=controller_paths.attempt_root,
        post_observation=tmp_path / "post.json",
        result=tmp_path / "result.json",
    )
    calls: list[str] = []

    with pytest.raises(S5A0PostprocessError, match="controller"):
        asyncio.run(
            execute_s5_a0_postprocess(
                paths=paths,
                git_commit="deadbeef",
                env_loader=lambda: calls.append("env"),
                driver_factory=lambda _env: calls.append("driver"),
                query_executor=QueryExecutor({}),
            )
        )

    assert calls == []
    assert not (tmp_path / "postprocess/checkpoint.json").exists()


def test_observer_failure_is_sanitized_and_driver_is_closed(tmp_path: Path) -> None:
    paths = _ready_chain(tmp_path)

    async def broken_query(_driver, _observation, _namespace):
        raise RuntimeError("private namespace, URI, password, and prompt")

    result, trace = _execute(paths, query_executor=broken_query)

    assert result["status"] == "incomplete_non_mergeable"
    assert result["failure_stage"] == "observation"
    assert result["error_class"].endswith("S5PostObservationError")
    assert trace == ["env", "driver", "close"]
    assert not paths.post_observation.exists()
    assert not paths.result.exists()
    checkpoint = inspect_s5_a0_postprocess_checkpoint(
        paths.controller_root.parent / "postprocess/checkpoint.json"
    )
    assert checkpoint["failure_stage"] == "observation"
    assert checkpoint["resume_authorized"] is False
    assert checkpoint["namespace_cleanup_authorized"] is False
    assert checkpoint["current_stage_pointer_update_authorized"] is False
    assert "private namespace" not in repr(result) + repr(checkpoint)


@pytest.mark.parametrize(
    ("kind", "expected_stage", "expected_trace", "observation_written"),
    [
        ("driver", "driver_construction", ["env", "driver"], False),
        ("close", "driver_close", ["env", "driver", "close"], True),
        ("finalizer", "finalization", ["env", "driver", "close"], True),
    ],
)
def test_driver_close_and_finalizer_failures_checkpoint_and_stop(
    tmp_path: Path,
    kind: str,
    expected_stage: str,
    expected_trace: list[str],
    observation_written: bool,
) -> None:
    paths = _ready_chain(tmp_path)
    authority = json.loads(paths.authority.read_text(encoding="utf-8"))
    run = authority["payload"]["run"]
    manifest = json.loads((paths.attempt_root / "manifest.json").read_text())
    sources = [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(manifest["source_sha256s"])
    ]

    def broken_finalizer(**_kwargs):
        raise RuntimeError("private finalizer detail")

    result, trace = _execute(
        paths,
        rows=_rows(run["namespace"], sources, violation=False),
        driver_error=(RuntimeError("private driver detail") if kind == "driver" else None),
        close_error=(RuntimeError("private close detail") if kind == "close" else None),
        finalizer=(broken_finalizer if kind == "finalizer" else None),
    )

    assert result["status"] == "incomplete_non_mergeable"
    assert result["failure_stage"] == expected_stage
    assert result["error_class"] == "builtins.RuntimeError"
    assert trace == expected_trace
    assert paths.post_observation.exists() is observation_written
    assert not paths.result.exists()
    assert "private" not in repr(result)


def test_git_commit_drift_blocks_before_private_env(tmp_path: Path) -> None:
    paths = _ready_chain(tmp_path)
    calls: list[str] = []

    with pytest.raises(S5A0PostprocessError, match="git_commit"):
        asyncio.run(
            execute_s5_a0_postprocess(
                paths=paths,
                git_commit="different-commit",
                env_loader=lambda: calls.append("env"),
                driver_factory=lambda _env: calls.append("driver"),
                query_executor=QueryExecutor({}),
            )
        )

    assert calls == []
    assert not paths.post_observation.exists()
    assert not paths.result.exists()


def test_cli_derives_run_scoped_paths_and_returns_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    captured: dict[str, object] = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"status": "PASS", "final_result_status": "PASS"}

    monkeypatch.setattr(
        "paper_eval.s5_a0_postprocess.execute_s5_a0_postprocess", fake_execute
    )
    run_root = tmp_path / "run"
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
            "--run-root",
            str(run_root),
            "--git-commit",
            "deadbeef",
        ]
    )

    assert code == 0
    assert captured["git_commit"] == "deadbeef"
    paths = captured["paths"]
    assert paths.consumption == run_root / "authority_consumption.json"
    assert paths.controller_root == run_root / "controller"
    assert paths.attempt_root == run_root / "attempt"
    assert paths.post_observation == run_root / "post_observation.json"
    assert paths.result == run_root / "S5_A0_RESULT.json"
    assert "PASS" in capsys.readouterr().out


def test_cli_unknown_failure_is_sanitized_and_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    async def fake_execute(**_kwargs):
        raise RuntimeError("private URI credential and namespace")

    monkeypatch.setattr(
        "paper_eval.s5_a0_postprocess.execute_s5_a0_postprocess", fake_execute
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
            "--run-root",
            str(tmp_path / "run"),
            "--git-commit",
            "deadbeef",
        ]
    )

    captured = capsys.readouterr()
    assert code != 0
    assert "RuntimeError" in captured.err
    assert "private URI" not in captured.err
    assert "credential" not in captured.err


def test_production_parser_has_no_cleanup_or_resume_surface() -> None:
    destinations = {action.dest for action in build_parser()._actions}

    assert {
        "production_identity",
        "production_identity_qualification",
        "current_stage_pointer",
        "preflight",
        "authority",
        "run_root",
        "git_commit",
        "env_file",
    }.issubset(destinations)
    assert "cleanup" not in destinations
    assert "resume" not in destinations
