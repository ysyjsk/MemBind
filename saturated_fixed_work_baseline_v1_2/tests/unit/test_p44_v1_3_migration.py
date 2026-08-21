from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.v1_3 import (
    V1_3_PROTOCOL_VERSION,
    CampaignResourceError,
    TestQualificationError,
    build_campaign_resource_envelope,
    build_v1_3_sampler_layers,
    compare_campaign_resource_envelopes,
    evaluate_test_qualification,
    require_campaign_resource_gate,
    validate_v1_3_preflight,
)


GPU_UUID = "GPU-01234567-89ab-cdef-0123-456789abcdef"


def _provider() -> dict[str, object]:
    common = {
        "argv": ["python", "-m", "vllm.entrypoints.openai.api_server"],
        "cuda_visible_devices": "0",
        "nvidia_visible_devices": None,
        "gpu_uuids": [GPU_UUID],
        "model_path": "/models/qwen",
        "model_revision": "a" * 40,
        "vllm_version": "0.26.0",
        "python_version": "3.12.3",
        "torch_version": "2.9.1",
        "xgrammar_version": "0.1.32",
    }
    return {
        "source_host_role": "PROVIDER",
        "hostname": "provider-a",
        "machine_id": "machine-a",
        "boot_id": "boot-a",
        "os": "Ubuntu 24.04",
        "kernel": "6.8.0",
        "cpu_model": "Example CPU",
        "physical_cores": 64,
        "memory_total_bytes": 512_000_000_000,
        "nvidia_driver": "580.65.06",
        "cuda_runtime": "12.8",
        "gpus": [
            {
                "name": "NVIDIA H100",
                "uuid": GPU_UUID,
                "memory_total_bytes": 85_000_000_000,
                "mig_mode": "Disabled",
                "power_limit_w": 700.0,
            }
        ],
        "services": {
            "8000": {**common, "pid": 101},
            "8001": {**common, "pid": 102},
        },
    }


def _neo4j(pid: int = 201) -> dict[str, object]:
    return {
        "hostname": "runner-a",
        "pid": pid,
        "version": "5.26.0",
        "edition": "community",
        "config_sha256": "b" * 64,
        "data_directory": "/neo4j/data",
    }


def _metadata() -> dict[str, str]:
    return {
        "runner_commit": "c" * 40,
        "workload_manifest_sha256": "d" * 64,
        "protocol_config_sha256": "e" * 64,
    }


def test_v1_3_current_resource_gate_does_not_require_historical_identity() -> None:
    envelope = build_campaign_resource_envelope(
        live_provider=_provider(), runner_neo4j=_neo4j(), campaign_metadata=_metadata()
    )

    assert envelope["status"] == "PASS"
    assert envelope["protocol_version"] == V1_3_PROTOCOL_VERSION
    assert envelope["historical_resource_parity_required"] is False
    assert envelope["historical_resource_match"] == "NOT_APPLICABLE"
    assert require_campaign_resource_gate(envelope)["authorized"] is True


def test_v1_3_resource_gate_fails_when_live_identity_is_incomplete() -> None:
    provider = _provider()
    del provider["gpus"]
    envelope = build_campaign_resource_envelope(
        live_provider=provider, runner_neo4j=_neo4j(), campaign_metadata=_metadata()
    )

    assert envelope["status"] == "INVALID"
    assert "live_provider.gpus" in envelope["missing_evidence"]
    with pytest.raises(CampaignResourceError, match="RESOURCE_GATE_FAILED"):
        require_campaign_resource_gate(envelope)


def test_campaign_envelope_allows_pid_restart_but_rejects_gpu_change() -> None:
    first = build_campaign_resource_envelope(
        live_provider=_provider(), runner_neo4j=_neo4j(), campaign_metadata=_metadata()
    )
    restarted_provider = copy.deepcopy(_provider())
    restarted_provider["services"]["8000"]["pid"] = 9001  # type: ignore[index]
    restarted_provider["services"]["8001"]["pid"] = 9002  # type: ignore[index]
    restarted = build_campaign_resource_envelope(
        live_provider=restarted_provider,
        runner_neo4j=_neo4j(pid=9020),
        campaign_metadata=_metadata(),
    )
    assert compare_campaign_resource_envelopes(first, restarted)["passed"] is True

    changed = copy.deepcopy(restarted_provider)
    changed["gpus"][0]["uuid"] = "GPU-ffffffff-ffff-ffff-ffff-ffffffffffff"  # type: ignore[index]
    changed["services"]["8000"]["gpu_uuids"] = [  # type: ignore[index]
        "GPU-ffffffff-ffff-ffff-ffff-ffffffffffff"
    ]
    changed["services"]["8001"]["gpu_uuids"] = [  # type: ignore[index]
        "GPU-ffffffff-ffff-ffff-ffff-ffffffffffff"
    ]
    changed_envelope = build_campaign_resource_envelope(
        live_provider=changed,
        runner_neo4j=_neo4j(pid=9021),
        campaign_metadata=_metadata(),
    )
    comparison = compare_campaign_resource_envelopes(first, changed_envelope)
    assert comparison["passed"] is False
    assert any("gpu" in path for path in comparison["mismatches"])


def test_test_qualification_accepts_exact_clean_head_preexisting_failures() -> None:
    existing = {
        "test_id": "tests/test_legacy.py::test_known",
        "signature": "AssertionError: legacy freeze mismatch",
    }
    result = evaluate_test_qualification(
        sfwb_failures=[],
        targeted_failures=[],
        repository_failures=[existing],
        clean_head_failures=[existing],
    )
    assert result["qualification_passed"] is True
    assert result["new_regression_count"] == 0
    assert result["repository_wide_status"] == "PASS_WITH_PREEXISTING_FAILURES"


def test_test_qualification_rejects_branch_regression() -> None:
    result = evaluate_test_qualification(
        sfwb_failures=[],
        targeted_failures=[],
        repository_failures=[
            {"test_id": "tests/test_new.py::test_regression", "signature": "new"}
        ],
        clean_head_failures=[],
    )
    assert result["qualification_passed"] is False
    assert result["new_regression_count"] == 1


def test_test_qualification_rejects_any_sfwb_or_targeted_failure() -> None:
    with pytest.raises(TestQualificationError, match="FAILURE_RECORD_INVALID"):
        evaluate_test_qualification(
            sfwb_failures=[{"test_id": "x"}],
            targeted_failures=[],
            repository_failures=[],
            clean_head_failures=[],
        )
    result = evaluate_test_qualification(
        sfwb_failures=[
            {"test_id": "tests/test_sfwb.py::test_bad", "signature": "bad"}
        ],
        targeted_failures=[],
        repository_failures=[],
        clean_head_failures=[],
    )
    assert result["qualification_passed"] is False
    assert result["new_regression_count"] == 1


def test_v1_3_preflight_has_no_historical_gate() -> None:
    result = validate_v1_3_preflight(
        {
            "workload_manifest_valid": True,
            "resource_envelope_captured": True,
            "resource_envelope_shared": True,
            "construction_healthy": True,
            "embedding_healthy": True,
            "neo4j_healthy": True,
            "models_config_correct": True,
            "services_idle": True,
            "warmup_passed": True,
            "telemetry_available": True,
            "test_qualification_passed": True,
            "new_regression_count": 0,
        }
    )
    assert result["status"] == "PASS"
    assert "historical_resource_match" not in result["required_gates"]


def test_identity_discovery_is_low_frequency_and_not_part_of_1hz_telemetry() -> None:
    identity_calls: list[str] = []
    telemetry_calls: list[str] = []

    def identity() -> dict[str, object]:
        identity_calls.append("identity")
        return {"resource_envelope_id": "e" * 64}

    def provider_gpu() -> list[dict[str, object]]:
        telemetry_calls.append("provider_gpu")
        return [{"uuid": GPU_UUID, "utilization_gpu_percent": 0.0}]

    layers = build_v1_3_sampler_layers(
        identity_probe=identity,
        telemetry_probes={"provider_gpu": provider_gpu},
    )
    assert identity_calls == []
    assert layers["telemetry"]["provider_gpu"]() == [
        {"uuid": GPU_UUID, "utilization_gpu_percent": 0.0}
    ]
    assert telemetry_calls == ["provider_gpu"]
    assert layers["identity"]() == {"resource_envelope_id": "e" * 64}
    assert identity_calls == ["identity"]


def test_v1_3_migration_only_reads_the_frozen_v1_2_stop(repository_root: Path) -> None:
    stop = (
        repository_root
        / "saturated_fixed_work_baseline_v1_2/artifacts/sfwb-v1-2-dev-20260821-001/"
        / "STOP_WITH_EXTERNAL_DIAGNOSIS.json"
    )
    assert stop.is_file()
    assert (
        hashlib.sha256(stop.read_bytes()).hexdigest()
        == "2cd5f9043136865df71085cd92840fa86512982c5fc77be01026fab244af5426"
    )
