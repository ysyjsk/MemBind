"""Fail-closed physical resource identity and historical parity contracts."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any


class ResourceEvidenceError(ValueError):
    """The physical resource gate is incomplete or mismatched."""


_GPU_UUID = re.compile(r"^GPU-[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_HOST_FIELDS = (
    "hostname",
    "os",
    "kernel",
    "cpu_model",
    "physical_cores",
    "memory_total_bytes",
    "nvidia_driver",
    "cuda_runtime",
)
_GPU_FIELDS = (
    "name",
    "uuid",
    "memory_total_bytes",
    "mig_mode",
    "power_limit_w",
)
_SERVICE_FIELDS = (
    "pid",
    "argv",
    "cuda_visible_devices",
    "gpu_uuids",
    "model_path",
    "model_revision",
    "vllm_version",
    "python_version",
    "torch_version",
    "xgrammar_version",
)


def _present(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, Sequence):
        return bool(value)
    return True


def _provider_missing(provider: Mapping[str, Any], prefix: str) -> list[str]:
    missing: list[str] = []
    if provider.get("source_host_role") != "PROVIDER":
        missing.append(f"{prefix}.source_host_role")
    for field in _HOST_FIELDS:
        if not _present(provider.get(field)):
            missing.append(f"{prefix}.{field}")
    gpus = provider.get("gpus")
    gpu_uuids: set[str] = set()
    if not isinstance(gpus, list) or not gpus:
        missing.append(f"{prefix}.gpus")
    else:
        for index, gpu in enumerate(gpus):
            if not isinstance(gpu, Mapping):
                missing.append(f"{prefix}.gpus[{index}]")
                continue
            for field in _GPU_FIELDS:
                value = gpu.get(field)
                if field == "uuid":
                    if not isinstance(value, str) or _GPU_UUID.fullmatch(value) is None:
                        missing.append(f"{prefix}.gpus[{index}].uuid")
                    else:
                        gpu_uuids.add(value)
                elif not _present(value):
                    missing.append(f"{prefix}.gpus[{index}].{field}")
    services = provider.get("services")
    if not isinstance(services, Mapping):
        missing.append(f"{prefix}.services")
        return missing
    for port in ("8000", "8001"):
        service = services.get(port)
        if not isinstance(service, Mapping):
            missing.append(f"{prefix}.services.{port}")
            continue
        for field in _SERVICE_FIELDS:
            value = service.get(field)
            if field == "pid":
                valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
            elif field == "argv":
                valid = isinstance(value, list) and bool(value) and all(
                    isinstance(item, str) and bool(item) for item in value
                )
            elif field == "gpu_uuids":
                valid = (
                    isinstance(value, list)
                    and bool(value)
                    and all(
                        isinstance(item, str)
                        and _GPU_UUID.fullmatch(item) is not None
                        and item in gpu_uuids
                        for item in value
                    )
                )
            elif field == "model_revision":
                valid = isinstance(value, str) and _REVISION.fullmatch(value) is not None
            else:
                valid = _present(value)
            if not valid:
                missing.append(f"{prefix}.services.{port}.{field}")
    return missing


def _neo4j_missing(neo4j: Mapping[str, Any]) -> list[str]:
    fields = ("hostname", "pid", "version", "edition", "config_sha256", "data_directory")
    missing = [f"runner_neo4j.{field}" for field in fields if not _present(neo4j.get(field))]
    digest = neo4j.get("config_sha256")
    if _present(digest) and (
        not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
    ):
        missing.append("runner_neo4j.config_sha256")
    return sorted(set(missing))


def _provider_comparison(provider: Mapping[str, Any]) -> dict[str, Any]:
    services = provider.get("services") or {}
    return {
        **{field: provider.get(field) for field in _HOST_FIELDS},
        "gpus": sorted(
            (
                {field: gpu.get(field) for field in _GPU_FIELDS}
                for gpu in provider.get("gpus", [])
            ),
            key=lambda row: str(row["uuid"]),
        ),
        "services": {
            port: {
                field: services[port].get(field)
                for field in _SERVICE_FIELDS
                if field != "pid"
            }
            for port in ("8000", "8001")
        },
    }


def _mismatch_paths(left: Any, right: Any, prefix: str = "provider") -> list[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return [
            path
            for key in sorted(set(left) | set(right))
            for path in _mismatch_paths(left.get(key), right.get(key), f"{prefix}.{key}")
        ]
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [prefix]
        return [
            path
            for index, (left_item, right_item) in enumerate(zip(left, right, strict=True))
            for path in _mismatch_paths(left_item, right_item, f"{prefix}[{index}]")
        ]
    return [] if left == right else [prefix]


def build_resource_envelope(
    *,
    live_provider: Mapping[str, Any],
    historical_provider: Mapping[str, Any],
    runner_neo4j: Mapping[str, Any],
) -> dict[str, Any]:
    live = copy.deepcopy(dict(live_provider))
    historical = copy.deepcopy(dict(historical_provider))
    neo4j = copy.deepcopy(dict(runner_neo4j))
    live_missing = _provider_missing(live, "live_provider")
    historical_missing = _provider_missing(historical, "historical_provider")
    neo4j_missing = _neo4j_missing(neo4j)
    live_verified = not live_missing and not neo4j_missing
    historical_valid = not historical_missing
    mismatches = (
        _mismatch_paths(
            _provider_comparison(live), _provider_comparison(historical)
        )
        if not live_missing and historical_valid
        else []
    )
    historical_match = live_verified and historical_valid and not mismatches
    missing = [*live_missing, *historical_missing, *neo4j_missing]
    return {
        "schema_version": "membind.saturated-fixed-work.resource-envelope.v1",
        "status": "PASS" if live_verified and historical_match else "INVALID",
        "historical_resource_match": historical_match,
        "live_resource_envelope_verified": live_verified,
        "all_formal_blocks_share_one_resource_envelope": "NOT_EVALUATED",
        "provider_gpu_uuids": sorted(
            gpu["uuid"]
            for gpu in live.get("gpus", [])
            if isinstance(gpu, Mapping)
            and isinstance(gpu.get("uuid"), str)
            and _GPU_UUID.fullmatch(gpu["uuid"]) is not None
        ),
        "missing_evidence": sorted(set(missing)),
        "historical_mismatches": mismatches,
        "live_provider": live,
        "historical_provider": historical,
        "runner_neo4j": neo4j,
    }


def require_resource_gate(envelope: Mapping[str, Any]) -> dict[str, Any]:
    if (
        envelope.get("status") != "PASS"
        or envelope.get("live_resource_envelope_verified") is not True
        or envelope.get("historical_resource_match") is not True
    ):
        raise ResourceEvidenceError("RESOURCE_GATE_FAILED")
    return {
        "schema_version": "membind.saturated-fixed-work.resource-gate.v1",
        "authorized": True,
    }


__all__ = [
    "ResourceEvidenceError",
    "build_resource_envelope",
    "require_resource_gate",
]
