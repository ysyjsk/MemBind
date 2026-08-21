"""Repeatable read-only probes for an externally blocked L0 resource gate."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .services import ServiceEvidenceError, direct_get_text, validate_model_catalog


CommandRunner = Callable[[tuple[str, ...], float], tuple[int, str, str]]
HttpGetter = Callable[[str, float], dict[str, Any]]

_GPU_UUID = re.compile(r"GPU-[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+|api[_-]?key\s*[:=]\s*)\S+"
)


def _default_runner(
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
        return 124, "", type(error).__name__
    return result.returncode, result.stdout, result.stderr


def _default_getter(url: str, timeout_s: float) -> dict[str, Any]:
    return direct_get_text(url, timeout_s=timeout_s)


def _safe_excerpt(value: str, limit: int = 600) -> str:
    selected = _SECRET.sub(r"\1[REDACTED]", _CONTROL.sub("", value)).strip()
    if len(selected) <= limit:
        return selected
    half = limit // 2
    return selected[:half] + "\n...[TRUNCATED]...\n" + selected[-half:]


def _command_observation(
    label: str,
    args: tuple[str, ...],
    *,
    runner: CommandRunner,
    timeout_s: float,
) -> tuple[dict[str, Any], str]:
    exit_code, stdout, stderr = runner(args, timeout_s)
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    return (
        {
            "label": label,
            "command": shlex.join(args),
            "exit_code": exit_code,
            "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
            "stdout_bytes": len(stdout_bytes),
            "stderr_bytes": len(stderr_bytes),
            "stdout_excerpt": _safe_excerpt(stdout),
            "stderr_excerpt": _safe_excerpt(stderr),
        },
        stdout,
    )


def _probe_services(getter: HttpGetter, timeout_s: float) -> dict[str, str]:
    probes: dict[str, str] = {}
    catalogs = (
        (
            "construction_models",
            "http://10.87.5.247:8000/v1/models",
            "qwen3-32b-fp8",
            65_536,
        ),
        (
            "embedding_models",
            "http://10.87.5.247:8001/v1/models",
            "qwen3-embedding-0.6b",
            32_768,
        ),
    )
    for label, url, model, context in catalogs:
        try:
            response = getter(url, timeout_s)
            validate_model_catalog(
                response["text"],
                expected_model=model,
                expected_max_model_len=context,
                endpoint=url,
            )
        except (KeyError, TypeError, ServiceEvidenceError):
            probes[label] = "INVALID"
        else:
            probes[label] = "PASS"
    for label, url in (
        ("construction_metrics", "http://10.87.5.247:8000/metrics"),
        ("embedding_metrics", "http://10.87.5.247:8001/metrics"),
    ):
        try:
            text = str(getter(url, timeout_s)["text"])
        except (KeyError, TypeError, ServiceEvidenceError):
            probes[label] = "INVALID"
        else:
            probes[label] = (
                "PASS"
                if "vllm:num_requests_running" in text
                and "vllm:num_requests_waiting" in text
                else "INVALID"
            )
    try:
        neo4j = json.loads(str(getter("http://127.0.0.1:7474/", timeout_s)["text"]))
    except (KeyError, TypeError, json.JSONDecodeError, ServiceEvidenceError):
        probes["neo4j_http"] = "INVALID"
    else:
        probes["neo4j_http"] = (
            "PASS"
            if neo4j.get("neo4j_version") == "5.26.0"
            and neo4j.get("neo4j_edition") == "community"
            else "INVALID"
        )
    return probes


def collect_recovery_round(
    *,
    ordinal: int,
    ssh_alias: str,
    command_runner: CommandRunner = _default_runner,
    http_getter: HttpGetter = _default_getter,
    observed_at: str | None = None,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise ValueError("RECOVERY_ORDINAL_INVALID")
    if not isinstance(ssh_alias, str) or not ssh_alias:
        raise ValueError("SSH_ALIAS_INVALID")
    remote_commands = (
        ("restricted_status", ("ssh", ssh_alias, "status")),
        ("provider_hostname", ("ssh", ssh_alias, "hostname")),
        ("provider_system", ("ssh", ssh_alias, "uname -a; lscpu; cat /proc/meminfo")),
        (
            "provider_gpu_inventory",
            (
                "ssh",
                ssh_alias,
                "nvidia-smi --query-gpu=index,name,uuid,memory.total,mig.mode.current,power.limit,driver_version --format=csv,noheader,nounits",
            ),
        ),
        ("provider_processes", ("ssh", ssh_alias, "ps -eo pid=,args=")),
        (
            "provider_process_environ",
            (
                "ssh",
                ssh_alias,
                "for p in $(pgrep -f 'api_server.*800[01]'); do echo PID=$p; tr '\\0' '\\n' </proc/$p/environ | sed -n '/^CUDA_VISIBLE_DEVICES=/p'; done",
            ),
        ),
        ("provider_tmux_service", ("ssh", ssh_alias, "tmux list-sessions; systemctl --no-pager --type=service --state=running")),
        ("provider_log_inventory", ("ssh", ssh_alias, "list", "logs")),
        (
            "provider_log_8000",
            (
                "ssh",
                ssh_alias,
                "read",
                "logs/qwen3-32b-fp8-server-gpu1-8000.log",
            ),
        ),
        (
            "provider_log_8001",
            (
                "ssh",
                ssh_alias,
                "read",
                "logs/qwen3-embedding-0.6b-server-gpu1-8001.log",
            ),
        ),
    )
    commands: list[dict[str, Any]] = []
    raw_by_label: dict[str, str] = {}
    for label, args in remote_commands:
        observation, stdout = _command_observation(
            label, args, runner=command_runner, timeout_s=timeout_s
        )
        commands.append(observation)
        raw_by_label[label] = stdout
    by_label = {row["label"]: row for row in commands}
    hostname_available = by_label["provider_hostname"]["exit_code"] == 0 and bool(
        raw_by_label["provider_hostname"].strip()
    )
    gpu_uuids = _GPU_UUID.findall(raw_by_label["provider_gpu_inventory"])
    process_text = raw_by_label["provider_processes"] + "\n" + raw_by_label[
        "provider_process_environ"
    ]
    process_mapping_available = (
        by_label["provider_processes"]["exit_code"] == 0
        and by_label["provider_process_environ"]["exit_code"] == 0
        and "8000" in process_text
        and "8001" in process_text
        and "CUDA_VISIBLE_DEVICES=" in process_text
        and bool(gpu_uuids)
    )
    restricted = (
        by_label["restricted_status"]["exit_code"] == 0
        and not hostname_available
    )
    return {
        "schema_version": "membind.saturated-fixed-work.recovery-round.v1",
        "round": ordinal,
        "observed_at": observed_at or datetime.now().astimezone().isoformat(),
        "provider_access": {
            "status": "RESTRICTED_READ_ONLY" if restricted else "PROBED",
            "hostname_available": hostname_available,
            "gpu_uuid_available": bool(gpu_uuids),
            "process_mapping_available": process_mapping_available,
        },
        "commands": commands,
        "service_probes": _probe_services(http_getter, timeout_s),
        "provider_logs": {
            "8000": {
                "path": "logs/qwen3-32b-fp8-server-gpu1-8000.log",
                "read_exit_code": by_label["provider_log_8000"]["exit_code"],
                "sha256": by_label["provider_log_8000"]["stdout_sha256"],
                "cuda_ordinal_is_not_gpu_uuid": True,
            },
            "8001": {
                "path": "logs/qwen3-embedding-0.6b-server-gpu1-8001.log",
                "read_exit_code": by_label["provider_log_8001"]["exit_code"],
                "sha256": by_label["provider_log_8001"]["stdout_sha256"],
                "cuda_ordinal_is_not_gpu_uuid": True,
            },
        },
        "observed_provider_gpu_uuids": sorted(set(gpu_uuids)),
    }


__all__ = ["collect_recovery_round"]
