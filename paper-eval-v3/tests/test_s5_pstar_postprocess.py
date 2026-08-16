"""Offline TDD for the P* postprocess lifecycle."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from paper_eval.s5_pstar_post_observation import build_s5_pstar_post_observation
from paper_eval.s5_pstar_postprocess import (
    S5PStarPostprocessError,
    build_parser,
    execute_s5_pstar_postprocess,
    inspect_s5_pstar_postprocess_checkpoint,
    main,
    _default_observer,
)
from paper_eval.s5_pstar_result_finalizer import verify_s5_pstar_result
from tests.test_s5_pstar_result_finalizer import _completed_chain


class _Driver:
    def __init__(self, trace): self.trace = trace
    async def close(self): self.trace.append("close")


def _ready(tmp_path: Path, *, fail=False):
    paths = _completed_chain(tmp_path, fail_source=1 if fail else None)
    existing = json.loads(paths.post_observation.read_text())
    paths.post_observation.unlink()
    return paths, existing


def _run(paths, observation, trace):
    async def observer(**kwargs):
        trace.append("observe")
        assert len(kwargs["source_terminals"]) == 49
        return observation
    return asyncio.run(execute_s5_pstar_postprocess(
        paths=paths, git_commit="deadbeef",
        env_loader=lambda: trace.append("env") or {"private": True},
        driver_factory=lambda _env: trace.append("driver") or _Driver(trace),
        observer=observer,
    ))


@pytest.mark.parametrize(("fail", "status"), [(False, "PASS"), (True, "TREATMENT_FAILURE_OBSERVED")])
def test_full_and_partial_terminal_branches_finalize(tmp_path: Path, fail: bool, status: str):
    paths, observation = _ready(tmp_path, fail=fail)
    trace = []
    result = _run(paths, observation, trace)
    assert result["status"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert result["post_observation_status"] == status
    assert trace == ["env", "driver", "observe", "close"]
    assert verify_s5_pstar_result(json.loads(paths.result.read_text()))["payload"]["verdict"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    checkpoint = inspect_s5_pstar_postprocess_checkpoint(paths.result.parent / "postprocess/checkpoint.json")
    assert checkpoint["status"] == "complete"
    assert sum(checkpoint["terminal_accounting"][k] for k in ("published", "failed", "censored")) == 49


def test_successful_repeat_is_verified_idempotent_without_private_io(tmp_path: Path):
    paths, observation = _ready(tmp_path)
    first = _run(paths, observation, [])
    calls = []
    second = asyncio.run(execute_s5_pstar_postprocess(
        paths=paths, git_commit="deadbeef",
        env_loader=lambda: calls.append("env"),
        driver_factory=lambda _env: calls.append("driver"),
        observer=lambda **_kwargs: calls.append("observe"),
    ))
    assert second == first
    assert calls == []


def test_existing_conflicting_output_fails_closed(tmp_path: Path):
    paths, observation = _ready(tmp_path)
    _run(paths, observation, [])
    paths.post_observation.write_text("{}\n")
    with pytest.raises(S5PStarPostprocessError, match="existing"):
        asyncio.run(execute_s5_pstar_postprocess(
            paths=paths, git_commit="deadbeef", env_loader=lambda: {},
            driver_factory=lambda _env: object(), observer=lambda **_kwargs: {},
        ))


def test_observer_failure_writes_sanitized_terminal_checkpoint(tmp_path: Path):
    paths, _observation = _ready(tmp_path)
    async def broken(**_kwargs):
        raise RuntimeError("private URI password namespace and prompt")
    result = asyncio.run(execute_s5_pstar_postprocess(
        paths=paths, git_commit="deadbeef", env_loader=lambda: {"private": True},
        driver_factory=lambda _env: _Driver([]), observer=broken,
    ))
    assert result["status"] == "incomplete_non_mergeable"
    assert result["failure_stage"] == "observation"
    assert result["error_class"] == "builtins.RuntimeError"
    assert "private" not in repr(result)
    checkpoint = inspect_s5_pstar_postprocess_checkpoint(paths.result.parent / "postprocess/checkpoint.json")
    assert checkpoint["status"] == "incomplete_non_mergeable"
    assert checkpoint["resume_authorized"] is False
    assert not paths.result.exists()


def test_parser_and_cli_derive_pstar_paths(tmp_path: Path, monkeypatch, capsys):
    captured = {}
    async def fake(**kwargs): captured.update(kwargs); return {"status": "SCIENTIFIC_OUTCOME_COMPLETE"}
    monkeypatch.setattr("paper_eval.s5_pstar_postprocess.execute_s5_pstar_postprocess", fake)
    args = []
    for option in ("production-identity", "production-identity-qualification", "preflight", "authority", "predecessor", "run-root"):
        args += [f"--{option}", str(tmp_path / option)]
    args += ["--git-commit", "deadbeef"]
    assert main(args) == 0
    assert captured["paths"].result == tmp_path / "run-root/S5_PSTAR_RESULT.json"
    destinations = {action.dest for action in build_parser()._actions}
    assert "resume" not in destinations and "cleanup" not in destinations
    required = {
        option for action in build_parser()._actions if action.required
        for option in action.option_strings if option.startswith("--")
    }
    assert required == {
        "--production-identity", "--production-identity-qualification",
        "--preflight", "--authority", "--predecessor", "--run-root",
        "--git-commit",
    }
    launcher = (Path(__file__).resolve().parents[1] / "scripts/run_s5_pstar_tmux.sh").read_text()
    assert all(option in launcher for option in required)
    assert "SCIENTIFIC_OUTCOME_COMPLETE" in capsys.readouterr().out


def test_default_bounded_observer_derives_real_violation_classes(monkeypatch):
    expected = [{"source_sequence": i, "source_sha256": f"{i + 1:064x}"} for i in range(49)]
    terminals = [{**row, "terminal_classification": "PUBLISHED"} for row in expected]
    episodes = [{**row, "record_id": f"ep-{row['source_sequence']}", "group_id": "ns"} for row in expected]
    entities = [{"record_id": "a", "group_id": "ns"}, {"record_id": "b", "group_id": "foreign"}]
    relations = [{
        "record_id": "r", "group_id": "foreign", "source_entity_id": "a",
        "target_entity_id": "b", "provenance": [{"episode_id": "ep-0", "group_id": "foreign", "exists": True}],
        "valid_at": "2026-01-02T00:00:00Z", "invalid_at": "2026-01-01T00:00:00Z",
    }]
    class Query:
        def __init__(self, **_kwargs): pass
        async def __call__(self, _driver, kind, _namespace):
            return {"EPISODIC": episodes, "ENTITY": entities, "RELATES_TO": relations}[kind]
    monkeypatch.setattr("paper_eval.s5_pstar_postprocess.S5GraphitiPostQueryExecutor", Query)
    post = asyncio.run(_default_observer(
        driver=object(), run={"run_id": "s5-p-star-20260816-222", "namespace": "ns"},
        expected_sources=expected, source_terminals=terminals,
    ))
    assert post["status"] == "DIRECT_INVARIANT_VIOLATION_OBSERVED"
    assert post["violation_counts"]["relation_namespace_escape_count"] == 1
    assert post["violation_counts"]["endpoint_escape_count"] == 1
    assert post["violation_counts"]["provenance_cross_namespace_count"] == 1
    assert post["violation_counts"]["valid_invalid_reversal_count"] == 1
    assert post["per_source_violation_counts"]["0"] == 4
