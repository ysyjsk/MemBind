from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6.runner import (
    V6Config,
    V6RunnerError,
    build_v6_live_command,
    build_v6_parser,
    _persist_v6_partial_evidence,
    run_v6_frontier_provider_free,
    run_v6_live_async,
    v6_live_authorization_checker,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import CapacityAuthority
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.executor import FrontierExecutor


def _config(tmp_path: Path, **overrides):
    values = {
        "repo_root": Path("/data/predator/ly/MemBind"),
        "baseline_root": Path("/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-formal-baseline-20260822-002"),
        "state_path": Path("/data/predator/ly/MemBind/membind-validation/CURRENT_STATE.json"),
        "output_root": tmp_path / "attempt",
        "run_id": "v6-r01-control",
        "history_id": "6071bd76",
        "policy": "matched-control",
        "full_history": False,
        "source_limit": 2,
    }
    values.update(overrides)
    return V6Config(**values)


def test_parser_requires_explicit_policy_and_history_mode() -> None:
    parser = build_v6_parser()
    args = parser.parse_args(
        [
            "--repo-root",
            "/repo",
            "--baseline-root",
            "/baseline",
            "--state",
            "/state",
            "--output-root",
            "/out",
            "--run-id",
            "v6-r01-control",
            "--history-id",
            "6071bd76",
            "--full-history",
            "--policy",
            "v6",
        ]
    )
    assert args.history_id == "6071bd76"
    assert args.full_history is True
    assert args.policy == "v6"


def test_config_rejects_v5_or_alternate_endpoint_overrides(tmp_path: Path) -> None:
    with pytest.raises(V6RunnerError, match="policy"):
        _config(tmp_path, policy="v5")
    with pytest.raises(V6RunnerError, match="8000/8001"):
        _config(tmp_path, construction_base_url="http://10.87.5.247:8002/v1/")


def test_full_history_requires_no_source_limit_and_uses_frozen_identity(tmp_path: Path) -> None:
    config = _config(tmp_path, full_history=True, source_limit=None)
    assert config.source_limit is None
    assert config.history_id == "6071bd76"
    command = build_v6_live_command(config, python="python")
    assert "run_v6.py" in command
    assert "--full-history" in command
    assert "--policy matched-control" in command
    assert "8002" not in command and "8003" not in command


def test_provider_free_frontier_preserves_order_and_failure_boundary() -> None:
    async def run() -> dict:
        prepared: list[int] = []
        published: list[int] = []

        async def prepare(sequence: int):
            prepared.append(sequence)
            await asyncio.sleep(0)
            return {"sequence": sequence}

        async def publish(sequence: int, _value):
            published.append(sequence)

        return await run_v6_frontier_provider_free(4, prepare, publish)

    result = asyncio.run(run())
    assert result["durable_frontier"] == 3
    assert result["publication_order"] == [0, 1, 2, 3]
    assert result["preparation_count"] == 4


def test_config_rejects_full_history_with_prefix_limit(tmp_path: Path) -> None:
    with pytest.raises(V6RunnerError, match="source_limit"):
        _config(tmp_path, full_history=True, source_limit=2)


def test_v6_live_authorization_does_not_read_legacy_formal_gate() -> None:
    decision = v6_live_authorization_checker("formal", state_path=Path("missing-state"))
    assert decision["allowed"] is True
    assert decision["reason"] == "explicit_v6_live_authorization"


def test_live_runner_does_not_call_legacy_formal_checker(tmp_path: Path) -> None:
    config = _config(tmp_path)
    called = {"runtime": False, "checker": False}

    def legacy_checker(*_args, **_kwargs):
        called["checker"] = True
        raise RuntimeError("FORMAL_GATE_MUST_NOT_BE_USED")

    def runtime_builder():
        called["runtime"] = True
        raise RuntimeError("runtime reached after V6-owned authorization")

    with pytest.raises(RuntimeError, match="runtime reached"):
        asyncio.run(
            run_v6_live_async(
                config,
                runtime_builder=runtime_builder,
                episode_loader=lambda *_args: (SimpleNamespace(source_sequence=0),),
                instrumentation_installer=lambda *_args: None,
                recorder_factory=lambda: None,
                graph_exporter=lambda *_args: {},
                authorization_checker=legacy_checker,
            )
        )
    assert called["runtime"] is True
    assert called["checker"] is False
    assert config.output_root.exists()


def test_future_prepare_failure_never_advances_v6_durable_frontier() -> None:
    async def run() -> tuple[list[int], list[dict]]:
        published: list[int] = []
        events: list[dict] = []

        async def prepare(sequence: int):
            await asyncio.sleep(0)
            if sequence == 2:
                raise RuntimeError("future failure")
            return sequence

        async def publish(sequence: int, _value):
            published.append(sequence)

        executor = FrontierExecutor(
            4,
            CapacityAuthority(4),
            prepare_admission=False,
            event_sink=events.append,
            admit_native=False,
        )
        with pytest.raises(RuntimeError, match="future failure"):
            await executor.run(prepare, publish)
        return published, events

    published, events = asyncio.run(run())
    assert published == [0, 1]
    assert [row["source_sequence"] for row in events if row.get("event") == "PUBLICATION_DURABLE"] == [0, 1]


def test_matched_control_keeps_v5_source_preparation_enabled() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v6.runner import matched_control_uses_preparation

    assert matched_control_uses_preparation() is True


def test_v6_failure_persists_transport_attempt_finish_reason(tmp_path: Path) -> None:
    recorder = SimpleNamespace(
        records=[
            SimpleNamespace(
                phase="llm-transport",
                source_sequence=12,
                start_ns=10,
                end_ns=20,
                status="ok",
                error_code=None,
                metadata={
                    "attempt_index": 3,
                    "input_tokens": 1200,
                    "output_tokens": 4096,
                    "usage_observed": True,
                    "finish_reason_observed": True,
                    "finish_reason": "length",
                },
            )
        ]
    )

    summary = _persist_v6_partial_evidence(tmp_path, recorder)

    assert summary["attempt_count"] == 1
    assert summary["successful_attempt_count"] == 1
    assert summary["finish_reason_observed_count"] == 1
    assert summary["finish_reasons"] == ["length"]
    assert summary["usage_observed_count"] == 1
    assert (tmp_path / "transport_evidence.json").is_file()
    rows = (tmp_path / "transport_attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert '"finish_reason": "length"' in rows[0]
