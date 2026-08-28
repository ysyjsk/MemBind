#!/usr/bin/env python3
"""Persistent serial supervisor for the five-context local V6.1 main table."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
PROFILE_ID = "local-qwen3-14b-awq-v1"
DEFAULT_ROOT = Path(
    "/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab/full5"
)
BLOCKS = (
    (0, "B0"),
    (0, "B1"),
    (0, "V6_1"),
    (1, "V6_1"),
    (1, "B0"),
    (1, "B1"),
    (2, "B1"),
    (2, "V6_1"),
    (2, "B0"),
    (3, "B0"),
    (3, "V6_1"),
    (3, "B1"),
    (4, "B1"),
    (4, "B0"),
    (4, "V6_1"),
)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _progress(
    output: Path, context_index: int, method: str, run_id: str
) -> dict[str, Any]:
    roots = sorted((output / "runs" / f"context-{context_index}" / method).glob("*"))
    matching = []
    for root in roots:
        attempt = root / "attempt.json"
        if not attempt.is_file():
            continue
        try:
            identity = json.loads(attempt.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if identity.get("run_id") == run_id:
            matching.append(root)
    if not matching:
        return {"durable_publications": 0, "attempt_root": None}
    root = matching[-1]
    journals = list(root.glob(".block*.jsonl"))
    durable_sources: set[int] = set()
    if journals:
        for line in journals[-1].read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == "PUBLICATION_DURABLE" and isinstance(
                row.get("source_sequence"), int
            ):
                durable_sources.add(int(row["source_sequence"]))
    return {"durable_publications": len(durable_sources), "attempt_root": str(root)}


def _preflight(path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/local_runtime/preflight.py"), "--timeout", "45"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"preflight failed: {completed.stderr[-500:]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--heartbeat-seconds", type=float, default=30)
    parser.add_argument("--max-block-retries", type=int, default=1)
    args = parser.parse_args()
    if os.environ.get("MEMBIND_PROFILE_ID") != PROFILE_ID:
        parser.error("activate the local-qwen3-14b-awq-v1 runtime first")
    selected = json.loads(args.policy_file.read_text(encoding="utf-8"))
    policy = selected.get("policy")
    if not isinstance(policy, Mapping):
        parser.error("selected policy file is invalid")
    output = args.output_root.resolve()
    state_root = output / "state"
    logs = output / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    queue = [
        {
            "block_index": index,
            "context_index": context,
            "method": method,
            "status": "QUEUED",
        }
        for index, (context, method) in enumerate(BLOCKS)
    ]
    manifest = {
        "schema_version": "membind.v6.1.full5-queue.v1",
        "status": "QUEUED",
        "profile_id": PROFILE_ID,
        "campaign_id": args.campaign_id,
        "policy": dict(policy),
        "block_count": len(queue),
        "contexts": [0, 1, 2, 3, 4],
        "methods": ["B0", "B1", "V6_1"],
        "serial_shared_provider": True,
        "blocks": queue,
        "created_at_unix": time.time(),
    }
    _atomic_json(output / "queue_manifest.json", manifest)
    ledger = output / "supervisor_ledger.jsonl"
    for block in queue:
        index = int(block["block_index"])
        context = int(block["context_index"])
        method = str(block["method"])
        success = False
        for retry in range(args.max_block_retries + 1):
            run_id = f"{args.campaign_id}-b{index:02d}-c{context}-{method.casefold().replace('_', '-')}-r{retry}"
            try:
                _preflight(output / "preflight" / f"{run_id}.json")
            except BaseException as exc:
                _append(
                    ledger,
                    {
                        "event": "PREFLIGHT_FAILURE",
                        "block_index": index,
                        "retry": retry,
                        "error": str(exc)[:1000],
                        "at_unix": time.time(),
                    },
                )
                time.sleep(min(30, args.heartbeat_seconds))
                continue
            command = [
                sys.executable,
                str(ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_v61_local.py"),
                "--output-root",
                str(output / "runs"),
                "--run-id",
                run_id,
                "--contexts",
                str(context),
                "--methods",
                method,
                "--lookahead",
                str(policy["lookahead"]),
                "--future-cap",
                str(policy["future_cap"]),
                "--native-future-quota",
                str(policy["native_future_quota"]),
            ]
            started = time.time()
            log_path = logs / f"{run_id}.log"
            with log_path.open("x", encoding="utf-8") as stream:
                child = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                _append(
                    ledger,
                    {
                        "event": "BLOCK_START",
                        **block,
                        "retry": retry,
                        "run_id": run_id,
                        "child_pid": child.pid,
                        "started_at_unix": started,
                    },
                )
                while child.poll() is None:
                    progress = _progress(output, context, method, run_id)
                    _atomic_json(
                        state_root / "supervisor_state.json",
                        {
                            "schema_version": "membind.v6.1.full5-state.v1",
                            "status": "RUNNING",
                            "profile_id": PROFILE_ID,
                            "campaign_id": args.campaign_id,
                            "supervisor_pid": os.getpid(),
                            "child_pid": child.pid,
                            "block": block,
                            "retry": retry,
                            "run_id": run_id,
                            "last_progress_at_unix": time.time(),
                            **progress,
                        },
                    )
                    time.sleep(args.heartbeat_seconds)
                return_code = int(child.returncode or 0)
            progress = _progress(output, context, method, run_id)
            _append(
                ledger,
                {
                    "event": "BLOCK_END",
                    **block,
                    "retry": retry,
                    "run_id": run_id,
                    "return_code": return_code,
                    "elapsed_s": time.time() - started,
                    "ended_at_unix": time.time(),
                    **progress,
                },
            )
            if return_code == 0:
                success = True
                break
        block["status"] = "PASS" if success else "FAILED_CONTINUING"
        block["completed_at_unix"] = time.time()
        manifest["status"] = "RUNNING"
        manifest["blocks"] = queue
        _atomic_json(output / "queue_manifest.json", manifest)
    final_status = "PASS" if all(row["status"] == "PASS" for row in queue) else "PARTIAL"
    manifest["status"] = final_status
    manifest["ended_at_unix"] = time.time()
    _atomic_json(output / "queue_manifest.json", manifest)
    _atomic_json(
        state_root / "supervisor_state.json",
        {
            "schema_version": "membind.v6.1.full5-state.v1",
            "status": final_status,
            "profile_id": PROFILE_ID,
            "campaign_id": args.campaign_id,
            "supervisor_pid": os.getpid(),
            "last_progress_at_unix": time.time(),
        },
    )
    return 0 if final_status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
