#!/usr/bin/env python3
"""Adaptive, observable autoresearch controller for the local V6.1 policy.

The controller keeps an append-only loop: hypothesis, isolated run, live
observation, analysis, keep/reject, and policy mutation.  Provisional results
never authorize the full five-context campaign.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
PROFILE_ID = "local-qwen3-14b-awq-v1"
DEFAULT_ROOT = Path(
    "/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab/autoresearch"
)
DEFAULT_BASELINE_ROOT = Path(
    "/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab/prefix30"
)
POLICY_LIMIT = 7
SEARCH_LOOKAHEAD_LIMIT = 8


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


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _policy_id(policy: Mapping[str, int]) -> str:
    return (
        f"w{int(policy['lookahead'])}-f{int(policy['future_cap'])}-"
        f"q{int(policy['native_future_quota'])}"
    )


def _normalise_policy(policy: Mapping[str, Any]) -> dict[str, int]:
    values = {
        "lookahead": int(policy["lookahead"]),
        "future_cap": int(policy["future_cap"]),
        "native_future_quota": int(policy["native_future_quota"]),
    }
    if not 1 <= values["lookahead"] <= SEARCH_LOOKAHEAD_LIMIT:
        raise ValueError(f"autoresearch lookahead must be in [1, {SEARCH_LOOKAHEAD_LIMIT}]")
    if not 0 <= values["native_future_quota"] <= values["future_cap"] <= POLICY_LIMIT:
        raise ValueError("policy bounds are invalid")
    return values


def _load_attempt(ledger: Path, run_id: str) -> dict[str, Any]:
    if not ledger.is_file():
        raise RuntimeError(f"campaign ledger is missing for {run_id}")
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    matches = [row for row in rows if row.get("run_id") == run_id]
    terminal = [
        row
        for row in matches
        if row.get("event") in {"ATTEMPT_COMPLETE", "ATTEMPT_FAILURE"}
    ]
    if not terminal:
        raise RuntimeError(f"no terminal ledger row for {run_id}")
    return terminal[-1]


def _status_from_proof(refinement: Mapping[str, Any]) -> bool:
    if refinement.get("refinement_status") != "PASS":
        return False
    proof = refinement.get("proof", {})
    if not isinstance(proof, Mapping):
        return False
    required = ("request", "replay", "provider", "shared_arbiter")
    return all(
        isinstance(proof.get(name), Mapping) and proof[name].get("status") == "PASS"
        for name in required
    )


def _failure_class(status: str, error_type: Any, error: Any) -> str:
    text = f"{error_type or ''} {error or ''}".casefold()
    if status == "TIMEOUT":
        return "CONTROLLER_TIMEOUT"
    if "cancel" in text:
        return "CANCELLATION"
    if "timeout" in text:
        return "PROVIDER_TIMEOUT"
    if "context" in text and "length" in text:
        return "CONTEXT_LIMIT"
    if "replay" in text or "frontier" in text or "evidence" in text:
        return "CORRECTNESS_EVIDENCE"
    if "preflight" in text or "service" in text:
        return "PREFLIGHT_OR_SERVICE"
    return "CANDIDATE_FAILURE"


def _read_result(terminal: Mapping[str, Any]) -> dict[str, Any]:
    """Read sealed, attempt-local artifacts and derive correctness metrics."""

    if terminal.get("status") != "PASS":
        error_type = terminal.get("error_type")
        return {
            "status": "FAIL", "correctness": "FAIL",
            "failure_class": _failure_class("FAIL", error_type, terminal.get("error")),
            "error_type": error_type, "error": terminal.get("error"),
        }
    seal = terminal.get("construction_seal")
    if not seal:
        return {"status": "INVALID", "correctness": "FAIL", "failure_class": "MISSING_SEAL"}
    block = Path(str(seal)).parent
    try:
        metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
        inventory = json.loads((block / "work_inventory.json").read_text(encoding="utf-8"))
        refinement = json.loads(
            (block / "refinement_validation.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "status": "INVALID", "correctness": "FAIL",
            "failure_class": "SEALED_ARTIFACT_UNREADABLE", "error": str(exc)[:1000],
        }
    provider_path = block / "provider_calls.jsonl"
    provider_rows = []
    if provider_path.is_file():
        provider_rows = [
            json.loads(line)
            for line in provider_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]
    native_real = [
        float(row["duration_ns"]) / 1_000_000_000
        for row in provider_rows
        if row.get("region") == "NATIVE" and row.get("replay") is False
        and row.get("status") == "success" and isinstance(row.get("duration_ns"), int)
    ]
    provider_real = [row for row in provider_rows if row.get("replay") is False]
    expected = int(inventory.get("expected_episode_count") or terminal.get("episode_count") or 0)
    completed = int(inventory.get("completed_count") or 0)
    frontier = -1
    raw_events = block / "raw_events.jsonl"
    if raw_events.is_file():
        for line in raw_events.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "PUBLICATION_DURABLE":
                frontier = max(frontier, int(event.get("source_sequence", -1)))
    correctness = _status_from_proof(refinement) and completed == expected and (
        expected == 0 or frontier in {-1, expected - 1}
    )
    return {
        "status": "PASS" if correctness else "INVALID",
        "correctness": "PASS" if correctness else "FAIL",
        "failure_class": None if correctness else "EVIDENCE_OR_FRONTIER_INVALID",
        "block_root": str(block), "makespan_s": float(metrics["t_build_ns"]) / 1_000_000_000,
        "expected_episode_count": expected, "completed_count": completed,
        "durable_frontier": frontier,
        "native_real_provider_p95_s": _percentile(native_real, 0.95),
        "native_real_provider_mean_s": (sum(native_real) / len(native_real)) if native_real else None,
        "real_provider_calls": len(provider_real),
        "transport_attempts": inventory.get("transport_attempts"),
        "embedding_items": inventory.get("embedding_items"),
        "db_writes": inventory.get("db_writes"),
        "extra_work_ratio": float(len(provider_real)) / float(expected) if expected else None,
        "provider_proof": refinement.get("proof", {}).get("provider"),
        "replay_proof": refinement.get("proof", {}).get("replay"),
        "shared_arbiter_proof": refinement.get("proof", {}).get("shared_arbiter"),
    }


def _load_baselines(root: Path) -> dict[str, dict[str, Any]]:
    ledger = root / "campaign_ledger.jsonl"
    if not ledger.is_file():
        raise RuntimeError("prefix30 baseline ledger is missing")
    selected: dict[str, dict[str, Any]] = {}
    v60_timeouts: list[dict[str, Any]] = []
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        method = row.get("method")
        seal = row.get("construction_seal")
        if (
            row.get("event") == "ATTEMPT_FAILURE" and row.get("status") == "FAILED"
            and row.get("episode_count") == 30 and method == "V6_0"
            and str(row.get("error_type", "")).endswith("APITimeoutError")
            and isinstance(row.get("started_at_unix"), (int, float))
            and isinstance(row.get("ended_at_unix"), (int, float))
        ):
            elapsed = float(row["ended_at_unix"]) - float(row["started_at_unix"])
            if elapsed > 0:
                v60_timeouts.append({"attempt_id": row.get("attempt_id"), "elapsed_s": elapsed})
        timing_invalid = bool(
            seal and (Path(str(seal)).parent.parent / "timing_invalidation.json").is_file()
        )
        if (
            row.get("event") != "ATTEMPT_COMPLETE" or row.get("status") != "PASS"
            or row.get("episode_count") != 30 or method not in {"B0", "V6_0"}
            or timing_invalid
        ):
            continue
        block = Path(str(row["construction_seal"])).parent
        metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
        inventory = json.loads((block / "work_inventory.json").read_text(encoding="utf-8"))
        selected[str(method)] = {
            "method": method, "status": "PASS", "comparison_kind": "SEALED_MAKESPAN",
            "attempt_id": row.get("attempt_id"), "block_root": str(block),
            "makespan_s": float(metrics["t_build_ns"]) / 1_000_000_000,
            "transport_attempts": inventory.get("transport_attempts"),
            "embedding_items": inventory.get("embedding_items"),
            "evidence_limit": row.get("evidence_limit"),
        }
    if "V6_0" not in selected and len(v60_timeouts) >= 3:
        lower_bound = min(float(row["elapsed_s"]) for row in v60_timeouts)
        selected["V6_0"] = {
            "method": "V6_0", "status": "RIGHT_CENSORED_TIMEOUT",
            "comparison_kind": "THREE_ATTEMPT_MINIMUM_TIMEOUT_LOWER_BOUND",
            "attempt_id": None, "block_root": None, "makespan_s": lower_bound,
            "timeout_attempts": v60_timeouts, "timeout_attempt_count": len(v60_timeouts),
            "evidence_limit": "V6_0_DID_NOT_COMPLETE; MAKESPAN_IS_A_RIGHT_CENSORED_LOWER_BOUND",
        }
    missing = {"B0", "V6_0"} - set(selected)
    if missing:
        raise RuntimeError(f"prefix30 baselines are incomplete: {sorted(missing)}")
    return selected


def _run_preflight(output: Path, timeout: float) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/local_runtime/preflight.py"), "--timeout", str(timeout)],
        cwd=ROOT, check=False, capture_output=True, text=True,
        timeout=max(60.0, timeout * 3),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"local preflight failed: {completed.stderr[-500:]}")


def _find_attempt_root(runs_root: Path, context_index: int, run_id: str) -> Path | None:
    roots = sorted((runs_root / f"context-{context_index}" / "V6_1").glob("*"))
    matches: list[Path] = []
    for candidate in roots:
        path = candidate / "attempt.json"
        if not path.is_file():
            continue
        try:
            identity = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if identity.get("run_id") == run_id:
            matches.append(candidate)
    return matches[-1] if matches else None


def _read_live_events(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _observe_candidate(
    *, root: Path, context_index: int, run_id: str,
    process: subprocess.Popen[str] | None, started_at: float,
    previous: Mapping[str, Any] | None = None, expected_episodes: int | None = None,
) -> dict[str, Any]:
    """Return a read-only heartbeat snapshot for a running candidate."""

    attempt_root = _find_attempt_root(root / "runs", context_index, run_id)
    journal = None
    log_path = root / "logs" / f"{run_id}.log"
    if attempt_root is not None:
        # V6.1 writes the crash-tolerant journal as ``.block.v61_live_events``;
        # the legacy runner uses ``.block.live_raw_events``.  Observe either
        # format without relying on materialized end-of-run artifacts.
        journals = sorted(attempt_root.glob(".block*.jsonl"))
        journal = journals[-1] if journals else None
    events = _read_live_events(journal)
    durable = [
        int(row["source_sequence"])
        for row in events
        if row.get("event") == "PUBLICATION_DURABLE" and isinstance(row.get("source_sequence"), int)
    ]
    provider_events = [row for row in events if row.get("channel") == "provider"]
    admission_events = [row for row in events if row.get("channel") == "admission"]
    frontier_events = [row for row in events if row.get("channel") == "frontier"]
    try:
        stat = log_path.stat()
    except OSError:
        stat = None
    now = time.time()
    snapshot: dict[str, Any] = {
        "at_unix": now, "elapsed_s": max(0.0, now - started_at),
        "pid": process.pid if process is not None else None,
        "pid_alive": process is not None and process.poll() is None,
        "attempt_root": str(attempt_root) if attempt_root else None,
        "attempt_id": attempt_root.name if attempt_root else None,
        "journal_rows": len(events), "durable_publications": len(set(durable)),
        "durable_frontier": max(durable, default=-1),
        "provider_events": len(provider_events),
        "provider_completed": sum(1 for row in provider_events if row.get("event") == "V61_PROVIDER_CALL"),
        "admission_events": len(admission_events),
        "frontier_events": len(frontier_events),
        "last_event": events[-1].get("event") if events else None,
        "log": str(log_path), "log_bytes": int(stat.st_size) if stat else 0,
        "log_mtime_ns": int(stat.st_mtime_ns) if stat else None,
    }
    if expected_episodes and durable:
        completed = len(set(durable))
        rate = completed / max(0.001, now - started_at)
        snapshot["durable_rate_eps_s"] = rate
        snapshot["projected_makespan_s"] = expected_episodes / rate
    else:
        snapshot["durable_rate_eps_s"] = None
        snapshot["projected_makespan_s"] = None
    if previous:
        same = all(snapshot.get(key) == previous.get(key) for key in (
            "attempt_id", "journal_rows", "durable_frontier", "log_bytes"
        ))
        snapshot["no_progress_s"] = (
            float(previous.get("no_progress_s", 0.0))
            + max(0.0, now - float(previous.get("at_unix", now))) if same else 0.0
        )
    else:
        snapshot["no_progress_s"] = 0.0
    return snapshot


def _mutate_policy(policy: Mapping[str, int], reason: str | None = None) -> list[dict[str, int]]:
    """Generate a bounded neighborhood, prioritising the observed failure."""

    base = _normalise_policy(policy)
    candidates: list[dict[str, int]] = []

    def add(**changes: int) -> None:
        try:
            candidate = _normalise_policy({**base, **changes})
        except (KeyError, TypeError, ValueError):
            return
        if candidate != base and candidate not in candidates:
            candidates.append(candidate)

    if reason in {"CONTROLLER_TIMEOUT", "PROVIDER_TIMEOUT", "CONTEXT_LIMIT"}:
        add(future_cap=base["future_cap"] - 1)
        add(lookahead=base["lookahead"] - 1)
    elif reason == "CORRECTNESS_EVIDENCE":
        add(native_future_quota=base["native_future_quota"] - 1)
        add(future_cap=base["future_cap"] - 1)
    else:
        add(future_cap=base["future_cap"] + 1)
        add(lookahead=base["lookahead"] + 1)
        add(native_future_quota=base["native_future_quota"] + 1)
        add(future_cap=base["future_cap"] - 1)
        add(lookahead=base["lookahead"] - 1)
        add(native_future_quota=base["native_future_quota"] - 1)
    return candidates


def _improvement_gate(
    row: Mapping[str, Any], baselines: Mapping[str, Mapping[str, Any]], *,
    final_scale: int, margin: float = 0.0,
) -> tuple[bool, dict[str, Any]]:
    """Require complete evidence and a strict speedup over both baselines."""

    scale = int(row.get("scale") or 0)
    makespan = row.get("makespan_s")
    complete = (
        row.get("status") == "PASS" and row.get("correctness") == "PASS"
        and scale >= final_scale and makespan is not None
        and int(row.get("expected_episode_count") or 0) >= final_scale
        and int(row.get("durable_frontier", -1)) == final_scale - 1
    )
    speedups: dict[str, float | None] = {}
    for name in ("B0", "V6_0"):
        baseline = baselines.get(name, {}).get("makespan_s")
        speedups[name] = float(baseline) / float(makespan) if complete and baseline else None
    improved = complete and all(
        speedups[name] is not None
        and float(makespan) < float(baselines[name]["makespan_s"]) * (1.0 - margin)
        for name in ("B0", "V6_0")
    )
    return improved, {
        "complete_evidence": complete, "speedup_vs_b0": speedups["B0"],
        "speedup_vs_v6_0": speedups["V6_0"],
        "improves_both_baselines": improved, "margin": margin,
    }


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in rows if row.get("status") == "PASS" and row.get("correctness") == "PASS"]
    return sorted(valid, key=lambda row: (
        float(row.get("makespan_s") or float("inf")),
        float(row.get("native_real_provider_p95_s") or float("inf")),
        float(row.get("extra_work_ratio") or float("inf")),
        int(row.get("policy", {}).get("future_cap", POLICY_LIMIT)),
        int(row.get("policy", {}).get("lookahead", POLICY_LIMIT)),
    ))


def _run_candidate(
    *, root: Path, campaign_id: str, context_index: int, scale: int,
    policy: Mapping[str, int], timeout_seconds: float, heartbeat_seconds: float = 15.0,
    candidate_index: int = 0, event_path: Path | None = None,
) -> dict[str, Any]:
    policy = _normalise_policy(policy)
    policy_id = _policy_id(policy)
    run_id = f"{campaign_id}-n{scale}-{policy_id}-c{candidate_index:03d}"
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    _run_preflight(root / "preflight" / f"{run_id}.json", timeout=45)
    command = [
        sys.executable,
        str(ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_v61_local.py"),
        "--output-root", str(root / "runs"), "--run-id", run_id,
        "--contexts", str(context_index), "--session-limit", str(scale), "--methods", "V6_1",
        "--lookahead", str(policy["lookahead"]), "--future-cap", str(policy["future_cap"]),
        "--native-future-quota", str(policy["native_future_quota"]),
    ]
    started = time.time()
    log_path = logs / f"{run_id}.log"
    snapshots: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    process: subprocess.Popen[str] | None = None
    timed_out = False
    with log_path.open("x", encoding="utf-8") as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, text=True)
        while process.poll() is None:
            snapshot = _observe_candidate(
                root=root, context_index=context_index, run_id=run_id, process=process,
                started_at=started, previous=previous, expected_episodes=scale,
            )
            snapshots.append(snapshot)
            previous = snapshot
            if event_path:
                _append(event_path, {"event": "CANDIDATE_HEARTBEAT", "run_id": run_id,
                                      "policy_id": policy_id, "scale": scale, "snapshot": snapshot})
                _atomic_json(root / "live_state.json", {
                    "schema_version": "membind.v6.1.autoresearch-live.v1", "status": "RUNNING",
                    "run_id": run_id, "policy": policy, "scale": scale,
                    "child_pid": process.pid, "snapshot": snapshot,
                })
            if time.time() - started >= timeout_seconds:
                timed_out = True
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=30)
                break
            time.sleep(max(0.2, heartbeat_seconds))
    return_code = process.poll() if process is not None else None
    final_snapshot = _observe_candidate(
        root=root, context_index=context_index, run_id=run_id, process=process,
        started_at=started, previous=previous, expected_episodes=scale,
    )
    snapshots.append(final_snapshot)
    live = {"heartbeats": len(snapshots), "last": final_snapshot}
    common = {"run_id": run_id, "scale": scale, "policy": policy,
              "elapsed_s": time.time() - started, "return_code": return_code,
              "log": str(log_path), "live": live}
    if timed_out:
        return {**common, "status": "TIMEOUT", "correctness": "FAIL",
                "failure_class": "CONTROLLER_TIMEOUT"}
    terminal = None
    ledger = root / "runs/campaign_ledger.jsonl"
    for _ in range(20):
        try:
            terminal = _load_attempt(ledger, run_id)
            break
        except RuntimeError:
            time.sleep(0.5)
    if terminal is None:
        return {**common, "status": "FAIL", "correctness": "FAIL",
                "failure_class": _failure_class("FAIL", None, f"child return code {return_code}")}
    try:
        measured = _read_result(terminal)
    except BaseException as exc:
        return {
            **common,
            "status": "ANALYSIS_FAILURE",
            "correctness": "UNKNOWN",
            "failure_class": "ARTIFACT_ANALYSIS_FAILURE",
            "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "error": str(exc)[:1000],
            "terminal_event": terminal.get("event"),
            "terminal_status": terminal.get("status"),
            "attempt_id": terminal.get("attempt_id"),
        }
    return {**measured, **common}


def _initial_policies() -> list[dict[str, int]]:
    return [
        {"lookahead": 1, "future_cap": 0, "native_future_quota": 0},
        {"lookahead": 1, "future_cap": 1, "native_future_quota": 0},
        {"lookahead": 2, "future_cap": 1, "native_future_quota": 0},
        {"lookahead": 2, "future_cap": 2, "native_future_quota": 0},
    ]


def _next_candidates(
    seeds: list[Mapping[str, int]], tried: set[tuple[int, str]], scale: int, limit: int,
    *, reason: str | None = None,
) -> list[dict[str, int]]:
    output: list[dict[str, int]] = []
    for seed in seeds:
        for candidate in [_normalise_policy(seed), *_mutate_policy(seed, reason)]:
            key = (scale, _policy_id(candidate))
            if key in tried or candidate in output:
                continue
            output.append(candidate)
            if len(output) >= limit:
                return output
    return output


def _promote_candidates(
    ranked: list[Mapping[str, Any]],
    tried: set[tuple[int, str]],
    scale: int,
    limit: int = 4,
) -> list[dict[str, int]]:
    """Carry the two best policies, then add local neighbors of the winner."""

    output: list[dict[str, int]] = []

    def add(policy: Mapping[str, Any]) -> None:
        candidate = _normalise_policy(policy)
        if (scale, _policy_id(candidate)) not in tried and candidate not in output:
            output.append(candidate)

    for row in ranked[:2]:
        policy = row.get("policy")
        if isinstance(policy, Mapping):
            add(policy)
    if ranked:
        best_policy = ranked[0].get("policy")
        if isinstance(best_policy, Mapping):
            for candidate in _mutate_policy(best_policy):
                add(candidate)
                if len(output) >= limit:
                    break
    return output[:limit]


def _load_campaign_checkpoint(path: Path) -> tuple[set[tuple[int, str]], int]:
    """Recover completed policy keys and the next candidate sequence number."""

    tried: set[tuple[int, str]] = set()
    next_index = 0
    if not path.is_file():
        return tried, next_index
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") in {"CANDIDATE_START", "CANDIDATE_FINISH", "CANDIDATE_ANALYSIS"}:
            policy_id = row.get("policy_id")
            scale = row.get("scale")
            if isinstance(policy_id, str) and isinstance(scale, int):
                tried.add((scale, policy_id))
            if isinstance(row.get("candidate_index"), int):
                next_index = max(next_index, int(row["candidate_index"]) + 1)
    return tried, next_index


def _round_candidate_budget(
    *, scale: int, final_scale: int, completed: int, maximum: int
) -> int:
    remaining = max(0, maximum - completed)
    return remaining if scale == final_scale else min(4, remaining)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--baseline-root", type=Path, default=DEFAULT_BASELINE_ROOT)
    parser.add_argument("--context-index", type=int, default=0)
    parser.add_argument("--scales", type=int, nargs="+", default=[2, 4, 8, 16, 30])
    parser.add_argument("--attempt-timeout-seconds", type=float, default=21_600)
    parser.add_argument("--heartbeat-seconds", type=float, default=15)
    parser.add_argument("--max-candidates", type=int, default=24)
    parser.add_argument("--max-wall-time-hours", type=float, default=24)
    parser.add_argument("--improvement-margin", type=float, default=0.0)
    args = parser.parse_args()
    if os.environ.get("MEMBIND_PROFILE_ID") != PROFILE_ID:
        parser.error("activate the local-qwen3-14b-awq-v1 runtime first")
    if args.context_index not in range(5) or not args.scales or any(value <= 0 for value in args.scales):
        parser.error("context/scales are invalid")
    if args.max_candidates <= 0 or args.max_wall_time_hours <= 0 or args.attempt_timeout_seconds <= 0 or args.heartbeat_seconds <= 0:
        parser.error("search budget is invalid")
    if not 0 <= args.improvement_margin < 1:
        parser.error("improvement margin must be in [0, 1)")
    root = args.output_root.resolve()
    state = root / "autoresearch.jsonl"
    baselines = _load_baselines(args.baseline_root.resolve())
    final_scale = max(args.scales)
    started_campaign = time.time()
    manifest = {
        "schema_version": "membind.v6.1.autoresearch-plan.v2", "status": "RUNNING",
        "profile_id": PROFILE_ID, "campaign_id": args.campaign_id,
        "context_index": args.context_index, "scales": args.scales,
        "initial_policies": _initial_policies(),
        "loop": "hypothesis -> run -> observe -> analyze -> keep/reject -> mutate -> repeat",
        "prefix30_baselines": baselines, "max_candidates": args.max_candidates,
        "max_wall_time_hours": args.max_wall_time_hours, "started_at_unix": started_campaign,
    }
    _atomic_json(root / "autoresearch_manifest.json", manifest)
    _append(state, {"event": "CAMPAIGN_START", **manifest})
    all_results: list[dict[str, Any]] = []
    tried, candidate_index = _load_campaign_checkpoint(state)
    best: dict[str, Any] | None = None
    candidates = _initial_policies()
    selected: dict[str, Any] | None = None
    for round_index, scale in enumerate(args.scales):
        round_results: list[dict[str, Any]] = []
        round_best: dict[str, Any] | None = None
        queue = list(candidates)
        # Reserve enough budget to reach the full prefix.  The final scale has
        # the remaining budget so a good policy can face real challengers.
        round_budget = _round_candidate_budget(
            scale=scale,
            final_scale=final_scale,
            completed=len(all_results),
            maximum=args.max_candidates,
        )
        while queue and len(all_results) < args.max_candidates and len(round_results) < round_budget:
            if time.time() - started_campaign >= args.max_wall_time_hours * 3600:
                break
            policy = _normalise_policy(queue.pop(0))
            key = (scale, _policy_id(policy))
            if key in tried:
                continue
            tried.add(key)
            hypothesis = (
                f"{_policy_id(policy)} at n={scale}: bound future work and preserve exact "
                "replay while reducing native tail latency"
            )
            _append(state, {"event": "HYPOTHESIS_PROPOSED", "round": round_index,
                            "scale": scale, "policy": policy, "policy_id": _policy_id(policy),
                            "hypothesis": hypothesis})
            _append(state, {"event": "CANDIDATE_START", "round": round_index,
                            "scale": scale, "policy": policy, "policy_id": _policy_id(policy),
                            "candidate_index": candidate_index, "started_at_unix": time.time()})
            try:
                result = _run_candidate(
                    root=root, campaign_id=args.campaign_id, context_index=args.context_index,
                    scale=scale, policy=policy, timeout_seconds=args.attempt_timeout_seconds,
                    heartbeat_seconds=args.heartbeat_seconds, candidate_index=candidate_index,
                    event_path=state,
                )
            except BaseException as exc:
                result = {"status": "FAIL", "correctness": "FAIL",
                          "failure_class": _failure_class("FAIL", type(exc).__name__, str(exc)),
                          "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                          "error": str(exc)[:1000], "scale": scale, "policy": policy}
            candidate_index += 1
            if result.get("makespan_s"):
                makespan = float(result["makespan_s"])
                result["speedup_vs_b0"] = baselines["B0"]["makespan_s"] / makespan
                result["speedup_vs_v6_0"] = baselines["V6_0"]["makespan_s"] / makespan
                result["normalised_makespan_per_episode_s"] = makespan / max(1, scale)
            gate, gate_detail = _improvement_gate(
                {**result, "scale": scale}, baselines, final_scale=final_scale,
                margin=args.improvement_margin,
            )
            result.update({"promotion_gate": gate_detail, "policy_id": _policy_id(policy),
                          "round": round_index, "scale": scale, "policy": policy})
            round_results.append(result)
            all_results.append(result)
            _append(state, {"event": "CANDIDATE_FINISH", **result})
            _append(state, {"event": "CANDIDATE_ANALYSIS", "run_id": result.get("run_id"),
                            "policy_id": result["policy_id"], "scale": scale,
                            "status": result.get("status"), "correctness": result.get("correctness"),
                            "failure_class": result.get("failure_class"), "promotion_gate": gate_detail})
            if gate:
                selected = result
                _atomic_json(root / "best_policy.json", {
                    "schema_version": "membind.v6.1.best-policy.v1", "status": "PASS",
                    "policy": policy, "result": result, "updated_at_unix": time.time(),
                })
                _append(state, {"event": "POLICY_KEEP", "reason": "FINAL_IMPROVEMENT",
                                "policy": policy, "run_id": result.get("run_id"), **gate_detail})
                break
            if result.get("status") == "PASS" and result.get("correctness") == "PASS":
                if round_best is None or float(result.get("makespan_s", float("inf"))) < float(round_best.get("makespan_s", float("inf"))):
                    round_best = result
                    best = result
                    _atomic_json(root / "best_policy.json", {
                        "schema_version": "membind.v6.1.best-policy.v1", "status": "PROVISIONAL",
                        "policy": policy, "result": result, "updated_at_unix": time.time(),
                    })
                    _append(state, {"event": "POLICY_KEEP", "reason": "BEST_SO_FAR_PROVISIONAL",
                                    "policy": policy, "run_id": result.get("run_id"), "scale": scale})
                else:
                    _append(state, {"event": "POLICY_REJECT", "reason": "SLOWER_THAN_BEST",
                                    "policy": policy, "run_id": result.get("run_id"), "scale": scale})
            else:
                _append(state, {"event": "POLICY_REJECT", "reason": result.get("failure_class", "INVALID"),
                                "policy": policy, "run_id": result.get("run_id"), "scale": scale})
            if not queue and len(all_results) < args.max_candidates and len(round_results) < round_budget:
                seeds = [round_best["policy"]] if round_best else [policy]
                mutations = _next_candidates(seeds, tried, scale, 2,
                                              reason=result.get("failure_class"))
                if mutations:
                    _append(state, {"event": "POLICY_MUTATE", "scale": scale,
                                    "parent_policy_id": _policy_id(seeds[0]),
                                    "reason": result.get("failure_class"),
                                    "next_policy_ids": [_policy_id(row) for row in mutations]})
                queue.extend(mutations)
        if selected:
            break
        ranked = _rank(round_results)
        next_scale = args.scales[round_index + 1] if round_index + 1 < len(args.scales) else None
        if next_scale is None:
            candidates = []
        elif ranked:
            candidates = _promote_candidates(ranked, tried, next_scale, 4)
        else:
            seeds = [row.get("policy", _initial_policies()[0]) for row in round_results[-2:]] or _initial_policies()[:1]
            candidates = _next_candidates(seeds, tried, next_scale, 4,
                                          reason=(round_results[-1].get("failure_class") if round_results else None))
        _append(state, {"event": "SCALE_PROMOTE", "round": round_index, "scale": scale,
                        "next_scale": next_scale,
                        "next_policy_ids": [_policy_id(row) for row in candidates],
                        "best_policy_id": _policy_id(best["policy"]) if best else None})
        _append(state, {"event": "CAMPAIGN_CHECKPOINT", "round": round_index,
                        "scale": scale, "candidate_count": len(all_results),
                        "best_policy_id": _policy_id(best["policy"]) if best else None,
                        "status": "RUNNING"})
    if selected is None:
        _append(state, {"event": "CAMPAIGN_NEEDS_MORE_SEARCH",
                        "reason": "NO_FULL_SCALE_IMPROVEMENT", "candidate_count": len(all_results),
                        "best_policy_id": _policy_id(best["policy"]) if best else None})
        _atomic_json(root / "summary.json", {**manifest, "status": "NEEDS_MORE_SEARCH",
                                              "ended_at_unix": time.time(), "best": best,
                                              "results": all_results})
        _atomic_json(root / "live_state.json", {
            "schema_version": "membind.v6.1.autoresearch-live.v1",
            "status": "NEEDS_MORE_SEARCH", "candidate_count": len(all_results),
        })
        return 2
    selected_payload = {
        "schema_version": "membind.v6.1.selected-policy.v2", "status": "SELECTED",
        "profile_id": PROFILE_ID, "campaign_id": args.campaign_id,
        "policy": selected["policy"], "selection_result": selected,
        "selected_at_unix": time.time(), "promotion_gate": selected["promotion_gate"],
    }
    _atomic_json(root / "selected_policy.json", selected_payload)
    _append(state, {"event": "CAMPAIGN_PASS", "policy": selected["policy"],
                    "run_id": selected.get("run_id"), "promotion_gate": selected["promotion_gate"]})
    _atomic_json(root / "summary.json", {**manifest, "status": "PASS", "ended_at_unix": time.time(),
                                          "selected": selected_payload, "results": all_results})
    _atomic_json(root / "live_state.json", {
        "schema_version": "membind.v6.1.autoresearch-live.v1", "status": "PASS",
        "selected_policy": selected["policy"], "candidate_count": len(all_results),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
