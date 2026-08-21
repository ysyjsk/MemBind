#!/usr/bin/env bash
set -euo pipefail

# Install this script provider-side as the implementation of the forced
# `resource-evidence` command. The controller must never execute this body via
# `ssh ... python3 -c`; only the provider-side forced-command dispatcher may.
exec python3 - <<'PY'
from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any


GPU_UUID = re.compile(
    r"^GPU-[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$"
)
SELECTED_ENV = (
    "CUDA_VISIBLE_DEVICES",
    "NVIDIA_VISIBLE_DEVICES",
    "CUDA_DEVICE_ORDER",
)
# Only redact options that carry credentials.  Substring matching would
# incorrectly hide benign limits such as --max-num-batched-tokens.
SECRET_FLAGS = re.compile(
    r"(?i)^(?:--?(?:api[-_]?key|authorization|password|secret|token))$"
)


def run(args: list[str], *, required: bool = True) -> str:
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        if required:
            raise RuntimeError(f"command_unavailable:{args[0]}") from None
        return ""
    if result.returncode != 0:
        if required:
            raise RuntimeError(f"command_failed:{args[0]}:{result.returncode}")
        return ""
    return result.stdout.strip()


def read_text(path: Path, *, required: bool = True) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        if required:
            raise RuntimeError(f"file_unavailable:{path}") from None
        return None


def proc_argv(pid: int) -> list[str]:
    try:
        values = (Path(f"/proc/{pid}/cmdline").read_bytes()).split(b"\0")
    except OSError:
        raise RuntimeError(f"cmdline_unavailable:{pid}") from None
    argv = [value.decode("utf-8", "replace") for value in values if value]
    if not argv:
        name = read_text(Path(f"/proc/{pid}/comm"), required=False)
        argv = [name] if name else []
    redacted: list[str] = []
    hide_next = False
    for value in argv:
        if hide_next:
            redacted.append("[REDACTED]")
            hide_next = False
        elif value.startswith("--") and "=" in value:
            name, selected = value.split("=", 1)
            redacted.append(f"{name}=[REDACTED]" if SECRET_FLAGS.search(name) else value)
        else:
            redacted.append(value)
            hide_next = value.startswith("--") and SECRET_FLAGS.search(value) is not None
    return redacted


def proc_env(pid: int) -> dict[str, str | None]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        raise RuntimeError(f"environ_unavailable:{pid}") from None
    selected: dict[str, str] = {}
    for value in raw:
        if b"=" not in value:
            continue
        key, item = value.split(b"=", 1)
        name = key.decode("utf-8", "replace")
        if name in SELECTED_ENV:
            selected[name] = item.decode("utf-8", "replace")
    return {name: selected.get(name) for name in SELECTED_ENV}


def service_versions(pid: int) -> dict[str, str | None]:
    try:
        executable = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        raise RuntimeError(f"python_executable_unavailable:{pid}") from None
    program = (
        "import importlib.metadata as m,json,platform;"
        "names=('vllm','torch','xgrammar');"
        "print(json.dumps({'python_version':platform.python_version(),"
        "**{name+'_version':m.version(name) for name in names}},sort_keys=True))"
    )
    try:
        value = json.loads(run([executable, "-c", program]))
    except (json.JSONDecodeError, RuntimeError):
        raise RuntimeError(f"service_versions_unavailable:{pid}") from None
    required = ("python_version", "vllm_version", "torch_version", "xgrammar_version")
    if not isinstance(value, dict) or any(not value.get(name) for name in required):
        raise RuntimeError(f"service_versions_invalid:{pid}")
    return {name: str(value[name]) for name in required}


def model_revision(model_path: str | None) -> str | None:
    if model_path is None:
        return None
    root = Path(model_path).resolve()
    git_revision = run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], required=False
    )
    if re.fullmatch(r"[0-9a-f]{40}", git_revision):
        return git_revision
    candidates = (
        "config.json",
        "generation_config.json",
        "tokenizer_config.json",
        "model.safetensors.index.json",
    )
    digest = hashlib.sha256()
    observed = 0
    for name in candidates:
        path = root / name
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        digest.update(name.encode("utf-8") + b"\0" + payload + b"\0")
        observed += 1
    return digest.hexdigest() if observed else None


def proc_row(pid: int) -> dict[str, Any] | None:
    stat = read_text(Path(f"/proc/{pid}/stat"), required=False)
    if not stat or ") " not in stat:
        return None
    fields = stat[stat.rfind(") ") + 2 :].split()
    if len(fields) <= 19:
        return None
    try:
        return {
            "pid": pid,
            "ppid": int(fields[1]),
            "start_time_ticks": int(fields[19]),
            "argv": proc_argv(pid),
        }
    except (RuntimeError, ValueError):
        return None


def process_table() -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        row = proc_row(int(path.name))
        if row is not None:
            rows[row["pid"]] = row
    return rows


def listener_pids() -> dict[int, int]:
    output = run(["ss", "-ltnpH"])
    listeners: dict[int, int] = {}
    for line in output.splitlines():
        match = re.search(r"\S+:(8000|8001)\s+.*\bpid=(\d+)", line)
        if match:
            listeners[int(match.group(1))] = int(match.group(2))
    if set(listeners) != {8000, 8001}:
        raise RuntimeError("required_listener_unavailable")
    return listeners


def descendants(root: int, rows: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    by_parent: dict[int, list[dict[str, Any]]] = {}
    for row in rows.values():
        by_parent.setdefault(row["ppid"], []).append(row)
    result: list[dict[str, Any]] = []
    frontier = [root]
    seen: set[int] = set()
    while frontier:
        parent = frontier.pop()
        for row in by_parent.get(parent, []):
            if row["pid"] in seen:
                continue
            seen.add(row["pid"])
            result.append(row)
            frontier.append(row["pid"])
    return sorted(result, key=lambda row: row["pid"])


def csv_rows(args: list[str], expected: int) -> list[list[str]]:
    rows = []
    for line in run(args).splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != expected:
            raise RuntimeError("nvidia_smi_csv_invalid")
        rows.append(fields)
    if not rows:
        raise RuntimeError("nvidia_smi_csv_empty")
    return rows


gpu_fields = csv_rows(
    [
        "nvidia-smi",
        "--query-gpu=index,uuid,name,pci.bus_id,memory.total,mig.mode.current,"
        "power.limit,driver_version,memory.used,utilization.gpu,clocks.sm,"
        "clocks.mem,temperature.gpu",
        "--format=csv,noheader,nounits",
    ],
    13,
)
gpus = []
telemetry_gpus = []
for fields in gpu_fields:
    if GPU_UUID.fullmatch(fields[1]) is None:
        raise RuntimeError("gpu_uuid_invalid")
    gpus.append(
        {
            "index": int(fields[0]),
            "uuid": fields[1],
            "name": fields[2],
            "pci_bus_id": fields[3],
            "memory_total_mib": float(fields[4]),
            "mig_mode": fields[5],
            "power_limit_w": None if fields[6] == "N/A" else float(fields[6]),
            "driver_version": fields[7],
        }
    )
    telemetry_gpus.append(
        {
            "index": int(fields[0]),
            "uuid": fields[1],
            "memory_used_mib": float(fields[8]),
            "utilization_gpu_percent": float(fields[9]),
            "clock_sm_mhz": float(fields[10]),
            "clock_memory_mhz": float(fields[11]),
            "temperature_c": float(fields[12]),
        }
    )

compute_processes = []
for fields in csv_rows(
    [
        "nvidia-smi",
        "--query-compute-apps=pid,gpu_uuid",
        "--format=csv,noheader,nounits",
    ],
    2,
):
    if GPU_UUID.fullmatch(fields[1]) is None:
        raise RuntimeError("compute_gpu_uuid_invalid")
    compute_processes.append({"pid": int(fields[0]), "gpu_uuid": fields[1]})

rows = process_table()
listeners = listener_pids()
services: dict[str, dict[str, Any]] = {}
for port in (8000, 8001):
    listener_pid = listeners[port]
    listener = rows.get(listener_pid)
    if listener is None:
        raise RuntimeError(f"listener_proc_unavailable:{port}")
    tree = descendants(listener_pid, rows)
    engines = [
        row
        for row in tree
        if "enginecore" in " ".join(row["argv"]).lower().replace("_", "")
    ]
    if len(engines) != 1:
        raise RuntimeError(f"engine_core_identity_ambiguous:{port}")
    engine = engines[0]
    mapped = sorted(
        {
            row["gpu_uuid"]
            for row in compute_processes
            if row["pid"] == engine["pid"]
        }
    )
    if not mapped:
        raise RuntimeError(f"engine_core_gpu_mapping_unavailable:{port}")
    listener_env = proc_env(listener_pid)
    engine_env = proc_env(engine["pid"])
    argv = listener["argv"]
    model_path = None
    if "serve" in argv:
        index = argv.index("serve")
        if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
            model_path = argv[index + 1]
    services[str(port)] = {
        "listener_pid": listener_pid,
        "listener_start_time_ticks": listener["start_time_ticks"],
        "argv": argv,
        "cuda_visible_devices": listener_env["CUDA_VISIBLE_DEVICES"],
        "nvidia_visible_devices": listener_env["NVIDIA_VISIBLE_DEVICES"],
        "cuda_device_order": listener_env["CUDA_DEVICE_ORDER"],
        "engine_core_pid": engine["pid"],
        "engine_core_start_time_ticks": engine["start_time_ticks"],
        "engine_core_argv": engine["argv"],
        "engine_core_cuda_visible_devices": engine_env["CUDA_VISIBLE_DEVICES"],
        "engine_core_nvidia_visible_devices": engine_env["NVIDIA_VISIBLE_DEVICES"],
        "gpu_uuids": mapped,
        "model_path": model_path,
        "model_revision": model_revision(model_path),
        **service_versions(listener_pid),
        "process_tree": tree,
    }

os_release = read_text(Path("/etc/os-release"), required=False)
meminfo = read_text(Path("/proc/meminfo")) or ""
mem_match = re.search(r"^MemTotal:\s+(\d+)\s+kB$", meminfo, re.MULTILINE)
cpuinfo = read_text(Path("/proc/cpuinfo")) or ""
cpu_match = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
physical_pairs = set(
    re.findall(r"physical id\s*:\s*(\d+).*?core id\s*:\s*(\d+)", cpuinfo, re.DOTALL)
)
nvcc = run(["nvcc", "--version"], required=False)
cuda_match = re.search(r"release\s+([0-9.]+)", nvcc)

snapshot = {
    "schema_version": "membind.provider.resource-evidence.v2",
    "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source_host_role": "PROVIDER",
    "hostname": platform.node(),
    "hostname_fqdn": run(["hostname", "-f"], required=False) or platform.node(),
    "machine_id": read_text(Path("/etc/machine-id")),
    "boot_id": read_text(Path("/proc/sys/kernel/random/boot_id")),
    "os": os_release,
    "kernel": platform.release(),
    "cpu_model": cpu_match.group(1).strip() if cpu_match else None,
    "physical_cores": len(physical_pairs) or None,
    "memory_total_bytes": int(mem_match.group(1)) * 1024 if mem_match else None,
    "nvidia_driver": gpus[0]["driver_version"],
    "cuda_runtime": cuda_match.group(1) if cuda_match else None,
    "gpus": sorted(gpus, key=lambda row: row["index"]),
    "compute_processes": sorted(
        compute_processes, key=lambda row: (row["pid"], row["gpu_uuid"])
    ),
    "services": services,
    "versions": {
        "collector_python": platform.python_version(),
        "services": {
            port: {
                name: service[name]
                for name in (
                    "python_version",
                    "vllm_version",
                    "torch_version",
                    "xgrammar_version",
                )
            }
            for port, service in services.items()
        },
    },
    "telemetry": {"gpus": sorted(telemetry_gpus, key=lambda row: row["index"])},
}
print(json.dumps(snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
PY
