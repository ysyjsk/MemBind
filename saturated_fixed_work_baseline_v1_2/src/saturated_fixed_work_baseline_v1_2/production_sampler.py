"""Production telemetry probes and the strict 60-second L0 sampler gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from .services import direct_get_text
from .telemetry import parse_vllm_026_metrics


class ProductionSamplerError(ValueError):
    """A production probe or sampler qualification contract failed."""


REQUIRED_PROBE_SOURCES = (
    "construction_vllm",
    "embedding_vllm",
    "runner_process",
    "neo4j_process",
    "runner_host",
    "provider_gpu",
)

_GPU_UUID = re.compile(
    r"^GPU-[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$"
)
_PROVIDER_GPU_QUERY = (
    "nvidia-smi --query-gpu=index,name,uuid,pci.bus_id,memory.total,memory.used,"
    "utilization.gpu,power.draw,clocks.sm,clocks.mem,temperature.gpu,"
    "mig.mode.current --format=csv,noheader,nounits"
)

_DYNAMIC_PROVIDER_SCRIPT = r'''
import json, os, re, subprocess

port = int(os.environ["SFWB_PORT"])

def run(command):
    return subprocess.run(command, check=False, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout

def argv(pid):
    with open(f"/proc/{pid}/cmdline", "rb") as handle:
        return [item.decode("utf-8", "replace") for item in handle.read().split(b"\\0") if item]

def environ(pid):
    with open(f"/proc/{pid}/environ", "rb") as handle:
        result = {}
        for item in handle.read().split(b"\\0"):
            if b"=" in item:
                key, value = item.split(b"=", 1)
                result[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
        return result

def process_rows():
    rows = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="utf-8") as handle:
                fields = handle.read().split()
            rows.append({"pid": int(name), "ppid": int(fields[3]),
                         "argv": argv(int(name))})
        except (OSError, ValueError, IndexError):
            continue
    return rows

def listener_pid():
    text = run(["ss", "-ltnp", f"sport = :{port}"])
    matches = re.findall(r"pid=(\\d+)", text)
    if not matches:
        text = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        matches = re.findall(r"\\b(\\d+)\\b", text)
    if not matches:
        raise RuntimeError("listener_pid_unavailable")
    return int(matches[0])

listener = listener_pid()
listener_argv = argv(listener)
listener_env = environ(listener)
rows = process_rows()
by_parent = {}
for row in rows:
    by_parent.setdefault(row["ppid"], []).append(row)
descendants = []
frontier = [listener]
seen = set()
while frontier:
    parent = frontier.pop()
    for row in by_parent.get(parent, []):
        if row["pid"] in seen:
            continue
        seen.add(row["pid"])
        descendants.append(row)
        frontier.append(row["pid"])
engine_rows = [row for row in descendants
               if any("enginecore" in item.lower().replace("_", "")
                      or "engine.core" in item.lower()
                      or "engine_core" in item.lower()
                      for item in row["argv"])]
if not engine_rows:
    raise RuntimeError("engine_core_pid_unavailable")
engine = sorted(engine_rows, key=lambda row: row["pid"])[0]
engine_env = environ(engine["pid"])
compute = run(["nvidia-smi", "--query-compute-apps=pid,gpu_uuid",
               "--format=csv,noheader,nounits"])
compute_rows = []
for line in compute.splitlines():
    fields = [item.strip() for item in line.split(",")]
    if len(fields) != 2:
        continue
    try:
        compute_rows.append({"pid": int(fields[0]), "gpu_uuid": fields[1]})
    except ValueError:
        continue
print(json.dumps({
    "hostname": run(["hostname"]).strip(),
    "hostname_f": run(["hostname", "-f"]).strip(),
    "machine_id": open("/etc/machine-id", encoding="utf-8").read().strip(),
    "boot_id": open("/proc/sys/kernel/random/boot_id", encoding="utf-8").read().strip(),
    "listener_pid": listener,
    "argv": listener_argv,
    "environ": listener_env,
    "process_tree": [{"pid": row["pid"], "ppid": row["ppid"], "argv": row["argv"]}
                      for row in descendants],
    "engine_core_pid": engine["pid"],
    "engine_core_argv": engine["argv"],
    "engine_core_environ": engine_env,
    "compute_processes": compute_rows,
}, sort_keys=True))
'''

HttpGetter = Callable[[str, float], Mapping[str, Any]]
CommandRunner = Callable[[tuple[str, ...], float], tuple[int, str, str]]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _default_http_getter(url: str, timeout_s: float) -> Mapping[str, Any]:
    return direct_get_text(url, timeout_s=timeout_s)


def _default_command_runner(
    args: tuple[str, ...], timeout_s: float
) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProductionSamplerError(
            f"PROVIDER_GPU_COMMAND_FAILED:{type(error).__name__}"
        ) from None
    return result.returncode, result.stdout, result.stderr


def _float(value: str, *, error: str) -> float:
    try:
        selected = float(value.strip())
    except ValueError:
        raise ProductionSamplerError(error) from None
    if not math.isfinite(selected) or selected < 0:
        raise ProductionSamplerError(error)
    return selected


def parse_provider_gpu_csv(text: str) -> list[dict[str, Any]]:
    if not isinstance(text, str) or not text.strip():
        raise ProductionSamplerError("PROVIDER_GPU_OUTPUT_INVALID")
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        fields = [field.strip() for field in raw.split(",")]
        if len(fields) not in (11, 12):
            raise ProductionSamplerError("PROVIDER_GPU_OUTPUT_INVALID")
        try:
            index = int(fields[0])
        except ValueError:
            raise ProductionSamplerError("PROVIDER_GPU_INDEX_INVALID") from None
        if index < 0:
            raise ProductionSamplerError("PROVIDER_GPU_INDEX_INVALID")
        if _GPU_UUID.fullmatch(fields[2]) is None:
            raise ProductionSamplerError("PROVIDER_GPU_UUID_INVALID")
        if not fields[1] or not fields[-1]:
            raise ProductionSamplerError("PROVIDER_GPU_OUTPUT_INVALID")
        offset = 1 if len(fields) == 12 else 0
        if offset and not fields[3]:
            raise ProductionSamplerError("PROVIDER_GPU_PCI_BUS_INVALID")
        rows.append(
            {
                "index": index,
                "name": fields[1],
                "uuid": fields[2],
                **({"pci_bus_id": fields[3]} if offset else {}),
                "memory_total_mib": _float(
                    fields[3 + offset], error="PROVIDER_GPU_MEMORY_INVALID"
                ),
                "memory_used_mib": _float(
                    fields[4 + offset], error="PROVIDER_GPU_MEMORY_INVALID"
                ),
                "utilization_gpu_percent": _float(
                    fields[5 + offset], error="PROVIDER_GPU_UTILIZATION_INVALID"
                ),
                "power_draw_w": _float(
                    fields[6 + offset], error="PROVIDER_GPU_POWER_INVALID"
                ),
                "clock_sm_mhz": _float(
                    fields[7 + offset], error="PROVIDER_GPU_CLOCK_INVALID"
                ),
                "clock_memory_mhz": _float(
                    fields[8 + offset], error="PROVIDER_GPU_CLOCK_INVALID"
                ),
                "temperature_c": _float(
                    fields[9 + offset], error="PROVIDER_GPU_TEMPERATURE_INVALID"
                ),
                "mig_mode": fields[10 + offset],
            }
        )
    if not rows or len({row["uuid"] for row in rows}) != len(rows):
        raise ProductionSamplerError("PROVIDER_GPU_OUTPUT_INVALID")
    return sorted(rows, key=lambda row: int(row["index"]))


def _provider_command(
    *,
    ssh_alias: str,
    command: str,
    provider_command_runner: CommandRunner,
    timeout_s: float,
    error_prefix: str,
) -> str:
    exit_code, stdout, stderr = provider_command_runner(
        ("ssh", ssh_alias, command), float(timeout_s)
    )
    if exit_code != 0:
        del stderr
        raise ProductionSamplerError(f"{error_prefix}:{exit_code}")
    return stdout


def _service_resource_evidence(
    *,
    port: int,
    payload: Mapping[str, Any],
    gpu_uuids: set[str],
) -> dict[str, Any]:
    listener_pid = payload.get("listener_pid")
    listener_argv = payload.get("argv")
    listener_env = payload.get("environ")
    engine_pid = payload.get("engine_core_pid")
    engine_argv = payload.get("engine_core_argv")
    engine_env = payload.get("engine_core_environ")
    compute = payload.get("compute_processes")
    if (
        isinstance(listener_pid, bool)
        or not isinstance(listener_pid, int)
        or listener_pid <= 0
        or not isinstance(listener_argv, list)
        or not listener_argv
        or not all(isinstance(item, str) and item for item in listener_argv)
        or not isinstance(listener_env, Mapping)
        or not isinstance(engine_pid, int)
        or isinstance(engine_pid, bool)
        or engine_pid <= 0
        or not isinstance(engine_argv, list)
        or not engine_argv
        or not all(isinstance(item, str) and item for item in engine_argv)
        or not isinstance(engine_env, Mapping)
        or not isinstance(compute, list)
    ):
        raise ProductionSamplerError(f"PROVIDER_SERVICE_EVIDENCE_INVALID:{port}")
    cuda = listener_env.get("CUDA_VISIBLE_DEVICES") or engine_env.get(
        "CUDA_VISIBLE_DEVICES"
    )
    nvidia = listener_env.get("NVIDIA_VISIBLE_DEVICES") or engine_env.get(
        "NVIDIA_VISIBLE_DEVICES"
    )
    if not isinstance(cuda, str) or not cuda.strip() or not isinstance(nvidia, str) or not nvidia.strip():
        raise ProductionSamplerError("PROVIDER_CUDA_ENV_INVALID")
    mapped = [
        row.get("gpu_uuid")
        for row in compute
        if isinstance(row, Mapping) and row.get("pid") == engine_pid
    ]
    if not mapped or any(not isinstance(uuid, str) or uuid not in gpu_uuids for uuid in mapped):
        raise ProductionSamplerError(f"PROVIDER_ENGINE_GPU_MAPPING_INVALID:{port}")
    return {
        "pid": listener_pid,
        "argv": list(listener_argv),
        "cuda_visible_devices": cuda,
        "nvidia_visible_devices": nvidia,
        "gpu_uuids": sorted(set(mapped)),
        "engine_core_pid": engine_pid,
        "engine_core_argv": list(engine_argv),
        "engine_core_environ": dict(engine_env),
        "process_tree": payload.get("process_tree", []),
    }


def collect_dynamic_provider_resource_evidence(
    *,
    ssh_alias: str,
    ports: tuple[int, ...] = (8000, 8001),
    provider_command_runner: CommandRunner = _default_command_runner,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    if (
        not isinstance(ssh_alias, str)
        or not ssh_alias
        or not ports
        or any(isinstance(port, bool) or not isinstance(port, int) or port <= 0 for port in ports)
        or isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ProductionSamplerError("PROVIDER_RESOURCE_CONFIGURATION_INVALID")
    inventory = parse_provider_gpu_csv(
        _provider_command(
            ssh_alias=ssh_alias,
            command=_PROVIDER_GPU_QUERY,
            provider_command_runner=provider_command_runner,
            timeout_s=timeout_s,
            error_prefix="PROVIDER_GPU_REMOTE_COMMAND_REJECTED",
        )
    )
    physical_uuids = {row["uuid"] for row in inventory}
    services: dict[str, dict[str, Any]] = {}
    for port in ports:
        command = (
            "SFWB_PORT="
            + str(port)
            + " python3 -c "
            + shlex.quote(_DYNAMIC_PROVIDER_SCRIPT)
        )
        raw = _provider_command(
            ssh_alias=ssh_alias,
            command=command,
            provider_command_runner=provider_command_runner,
            timeout_s=timeout_s,
            error_prefix=f"PROVIDER_SERVICE_COMMAND_REJECTED:{port}",
        )
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            raise ProductionSamplerError(f"PROVIDER_SERVICE_OUTPUT_INVALID:{port}") from None
        if not isinstance(payload, Mapping):
            raise ProductionSamplerError(f"PROVIDER_SERVICE_OUTPUT_INVALID:{port}")
        services[str(port)] = _service_resource_evidence(
            port=port, payload=payload, gpu_uuids=physical_uuids
        )
    return {
        "hostname": services[str(ports[0])].get("hostname", ""),
        "gpus": inventory,
        "services": services,
        "dynamic": True,
    }


def _read_proc_status(pid: int) -> dict[str, str]:
    try:
        lines = (Path("/proc") / str(pid) / "status").read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError):
        raise ProductionSamplerError("PROCESS_STATUS_UNREADABLE") from None
    return {
        key.strip(): value.strip()
        for line in lines
        if ":" in line
        for key, value in (line.split(":", 1),)
    }


def _kib(status: Mapping[str, str], field: str) -> int:
    value = status.get(field)
    if not isinstance(value, str):
        raise ProductionSamplerError(f"PROCESS_{field.upper()}_MISSING")
    parts = value.split()
    if len(parts) != 2 or parts[1] != "kB":
        raise ProductionSamplerError(f"PROCESS_{field.upper()}_INVALID")
    try:
        return int(parts[0])
    except ValueError:
        raise ProductionSamplerError(f"PROCESS_{field.upper()}_INVALID") from None


def probe_process(pid: int) -> dict[str, Any]:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ProductionSamplerError("PROCESS_PID_INVALID")
    root = Path("/proc") / str(pid)
    status = _read_proc_status(pid)
    try:
        stat = (root / "stat").read_text(encoding="utf-8")
        remainder = stat[stat.rfind(")") + 2 :].split()
        cmdline = [
            part.decode("utf-8", errors="replace")
            for part in (root / "cmdline").read_bytes().split(b"\0")
            if part
        ]
        fd_count = sum(1 for _ in (root / "fd").iterdir())
    except (OSError, UnicodeError, IndexError):
        raise ProductionSamplerError("PROCESS_PROCFS_UNREADABLE") from None
    if len(remainder) < 22:
        raise ProductionSamplerError("PROCESS_STAT_INVALID")
    try:
        user_ticks = int(remainder[11])
        system_ticks = int(remainder[12])
        threads = int(status["Threads"])
    except (KeyError, ValueError):
        raise ProductionSamplerError("PROCESS_STAT_INVALID") from None
    return {
        "pid": pid,
        "state": remainder[0],
        "user_cpu_ticks": user_ticks,
        "system_cpu_ticks": system_ticks,
        "clock_ticks_per_second": int(os.sysconf("SC_CLK_TCK")),
        "rss_bytes": _kib(status, "VmRSS") * 1024,
        "virtual_memory_bytes": _kib(status, "VmSize") * 1024,
        "threads": threads,
        "file_descriptors": fd_count,
        "argv_sha256": hashlib.sha256("\0".join(cmdline).encode("utf-8")).hexdigest(),
    }


def probe_runner_host() -> dict[str, Any]:
    try:
        cpu_line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
        load = Path("/proc/loadavg").read_text(encoding="ascii").split()
        mem_lines = Path("/proc/meminfo").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError, IndexError):
        raise ProductionSamplerError("RUNNER_HOST_PROCFS_UNREADABLE") from None
    cpu_parts = cpu_line.split()
    if not cpu_parts or cpu_parts[0] != "cpu":
        raise ProductionSamplerError("RUNNER_HOST_CPU_INVALID")
    try:
        cpu_ticks = [int(value) for value in cpu_parts[1:]]
        loads = [float(value) for value in load[:3]]
        memory_kib = {
            key.strip(): int(value.split()[0])
            for line in mem_lines
            if ":" in line
            for key, value in (line.split(":", 1),)
        }
    except (ValueError, IndexError):
        raise ProductionSamplerError("RUNNER_HOST_METRICS_INVALID") from None
    required_memory = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
    if any(name not in memory_kib for name in required_memory):
        raise ProductionSamplerError("RUNNER_HOST_MEMORY_INVALID")
    return {
        "cpu_count_logical": os.cpu_count(),
        "cpu_ticks": cpu_ticks,
        "load_average_1m": loads[0],
        "load_average_5m": loads[1],
        "load_average_15m": loads[2],
        "memory_total_bytes": memory_kib["MemTotal"] * 1024,
        "memory_available_bytes": memory_kib["MemAvailable"] * 1024,
        "swap_total_bytes": memory_kib["SwapTotal"] * 1024,
        "swap_free_bytes": memory_kib["SwapFree"] * 1024,
    }


def build_production_probes(
    *,
    repository_root: Path,
    runner_pid: int,
    neo4j_pid: int,
    ssh_alias: str,
    http_getter: HttpGetter = _default_http_getter,
    provider_command_runner: CommandRunner = _default_command_runner,
    timeout_s: float = 10.0,
) -> dict[str, Callable[[], Any]]:
    if (
        not repository_root.is_dir()
        or isinstance(runner_pid, bool)
        or runner_pid <= 0
        or isinstance(neo4j_pid, bool)
        or neo4j_pid <= 0
        or not isinstance(ssh_alias, str)
        or not ssh_alias
        or isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ProductionSamplerError("PRODUCTION_PROBE_CONFIGURATION_INVALID")

    def vllm(port: int) -> dict[str, Any]:
        try:
            payload = http_getter(
                f"http://10.87.5.247:{port}/metrics", float(timeout_s)
            )["text"]
        except (KeyError, TypeError, OSError, ValueError):
            raise ProductionSamplerError("VLLM_METRICS_UNAVAILABLE") from None
        observation = parse_vllm_026_metrics(
            str(payload),
            timestamp_ns=time.monotonic_ns(),
            repository_root=repository_root,
        )
        if observation.value is None:
            raise ProductionSamplerError(
                f"VLLM_METRICS_INVALID:{observation.reason or 'unknown'}"
            )
        snapshot = observation.value
        return {
            "timestamp_ns": int(snapshot.timestamp_ns),
            "values": dict(snapshot.values),
        }

    def provider_gpu() -> list[dict[str, Any]]:
        exit_code, stdout, _stderr = provider_command_runner(
            ("ssh", ssh_alias, _PROVIDER_GPU_QUERY), float(timeout_s)
        )
        if exit_code != 0:
            raise ProductionSamplerError(
                f"PROVIDER_GPU_REMOTE_COMMAND_REJECTED:{exit_code}"
            )
        return parse_provider_gpu_csv(stdout)

    return {
        "construction_vllm": lambda: vllm(8000),
        "embedding_vllm": lambda: vllm(8001),
        "runner_process": lambda: probe_process(runner_pid),
        "neo4j_process": lambda: probe_process(neo4j_pid),
        "runner_host": probe_runner_host,
        "provider_gpu": provider_gpu,
    }


def qualify_sampler_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        raise ProductionSamplerError("SAMPLER_SUMMARY_INVALID")
    failed: list[str] = []

    def finite_number(name: str) -> float | None:
        value = summary.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return None
        return float(value)

    duration = finite_number("duration_s")
    expected = summary.get("expected_samples")
    actual = summary.get("actual_samples")
    coverage = finite_number("coverage")
    gap_p95 = finite_number("gap_p95_s")
    gap_max = finite_number("gap_max_s")
    if duration is None or duration < 60.0:
        failed.append("DURATION")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 60:
        failed.append("EXPECTED_SAMPLE_COUNT")
    if isinstance(actual, bool) or not isinstance(actual, int) or actual < 1:
        failed.append("ACTUAL_SAMPLE_COUNT")
    if coverage is None or coverage < 0.9 or coverage > 1.0:
        failed.append("TOTAL_COVERAGE")
    if gap_p95 is None or gap_p95 > 1.5 or gap_p95 < 0:
        failed.append("GAP_P95")
    if gap_max is None or gap_max > 2.5 or gap_max < 0:
        failed.append("GAP_MAX")
    source_coverage = summary.get("source_coverage")
    if not isinstance(source_coverage, Mapping):
        failed.extend(f"SOURCE_COVERAGE_{name.upper()}" for name in REQUIRED_PROBE_SOURCES)
    else:
        for name in REQUIRED_PROBE_SOURCES:
            value = source_coverage.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.9 <= float(value) <= 1.0
            ):
                failed.append(f"SOURCE_COVERAGE_{name.upper()}")
    passed = not failed
    result = {
        "schema_version": "membind.saturated-fixed-work.sampler-qualification.v1",
        "status": "PASS" if passed else "INVALID",
        "formal_run_authorized": passed,
        "required_sources": list(REQUIRED_PROBE_SOURCES),
        "failed_gates": failed,
        "summary": dict(summary),
    }
    result["payload_sha256"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


async def run_sampler_qualification(
    *,
    sampler: Any,
    duration_s: float,
    output_path: Path,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    if (
        isinstance(duration_s, bool)
        or not isinstance(duration_s, (int, float))
        or not math.isfinite(duration_s)
        or duration_s < 60.0
    ):
        raise ProductionSamplerError("SAMPLER_DURATION_TOO_SHORT")
    if output_path.exists():
        raise ProductionSamplerError("SAMPLER_QUALIFICATION_EXISTS")
    if (
        not callable(getattr(sampler, "start", None))
        or not callable(getattr(sampler, "stop", None))
        or not callable(sleep)
    ):
        raise ProductionSamplerError("SAMPLER_DRIVER_INVALID")
    await sampler.start()
    stopped = False
    try:
        await sleep(float(duration_s))
        summary = await sampler.stop()
        stopped = True
    finally:
        if not stopped:
            await sampler.stop()
    result = qualify_sampler_summary(summary)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(
            output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError:
        raise ProductionSamplerError("SAMPLER_QUALIFICATION_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return result


__all__ = [
    "REQUIRED_PROBE_SOURCES",
    "ProductionSamplerError",
    "build_production_probes",
    "parse_provider_gpu_csv",
    "probe_process",
    "probe_runner_host",
    "qualify_sampler_summary",
    "run_sampler_qualification",
]
