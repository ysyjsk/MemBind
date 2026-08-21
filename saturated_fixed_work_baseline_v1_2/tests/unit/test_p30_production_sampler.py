from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.production_sampler import (
    REQUIRED_PROBE_SOURCES,
    ProductionSamplerError,
    build_production_probes,
    parse_provider_gpu_csv,
    qualify_sampler_summary,
    run_sampler_qualification,
)
from saturated_fixed_work_baseline_v1_2.sampler import collect_sample


GPU_UUID = "GPU-01234567-89ab-cdef-0123-456789abcdef"


def _metrics(running: int = 0, waiting: int = 0) -> str:
    return "\n".join(
        (
            f"vllm:num_requests_running {running}",
            f"vllm:num_requests_waiting {waiting}",
            "vllm:kv_cache_usage_perc 0.125",
            "vllm:prefix_cache_queries_total 10",
            "vllm:prefix_cache_hits_total 4",
            "vllm:num_preemptions_total 0",
            "vllm:prompt_tokens_total 100",
            "vllm:generation_tokens_total 20",
        )
    )


def test_provider_gpu_csv_requires_physical_uuid_and_numeric_fields() -> None:
    rows = parse_provider_gpu_csv(
        "0, NVIDIA H100 80GB HBM3, "
        f"{GPU_UUID}, 81559, 1024, 41, 22, 1980, 1593, 0, Disabled\n"
    )
    assert rows == [
        {
            "index": 0,
            "name": "NVIDIA H100 80GB HBM3",
            "uuid": GPU_UUID,
            "memory_total_mib": 81559.0,
            "memory_used_mib": 1024.0,
            "utilization_gpu_percent": 41.0,
            "power_draw_w": 22.0,
            "clock_sm_mhz": 1980.0,
            "clock_memory_mhz": 1593.0,
            "temperature_c": 0.0,
            "mig_mode": "Disabled",
        }
    ]
    with pytest.raises(ProductionSamplerError, match="PROVIDER_GPU_UUID_INVALID"):
        parse_provider_gpu_csv(
            "0, NVIDIA H100, gpu1, 81559, 1024, 41, 22, 1980, 1593, 40, Disabled\n"
        )


def test_production_probe_composition_preserves_provider_failure(
    repository_root: Path,
) -> None:
    def getter(url: str, timeout_s: float) -> dict[str, Any]:
        del timeout_s
        assert url in {
            "http://10.87.5.247:8000/metrics",
            "http://10.87.5.247:8001/metrics",
        }
        return {"text": _metrics()}

    def denied(args: tuple[str, ...], timeout_s: float) -> tuple[int, str, str]:
        del timeout_s
        assert args[0:2] == ("ssh", "zju-liuyi")
        return 126, "", "forced command denied"

    probes = build_production_probes(
        repository_root=repository_root,
        runner_pid=os.getpid(),
        neo4j_pid=os.getpid(),
        ssh_alias="zju-liuyi",
        http_getter=getter,
        provider_command_runner=denied,
    )
    assert tuple(probes) == REQUIRED_PROBE_SOURCES

    row = collect_sample(
        probes,
        monotonic_ns=10,
        wall_time="2026-08-21T04:40:00+08:00",
    )
    assert row["observations"]["construction_vllm"]["availability"] == "MEASURED"
    assert row["observations"]["construction_vllm"]["value"]["values"][
        "running_requests"
    ] == 0.0
    assert row["observations"]["embedding_vllm"]["availability"] == "MEASURED"
    assert row["observations"]["runner_process"]["value"]["pid"] == os.getpid()
    assert row["observations"]["neo4j_process"]["value"]["pid"] == os.getpid()
    assert row["observations"]["runner_host"]["availability"] == "MEASURED"
    assert row["observations"]["provider_gpu"] == {
        "availability": "INVALID",
        "value": None,
        "reason": (
            "saturated_fixed_work_baseline_v1_2.production_sampler."
            "ProductionSamplerError"
        ),
    }


def _summary(**overrides: Any) -> dict[str, Any]:
    result = {
        "duration_s": 60.2,
        "expected_samples": 61,
        "actual_samples": 61,
        "coverage": 1.0,
        "gap_p95_s": 1.02,
        "gap_max_s": 1.09,
        "source_coverage": {name: 1.0 for name in REQUIRED_PROBE_SOURCES},
    }
    result.update(overrides)
    return result


def test_sampler_qualification_requires_every_production_source() -> None:
    passed = qualify_sampler_summary(_summary())
    assert passed["status"] == "PASS"
    assert passed["formal_run_authorized"] is True

    blocked_summary = _summary()
    blocked_summary["source_coverage"] = {
        **blocked_summary["source_coverage"],
        "provider_gpu": 0.0,
    }
    blocked = qualify_sampler_summary(blocked_summary)
    assert blocked["status"] == "INVALID"
    assert blocked["formal_run_authorized"] is False
    assert blocked["failed_gates"] == ["SOURCE_COVERAGE_PROVIDER_GPU"]


@pytest.mark.asyncio
async def test_60_second_driver_writes_once_and_cannot_overwrite(
    tmp_path: Path,
) -> None:
    class FakeSampler:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def start(self) -> None:
            self.events.append("start")

        async def stop(self) -> dict[str, Any]:
            self.events.append("stop")
            return _summary()

    durations: list[float] = []

    async def fake_sleep(duration_s: float) -> None:
        durations.append(duration_s)

    sampler = FakeSampler()
    output = tmp_path / "sampler_qualification.json"
    result = await run_sampler_qualification(
        sampler=sampler,
        duration_s=60.0,
        output_path=output,
        sleep=fake_sleep,
    )
    assert sampler.events == ["start", "stop"]
    assert durations == [60.0]
    assert result["status"] == "PASS"
    assert json.loads(output.read_text(encoding="utf-8")) == result

    with pytest.raises(ProductionSamplerError, match="SAMPLER_QUALIFICATION_EXISTS"):
        await run_sampler_qualification(
            sampler=FakeSampler(),
            duration_s=60.0,
            output_path=output,
            sleep=fake_sleep,
        )


@pytest.mark.asyncio
async def test_60_second_driver_refuses_short_window(tmp_path: Path) -> None:
    with pytest.raises(ProductionSamplerError, match="SAMPLER_DURATION_TOO_SHORT"):
        await run_sampler_qualification(
            sampler=object(),
            duration_s=59.99,
            output_path=tmp_path / "sampler_qualification.json",
        )
