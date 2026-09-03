#!/usr/bin/env python3
"""Execute the sealed 45-cell formal campaign with full QA per cell."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen
from pathlib import Path
from typing import Any

from formal_three_arm_harness import (
    ARMS,
    _json,
    _valid_cell,
    _valid_construction,
    _valid_full_qa,
    _write,
    reduce_formal,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
ENV_SCRIPT = ROOT / "scripts/local_runtime_8b_dual/activate.sh"
RUNNER = ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_v61_8b.py"
QA_RUNNER = ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_v13_qa_resume.py"


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


HEARTBEAT_SECONDS = 30.0


def _proc_cmdline(pid: int) -> list[str]:
    """Return argv for *pid*, or [] when it has exited/unreadable."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return []
    return [item.decode("utf-8", "replace") for item in raw.split(b"\0") if item]


def _argv_has_exact_identity(argv: list[str], *, attempt_root: Path, cell: dict[str, Any]) -> bool:
    """Recognise only this exact construction attempt.

    A PID file can be stale or recycled.  Matching the project runner path,
    output root, attempt id and namespace prevents accidentally waiting on or
    launching alongside an unrelated process.
    """
    required = {
        str(RUNNER),
        "--output-root",
        str(attempt_root),
        "--attempt-id",
        str(cell["attempt_id"]),
        "--namespace",
        str(cell["namespace"]),
    }
    return bool(argv) and required.issubset(set(argv))


def _active_exact_pids(attempt_root: Path, cell: dict[str, Any], pidfile: Path) -> list[int]:
    """Find active children for one cell, ignoring stale/mismatched PIDs."""
    candidates: set[int] = set()
    if pidfile.is_file():
        try:
            candidates.add(int(pidfile.read_text(encoding="utf-8").strip()))
        except (ValueError, OSError):
            pass
    # PID reuse is possible, so scan /proc for the exact identity as a second
    # source of truth instead of trusting the file alone.
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            candidates.add(int(entry.name))
    return sorted(pid for pid in candidates if pid > 0 and _argv_has_exact_identity(_proc_cmdline(pid), attempt_root=attempt_root, cell=cell))


def _qa_argv_has_exact_identity(
    argv: list[str], *, attempt: Path, qa_root: Path
) -> bool:
    required = {
        str(QA_RUNNER),
        "--block-root",
        str(attempt / "block"),
        "--qa-output-root",
        str(qa_root),
        "--scope",
        "FULL",
    }
    return bool(argv) and required.issubset(set(argv))


def _active_qa_pids(
    *, attempt: Path, qa_root: Path, pidfile: Path
) -> list[int]:
    candidates: set[int] = set()
    if pidfile.is_file():
        try:
            candidates.add(int(pidfile.read_text(encoding="utf-8").strip()))
        except (ValueError, OSError):
            pass
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            candidates.add(int(entry.name))
    return sorted(
        pid
        for pid in candidates
        if pid > 0
        and _qa_argv_has_exact_identity(
            _proc_cmdline(pid), attempt=attempt, qa_root=qa_root
        )
    )


def _provider_metrics() -> dict[str, float | None]:
    """Best-effort local vLLM metrics; missing metrics are recorded as null."""
    result: dict[str, float | None] = {
        "provider_running": None,
        "provider_waiting": None,
        "provider_kv_usage": None,
        "generation_tokens": None,
    }
    try:
        with urlopen("http://127.0.0.1:18200/metrics", timeout=2) as response:
            payload = response.read().decode("utf-8", "replace")
    except Exception:
        return result
    patterns = {
        "provider_running": (r"(?:num_requests_running|requests_running)\s+([0-9.eE+-]+)",),
        "provider_waiting": (r"(?:num_requests_waiting|requests_waiting)\s+([0-9.eE+-]+)",),
        "provider_kv_usage": (r"(?:gpu_cache_usage_perc|kv_cache_usage_perc|kv_cache_usage)\s+([0-9.eE+-]+)",),
        "generation_tokens": (r"(?:generation_tokens_total|num_generation_tokens_total)\s+([0-9.eE+-]+)",),
    }
    for key, candidates in patterns.items():
        for pattern in candidates:
            match = re.search(pattern, payload)
            if match:
                try:
                    result[key] = float(match.group(1))
                except ValueError:
                    pass
                break
    return result


def _event_frontier(log: Path, attempt: Path) -> int:
    """Return a monotone-ish event frontier from available local artifacts."""
    for path in (attempt / "campaign_ledger.jsonl", log):
        if path.is_file():
            try:
                return sum(1 for line in path.open(encoding="utf-8", errors="replace") if line.strip())
            except OSError:
                continue
    return 0


def _append_heartbeat(path: Path, *, runner_pid: int, child_pid: int | None, cell: dict[str, Any], attempt: Path, log: Path) -> None:
    metrics = _provider_metrics()
    row = {
        "timestamp": time.time(),
        "runner_pid": runner_pid,
        "active_child_pid": child_pid,
        "cell_id": cell.get("cell_id"),
        "attempt_id": cell.get("attempt_id"),
        "namespace": cell.get("namespace"),
        "event_frontier": _event_frontier(log, attempt),
        **metrics,
    }
    _append(path, row)


def _run_process(cmd: list[str], *, env: dict[str, str], log: Path, pidfile: Path, heartbeat: Path, cell: dict[str, Any], attempt: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as output:
        proc = subprocess.Popen(cmd, stdout=output, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
        pidfile.write_text(str(proc.pid) + "\n", encoding="utf-8")
        _append_heartbeat(heartbeat, runner_pid=os.getpid(), child_pid=proc.pid, cell=cell, attempt=attempt, log=log)
        while True:
            try:
                returncode = proc.wait(timeout=HEARTBEAT_SECONDS)
                _append_heartbeat(heartbeat, runner_pid=os.getpid(), child_pid=None, cell=cell, attempt=attempt, log=log)
                return returncode
            except subprocess.TimeoutExpired:
                _append_heartbeat(heartbeat, runner_pid=os.getpid(), child_pid=proc.pid, cell=cell, attempt=attempt, log=log)


def _formal_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "MAB_RUNTIME_PROVIDER": "LOCAL_8B",
        "CONSTRUCTION_LLM_BASE_URL": "http://127.0.0.1:18200/v1",
        "CONSTRUCTION_LLM_MODEL": "qwen3-8b-awq",
        "QUALITY_LLM_BASE_URL": "http://127.0.0.1:18200/v1",
        "QUALITY_LLM_MODEL": "qwen3-8b-awq",
        "CONSTRUCTION_LLM_API_KEY": env.get("MEMBIND_LOCAL_API_KEY", "membind-local"),
        "QUALITY_LLM_API_KEY": env.get("MEMBIND_LOCAL_API_KEY", "membind-local"),
        "VLLM_API_KEY": env.get("MEMBIND_LOCAL_API_KEY", "membind-local"),
        "EMBEDDING_BASE_URL": "http://127.0.0.1:18202/v1",
        "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
        "EMBEDDING_API_KEY": env.get("MEMBIND_LOCAL_API_KEY", "membind-local"),
        "EMBEDDING_DIM": "1024",
        "NEO4J_URI": "bolt://127.0.0.1:7687",
        "NEO4J_USER": env.get("NEO4J_USER", "neo4j"),
        "NEO4J_PASSWORD": env.get("NEO4J_PASSWORD", "password"),
        "NEO4J_DATABASE": env.get("NEO4J_DATABASE", "neo4j"),
    })
    return env


def _attempt_env(env: dict[str, str], cell: dict[str, Any]) -> dict[str, str]:
    bound = dict(env)
    run_id = f"{cell['campaign_id']}-{cell['cell_id']}-{cell['attempt_id']}"
    if not run_id or run_id == "UNBOUND_PROVIDER_FREE":
        raise ValueError("measured provenance run identity is not bound")
    bound["MEMBIND_PROVENANCE_RUN_ID"] = run_id
    return bound


def _cell_root(root: Path, cell: dict[str, Any]) -> Path:
    return root / "cells" / cell["cell_id"]


def _optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return _json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                return []
            rows.append(value)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def _construction_contract(
    attempt: Path, cell: dict[str, Any], *, returncode: int
) -> dict[str, Any]:
    complete_path = attempt / "complete.json"
    seal_path = attempt / "block" / "construction_seal.json"
    complete = _optional_json(complete_path)
    seal = _optional_json(seal_path)
    identity = seal.get("identity") if isinstance(seal.get("identity"), dict) else {}
    expected = [str(value) for value in cell.get("expected_construction_artifacts", ())]
    missing = [relative for relative in expected if not (attempt / relative).is_file()]
    complete_valid = (
        returncode == 0
        and complete.get("status") == "PASS"
        and complete.get("attempt_id") == cell.get("attempt_id")
        and complete.get("namespace") == cell.get("namespace")
        and complete.get("method") == cell.get("arm")
    )
    seal_valid = (
        seal.get("status") == "CONSTRUCTION_SEALED"
        and identity.get("namespace") == cell.get("namespace")
        and identity.get("method") == cell.get("arm")
        and identity.get("context_id") == cell.get("history_id")
    )
    return {
        "construction_status": (
            "PASS" if complete_valid and seal_valid and not missing else "INVALID"
        ),
        "construction_complete_status": complete.get("status", "MISSING"),
        "construction_seal_status": seal.get("status", "MISSING"),
        "construction_artifacts_complete": not missing,
        "missing_construction_artifacts": missing,
        "t_build_ns": complete.get("build_makespan_ns"),
        "construction_complete": str(complete_path),
        "construction_seal": str(seal_path),
    }


def _qa_contract(
    qa_root: Path,
    *,
    returncode: int,
    cell: dict[str, Any],
) -> dict[str, Any]:
    summary_path = qa_root / "quality_summary.json"
    seal_path = qa_root / "qa_seal.json"
    results_path = qa_root / "qa_results.jsonl"
    summary = _optional_json(summary_path)
    seal = _optional_json(seal_path)
    sealed_summary = seal.get("summary") if isinstance(seal.get("summary"), dict) else {}
    parent = (
        seal.get("parent_construction_seal")
        if isinstance(seal.get("parent_construction_seal"), dict)
        else {}
    )
    results = _jsonl_rows(results_path)
    expected_identity = (
        cell.get("history_id"),
        cell.get("arm"),
        cell.get("namespace"),
    )

    def _artifact_identity(value: dict[str, Any]) -> tuple[Any, Any, Any]:
        return (
            value.get("context_id"),
            value.get("method"),
            value.get("namespace"),
        )

    result_identities = {
        (
            row.get("context_id"),
            row.get("qa_pair_id"),
            row.get("question_id"),
        )
        for row in results
    }
    qa_identity_hashes = {row.get("qa_identity_sha256") for row in results}
    rows_have_explicit_identity = all(
        all(isinstance(row.get(key), str) and row[key] for key in (
            "context_id",
            "qa_pair_id",
            "question_id",
            "qa_identity_sha256",
        ))
        and row.get("context_id") == cell.get("history_id")
        and row.get("status") == "COMPLETE"
        and row.get("judge_valid") is True
        for row in results
    )
    valid = (
        returncode == 0
        and _artifact_identity(summary) == expected_identity
        and summary.get("quality_status") == "PASS"
        and summary.get("expected_count") == 60
        and summary.get("completed_count") == 60
        and summary.get("invalid_count") == 0
        and seal.get("status") == "QA_SEALED"
        and parent.get("status") == "CONSTRUCTION_SEALED"
        and _artifact_identity(parent) == expected_identity
        and _artifact_identity(sealed_summary) == expected_identity
        and sealed_summary.get("quality_status") == "PASS"
        and sealed_summary.get("expected_count") == 60
        and sealed_summary.get("completed_count") == 60
        and sealed_summary.get("invalid_count") == 0
        and len(results) == 60
        and len(result_identities) == 60
        and len(qa_identity_hashes) == 60
        and rows_have_explicit_identity
    )
    return {
        "qa_status": "PASS" if valid else "INVALID",
        "qa_seal_status": seal.get("status", "MISSING"),
        "qa_rows": int(summary.get("completed_count") or 0),
        "qa_result_rows": len(results),
        "qa_quality_status": summary.get("quality_status"),
        "qa_summary": str(summary_path),
        "qa_seal": str(seal_path),
        "qa_results": str(results_path),
    }


INFRASTRUCTURE_ERROR_TYPES = frozenset(
    {
        "openai.APIConnectionError",
        "openai.APITimeoutError",
        "httpx.ConnectError",
        "httpx.ConnectTimeout",
        "httpx.ReadTimeout",
        "httpx.WriteTimeout",
        "httpx.PoolTimeout",
        "httpcore.ConnectError",
        "httpcore.ConnectTimeout",
        "httpcore.ReadTimeout",
        "builtins.ConnectionError",
        "builtins.ConnectionRefusedError",
        "builtins.TimeoutError",
        "asyncio.exceptions.TimeoutError",
    }
)


def _failure_class(row: dict[str, Any]) -> str:
    evidence = (
        row.get("construction_failure")
        if row.get("construction_status") != "PASS"
        else row.get("qa_failure")
    )
    evidence = evidence if isinstance(evidence, dict) else {}
    if evidence.get("error_type") in INFRASTRUCTURE_ERROR_TYPES:
        return "INFRASTRUCTURE_TRANSIENT"
    service_exit = evidence.get("service_exit_evidence")
    if (
        evidence.get("reason") == "provider_service_process_exited"
        and isinstance(service_exit, dict)
        and service_exit.get("verified") is True
    ):
        return "INFRASTRUCTURE_TRANSIENT"
    return "DETERMINISTIC_SYSTEM_FAILURE"


def _execute_cell(root: Path, frozen_root: Path, cell: dict[str, Any], *, env: dict[str, str], ledger: Path) -> dict[str, Any]:
    cell_root = _cell_root(root, cell)
    cell_root.mkdir(parents=True, exist_ok=True)
    attempt_root = cell_root / "construction"
    attempt_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{cell['campaign_id']}-{cell['cell_id']}-{cell['attempt_id']}"
    measured_env = _attempt_env(env, cell)
    cmd = [
        sys.executable,
        str(RUNNER),
        "--output-root",
        str(attempt_root),
        "--run-id",
        run_id,
        "--contexts",
        str(cell["history_index"]),
        "--methods",
        cell["arm"],
        "--v61-boundary",
        "MEMBIND_CORE",
        "--force-reference-rerun",
        "--attempt-id",
        cell["attempt_id"],
        "--namespace",
        cell["namespace"],
    ]
    attempt = attempt_root / f"context-{cell['history_index']}" / cell["arm"] / cell["attempt_id"]
    complete = attempt / "complete.json"
    failure = attempt / "failure.json"
    # If a persistent client was interrupted after dispatch, wait for that
    # exact PID instead of issuing a second request.  A terminal artifact is
    # consumed as-is; failed attempts are handled by the caller's replacement
    # policy and are never resumed.
    existing_terminal = complete.is_file() or failure.is_file()
    start = {"event": "CELL_CONSTRUCTION_START", **cell, "started_at": time.time(), "existing_attempt": existing_terminal}
    _append(ledger, start)
    if not existing_terminal:
        # Wait only for an exact, still-live attempt.  A stale PID file or a
        # recycled PID with different argv is ignored and cannot suppress a
        # fresh launch.  There is deliberately no short outer timeout: a
        # provider request may legally run for up to the configured 3600 s.
        while not complete.is_file() and not failure.is_file():
            active = _active_exact_pids(attempt_root, cell, cell_root / "construction.pid")
            if not active:
                break
            _append_heartbeat(root / "heartbeat.jsonl", runner_pid=os.getpid(), child_pid=active[0], cell=cell, attempt=attempt, log=cell_root / "construction.log")
            time.sleep(HEARTBEAT_SECONDS)
        existing_terminal = complete.is_file() or failure.is_file()
    if existing_terminal:
        rc = 0 if complete.is_file() else 2
    else:
        rc = _run_process(
            cmd,
            env=measured_env,
            log=cell_root / "construction.log",
            pidfile=cell_root / "construction.pid",
            heartbeat=root / "heartbeat.jsonl",
            cell=cell,
            attempt=attempt,
        )
    if not complete.is_file() and not failure.is_file():
        # A child can exit without materialising its terminal seal (for
        # example, SIGTERM from an external supervisor).  Make that outcome
        # explicit before the replacement policy is considered.
        _write(failure, {"status": "FAIL", "reason": "runner_exit_without_terminal_artifact", "attempt_id": cell["attempt_id"], "namespace": cell["namespace"], "returncode": rc, "created_at": time.time()})
    construction = _construction_contract(attempt, cell, returncode=rc)
    row: dict[str, Any] = {
        **cell,
        "actual_attempt_id": cell["attempt_id"],
        "actual_namespace": cell["namespace"],
        "measured_provenance_run_id": measured_env["MEMBIND_PROVENANCE_RUN_ID"],
        "construction_returncode": rc,
        "construction_root": str(attempt.resolve()),
        **construction,
        "qa_status": "MISSING",
        "qa_seal_status": "MISSING",
        "qa_rows": 0,
        "qa_result_rows": 0,
    }
    if construction["construction_status"] != "PASS":
        row["construction_failure"] = _optional_json(failure) or {
            "reason": "construction_contract_invalid",
            "returncode": rc,
            "missing_construction_artifacts": construction[
                "missing_construction_artifacts"
            ],
        }
    else:
        qa_root = attempt / "block" / "qa_full"
        qa_seal = qa_root / "qa_seal.json"
        qa_failure = attempt / "block" / "qa_resume_failure.json"
        qa_results = qa_root / "qa_results.jsonl"
        qa_cmd = [
            sys.executable,
            str(QA_RUNNER),
            "--frozen-root",
            str(frozen_root),
            "--block-root",
            str(attempt / "block"),
            "--scope",
            "FULL",
            "--qa-output-root",
            str(qa_root),
        ]
        while not qa_seal.is_file() and not qa_failure.is_file():
            active = _active_qa_pids(
                attempt=attempt,
                qa_root=qa_root,
                pidfile=cell_root / "qa.pid",
            )
            if not active:
                break
            _append_heartbeat(
                root / "heartbeat.jsonl",
                runner_pid=os.getpid(),
                child_pid=active[0],
                cell=cell,
                attempt=attempt,
                log=cell_root / "qa.log",
            )
            time.sleep(HEARTBEAT_SECONDS)
        if qa_seal.is_file():
            qa_rc = 0
        elif qa_failure.is_file():
            qa_rc = 2
        elif qa_results.is_file():
            # NO_RESUME_FORMAL_ATTEMPT applies to the whole cell.  Partial QA
            # without a live exact child or terminal seal cannot be resumed.
            _write(
                qa_failure,
                {
                    "status": "FAILED",
                    "reason": "qa_partial_without_terminal_seal",
                    "attempt_id": cell["attempt_id"],
                    "namespace": cell["namespace"],
                    "created_at": time.time(),
                },
            )
            qa_rc = 2
        else:
            qa_rc = _run_process(
                qa_cmd,
                env=measured_env,
                log=cell_root / "qa.log",
                pidfile=cell_root / "qa.pid",
                heartbeat=root / "heartbeat.jsonl",
                cell=cell,
                attempt=attempt,
            )
        qa = _qa_contract(qa_root, returncode=qa_rc, cell=cell)
        row.update({"qa_returncode": qa_rc, **qa})
        if qa["qa_status"] != "PASS":
            row["qa_failure"] = _optional_json(qa_failure) or {
                "reason": "qa_contract_invalid",
                "returncode": qa_rc,
                "qa_seal_status": qa["qa_seal_status"],
                "qa_rows": qa["qa_rows"],
                "qa_result_rows": qa["qa_result_rows"],
                "qa_quality_status": qa["qa_quality_status"],
            }
    _append(ledger, {"event": "CELL_COMPLETE", **row, "ended_at": time.time()})
    return row


def _progress(
    rows: list[dict[str, Any]],
    *,
    processed_cells: int,
    attempts_executed: int,
    history: int,
    replicate: int,
    last_cell: str,
) -> dict[str, Any]:
    return {
        "processed_cells": processed_cells,
        "attempts_executed": attempts_executed,
        "valid_construction_cells": sum(_valid_construction(row) for row in rows),
        "valid_full_qa_cells": sum(
            _valid_construction(row) and _valid_full_qa(row) for row in rows
        ),
        "selected_valid_cells": sum(_valid_cell(row) for row in rows),
        "history": history,
        "replicate": replicate,
        "last_cell": last_cell,
        "updated_at": time.time(),
    }


def _stop_campaign(
    root: Path,
    *,
    rows: list[dict[str, Any]],
    invalid: list[dict[str, Any]],
    failure_class: str,
    failure_row: dict[str, Any],
    processed_cells: int,
    attempts_executed: int,
    history: int,
    replicate: int,
) -> dict[str, Any]:
    progress = _progress(
        rows,
        processed_cells=processed_cells,
        attempts_executed=attempts_executed,
        history=history,
        replicate=replicate,
        last_cell=str(failure_row.get("cell_id")),
    )
    _write(root / "FORMAL_PROGRESS.json", progress)
    reduced = reduce_formal(rows)
    _write(root / "FORMAL_REDUCTION.json", reduced)
    _write(
        root / "INVALID_ATTEMPT_LEDGER.json",
        {
            "schema_version": "membind.invalid-attempt-ledger.v2",
            "entries": invalid,
        },
    )
    seal = {
        "schema_version": "membind.formal-campaign-failure.v1",
        "status": failure_class,
        "failure_class": failure_class,
        "scheduling_status": "STOPPED",
        "failed_cell_id": failure_row.get("cell_id"),
        "failed_attempt_id": failure_row.get("attempt_id"),
        "failed_namespace": failure_row.get("namespace"),
        **progress,
        "created_at": time.time(),
    }
    _write(root / "FORMAL_CAMPAIGN_FAILURE.json", seal)
    return {
        **reduced,
        "reduction_status": reduced["status"],
        "status": failure_class,
        **progress,
    }


def run(root: Path, frozen_root: Path) -> dict[str, Any]:
    manifest = _json(root / "FORMAL_CAMPAIGN_MANIFEST_SEAL.json")
    validate_manifest(manifest)
    ledger = root / "formal_ledger.jsonl"
    (root / "formal_runner.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    env = _formal_env()
    rows: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    processed_cells = 0
    attempts_executed = 0
    # History-atomic order is explicit and independent of filesystem order.
    for history in range(5):
        for replicate in range(3):
            for arm in ARMS:
                cell = next(c for c in manifest["cells"] if c["history_index"] == history and c["replicate_id"] == replicate and c["arm"] == arm)
                row = _execute_cell(root, frozen_root, cell, env=env, ledger=ledger)
                processed_cells += 1
                attempts_executed += 1
                if not _valid_cell(row):
                    classification = _failure_class(row)
                    entry = {
                        "cell_id": cell["cell_id"],
                        "attempt_id": cell["attempt_id"],
                        "namespace": cell["namespace"],
                        "reason": "NO_RESUME_FORMAL_ATTEMPT",
                        "failure_class": classification,
                        "row": row,
                    }
                    invalid.append(entry)
                    if classification == "DETERMINISTIC_SYSTEM_FAILURE":
                        rows.append(row)
                        return _stop_campaign(
                            root,
                            rows=rows,
                            invalid=invalid,
                            failure_class=classification,
                            failure_row=row,
                            processed_cells=processed_cells,
                            attempts_executed=attempts_executed,
                            history=history,
                            replicate=replicate,
                        )
                    # Only a proven infrastructure transient receives one
                    # fresh whole-cell replacement.  A second failure stops
                    # immediately, regardless of its classification.
                    replacement = dict(cell)
                    replacement["attempt_id"] = uuid.uuid4().hex[:12]
                    replacement["namespace"] = f"{cell['namespace']}-replacement-{replacement['attempt_id']}"
                    replacement["replacement_of"] = cell["attempt_id"]
                    replacement_row = _execute_cell(root, frozen_root, replacement, env=env, ledger=ledger)
                    attempts_executed += 1
                    entry["replacement_attempt_id"] = replacement["attempt_id"]
                    entry["replacement_namespace"] = replacement["namespace"]
                    entry["replacement_row"] = replacement_row
                    if _valid_cell(replacement_row):
                        row = replacement_row
                    else:
                        entry["replacement_failure_class"] = _failure_class(
                            replacement_row
                        )
                        rows.append(replacement_row)
                        return _stop_campaign(
                            root,
                            rows=rows,
                            invalid=invalid,
                            failure_class="REPLACEMENT_FAILURE",
                            failure_row=replacement_row,
                            processed_cells=processed_cells,
                            attempts_executed=attempts_executed,
                            history=history,
                            replicate=replicate,
                        )
                rows.append(row)
                _write(
                    root / "FORMAL_PROGRESS.json",
                    _progress(
                        rows,
                        processed_cells=processed_cells,
                        attempts_executed=attempts_executed,
                        history=history,
                        replicate=replicate,
                        last_cell=str(row.get("cell_id")),
                    ),
                )
    reduced = reduce_formal(rows)
    _write(root / "FORMAL_REDUCTION.json", reduced)
    _write(root / "INVALID_ATTEMPT_LEDGER.json", {"schema_version": "membind.invalid-attempt-ledger.v2", "entries": invalid})
    return reduced


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--frozen-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.root.resolve(), args.frozen_root.resolve())
    print(json.dumps({"status": result["status"], "construction_cell_count": result["construction_cell_count"], "qa_seal_count": result["qa_seal_count"]}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
