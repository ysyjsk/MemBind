from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.sampler import (
    DurableSampleWriter,
    PeriodicSampler,
    SamplerError,
    collect_sample,
    summarize_samples,
)


def _row(timestamp_ns: int, construction: str = "MEASURED") -> dict[str, object]:
    return {
        "monotonic_ns": timestamp_ns,
        "observations": {
            "construction_vllm": {"availability": construction},
            "embedding_vllm": {"availability": "MEASURED"},
            "runner_cpu": {"availability": "MEASURED"},
        },
    }


def test_sampler_summary_preserves_actual_gaps_and_per_source_coverage() -> None:
    rows = [_row(0), _row(1_000_000_000), _row(2_000_000_000), _row(4_000_000_000)]
    summary = summarize_samples(
        rows,
        window_start_ns=0,
        window_end_ns=4_000_000_000,
        target_period_s=1.0,
    )
    assert summary["expected_samples"] == 5
    assert summary["actual_samples"] == 4
    assert summary["coverage"] == pytest.approx(0.8)
    assert summary["gap_p50_s"] == pytest.approx(1.0)
    assert summary["gap_p95_s"] == pytest.approx(2.0)
    assert summary["gap_max_s"] == pytest.approx(2.0)
    assert summary["source_coverage"] == {
        "construction_vllm": 1.0,
        "embedding_vllm": 1.0,
        "runner_cpu": 1.0,
    }


def test_sampler_does_not_convert_probe_failures_to_zero() -> None:
    def failed_probe() -> object:
        raise TimeoutError("provider timeout")

    row = collect_sample(
        {"provider_gpu": failed_probe, "runner_cpu": lambda: {"utilization": 12.0}},
        monotonic_ns=10,
        wall_time="2026-08-21T04:30:00+08:00",
    )
    assert row["observations"]["provider_gpu"] == {
        "availability": "INVALID",
        "value": None,
        "reason": "builtins.TimeoutError",
    }
    assert row["observations"]["runner_cpu"] == {
        "availability": "MEASURED",
        "value": {"utilization": 12.0},
        "reason": None,
    }


def test_durable_sample_writer_is_append_only_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    writer = DurableSampleWriter(path)
    writer.append(_row(1))
    writer.append(_row(2))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["monotonic_ns"] for row in rows] == [1, 2]
    with pytest.raises(SamplerError, match="SAMPLE_TIMESTAMP_NOT_MONOTONIC"):
        writer.append(_row(2))


@pytest.mark.asyncio
async def test_periodic_sampler_runs_until_explicit_stop(tmp_path: Path) -> None:
    sampler = PeriodicSampler(
        probes={"runner_cpu": lambda: {"utilization": 10.0}},
        output_path=tmp_path / "telemetry.jsonl",
        target_period_s=0.01,
    )
    await sampler.start()
    await asyncio.sleep(0.045)
    summary = await sampler.stop()
    assert summary["actual_samples"] >= 3
    assert summary["source_coverage"] == {"runner_cpu": 1.0}
    assert summary["target_period_s"] == pytest.approx(0.01)
    with pytest.raises(SamplerError, match="SAMPLER_ALREADY_STOPPED"):
        await sampler.stop()
