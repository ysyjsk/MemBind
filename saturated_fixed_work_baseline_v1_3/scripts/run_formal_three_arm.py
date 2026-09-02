#!/usr/bin/env python3
"""Execute the sealed 45-cell formal campaign with full QA per cell."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from formal_three_arm_harness import ARMS, _json, _write, reduce_formal, validate_manifest


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


def _run_process(cmd: list[str], *, env: dict[str, str], log: Path, pidfile: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as output:
        proc = subprocess.Popen(cmd, stdout=output, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
        pidfile.write_text(str(proc.pid) + "\n", encoding="utf-8")
        return proc.wait()


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


def _cell_root(root: Path, cell: dict[str, Any]) -> Path:
    return root / "cells" / cell["cell_id"]


def _execute_cell(root: Path, frozen_root: Path, cell: dict[str, Any], *, env: dict[str, str], ledger: Path) -> dict[str, Any]:
    cell_root = _cell_root(root, cell)
    cell_root.mkdir(parents=True, exist_ok=True)
    attempt_root = cell_root / "construction"
    attempt_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{cell['campaign_id']}-{cell['cell_id']}-{cell['attempt_id']}"
    cmd = [sys.executable, str(RUNNER), "--output-root", str(attempt_root), "--run-id", run_id, "--contexts", str(cell["history_index"]), "--methods", cell["arm"], "--v61-boundary", "MEMBIND_CORE", "--continue-on-error", "--force-reference-rerun", "--attempt-id", cell["attempt_id"], "--namespace", cell["namespace"]]
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
    if not existing_terminal and (cell_root / "construction.pid").is_file():
        try:
            pid = int((cell_root / "construction.pid").read_text().strip())
        except ValueError:
            pid = -1
        deadline = time.time() + 3600
        while pid > 0 and time.time() < deadline and Path(f"/proc/{pid}").exists() and not complete.is_file() and not failure.is_file():
            time.sleep(30)
        existing_terminal = complete.is_file() or failure.is_file()
    if existing_terminal:
        rc = 0 if complete.is_file() else 2
    else:
        rc = _run_process(cmd, env=env, log=cell_root / "construction.log", pidfile=cell_root / "construction.pid")
    construction_status = "PASS" if rc == 0 and complete.is_file() and _json(complete).get("status") == "PASS" else "INVALID"
    row: dict[str, Any] = {**cell, "actual_attempt_id": cell["attempt_id"], "actual_namespace": cell["namespace"], "construction_status": construction_status, "construction_returncode": rc, "construction_root": str(attempt.resolve()), "qa_status": "MISSING", "qa_rows": 0}
    if construction_status == "PASS":
        qa_cmd = [sys.executable, str(QA_RUNNER), "--frozen-root", str(frozen_root), "--block-root", str(attempt / "block"), "--scope", "FULL", "--qa-output-root", str(attempt / "block" / "qa_full")]
        qa_rc = _run_process(qa_cmd, env=env, log=cell_root / "qa.log", pidfile=cell_root / "qa.pid")
        summary_path = attempt / "block" / "qa_full" / "quality_summary.json"
        summary = _json(summary_path) if summary_path.is_file() else {}
        row["qa_status"] = "PASS" if qa_rc == 0 and summary.get("quality_status") == "PASS" and summary.get("expected_count") == 60 and summary.get("completed_count") == 60 and summary.get("invalid_count") == 0 else "INVALID"
        row["qa_rows"] = int(summary.get("completed_count") or 0)
        row["qa_quality_status"] = summary.get("quality_status")
        row["qa_summary"] = str(summary_path)
    _append(ledger, {"event": "CELL_COMPLETE", **row, "ended_at": time.time()})
    return row


def run(root: Path, frozen_root: Path) -> dict[str, Any]:
    manifest = _json(root / "FORMAL_CAMPAIGN_MANIFEST_SEAL.json")
    validate_manifest(manifest)
    ledger = root / "formal_ledger.jsonl"
    env = _formal_env()
    rows: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    # History-atomic order is explicit and independent of filesystem order.
    for history in range(5):
        for replicate in range(3):
            for arm in ARMS[replicate:] + ARMS[:replicate]:
                cell = next(c for c in manifest["cells"] if c["history_index"] == history and c["replicate_id"] == replicate and c["arm"] == arm)
                row = _execute_cell(root, frozen_root, cell, env=env, ledger=ledger)
                if row["construction_status"] != "PASS" or row["qa_status"] != "PASS":
                    invalid.append({"cell_id": cell["cell_id"], "attempt_id": cell["attempt_id"], "reason": "NO_RESUME_FORMAL_ATTEMPT", "row": row})
                    # One fresh replacement is allowed without mutating the
                    # sealed manifest.  The failed attempt remains preserved.
                    replacement = dict(cell)
                    replacement["attempt_id"] = uuid.uuid4().hex[:12]
                    replacement["namespace"] = f"{cell['namespace']}-replacement-{replacement['attempt_id']}"
                    replacement["replacement_of"] = cell["attempt_id"]
                    replacement_row = _execute_cell(root, frozen_root, replacement, env=env, ledger=ledger)
                    invalid[-1]["replacement_attempt_id"] = replacement["attempt_id"]
                    if replacement_row["construction_status"] == "PASS" and replacement_row["qa_status"] == "PASS":
                        row = replacement_row
                    else:
                        invalid[-1]["replacement_status"] = replacement_row
                rows.append(row)
                _write(root / "FORMAL_PROGRESS.json", {"completed_cells": len(rows), "valid_cells": sum(int(_valid_cell(r)) for r in rows), "history": history, "replicate": replicate, "last_cell": row.get("cell_id"), "updated_at": time.time()})
    reduced = reduce_formal(rows)
    _write(root / "FORMAL_REDUCTION.json", reduced)
    _write(root / "INVALID_ATTEMPT_LEDGER.json", {"schema_version": "membind.invalid-attempt-ledger.v1", "entries": invalid})
    return reduced


def _valid_cell(row: dict[str, Any]) -> bool:
    return row.get("construction_status") == "PASS" and row.get("qa_status") == "PASS" and row.get("qa_rows") == 60


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
