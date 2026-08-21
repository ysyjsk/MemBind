from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.recovery_runner import (
    RecoveryRunnerError,
    run_external_recovery,
)


def _collector(*, ordinal: int, ssh_alias: str) -> dict[str, object]:
    return {
        "round": ordinal,
        "observed_at": f"2026-08-21T04:{20 + ordinal}:00+08:00",
        "provider_access": {
            "status": "RESTRICTED_READ_ONLY",
            "hostname_available": False,
            "gpu_uuid_available": False,
            "process_mapping_available": False,
        },
        "commands": [
            {
                "command": f"ssh {ssh_alias} status",
                "exit_code": 0,
                "stdout_sha256": f"{ordinal:064x}",
            }
        ],
        "service_probes": {"construction_models": "PASS"},
    }


def test_recovery_runner_materializes_three_rounds_then_stop(tmp_path: Path) -> None:
    sleeps: list[float] = []
    diagnosis = run_external_recovery(
        run_root=tmp_path,
        ssh_alias="zju-liuyi",
        rounds=3,
        interval_s=2.0,
        collector=_collector,
        sleeper=sleeps.append,
    )
    assert sleeps == [2.0, 2.0]
    for ordinal in range(1, 4):
        path = tmp_path / f"service_evidence/recovery_round_{ordinal:03d}.json"
        assert json.loads(path.read_text())["round"] == ordinal
    assert diagnosis["completed"] is False
    assert (tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json").is_file()


def test_recovery_runner_refuses_to_overwrite_existing_evidence(tmp_path: Path) -> None:
    run_external_recovery(
        run_root=tmp_path,
        ssh_alias="zju-liuyi",
        rounds=3,
        interval_s=0.0,
        collector=_collector,
        sleeper=lambda value: None,
    )
    with pytest.raises(RecoveryRunnerError, match="RECOVERY_ARTIFACT_ALREADY_EXISTS"):
        run_external_recovery(
            run_root=tmp_path,
            ssh_alias="zju-liuyi",
            rounds=3,
            interval_s=0.0,
            collector=_collector,
            sleeper=lambda value: None,
        )


def test_recovery_runner_resumes_a_durable_prefix_without_overwrite(
    tmp_path: Path,
) -> None:
    def interrupted_collector(*, ordinal: int, ssh_alias: str) -> dict[str, object]:
        if ordinal == 3:
            raise RuntimeError("external interruption")
        return _collector(ordinal=ordinal, ssh_alias=ssh_alias)

    with pytest.raises(RuntimeError, match="external interruption"):
        run_external_recovery(
            run_root=tmp_path,
            ssh_alias="zju-liuyi",
            rounds=3,
            interval_s=0.0,
            collector=interrupted_collector,
            sleeper=lambda value: None,
        )
    first_before = (tmp_path / "service_evidence/recovery_round_001.json").read_bytes()
    diagnosis = run_external_recovery(
        run_root=tmp_path,
        ssh_alias="zju-liuyi",
        rounds=3,
        interval_s=0.0,
        collector=_collector,
        sleeper=lambda value: None,
    )
    assert diagnosis["completed"] is False
    assert (tmp_path / "service_evidence/recovery_round_001.json").read_bytes() == first_before
    assert (tmp_path / "service_evidence/recovery_round_003.json").is_file()
