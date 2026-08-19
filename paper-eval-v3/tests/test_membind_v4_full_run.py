"""TDD contracts for the immutable four-history v4 formal orchestration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v4.freeze import FORMAL_HISTORY_IDS
from paper_eval.membind_v4.full_run import (
    FORMAL_HISTORY_SOURCE_COUNTS,
    V4FullRunError,
    run_v4_full,
)


def _sealed(path: Path, body: dict[str, object]) -> Path:
    value = dict(body)
    value["payload_sha256"] = payload_sha256(value)
    atomic_write_json(path, value)
    return path


def _frozen(tmp_path: Path) -> Path:
    return _sealed(
        tmp_path / "V4_FROZEN_METHOD.json",
        {
            "schema_version": "membind.paper-eval-v4.frozen-method.v1",
            "status": "FROZEN",
            "candidate_id": "c01",
            "policy": "IDLE_SLOT_VALIDATED_SPEC",
            "thresholds": {"global_k": 2, "speculation_distance": 1},
            "formal_history_ids": list(FORMAL_HISTORY_IDS),
        },
    )


def _ready_preflight() -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.service-preflight.v1",
        "status": "READY",
        "classification": "READY",
        "mutations_performed": False,
        "credentials_recorded": False,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _pass_runner(calls: list[dict[str, object]]):
    def run(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "status": "PASS",
            "history_id": kwargs["history_id"],
            "run_id": kwargs["run_id"],
            "namespace": kwargs["namespace"],
            "source_count": kwargs["source_count"],
            "direct_violation_count": 0,
            "performance": {"makespan_ns": int(kwargs["source_count"]) * 100},
            "telemetry": {"semantic_hit_count": 1},
        }

    return run


def test_live_full_run_uses_exact_order_fresh_identities_and_seals_188(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    root = tmp_path / "run"
    result = run_v4_full(
        frozen_method_path=_frozen(tmp_path),
        output_root=root,
        run_id="v4-full-test-001",
        mode="live",
        preflight=_ready_preflight(),
        history_runner=_pass_runner(calls),
    )

    assert result["status"] == "PASS"
    assert result["formal_main_table_eligible"] is True
    assert result["history_ids"] == list(FORMAL_HISTORY_IDS)
    assert result["source_count"] == 188
    assert [call["history_id"] for call in calls] == list(FORMAL_HISTORY_IDS)
    assert [call["source_count"] for call in calls] == [49, 46, 44, 49]
    assert len({call["run_id"] for call in calls}) == 4
    assert len({call["namespace"] for call in calls}) == 4
    assert all(call["fresh_namespace"] is True for call in calls)

    for name in ("FULL_RUN_MANIFEST.json", "FULL_RUN_CHECKPOINT.json", "FULL_RUN_RESULT.json"):
        artifact = json.loads((root / name).read_text(encoding="utf-8"))
        digest = artifact.pop("payload_sha256")
        assert digest == payload_sha256(artifact)


@pytest.mark.parametrize(
    "histories",
    [
        FORMAL_HISTORY_IDS[:-1],
        (*FORMAL_HISTORY_IDS[:-1], FORMAL_HISTORY_IDS[0]),
        tuple(reversed(FORMAL_HISTORY_IDS)),
    ],
)
def test_full_run_rejects_subset_duplicate_or_order_drift_before_creation(
    tmp_path: Path, histories: tuple[str, ...]
) -> None:
    root = tmp_path / "run"
    with pytest.raises(V4FullRunError, match="formal_history_order_drift"):
        run_v4_full(
            frozen_method_path=_frozen(tmp_path),
            output_root=root,
            run_id="v4-full-test-002",
            histories=histories,
            mode="fixture",
        )
    assert not root.exists()


def test_blocked_preflight_is_non_mergeable_and_never_fakes_formal_result(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    preflight = {
        "status": "BLOCKED_SERVICE_PREFLIGHT",
        "classification": "EXECUTION_SANDBOX_NETWORK_ISOLATION",
    }
    root = tmp_path / "run"
    result = run_v4_full(
        frozen_method_path=_frozen(tmp_path),
        output_root=root,
        run_id="v4-full-test-003",
        mode="live",
        preflight=preflight,
        history_runner=_pass_runner(calls),
    )

    assert result["status"] == "FAILED_NON_MERGEABLE"
    assert result["formal_main_table_eligible"] is False
    assert result["classification"] == "EXECUTION_SANDBOX_NETWORK_ISOLATION"
    assert calls == []
    assert (root / "FAILURE.json").is_file()
    assert not (root / "FULL_RUN_RESULT.json").exists()


def test_explicit_blocked_mode_never_runs_even_with_ready_preflight(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    result = run_v4_full(
        frozen_method_path=_frozen(tmp_path),
        output_root=tmp_path / "run",
        run_id="v4-full-test-blocked",
        mode="blocked",
        preflight=_ready_preflight(),
        history_runner=_pass_runner(calls),
    )
    assert result["status"] == "FAILED_NON_MERGEABLE"
    assert result["classification"] == "SERVICE_PREFLIGHT_BLOCKED"
    assert calls == []


def test_fixture_run_is_complete_but_explicitly_not_formal_eligible(tmp_path: Path) -> None:
    result = run_v4_full(
        frozen_method_path=_frozen(tmp_path),
        output_root=tmp_path / "run",
        run_id="v4-full-test-004",
        mode="fixture",
    )
    assert result["status"] == "PASS"
    assert result["source_count"] == sum(FORMAL_HISTORY_SOURCE_COUNTS.values()) == 188
    assert result["formal_main_table_eligible"] is False
    assert result["runner_mode"] == "fixture"


def test_resume_skips_only_sealed_pass_histories_and_rejects_drift(tmp_path: Path) -> None:
    class SimulatedProcessExit(BaseException):
        pass

    root = tmp_path / "run"
    first_calls: list[dict[str, object]] = []

    def interrupted(**kwargs: object) -> dict[str, object]:
        if first_calls:
            raise SimulatedProcessExit
        return _pass_runner(first_calls)(**kwargs)

    arguments = {
        "frozen_method_path": _frozen(tmp_path),
        "output_root": root,
        "run_id": "v4-full-test-005",
        "mode": "live",
        "preflight": _ready_preflight(),
    }
    with pytest.raises(SimulatedProcessExit):
        run_v4_full(**arguments, history_runner=interrupted)

    first_result_path = root / "histories" / FORMAL_HISTORY_IDS[0] / "result.json"
    original = first_result_path.read_bytes()
    resumed_calls: list[dict[str, object]] = []
    result = run_v4_full(**arguments, history_runner=_pass_runner(resumed_calls))
    assert result["status"] == "PASS"
    assert [call["history_id"] for call in resumed_calls] == list(FORMAL_HISTORY_IDS[1:])
    assert first_result_path.read_bytes() == original

    drifted = json.loads(first_result_path.read_text(encoding="utf-8"))
    drifted["source_count"] = 48
    first_result_path.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(V4FullRunError, match="history_result_payload_hash_mismatch"):
        run_v4_full(**arguments, history_runner=_pass_runner([]))
    assert json.loads(first_result_path.read_text(encoding="utf-8"))["source_count"] == 48


def test_failed_history_is_terminal_non_mergeable(tmp_path: Path) -> None:
    def failed(**kwargs: object) -> dict[str, object]:
        return {
            "status": "FAILED",
            "history_id": kwargs["history_id"],
            "run_id": kwargs["run_id"],
            "namespace": kwargs["namespace"],
            "source_count": kwargs["source_count"],
        }

    root = tmp_path / "run"
    arguments = {
        "frozen_method_path": _frozen(tmp_path),
        "output_root": root,
        "run_id": "v4-full-test-006",
        "mode": "live",
        "preflight": _ready_preflight(),
    }
    result = run_v4_full(**arguments, history_runner=failed)
    assert result["status"] == "FAILED_NON_MERGEABLE"
    assert result["classification"] == "HISTORY_FAILED"
    assert not (root / "FULL_RUN_RESULT.json").exists()
    assert run_v4_full(**arguments, history_runner=_pass_runner([])) == result


def test_resume_can_finish_sealing_after_all_history_results_pass(tmp_path: Path) -> None:
    root = tmp_path / "run"
    arguments = {
        "frozen_method_path": _frozen(tmp_path),
        "output_root": root,
        "run_id": "v4-full-test-007",
        "mode": "fixture",
    }
    expected = run_v4_full(**arguments)
    (root / "FULL_RUN_RESULT.json").unlink()
    checkpoint_path = root / "FULL_RUN_CHECKPOINT.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint.pop("payload_sha256")
    checkpoint["status"] = "RUNNING"
    checkpoint["formal_main_table_eligible"] = False
    checkpoint["payload_sha256"] = payload_sha256(checkpoint)
    atomic_write_json(checkpoint_path, checkpoint)
    resumed = run_v4_full(**arguments)
    assert resumed == expected


def test_full_run_cli_executes_fixture_without_formal_eligibility(tmp_path: Path) -> None:
    root = tmp_path / "cli-run"
    script = Path(__file__).resolve().parents[1] / "scripts/run_membind_v4_full.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--frozen-method",
            str(_frozen(tmp_path)),
            "--fresh-namespaces",
            "--mode",
            "fixture",
            "--run-id",
            "v4-full-cli-test",
            "--output-root",
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    public = json.loads(completed.stdout)
    assert public["status"] == "PASS"
    assert public["source_count"] == 188
    assert public["formal_main_table_eligible"] is False
    result = json.loads((root / "FULL_RUN_RESULT.json").read_text(encoding="utf-8"))
    assert result["runner_mode"] == "fixture"
    assert result["formal_main_table_eligible"] is False
