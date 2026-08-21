from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.production_sampler import (
    REQUIRED_PROBE_SOURCES,
    ProductionSamplerError,
    build_production_probes,
    collect_dynamic_provider_resource_evidence,
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


def test_provider_side_collector_is_dynamic_and_emits_canonical_json(
    repository_root: Path,
) -> None:
    path = (
        repository_root
        / "saturated_fixed_work_baseline_v1_2/scripts/provider/resource-evidence.sh"
    )
    source = path.read_text(encoding="utf-8")
    syntax = subprocess.run(
        ("bash", "-n", str(path)), check=False, capture_output=True, text=True
    )
    assert syntax.returncode == 0, syntax.stderr
    assert "for port in (8000, 8001)" in source
    assert "--query-compute-apps=pid,gpu_uuid" in source
    assert '"schema_version"' in source
    assert "/proc/{pid}/cmdline" in source
    assert "/proc/{pid}/environ" in source
    assert "/proc/{pid}/exe" in source
    assert "CUDA_VISIBLE_DEVICES" in source
    assert "NVIDIA_VISIBLE_DEVICES" in source
    assert '"model_revision"' in source
    assert '"vllm_version"' in source
    assert '"torch_version"' in source
    assert '"xgrammar_version"' in source
    for forbidden_pid in (739403, 739223, 1645134, 1645531, 1645619, 1646146):
        assert str(forbidden_pid) not in source


def _provider_snapshot(pid_8000: int = 41001, pid_8001: int = 42001, uuid: str = GPU_UUID,
                       *, nvidia_visible: str | None = "GPU-01234567-89ab-cdef-0123-456789abcdef") -> dict[str, Any]:
    def service(port: int, pid: int, engine_pid: int) -> dict[str, Any]:
        return {
            "listener_pid": pid,
            "argv": ["python", "-m", "vllm.entrypoints.openai.api_server", "--port", str(port)],
            "cuda_visible_devices": "1",
            "nvidia_visible_devices": nvidia_visible,
            "engine_core_pid": engine_pid,
            "engine_core_argv": ["VLLM::EngineCore"],
            "engine_core_cuda_visible_devices": "1",
            "engine_core_nvidia_visible_devices": nvidia_visible,
            "gpu_uuids": [uuid],
        }
    return {
        "schema_version": "membind.provider.resource-evidence.v2",
        "hostname": "ZJU-Pro6000",
        "machine_id": "machine-a",
        "boot_id": "boot-a",
        "gpus": [{"index": 1, "name": "NVIDIA RTX PRO 6000", "uuid": uuid,
                  "pci_bus_id": "00000000:E1:00.0", "memory_total_mib": 179292,
                  "mig_mode": "Disabled"}],
        "compute_processes": [
            {"pid": 41077, "gpu_uuid": uuid},
            {"pid": 42077, "gpu_uuid": uuid},
        ],
        "services": {"8000": service(8000, pid_8000, 41077),
                     "8001": service(8001, pid_8001, 42077)},
    }


def test_dynamic_provider_evidence_uses_only_fixed_resource_evidence_rpc() -> None:
    calls: list[tuple[str, ...]] = []
    snapshot = _provider_snapshot()

    def runner(args: tuple[str, ...], timeout_s: float) -> tuple[int, str, str]:
        del timeout_s
        calls.append(args)
        if args == ("ssh", "zju-liuyi", "resource-evidence"):
            return 0, json.dumps(snapshot), ""
        return 126, "", "forced command denied"

    evidence = collect_dynamic_provider_resource_evidence(
        ssh_alias="zju-liuyi", provider_command_runner=runner
    )
    assert calls == [("ssh", "zju-liuyi", "resource-evidence")]
    assert evidence["services"]["8000"]["listener_pid"] == 41001
    assert evidence["services"]["8000"]["engine_core_pid"] == 41077
    assert evidence["services"]["8000"]["gpu_uuids"] == [GPU_UUID]
    assert evidence["services"]["8001"]["listener_pid"] == 42001


def test_dynamic_provider_evidence_accepts_restart_pid_changes_without_manual_edit() -> None:
    snapshots = [_provider_snapshot(), _provider_snapshot(51001, 52001)]
    calls: list[tuple[str, ...]] = []

    def runner(args: tuple[str, ...], timeout_s: float) -> tuple[int, str, str]:
        del timeout_s
        calls.append(args)
        return 0, json.dumps(snapshots.pop(0)), ""

    first = collect_dynamic_provider_resource_evidence(
        ssh_alias="zju-liuyi", provider_command_runner=runner
    )
    second = collect_dynamic_provider_resource_evidence(
        ssh_alias="zju-liuyi", provider_command_runner=runner
    )
    assert first["services"]["8000"]["listener_pid"] != second["services"]["8000"]["listener_pid"]
    assert first["gpus"] == second["gpus"]
    assert len(calls) == 2


def test_dynamic_provider_evidence_rejects_physical_uuid_change() -> None:
    changed = _provider_snapshot(uuid="GPU-ffffffff-ffff-ffff-ffff-ffffffffffff")

    def runner(args: tuple[str, ...], timeout_s: float) -> tuple[int, str, str]:
        del args, timeout_s
        return 0, json.dumps(changed), ""

    with pytest.raises(ProductionSamplerError, match="PROVIDER_GPU_UUID_IDENTITY_CHANGED"):
        collect_dynamic_provider_resource_evidence(
            ssh_alias="zju-liuyi",
            provider_command_runner=runner,
            expected_gpu_uuids=[GPU_UUID],
        )


def test_dynamic_provider_evidence_requires_cuda_but_allows_unset_nvidia_visible() -> None:
    snapshot = _provider_snapshot(nvidia_visible=None)

    def runner(args: tuple[str, ...], timeout_s: float) -> tuple[int, str, str]:
        del args, timeout_s
        return 0, json.dumps(snapshot), ""

    evidence = collect_dynamic_provider_resource_evidence(
        ssh_alias="zju-liuyi", provider_command_runner=runner
    )
    assert evidence["services"]["8000"]["nvidia_visible_devices"] is None

    missing_cuda = _provider_snapshot()
    missing_cuda["services"]["8000"]["cuda_visible_devices"] = None

    def missing_runner(args: tuple[str, ...], timeout_s: float) -> tuple[int, str, str]:
        del args, timeout_s
        return 0, json.dumps(missing_cuda), ""

    with pytest.raises(ProductionSamplerError, match="PROVIDER_CUDA_ENV_INVALID"):
        collect_dynamic_provider_resource_evidence(
            ssh_alias="zju-liuyi", provider_command_runner=missing_runner
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


def test_production_probe_path_uses_the_fixed_resource_evidence_rpc(
    repository_root: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    def getter(url: str, timeout_s: float) -> dict[str, Any]:
        del url, timeout_s
        return {"text": _metrics()}

    def runner(args: tuple[str, ...], timeout_s: float) -> tuple[int, str, str]:
        del timeout_s
        commands.append(args)
        if args != ("ssh", "zju-liuyi", "resource-evidence"):
            return 126, "", "forced command denied"
        return 0, json.dumps(_provider_snapshot()), ""

    probes = build_production_probes(
        repository_root=repository_root,
        runner_pid=os.getpid(),
        neo4j_pid=os.getpid(),
        ssh_alias="zju-liuyi",
        http_getter=getter,
        provider_command_runner=runner,
    )
    observed = probes["provider_gpu"]()
    assert observed[0]["uuid"] == GPU_UUID
    assert commands == [("ssh", "zju-liuyi", "resource-evidence")]


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
