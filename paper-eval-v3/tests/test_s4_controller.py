"""Offline orchestration tests for the one-history S4 tmux controller."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.s4_controller import (
    compose_phase_specs,
    ensure_authority_consumption,
    orchestrate_s4_smoke,
    resolve_private_cache_paths,
    safe_event_sink,
)


def _authority() -> dict:
    return {
        "payload": {
            "execution_order": ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"],
            "history": {
                "data_role": "DEVELOPMENT_EXPOSED",
                "episode_count": 49,
                "history_id": "07741c45",
            },
            "runs": {
                "U0_CAPTURE": {
                    "cache_id": "s4-cache",
                    "method": "U0",
                    "mode": "capture",
                    "namespace": "pev3-s4-capture",
                    "run_id": "s4-capture",
                },
                "D0_READ_ONLY_REPLAY": {
                    "cache_id": "s4-cache",
                    "method": "D0",
                    "mode": "replay",
                    "namespace": "pev3-s4-replay",
                    "run_id": "s4-replay",
                },
            },
            "private_cache": {
                "prompt_relpath": "runtime/private/s4/prompt.jsonl",
                "embedding_relpath": "runtime/private/s4/embedding.jsonl",
                "reportable_contents": False,
            },
        }
    }


def _phase(mode: str, status: str = "PASS") -> dict:
    return {
        "payload": {
            "phase": "U0_CAPTURE" if mode == "capture" else "D0_READ_ONLY_REPLAY",
            "mode": mode,
            "status": status,
        }
    }


@pytest.mark.asyncio
async def test_orchestrator_runs_capture_then_conditional_replay() -> None:
    calls: list[str] = []

    async def execute(spec: dict) -> dict:
        calls.append(f"{spec['phase']}:{spec['mode']}:{spec['history_id']}")
        return _phase(spec["mode"])

    def evaluate(*, capture_result: dict, replay_result: dict) -> dict:
        assert capture_result["payload"]["mode"] == "capture"
        assert replay_result["payload"]["mode"] == "replay"
        return {"verdict": "PASS", "s4_four_history_qualification_authorized": True}

    result = await orchestrate_s4_smoke(
        authority=_authority(),
        execute_phase=execute,
        evaluate=evaluate,
    )

    assert calls == [
        "U0_CAPTURE:capture:07741c45",
        "D0_READ_ONLY_REPLAY:replay:07741c45",
    ]
    assert result["evaluation"]["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_orchestrator_never_runs_replay_after_capture_failure() -> None:
    calls: list[str] = []

    async def execute(spec: dict) -> dict:
        calls.append(f"{spec['phase']}:{spec['mode']}:{spec['history_id']}")
        return _phase(spec["mode"], status="INCOMPLETE")

    with pytest.raises(RuntimeError, match="capture"):
        await orchestrate_s4_smoke(
            authority=_authority(),
            execute_phase=execute,
            evaluate=lambda **_kwargs: {"verdict": "PASS"},
        )
    assert calls == ["U0_CAPTURE:capture:07741c45"]


def test_phase_specs_are_complete_before_authority_consumption() -> None:
    specs = compose_phase_specs(_authority())
    assert [set(spec) for spec in specs] == [
        {"phase", "run_id", "history_id", "namespace", "method", "mode", "cache_id"},
        {"phase", "run_id", "history_id", "namespace", "method", "mode", "cache_id"},
    ]
    assert [spec["history_id"] for spec in specs] == ["07741c45", "07741c45"]


def test_private_cache_paths_cannot_escape_project(tmp_path: Path) -> None:
    paths = resolve_private_cache_paths(_authority(), project_root=tmp_path)
    assert paths.prompt == tmp_path / "runtime/private/s4/prompt.jsonl"
    assert paths.embedding == tmp_path / "runtime/private/s4/embedding.jsonl"

    authority = _authority()
    authority["payload"]["private_cache"]["prompt_relpath"] = "../secret"
    with pytest.raises(ValueError, match="private cache"):
        resolve_private_cache_paths(authority, project_root=tmp_path)


def test_existing_matching_consumption_is_a_resume_not_reuse(tmp_path: Path) -> None:
    path = tmp_path / "consumption.json"
    expected = {
        "payload": {
            "authority_file_sha256": "1" * 64,
            "authority_payload_sha256": "2" * 64,
            "consumed_action": "S4_SMOKE_PIPELINE",
        }
    }
    calls: list[bool] = []

    def consume() -> dict:
        calls.append(True)
        path.write_text(json.dumps(expected), encoding="utf-8")
        return expected

    def verify(value: dict) -> dict:
        return value

    first = ensure_authority_consumption(
        path=path,
        authority_file_sha256="1" * 64,
        authority_payload_sha256="2" * 64,
        consume=consume,
        verify=verify,
    )
    second = ensure_authority_consumption(
        path=path,
        authority_file_sha256="1" * 64,
        authority_payload_sha256="2" * 64,
        consume=consume,
        verify=verify,
    )
    assert first == second == expected
    assert calls == [True]

    bad = json.loads(path.read_text())
    bad["payload"]["authority_file_sha256"] = "9" * 64
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="consumption"):
        ensure_authority_consumption(
            path=path,
            authority_file_sha256="1" * 64,
            authority_payload_sha256="2" * 64,
            consume=consume,
            verify=verify,
        )


def test_safe_event_sink_prints_no_private_fields(capsys: pytest.CaptureFixture) -> None:
    safe_event_sink(
        {
            "event_type": "failure",
            "source_sequence": 7,
            "error_class": "ConnectionError",
            "raw_output": "private",
            "prompt": "private",
        }
    )
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "event_type": "failure",
        "source_sequence": 7,
        "error_class": "ConnectionError",
    }
    assert "private" not in output
