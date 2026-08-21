from __future__ import annotations

from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.external_diagnosis import (
    ExternalDiagnosisError,
    build_stop_diagnosis,
    write_stop_diagnosis,
)
from saturated_fixed_work_baseline_v1_2.recovery_probe import collect_recovery_round


def _round(ordinal: int) -> dict[str, object]:
    return {
        "round": ordinal,
        "observed_at": f"2026-08-21T04:1{ordinal}:00+08:00",
        "provider_access": {
            "status": "RESTRICTED_READ_ONLY",
            "hostname_available": False,
            "gpu_uuid_available": False,
            "process_mapping_available": False,
        },
        "commands": [
            {
                "command": "ssh zju-liuyi status",
                "exit_code": 0,
                "stdout_sha256": f"{ordinal:064x}",
            },
            {
                "command": "ssh zju-liuyi nvidia-smi --query-gpu=uuid",
                "exit_code": 2,
                "stdout_sha256": f"{ordinal + 10:064x}",
            },
        ],
        "service_probes": {
            "construction_models": "PASS",
            "embedding_models": "PASS",
            "neo4j_http": "PASS",
        },
    }


def test_stop_diagnosis_requires_three_distinct_recovery_rounds() -> None:
    with pytest.raises(ExternalDiagnosisError, match="RECOVERY_ROUNDS_INCOMPLETE"):
        build_stop_diagnosis([_round(1), _round(2)])
    repeated = [_round(1), _round(1), _round(1)]
    with pytest.raises(ExternalDiagnosisError, match="RECOVERY_ROUNDS_NOT_DISTINCT"):
        build_stop_diagnosis(repeated)


def test_unchanged_external_state_is_valid_new_observation_when_times_differ() -> None:
    rounds = [_round(1), _round(2), _round(3)]
    for row in rounds:
        row["commands"] = _round(1)["commands"]
        row["service_probes"] = _round(1)["service_probes"]
        row["provider_access"] = _round(1)["provider_access"]
    diagnosis = build_stop_diagnosis(rounds)
    assert len(diagnosis["recovery_rounds"]) == 3


def test_stop_diagnosis_is_explicitly_non_success_and_resumable() -> None:
    diagnosis = build_stop_diagnosis([_round(1), _round(2), _round(3)])
    assert diagnosis["status"] == "BLOCKED_EXTERNAL_PROVIDER_RESOURCE_IDENTITY"
    assert diagnosis["completed"] is False
    assert diagnosis["resume_from_gate"] == "L0_RESOURCE_IDENTITY"
    assert diagnosis["formal_blocks_started"] == 0
    assert diagnosis["qa_rows_created"] == 0
    assert diagnosis["missing_evidence"] == [
        "provider_hostname",
        "provider_gpu_uuid",
        "provider_8000_pid_argv_cuda_to_gpu_uuid",
        "provider_8001_pid_argv_cuda_to_gpu_uuid",
        "historical_provider_gpu_uuid_and_process_mapping",
    ]
    assert len(diagnosis["recovery_rounds"]) == 3
    assert len(diagnosis["payload_sha256"]) == 64


def test_stop_diagnosis_write_is_append_only(tmp_path: Path) -> None:
    diagnosis = build_stop_diagnosis([_round(1), _round(2), _round(3)])
    path = write_stop_diagnosis(tmp_path, diagnosis)
    assert path.name == "STOP_WITH_EXTERNAL_DIAGNOSIS.json"
    assert path.is_file()
    with pytest.raises(ExternalDiagnosisError, match="STOP_DIAGNOSIS_ALREADY_EXISTS"):
        write_stop_diagnosis(tmp_path, diagnosis)


def test_recovery_probe_keeps_healthy_services_separate_from_restricted_identity() -> None:
    def runner(args: tuple[str, ...], timeout_s: float) -> tuple[int, str, str]:
        del timeout_s
        if args[-1] == "status":
            return 0, "readonly liuyi access OK\n", ""
        if args[-2:] == ("list", "logs"):
            return 0, "qwen3-32b-fp8-server-gpu1-8000.log\n", ""
        if args[-2] == "read":
            return 0, "vLLM API server version 0.26.0\n", ""
        return 2, "allowed: status, list, read, tail, follow\n", ""

    def getter(url: str, timeout_s: float) -> dict[str, object]:
        del timeout_s
        if url.endswith("/v1/models"):
            if ":8000/" in url:
                model, length = "qwen3-32b-fp8", 65_536
            else:
                model, length = "qwen3-embedding-0.6b", 32_768
            return {"text": _model_payload(model, length), "body_sha256": "a" * 64}
        if url.endswith("/metrics"):
            return {
                "text": "vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n",
                "body_sha256": "b" * 64,
            }
        return {
            "text": '{"neo4j_version":"5.26.0","neo4j_edition":"community"}',
            "body_sha256": "c" * 64,
        }

    result = collect_recovery_round(
        ordinal=1,
        ssh_alias="zju-liuyi",
        command_runner=runner,
        http_getter=getter,
        observed_at="2026-08-21T04:20:00+08:00",
    )
    assert result["service_probes"] == {
        "construction_models": "PASS",
        "construction_metrics": "PASS",
        "embedding_models": "PASS",
        "embedding_metrics": "PASS",
        "neo4j_http": "PASS",
    }
    assert result["provider_access"] == {
        "status": "RESTRICTED_READ_ONLY",
        "hostname_available": False,
        "gpu_uuid_available": False,
        "process_mapping_available": False,
    }
    assert all(len(row["stdout_sha256"]) == 64 for row in result["commands"])
    assert result["provider_logs"]["8000"]["read_exit_code"] == 0


def _model_payload(model: str, max_model_len: int) -> str:
    import json

    return json.dumps(
        {
            "data": [
                {
                    "id": model,
                    "root": f"/models/{model}",
                    "max_model_len": max_model_len,
                }
            ]
        }
    )
