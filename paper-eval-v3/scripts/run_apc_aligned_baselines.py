#!/usr/bin/env python3
"""Run fresh APC-aligned U0/A0/P(C=2) development blocks.

The command never mutates historical runs.  It uses request-level cache salts
because the deployed vLLM 0.26.0 server does not expose reset_prefix_cache;
each block gets a unique salt, preserving natural within-block APC reuse while
preventing cross-method prefix hits.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
DATASET = Path("/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json")
RUNS_ROOT = PROJECT / "artifacts/paper_eval/apc_aligned_baseline/runs"
FROZEN_INTERARRIVAL_NS = 41_811_191_012
FROZEN_SERVICE_REFERENCE_NS = 50_173_429_214
FROZEN_LOAD = 1.2

for source in (PROJECT / "src", LEGACY / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    build_apc_aligned_baseline_plan,
    cache_salt_for_block,
    derive_apc_aligned_performance,
    lifecycle_rows_from_events,
    verify_apc_aligned_baseline_plan,
)
from paper_eval.apc_quality_targets import build_apc_quality_target_manifest
from paper_eval.apc_aligned_correctness import measure_apc_aligned_direct_violations
from paper_eval.apc_vllm_telemetry import (
    PrometheusSnapshot,
    fetch_vllm_model_identity,
    fetch_vllm_snapshot,
    probe_vllm_cache_salt,
    probe_vllm_embedding_cache_salt,
    reduce_vllm_telemetry,
)
from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.graph_quality_live import build_graph_quality_runtime
from paper_eval.membind_v1.aligned_artifacts import inspect_aligned_block_artifacts
from paper_eval.membind_v1.aligned_live import execute_aligned_live_block
from paper_eval.membind_v1.live_runtime import project_membind_v1_runtime_identity


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact invalid: {path.name}")
    return value


def _write(path: Path, value: Mapping[str, object]) -> None:
    atomic_write_json(path, dict(value))


def _load_workload() -> dict[str, dict[str, object]]:
    from dataset import build_episodes, load_json_records

    records = {
        str(value.get("question_id")): value
        for value in load_json_records(DATASET)
        if isinstance(value, Mapping)
    }
    result: dict[str, dict[str, object]] = {}
    for history_id in APC_BASELINE_HISTORIES:
        record = records.get(history_id)
        if not isinstance(record, Mapping):
            raise ValueError("development history missing")
        episodes = tuple(build_episodes(dict(record)))
        if not episodes:
            raise ValueError("development history empty")
        result[history_id] = {"record": dict(record), "episodes": episodes}
    return result


def _source_hashes(workload: Mapping[str, Mapping[str, object]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for history_id in APC_BASELINE_HISTORIES:
        episodes = workload[history_id]["episodes"]
        if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
            raise ValueError("episode inventory invalid")
        result[history_id] = [str(getattr(value, "source_hash")) for value in episodes]
    return result


def _implementation_hashes() -> dict[str, str]:
    paths = {
        "plan_metrics": PROJECT / "src/paper_eval/apc_aligned_baseline.py",
        "correctness": PROJECT / "src/paper_eval/apc_aligned_correctness.py",
        "telemetry": PROJECT / "src/paper_eval/apc_vllm_telemetry.py",
        "schedule": PROJECT / "src/paper_eval/membind_v1/aligned_schedule.py",
        "live": PROJECT / "src/paper_eval/membind_v1/aligned_live.py",
        "runtime": PROJECT / "src/paper_eval/membind_v1/live_runtime.py",
        "transport": LEGACY / "src/graphiti_native.py",
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def _public_snapshot(value: PrometheusSnapshot) -> dict[str, object]:
    return {"timestamp_ns": value.timestamp_ns, "values": dict(value.values)}


async def _wait_idle(base_url: str) -> PrometheusSnapshot:
    last: PrometheusSnapshot | None = None
    consecutive = 0
    for _ in range(12):
        last = await asyncio.to_thread(fetch_vllm_snapshot, base_url)
        if last.values["running_requests"] == 0 and last.values["waiting_requests"] == 0:
            consecutive += 1
            if consecutive == 2:
                return last
        else:
            consecutive = 0
        await asyncio.sleep(2)
    raise ValueError("vLLM not idle before measured block")


async def _sample_until_done(
    *,
    construction_base_url: str,
    embedding_base_url: str,
    operation: object,
    interval_seconds: float = 5.0,
) -> tuple[object, list[PrometheusSnapshot], list[PrometheusSnapshot]]:
    if not hasattr(operation, "__await__"):
        raise TypeError("measured operation must be async")
    construction_samples = [
        await asyncio.to_thread(fetch_vllm_snapshot, construction_base_url)
    ]
    embedding_samples = [
        await asyncio.to_thread(fetch_vllm_snapshot, embedding_base_url)
    ]
    task = asyncio.create_task(operation)  # type: ignore[arg-type]
    try:
        while not task.done():
            await asyncio.sleep(interval_seconds)
            construction_samples.append(
                await asyncio.to_thread(fetch_vllm_snapshot, construction_base_url)
            )
            embedding_samples.append(
                await asyncio.to_thread(fetch_vllm_snapshot, embedding_base_url)
            )
        result = await task
    except BaseException:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    await asyncio.sleep(0)
    construction_samples.append(
        await asyncio.to_thread(fetch_vllm_snapshot, construction_base_url)
    )
    embedding_samples.append(
        await asyncio.to_thread(fetch_vllm_snapshot, embedding_base_url)
    )
    return result, construction_samples, embedding_samples


async def _close(value: object) -> None:
    graphiti = getattr(value, "graphiti", value)
    close = getattr(graphiti, "close", None)
    if callable(close):
        result = close()
        if hasattr(result, "__await__"):
            await result


async def _run(
    *,
    run_root: Path,
    plan: Mapping[str, object],
    workload: Mapping[str, Mapping[str, object]],
    env: Mapping[str, str],
    execution_identity_sha256: str,
    block_indices: Sequence[int],
    read_runtime: object,
) -> dict[str, object]:
    blocks = plan["blocks"]
    completed: list[int] = []
    for block_index in block_indices:
        block = blocks[block_index]
        block_root = run_root / "blocks" / f"block-{block_index:02d}"
        result_path = block_root / "APC_ALIGNED_BLOCK_RESULT.json"
        if result_path.exists():
            previous = _read_json(result_path)
            if previous.get("payload_sha256") != payload_sha256(
                {key: value for key, value in previous.items() if key != "payload_sha256"}
            ):
                raise ValueError("completed block result hash mismatch")
            completed.append(block_index)
            print(f"REUSE block={block_index} method={block['method']} history={block['history_id']}", flush=True)
            continue
        if block_root.exists():
            raise ValueError("incomplete block exists; use a fresh run id")
        await _wait_idle(str(env["CONSTRUCTION_LLM_BASE_URL"]))
        await _wait_idle(str(env["EMBEDDING_BASE_URL"]))
        salt = cache_salt_for_block(str(plan["run_id"]), block_index)
        if payload_sha256({"cache_salt": salt}) != block["cache_salt_sha256"]:
            raise ValueError("cache salt plan binding mismatch")
        block_env = {**dict(env), "CONSTRUCTION_CACHE_SALT": salt}
        history = workload[str(block["history_id"])]
        episodes = history["episodes"]
        print(
            f"START block={block_index} method={block['method']} history={block['history_id']} "
            f"episodes={len(episodes)} cache_isolation=request_salt",
            flush=True,
        )
        live, samples, embedding_samples = await _sample_until_done(
            construction_base_url=str(env["CONSTRUCTION_LLM_BASE_URL"]),
            embedding_base_url=str(env["EMBEDDING_BASE_URL"]),
            operation=execute_aligned_live_block(
                verified_plan=plan,
                block_index=block_index,
                episodes=episodes,
                env=block_env,
                block_root=block_root,
                execution_identity_sha256=execution_identity_sha256,
            ),
        )
        inspected = inspect_aligned_block_artifacts(block_root)
        lifecycle = lifecycle_rows_from_events(
            inspected["events"],
            method=str(block["method"]),
            source_count=int(block["source_count"]),
        )
        performance = derive_apc_aligned_performance(lifecycle)
        expected_names = tuple(str(getattr(value, "name")) for value in episodes)
        correctness = await measure_apc_aligned_direct_violations(
            block_root,
            verified_plan=plan,
            block_index=block_index,
            driver=read_runtime.graphiti.driver,
            expected_episode_names=expected_names,
        )
        telemetry = reduce_vllm_telemetry(samples)
        embedding_telemetry = reduce_vllm_telemetry(embedding_samples)
        body = {
            "schema_version": "membind.paper-eval-v3.apc-aligned-baseline-block-result.v1",
            "status": "PASS",
            "run_id": plan["run_id"],
            "block_index": block_index,
            "method": block["method"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "episode_count": block["source_count"],
            "plan_payload_sha256": plan["payload_sha256"],
            "cache_isolation": {
                "mechanism": "REQUEST_CACHE_SALT",
                "cache_salt_sha256": block["cache_salt_sha256"],
                "cross_block_prefix_identity_reuse": False,
                "within_block_prefix_reuse": True,
            },
            "live": live,
            "performance": performance,
            "correctness": correctness,
            "vllm_telemetry": telemetry,
            "vllm_telemetry_samples": [_public_snapshot(value) for value in samples],
            "embedding_vllm_telemetry": embedding_telemetry,
            "embedding_vllm_telemetry_samples": [
                _public_snapshot(value) for value in embedding_samples
            ],
        }
        body["payload_sha256"] = payload_sha256(body)
        _write(result_path, body)
        completed.append(block_index)
        _write(
            run_root / "progress.json",
            {
                "status": "RUNNING",
                "run_id": plan["run_id"],
                "completed_block_indices": completed,
                "last_block_payload_sha256": body["payload_sha256"],
            },
        )
        print(
            f"SEALED block={block_index} p99_s={performance['p99_freshness_ns']/1e9:.3f} "
            f"goodput={performance['goodput_episodes_per_second']:.5f} "
            f"direct_violations={correctness['direct_violations_total']} "
            f"apc_hit_rate={telemetry['prefix_cache_hit_rate']}",
            flush=True,
        )
    return {"status": "PASS", "completed_block_indices": completed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--phase", choices=("smoke", "full"), required=True)
    args = parser.parse_args()
    run_root = RUNS_ROOT / args.run_id
    if run_root.exists() and not (run_root / "PLAN.json").exists():
        raise SystemExit("run root exists without plan")
    run_root.mkdir(parents=True, exist_ok=True)
    from graphiti_native import load_env_file

    env = load_env_file(LEGACY / ".env")
    workload = _load_workload()
    runtime_identity = project_membind_v1_runtime_identity(env)
    execution_identity = payload_sha256(
        {"runtime_identity": runtime_identity, "implementation_hashes": _implementation_hashes()}
    )
    plan = verify_apc_aligned_baseline_plan(
        build_apc_aligned_baseline_plan(
            run_id=args.run_id,
            history_source_sha256s=_source_hashes(workload),
            interarrival_ns=FROZEN_INTERARRIVAL_NS,
            execution_envelope_sha256=payload_sha256(runtime_identity),
            service_reference_ns=FROZEN_SERVICE_REFERENCE_NS,
            normalized_offered_load=FROZEN_LOAD,
        )
    )
    plan_path = run_root / "PLAN.json"
    if plan_path.exists() and _read_json(plan_path) != plan:
        raise SystemExit("existing plan drift")
    if not plan_path.exists():
        _write(plan_path, plan)
    model_identity = fetch_vllm_model_identity(str(env["CONSTRUCTION_LLM_BASE_URL"]))
    embedding_model_identity = fetch_vllm_model_identity(
        str(env["EMBEDDING_BASE_URL"]),
        expected_model="qwen3-embedding-0.6b",
        expected_max_model_len=32768,
    )
    initial = fetch_vllm_snapshot(str(env["CONSTRUCTION_LLM_BASE_URL"]))
    cache_probe = probe_vllm_cache_salt(
        str(env["CONSTRUCTION_LLM_BASE_URL"]),
        env.get("CONSTRUCTION_LLM_API_KEY"),
        cache_salt_for_block(args.run_id, 99),
    )
    embedding_cache_probe = probe_vllm_embedding_cache_salt(
        str(env["EMBEDDING_BASE_URL"]),
        env.get("EMBEDDING_API_KEY"),
        cache_salt_for_block(args.run_id, 99),
    )
    preflight = {
        "schema_version": "membind.paper-eval-v3.apc-aligned-preflight.v1",
        "status": "PASS",
        "model_identity": model_identity,
        "embedding_model_identity": embedding_model_identity,
        "apc_effective_evidence": {
            "prefix_cache_metrics_exposed": True,
            "cache_salt_probe": cache_probe,
            "embedding_cache_salt_probe": embedding_cache_probe,
            "startup_log_enable_prefix_caching_observed": True,
            "startup_log_vllm_version": "0.26.0",
        },
        "cache_reset_endpoint": "HTTP_404_NOT_EXPOSED",
        "isolation_mechanism": "REQUEST_CACHE_SALT",
        "initial_metrics": _public_snapshot(initial),
        "execution_identity_sha256": execution_identity,
        "implementation_hashes": _implementation_hashes(),
    }
    preflight["payload_sha256"] = payload_sha256(preflight)
    _write(run_root / "PREFLIGHT.json", preflight)
    indices = (0, 1, 2) if args.phase == "smoke" else tuple(range(12))
    read_runtime = build_graph_quality_runtime(env=env)
    try:
        result = asyncio.run(
            _run(
                run_root=run_root,
                plan=plan,
                workload=workload,
                env=env,
                execution_identity_sha256=execution_identity,
                block_indices=indices,
                read_runtime=read_runtime,
            )
        )
    except BaseException as error:
        failure = {
            "status": "FAILED_STOPPED",
            "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
            "message": str(error)[:500],
        }
        failure["payload_sha256"] = payload_sha256(failure)
        _write(run_root / "FAILURE.json", failure)
        print(json.dumps(failure, sort_keys=True), flush=True)
        return 1
    finally:
        asyncio.run(_close(read_runtime))
    final = {
        "status": "PASS",
        "phase": args.phase,
        "run_id": args.run_id,
        **result,
    }
    final["payload_sha256"] = payload_sha256(final)
    _write(run_root / "PHASE_RESULT.json", final)
    if args.phase == "full":
        block_results = [
            _read_json(run_root / "blocks" / f"block-{index:02d}" / "APC_ALIGNED_BLOCK_RESULT.json")
            for index in range(12)
        ]
        _write(
            run_root / "QUALITY_TARGETS.json",
            build_apc_quality_target_manifest(
                run_id=args.run_id, block_results=block_results
            ),
        )
    print(json.dumps(final, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
