#!/usr/bin/env python3
"""Persist the baseline -> autoresearch -> full-five local campaign chain."""

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
EXPERIMENT_ROOT = Path(
    "/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab"
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


def _baseline_methods(ledger: Path) -> set[str]:
    if not ledger.is_file():
        return set()
    completed = set()
    v60_timeout_attempts: set[str] = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (
            row.get("event") == "ATTEMPT_FAILURE"
            and row.get("status") == "FAILED"
            and row.get("episode_count") == 30
            and row.get("method") == "V6_0"
            and str(row.get("error_type", "")).endswith("APITimeoutError")
            and isinstance(row.get("attempt_id"), str)
        ):
            v60_timeout_attempts.add(str(row["attempt_id"]))
        seal = row.get("construction_seal")
        timing_invalid = bool(
            seal
            and (Path(str(seal)).parent.parent / "timing_invalidation.json").is_file()
        )
        if (
            row.get("event") == "ATTEMPT_COMPLETE"
            and row.get("status") == "PASS"
            and row.get("episode_count") == 30
            and row.get("method") in {"B0", "V6_0"}
            and not timing_invalid
        ):
            completed.add(str(row["method"]))
    # Three independent fresh-namespace timeouts establish a conservative,
    # right-censored lower bound.  This permits research to continue without
    # relabelling a failed V6.0 attempt as a successful makespan.
    if len(v60_timeout_attempts) >= 3:
        completed.add("V6_0")
    return completed


def _tmux_alive(session: str) -> bool:
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", session],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _approved_policy_file(root: Path) -> Path | None:
    candidates = [root / "autoresearch/selected_policy.json"]
    candidates.extend(sorted(root.glob("autoresearch_retry*/selected_policy.json")))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        gate = payload.get("promotion_gate")
        if (
            payload.get("status") == "SELECTED"
            and isinstance(gate, dict)
            and gate.get("complete_evidence") is True
            and gate.get("improves_both_baselines") is True
        ):
            return path
    return None


def _next_autoresearch_campaign(root: Path) -> int:
    if not (root / "autoresearch").exists():
        return 0
    observed = [0]
    for path in root.glob("autoresearch_retry*"):
        suffix = path.name.removeprefix("autoresearch_retry")
        if suffix.isdigit():
            observed.append(int(suffix))
    return max(observed) + 1


def _run_child(
    command: list[str],
    *,
    log: Path,
    stage: str,
    state_path: Path,
    heartbeat_seconds: float,
) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log.open("a", encoding="utf-8") as stream:
        child = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
        while child.poll() is None:
            _atomic_json(
                state_path,
                {
                    "schema_version": "membind.v6.1.pipeline-state.v1",
                    "status": "RUNNING",
                    "stage": stage,
                    "pipeline_pid": os.getpid(),
                    "child_pid": child.pid,
                    "stage_started_at_unix": started,
                    "last_heartbeat_at_unix": time.time(),
                    "log": str(log),
                },
            )
            time.sleep(heartbeat_seconds)
    return int(child.returncode or 0)


def _preflight(log: Path, state_path: Path, heartbeat_seconds: float) -> int:
    return _run_child(
        [sys.executable, str(ROOT / "scripts/local_runtime/preflight.py"), "--timeout", "45"],
        log=log,
        stage="PREFLIGHT",
        state_path=state_path,
        heartbeat_seconds=heartbeat_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-root", type=Path, default=EXPERIMENT_ROOT)
    parser.add_argument("--baseline-session", default="membind-v61-prefix30-baselines")
    parser.add_argument("--autoresearch-id", default="v61-ar-20260827-01")
    parser.add_argument("--full5-id", default="v61-full5-20260827-01")
    parser.add_argument("--heartbeat-seconds", type=float, default=30)
    parser.add_argument(
        "--max-autoresearch-campaigns",
        type=int,
        default=0,
        help="maximum independent search campaigns; 0 keeps searching until the gate passes",
    )
    args = parser.parse_args()
    if os.environ.get("MEMBIND_PROFILE_ID") != PROFILE_ID:
        parser.error("activate the local-qwen3-14b-awq-v1 runtime first")
    root = args.experiment_root.resolve()
    state_path = root / "state/pipeline_state.json"
    ledger = root / "state/pipeline_ledger.jsonl"
    prefix_root = root / "prefix30"
    baseline_ledger = prefix_root / "campaign_ledger.jsonl"
    _append(
        ledger,
        {
            "event": "PIPELINE_START",
            "profile_id": PROFILE_ID,
            "pipeline_pid": os.getpid(),
            "at_unix": time.time(),
        },
    )
    required = {"B0", "V6_0"}
    while True:
        complete = _baseline_methods(baseline_ledger)
        if complete == required:
            break
        missing = sorted(required - complete)
        _atomic_json(
            state_path,
            {
                "schema_version": "membind.v6.1.pipeline-state.v1",
                "status": "WAITING_FOR_BASELINES",
                "pipeline_pid": os.getpid(),
                "complete_methods": sorted(complete),
                "missing_methods": missing,
                "baseline_tmux_alive": _tmux_alive(args.baseline_session),
                "last_heartbeat_at_unix": time.time(),
            },
        )
        if _tmux_alive(args.baseline_session):
            time.sleep(args.heartbeat_seconds)
            continue
        method = missing[0]
        recovery_id = f"prefix30-recovery-{method.casefold().replace('_', '-')}-{int(time.time())}"
        if _preflight(
            root / f"logs/{recovery_id}-preflight.log",
            state_path,
            args.heartbeat_seconds,
        ) != 0:
            time.sleep(args.heartbeat_seconds)
            continue
        return_code = _run_child(
            [
                sys.executable,
                str(ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_v61_local.py"),
                "--output-root",
                str(prefix_root),
                "--run-id",
                recovery_id,
                "--contexts",
                "0",
                "--session-limit",
                "30",
                "--methods",
                method,
            ],
            log=root / f"logs/{recovery_id}.log",
            stage=f"RECOVER_{method}",
            state_path=state_path,
            heartbeat_seconds=args.heartbeat_seconds,
        )
        _append(
            ledger,
            {
                "event": "BASELINE_RECOVERY_END",
                "method": method,
                "run_id": recovery_id,
                "return_code": return_code,
                "at_unix": time.time(),
            },
        )

    _append(ledger, {"event": "BASELINES_READY", "at_unix": time.time()})
    # A failed/no-improvement campaign is evidence for the next search, not a
    # reason to authorize full5 or terminate the supervisor.  Each retry gets a
    # fresh output namespace so its attempts and artifacts remain auditable.
    campaign_number = _next_autoresearch_campaign(root)
    policy_file = _approved_policy_file(root)
    while policy_file is None:
        autoresearch_root = root / ("autoresearch" if campaign_number == 0 else f"autoresearch_retry{campaign_number}")
        autoresearch_id = args.autoresearch_id if campaign_number == 0 else f"{args.autoresearch_id}-retry{campaign_number}"
        stage = "AUTORESEARCH" if campaign_number == 0 else "AUTORESEARCH_RETRY"
        _append(ledger, {"event": "AUTORESEARCH_START", "campaign": campaign_number,
                         "campaign_id": autoresearch_id, "output_root": str(autoresearch_root),
                         "at_unix": time.time()})
        return_code = _run_child(
            [
                sys.executable,
                str(ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_v61_autoresearch_local.py"),
                "--output-root", str(autoresearch_root), "--baseline-root", str(prefix_root),
                "--campaign-id", autoresearch_id,
            ],
            log=root / f"logs/{autoresearch_id}.pipeline.log",
            stage=stage, state_path=state_path, heartbeat_seconds=args.heartbeat_seconds,
        )
        policy_file = autoresearch_root / "selected_policy.json"
        approved = False
        gate = None
        if return_code == 0 and policy_file.is_file():
            try:
                payload = json.loads(policy_file.read_text(encoding="utf-8"))
                gate = payload.get("promotion_gate")
                approved = (
                    payload.get("status") == "SELECTED"
                    and isinstance(gate, dict)
                    and gate.get("complete_evidence") is True
                    and gate.get("improves_both_baselines") is True
                )
            except (OSError, UnicodeError, json.JSONDecodeError):
                approved = False
        _append(ledger, {"event": "AUTORESEARCH_END", "campaign": campaign_number,
                         "campaign_id": autoresearch_id, "return_code": return_code,
                         "approved": approved, "promotion_gate": gate, "at_unix": time.time()})
        if approved:
            policy_file = autoresearch_root / "selected_policy.json"
            break
        campaign_number += 1
        _atomic_json(
            state_path,
            {
                "schema_version": "membind.v6.1.pipeline-state.v1",
                "status": "AUTORESEARCH_NEEDS_MORE_SEARCH",
                "pipeline_pid": os.getpid(),
                "campaign": campaign_number,
                "previous_return_code": return_code,
                "previous_output_root": str(autoresearch_root),
                "last_heartbeat_at_unix": time.time(),
            },
        )
        if args.max_autoresearch_campaigns and campaign_number >= args.max_autoresearch_campaigns:
            return 2
        time.sleep(args.heartbeat_seconds)

    b1_smoke_id = f"{args.autoresearch_id}-b1-smoke-n2"
    b1_preflight = _preflight(
        root / f"logs/{b1_smoke_id}-preflight.log",
        state_path,
        args.heartbeat_seconds,
    )
    if b1_preflight != 0:
        _atomic_json(
            state_path,
            {
                "schema_version": "membind.v6.1.pipeline-state.v1",
                "status": "B1_PREFLIGHT_RETRY_PENDING",
                "pipeline_pid": os.getpid(),
                "return_code": b1_preflight,
                "last_heartbeat_at_unix": time.time(),
            },
        )
        time.sleep(args.heartbeat_seconds)
        b1_preflight = _preflight(
            root / f"logs/{b1_smoke_id}-preflight-retry.log",
            state_path,
            args.heartbeat_seconds,
        )
    if b1_preflight != 0:
        return 2
    b1_status = _run_child(
        [
            sys.executable,
            str(ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_v61_local.py"),
            "--output-root",
            str(root / "smoke"),
            "--run-id",
            b1_smoke_id,
            "--contexts",
            "0",
            "--session-limit",
            "2",
            "--methods",
            "B1",
        ],
        log=root / f"logs/{b1_smoke_id}.log",
        stage="B1_SMOKE",
        state_path=state_path,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    _append(
        ledger,
        {"event": "B1_SMOKE_END", "return_code": b1_status, "at_unix": time.time()},
    )
    if b1_status != 0:
        _atomic_json(
            state_path,
            {
                "schema_version": "membind.v6.1.pipeline-state.v1",
                "status": "B1_SMOKE_NEEDS_INTERVENTION",
                "pipeline_pid": os.getpid(),
                "return_code": b1_status,
                "last_heartbeat_at_unix": time.time(),
            },
        )
        return 2

    _append(
        ledger,
        {"event": "FULL5_SUPERVISOR_START", "policy_file": str(policy_file), "at_unix": time.time()},
    )
    return _run_child(
        [
            sys.executable,
            str(ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_v61_full5_supervisor.py"),
            "--output-root",
            str(root / "full5"),
            "--campaign-id",
            args.full5_id,
            "--policy-file",
            str(policy_file),
            "--heartbeat-seconds",
            str(args.heartbeat_seconds),
        ],
        log=root / f"logs/{args.full5_id}.pipeline.log",
        stage="FULL5_SUPERVISOR",
        state_path=state_path,
        heartbeat_seconds=args.heartbeat_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
