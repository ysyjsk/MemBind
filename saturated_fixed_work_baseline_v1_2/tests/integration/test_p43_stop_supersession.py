from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_2.cli import _active_stop, _guarded_stage
from saturated_fixed_work_baseline_v1_2.stop_supersession import (
    materialize_stop_supersession,
    verify_stop_supersession,
)


GPU_UUID = "GPU-01234567-89ab-cdef-0123-456789abcdef"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _write_self_hashed(path: Path, body: dict[str, object]) -> None:
    value = dict(body)
    value["payload_sha256"] = _hash(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _provider() -> dict[str, object]:
    common = {
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
                **common,
                "pid": 101,
                "model_path": "/models/Qwen3-32B-FP8",
                "model_revision": "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df",
            },
            "8001": {
                **common,
                "pid": 102,
                "model_path": "/models/Qwen3-Embedding-0.6B",
                "model_revision": "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626",
            },
        },
    }


def _materialize_inputs(root: Path) -> None:
    live = _provider()
    historical = copy.deepcopy(live)
    historical["services"]["8000"]["pid"] = 901
    historical["services"]["8001"]["pid"] = 902
    _write_self_hashed(root / "service_evidence/live_provider_resource.json", live)
    _write_self_hashed(
        root / "service_evidence/historical_provider_resource.json", historical
    )
    _write_self_hashed(
        root / "service_evidence/runner_neo4j_resource.json",
        {
            "hostname": "runner-a",
            "pid": 201,
            "version": "5.26.0",
            "edition": "community",
            "config_sha256": "a" * 64,
            "data_directory": "/neo4j/data",
        },
    )


def test_valid_resource_evidence_supersedes_stop_without_rewriting_it(
    tmp_path: Path,
) -> None:
    stop_body = {
        "schema_version": "membind.saturated-fixed-work.external-diagnosis.v1",
        "status": "BLOCKED_EXTERNAL_PROVIDER_RESOURCE_IDENTITY",
        "completed": False,
        "resume_from_gate": "L0_RESOURCE_IDENTITY",
    }
    _write_self_hashed(tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json", stop_body)
    stop_before = (tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json").read_bytes()
    _materialize_inputs(tmp_path)

    seal = materialize_stop_supersession(tmp_path)

    assert seal["status"] == "RESOURCE_IDENTITY_RECOVERED"
    assert verify_stop_supersession(tmp_path)["verified"] is True
    assert (tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json").read_bytes() == stop_before
    assert _active_stop(tmp_path) is None


def test_supersession_fails_closed_after_bound_resource_tampering(
    tmp_path: Path,
) -> None:
    stop_body = {
        "status": "BLOCKED_EXTERNAL_PROVIDER_RESOURCE_IDENTITY",
        "completed": False,
    }
    _write_self_hashed(tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json", stop_body)
    _materialize_inputs(tmp_path)
    materialize_stop_supersession(tmp_path)
    path = tmp_path / "service_evidence/live_provider_resource.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    blocked = _active_stop(tmp_path)

    assert blocked is not None
    assert blocked["reason"] == "STOP_SUPERSESSION_INVALID"


def test_preflight_materializes_recovery_before_rechecking_stop(
    tmp_path: Path,
) -> None:
    stop_body = {
        "status": "BLOCKED_EXTERNAL_PROVIDER_RESOURCE_IDENTITY",
        "completed": False,
        "resume_from_gate": "L0_RESOURCE_IDENTITY",
    }
    _write_self_hashed(tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json", stop_body)
    stop_before = (tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json").read_bytes()
    _materialize_inputs(tmp_path)

    exit_code = _guarded_stage(
        "preflight",
        tmp_path,
        workflows={"preflight": lambda _: {"status": "PREFLIGHT_COMPLETE"}},
    )

    assert exit_code == 0
    assert verify_stop_supersession(tmp_path)["verified"] is True
    assert (tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json").read_bytes() == stop_before
