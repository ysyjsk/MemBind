"""Reproducibility and scope contracts for the parallelism funnel artifact."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file


PROJECT = Path(__file__).resolve().parents[1]
V4_ROOT = PROJECT / "artifacts/paper_eval/membind_v4"
ARTIFACT = V4_ROOT / "V4_PARALLELISM_FUNNEL.json"
REPORT = V4_ROOT / "V4_PARALLELISM_FUNNEL.md"
DECISION = V4_ROOT / "V4_PARALLELISM_ROOT_CAUSE_DECISION.md"
SCRIPT = PROJECT / "scripts/generate_membind_v4_parallelism_funnel.py"


def _run_generator(tmp_path: Path) -> tuple[dict[str, object], str, str]:
    output = tmp_path / "funnel.json"
    report = tmp_path / "funnel.md"
    decision = tmp_path / "decision.md"
    environment = os.environ.copy()
    source_root = str(PROJECT / "src")
    environment["PYTHONPATH"] = (
        source_root
        if not environment.get("PYTHONPATH")
        else f"{source_root}{os.pathsep}{environment['PYTHONPATH']}"
    )
    subprocess.run(
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


def test_parallelism_funnel_is_sealed_reproducible_and_non_mergeable(
    tmp_path: Path,
) -> None:
    persisted = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    regenerated, report, decision = _run_generator(tmp_path)

    assert regenerated == persisted
    assert persisted["payload_sha256"] == payload_sha256(
        {
            key: value
            for key, value in persisted.items()
            if key != "payload_sha256"
        }
    )
    assert persisted["status"] == "DIAGNOSTIC_ONLY_NON_MERGEABLE"
    assert persisted["scope"] == {
        "arrival_trace_unchanged": True,
        "baseline_registered_source_count": 49,
        "formal_main_table_eligible": False,
        "history_id": "07741c45",
        "live_candidate_authorized": False,
        "network_calls": 0,
        "persistent_writes": 0,
        "pilot_source_count": 12,
        "pilot_source_prefix": "0..11",
        "scheduler_implemented": False,
    }

    funnel = persisted["funnel"]
    assert funnel["source_outstanding"]["peak_width"] == 2
    assert funnel["workflow_ready_waiting"]["peak_width"] == 1
    assert funnel["workflow_active"]["peak_width"] == 2
    assert funnel["llm_request_pending"]["peak_width"] == 21
    assert funnel["llm_admission_waiting"]["peak_width"] == 20
    assert funnel["llm_client_running"]["peak_width"] == 2
    assert funnel["admission_snapshots"]["peak_waiting_count"] == 19
    assert funnel["admission_snapshots"]["peak_active_count"] == 2
    assert funnel["backend_internal"]["status"] == "NOT_OBSERVABLE"

    assert persisted["identity_scope_audit"]["source_scope"][
        "same_first_12_source_hashes"
    ] is True
    assert persisted["identity_scope_audit"]["arrival"][
        "same_first_12_offsets"
    ] is True
    assert persisted["identity_scope_audit"]["arrival"]["same_interarrival"] is True
    assert persisted["identity_scope_audit"]["execution_envelope"][
        "same_shared_execution_envelope"
    ] is True
    assert persisted["identity_scope_audit"]["execution_identity"][
        "same_execution_identity"
    ] is False
    assert persisted["identity_scope_audit"]["source_manifest"]["equal"] is False

    prefixes = {row["method"]: row for row in persisted["baseline_prefix_audit"]}
    assert prefixes["U0-aligned"]["recomputed_prefix"]["source_outstanding"][
        "peak_width"
    ] == 4
    assert prefixes["U0-aligned"]["recomputed_prefix"][
        "arrival_to_service_start_waiting"
    ]["peak_width"] == 3
    assert prefixes["U0-aligned"]["recomputed_prefix"]["mean_queue_delay_ns"] == (
        131641680682.66667
    )
    assert prefixes["A0-aligned"]["recomputed_prefix"]["mean_queue_delay_ns"] == (
        133903497322.41667
    )
    assert prefixes["P(C=2)-aligned"]["recomputed_prefix"][
        "mean_queue_delay_ns"
    ] == 732008554.3333334
    for row in prefixes.values():
        assert row["scope"]["prefix_censored"] is True
        assert row["registered_full_run"]["source_count"] == 49
        assert row["registered_full_run"]["performance"][
            "max_outstanding_backlog"
        ] in {5, 19}

    assert persisted["decision"] == {
        "backend_bottleneck_proven": False,
        "coarse_stage_scheduler_authorized": False,
        "end_to_end_parallelism_collapse_proven": False,
        "reason": (
            "The observable coarse ready pool has no scheduling choice, while "
            "source overlap and internal client admission pressure are both present."
        ),
        "root_cause_classification": "COARSE_READY_POOL_NO_CHOICE_WITH_INTERNAL_LLM_FANOUT",
        "source_backlog_observed": True,
        "stop_v4_node_resolve": True,
        "terminal": "NO_STAGE_SCHEDULER_CHOICE_LLM_ADMISSION_BACKLOG_OBSERVED",
        "workload_too_sparse_proven": False,
    }
    assert "NO_STAGE_SCHEDULER_CHOICE_LLM_ADMISSION_BACKLOG_OBSERVED" in report
    assert "STOP_V4_NODE_RESOLVE" in decision


def test_parallelism_artifact_does_not_modify_prior_v4_evidence() -> None:
    assert sha256_file(V4_ROOT / "V4_CONFLICT_OFFLINE_REPLAY.json") == (
        "d003baeca9858cbe91ec11b0d0216741aa2cc32529536bb5616ebe0d412c0834"
    )
    assert sha256_file(V4_ROOT / "V4_READY_TASK_OPPORTUNITY_PROFILE.json") == (
        "5f33ca8c7e15627684e62b2f4e79b8a32ccb7d07c07a93e24c4da810b5081d65"
    )
    assert not (V4_ROOT / "V4_C01_CA_RESULT.json").exists()
    assert not (V4_ROOT / "V4_C01_CA_REDUCED.json").exists()
