from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.resource_evidence import (
    ResourceEvidenceError,
    build_resource_envelope,
    require_resource_gate,
)
from saturated_fixed_work_baseline_v1_2.services import (
    ServiceEvidenceError,
    validate_model_catalog,
)


GPU_UUID = "GPU-01234567-89ab-cdef-0123-456789abcdef"


def _catalog(model: str, max_model_len: int) -> str:
    return json.dumps(
        {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "root": f"/models/{model}",
                    "max_model_len": max_model_len,
                }
            ],
        }
    )


def test_model_catalog_requires_exact_model_and_context() -> None:
    evidence = validate_model_catalog(
        _catalog("qwen3-32b-fp8", 65_536),
        expected_model="qwen3-32b-fp8",
        expected_max_model_len=65_536,
        endpoint="http://10.87.5.247:8000/v1/models",
    )
    assert evidence["status"] == "PASS"
    assert evidence["model_root"] == "/models/qwen3-32b-fp8"
    with pytest.raises(ServiceEvidenceError, match="MODEL_CONTEXT_MISMATCH"):
        validate_model_catalog(
            _catalog("qwen3-32b-fp8", 32_768),
            expected_model="qwen3-32b-fp8",
            expected_max_model_len=65_536,
            endpoint="http://10.87.5.247:8000/v1/models",
        )


def _resource() -> dict[str, object]:
    service_common = {
        "argv": ["python", "-m", "vllm.entrypoints.openai.api_server"],
        "cuda_visible_devices": "1",
        "gpu_uuids": [GPU_UUID],
        "vllm_version": "0.26.0",
        "python_version": "3.12.3",
        "torch_version": "2.9.1",
        "xgrammar_version": "0.1.32",
    }
    return {
        "source_host_role": "PROVIDER",
        "hostname": "provider-a",
        "os": "Ubuntu 24.04",
        "kernel": "6.8.0",
        "cpu_model": "Example CPU",
        "physical_cores": 64,
        "memory_total_bytes": 512_000_000_000,
        "nvidia_driver": "580.65.06",
        "cuda_runtime": "12.8",
        "gpus": [
            {
                "name": "NVIDIA H100 80GB HBM3",
                "uuid": GPU_UUID,
                "memory_total_bytes": 85_000_000_000,
                "mig_mode": "Disabled",
                "power_limit_w": 700.0,
            }
        ],
        "services": {
            "8000": {
                **service_common,
                "pid": 101,
                "model_path": "/models/Qwen3-32B-FP8",
                "model_revision": "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df",
            },
            "8001": {
                **service_common,
                "pid": 102,
                "model_path": "/models/Qwen3-Embedding-0.6B",
                "model_revision": "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626",
            },
        },
    }


def test_resource_envelope_matches_identity_but_allows_pid_restart() -> None:
    live = _resource()
    historical = copy.deepcopy(live)
    historical["services"]["8000"]["pid"] = 901
    historical["services"]["8001"]["pid"] = 902
    envelope = build_resource_envelope(
        live_provider=live,
        historical_provider=historical,
        runner_neo4j={
            "hostname": "runner-a",
            "pid": 201,
            "version": "5.26.0",
            "edition": "community",
            "config_sha256": "a" * 64,
            "data_directory": "/neo4j/data",
        },
    )
    assert envelope["status"] == "PASS"
    assert envelope["live_resource_envelope_verified"] is True
    assert envelope["historical_resource_match"] is True
    assert envelope["provider_gpu_uuids"] == [GPU_UUID]
    assert require_resource_gate(envelope)["authorized"] is True


def test_resource_envelope_does_not_treat_cuda_ordinal_as_gpu_uuid() -> None:
    live = _resource()
    live["gpus"][0]["uuid"] = "gpu1"
    live["services"]["8000"]["gpu_uuids"] = ["gpu1"]
    live["services"]["8001"]["gpu_uuids"] = ["gpu1"]
    envelope = build_resource_envelope(
        live_provider=live,
        historical_provider=_resource(),
        runner_neo4j={},
    )
    assert envelope["status"] == "INVALID"
    assert envelope["live_resource_envelope_verified"] is False
    assert "live_provider.gpus[0].uuid" in envelope["missing_evidence"]
    with pytest.raises(ResourceEvidenceError, match="RESOURCE_GATE_FAILED"):
        require_resource_gate(envelope)


def test_resource_envelope_rejects_runner_hardware_as_provider_evidence() -> None:
    live = _resource()
    live["source_host_role"] = "RUNNER"
    envelope = build_resource_envelope(
        live_provider=live,
        historical_provider=_resource(),
        runner_neo4j={
            "hostname": "runner-a",
            "pid": 201,
            "version": "5.26.0",
            "edition": "community",
            "config_sha256": "a" * 64,
            "data_directory": "/neo4j/data",
        },
    )
    assert envelope["live_resource_envelope_verified"] is False
    assert "live_provider.source_host_role" in envelope["missing_evidence"]


def test_resource_envelope_reports_historical_drift_without_claiming_match() -> None:
    historical = _resource()
    historical_uuid = "GPU-ffffffff-ffff-ffff-ffff-ffffffffffff"
    historical["gpus"][0]["uuid"] = historical_uuid
    historical["services"]["8000"]["gpu_uuids"] = [historical_uuid]
    historical["services"]["8001"]["gpu_uuids"] = [historical_uuid]
    envelope = build_resource_envelope(
        live_provider=_resource(),
        historical_provider=historical,
        runner_neo4j={
            "hostname": "runner-a",
            "pid": 201,
            "version": "5.26.0",
            "edition": "community",
            "config_sha256": "a" * 64,
            "data_directory": "/neo4j/data",
        },
    )
    assert envelope["live_resource_envelope_verified"] is True
    assert envelope["historical_resource_match"] is False
    assert envelope["historical_mismatches"]


def test_direct_http_implementation_explicitly_disables_proxy(
    repository_root: Path,
) -> None:
    source = (
        repository_root
        / "saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/services.py"
    ).read_text(encoding="utf-8")
    assert "ProxyHandler({})" in source
