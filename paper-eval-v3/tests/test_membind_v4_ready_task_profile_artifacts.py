"""Reproducibility and scope contracts for the ready-task profile artifact."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file


PROJECT = Path(__file__).resolve().parents[1]
V4_ROOT = PROJECT / "artifacts/paper_eval/membind_v4"
PROFILE = V4_ROOT / "V4_READY_TASK_OPPORTUNITY_PROFILE.json"
REPORT = V4_ROOT / "V4_READY_TASK_OPPORTUNITY_PROFILE.md"
DECISION = V4_ROOT / "V4_READY_TASK_OFFLINE_DECISION.md"
SCRIPT = PROJECT / "scripts/generate_membind_v4_ready_task_profile.py"


def _run_generator(tmp_path: Path) -> tuple[dict[str, object], str, str]:
    output = tmp_path / "profile.json"
    report = tmp_path / "profile.md"
    decision = tmp_path / "decision.md"
    environment = os.environ.copy()
    source_root = str(PROJECT / "src")
    environment["PYTHONPATH"] = (
        source_root
        if not environment.get("PYTHONPATH")
        else f"{source_root}{os.pathsep}{environment['PYTHONPATH']}"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output",
            str(output),
            "--report",
            str(report),
            "--decision",
            str(decision),
        ],
        cwd=PROJECT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return (
        json.loads(output.read_text(encoding="utf-8")),
        report.read_text(encoding="utf-8"),
        decision.read_text(encoding="utf-8"),
    )


def test_ready_profile_is_sealed_reproducible_and_non_mergeable(
    tmp_path: Path,
) -> None:
    persisted = json.loads(PROFILE.read_text(encoding="utf-8"))
    regenerated, report, decision = _run_generator(tmp_path)

    assert regenerated == persisted
    assert persisted["payload_sha256"] == payload_sha256(
        {
            key: value
            for key, value in persisted.items()
            if key != "payload_sha256"
        }
    )
    assert persisted["status"] == "DIAGNOSTIC_ONLY"
    assert persisted["scope"] == {
        "arrival_trace_unchanged": True,
        "history_id": "07741c45",
        "network_calls": 0,
        "persistent_writes": 0,
        "scheduler_implemented": False,
        "source_count": 12,
        "source_prefix": "0..11",
    }
    assert persisted["decision"] == {
        "backend_speedup_proven": False,
        "fine_grained_operator_direction": "STOP_FINE_OPERATOR_CLAIMS_UNOBSERVABLE",
        "formal_main_table_eligible": False,
        "ready_scheduler_direction": "NO_SCHEDULING_OPPORTUNITY_OBSERVED",
        "reason": (
            "The sealed trace exposes no ready width >= 2; fine-grained operator "
            "membership and backend batching are not observable."
        ),
    }
    profile = persisted["profile"]
    assert profile["scheduler_selectable"]["peak_ready_width"] == 1
    assert profile["scheduler_selectable"]["time_fraction_at_ready_width_ge"] == {
        "2": 0.0,
        "4": 0.0,
        "8": 0.0,
    }
    assert profile["workflow_ready"]["peak_ready_width"] == 1
    assert profile["workflow_ready"]["peak_llm_heavy_ready_width"] == 1
    assert profile["workflow_ready"][
        "time_fraction_at_llm_heavy_ready_width_ge"
    ] == {"2": 0.0, "4": 0.0, "8": 0.0}
    assert profile["fine_grained_operator_profile"] == {
        "operator_resolution": "WORKFLOW_STAGE_ONLY",
        "reason": (
            "sealed traces expose request kind and lifecycle stage, but no "
            "EntityExtract/EdgeExtract/NodeResolve member IDs"
        ),
        "status": "NOT_OBSERVABLE",
        "unavailable_fields": [
            "operator_type_within_compile_or_bind",
            "ready_task_member_ids",
            "fine_grained_same_type_ready_width",
        ],
    }
    assert "NO_SCHEDULING_OPPORTUNITY_OBSERVED" in report
    assert "STOP_V4_NODE_RESOLVE" in decision


def test_profile_binds_sealed_inputs_without_creating_live_artifacts() -> None:
    binding = PROFILE.read_text(encoding="utf-8")
    input_binding = json.loads(binding)["input_binding"]
    pilot = PROJECT / input_binding["pilot_root"]
    assert input_binding["queue_file_sha256"] == sha256_file(pilot / "queue.jsonl")
    assert input_binding["events_file_sha256"] == sha256_file(pilot / "events.jsonl")
    assert input_binding["llm_file_sha256"] == sha256_file(pilot / "llm.jsonl")
    assert not (V4_ROOT / "V4_C01_CA_RESULT.json").exists()
    assert not (V4_ROOT / "V4_C01_CA_REDUCED.json").exists()
