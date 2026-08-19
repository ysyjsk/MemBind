#!/usr/bin/env python3
"""Run the isolated U0/P(C=2)/P(C=4) APC baseline lane.

The lane uses the two newly qualified vLLM endpoints, fresh namespaces and a
unique request cache salt per method/history block.  It never reads or mutates
the historical ``apc-baseline-*`` roots.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
DATASET = Path("/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json")
RUNS_ROOT = PROJECT / "artifacts/paper_eval/c246_baseline/runs"
for source in (PROJECT / "src", LEGACY / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.apc_aligned_correctness import measure_apc_aligned_direct_violations
from paper_eval.apc_aligned_baseline import (
    derive_apc_aligned_performance,
    lifecycle_rows_from_events,
)
from paper_eval.apc_vllm_telemetry import (
    PrometheusSnapshot,
    fetch_vllm_model_identity,
    fetch_vllm_snapshot,
    probe_vllm_cache_salt,
    probe_vllm_embedding_cache_salt,
    reduce_vllm_telemetry,
)
from paper_eval.c246_plan import (
    C246_HISTORIES,
    C8_EXTENSION_SCHEMA,
    build_c246_plan,
    build_c8_extension_plan,
    cache_salt_for_block,
    cache_salt_for_extension_block,
    verify_c246_plan,
    verify_c8_extension_plan,
)
from paper_eval.graph_quality_live import build_graph_quality_runtime
from paper_eval.membind_v1.aligned_artifacts import inspect_aligned_block_artifacts
from paper_eval.membind_v1.aligned_live import execute_aligned_live_block
from paper_eval.membind_v1.live_runtime import project_membind_v1_runtime_identity


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact invalid: {path}")
    return value


def _write(path: Path, value: Mapping[str, object]) -> None:
    atomic_write_json(path, dict(value))


def _load_env() -> dict[str, str]:
    sys.path.insert(0, str(LEGACY / "src"))
    from graphiti_native import load_env_file

    return dict(load_env_file(LEGACY / ".env"))


def _load_workload() -> dict[str, dict[str, object]]:
    from dataset import build_episodes, load_json_records

    records = {
        str(value.get("question_id")): value
        for value in load_json_records(DATASET)
        if isinstance(value, Mapping)
    }
    result: dict[str, dict[str, object]] = {}
    for history_id in C246_HISTORIES:
        record = records.get(history_id)
        if not isinstance(record, Mapping):
            raise ValueError(f"development history missing: {history_id}")
        episodes = tuple(build_episodes(dict(record)))
        if not episodes:
            raise ValueError(f"development history empty: {history_id}")
        result[history_id] = {"record": dict(record), "episodes": episodes}
    return result


def _source_hashes(workload: Mapping[str, Mapping[str, object]]) -> dict[str, list[str]]:
    return {
        history: [str(getattr(episode, "source_hash")) for episode in workload[history]["episodes"]]
        for history in C246_HISTORIES
    }


def _patch_runtime_endpoint_identities(construction_url: str, embedding_url: str) -> None:
    """Apply endpoint selection only inside this isolated runner process."""

    import paper_eval.graph_quality_live as quality
    import paper_eval.membind_v1.live_runtime as construction

    construction.CONSTRUCTION_BASE_URL = construction_url.rstrip("/")
    construction.EMBEDDING_BASE_URL = embedding_url.rstrip("/")
    quality.EMBEDDING_BASE_URL = embedding_url.rstrip("/")


def _implementation_hashes() -> dict[str, str]:
    paths = {
        "c246_plan": PROJECT / "src/paper_eval/c246_plan.py",
        "schedule": PROJECT / "src/paper_eval/membind_v1/aligned_schedule.py",
        "live": PROJECT / "src/paper_eval/membind_v1/aligned_live.py",
        "runtime": PROJECT / "src/paper_eval/membind_v1/live_runtime.py",
        "correctness": PROJECT / "src/paper_eval/apc_aligned_correctness.py",
        "telemetry": PROJECT / "src/paper_eval/apc_vllm_telemetry.py",
        "transport": LEGACY / "src/graphiti_native.py",
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def _public_snapshot(value: PrometheusSnapshot) -> dict[str, object]:
    return {"timestamp_ns": value.timestamp_ns, "values": dict(value.values)}


async def _wait_idle(base_url: str) -> PrometheusSnapshot:
    for _ in range(18):
        snapshot = await asyncio.to_thread(fetch_vllm_snapshot, base_url)
        if snapshot.values["running_requests"] == 0 and snapshot.values["waiting_requests"] == 0:
            await asyncio.sleep(1)
            second = await asyncio.to_thread(fetch_vllm_snapshot, base_url)
            if second.values["running_requests"] == 0 and second.values["waiting_requests"] == 0:
                return second
        await asyncio.sleep(2)
    raise RuntimeError("vLLM not idle before block")


async def _sample_until_done(
    *, construction_url: str, embedding_url: str, operation: object
) -> tuple[object, list[PrometheusSnapshot], list[PrometheusSnapshot]]:
    if not hasattr(operation, "__await__"):
        raise TypeError("operation must be awaitable")
    construction_samples = [await asyncio.to_thread(fetch_vllm_snapshot, construction_url)]
    embedding_samples = [await asyncio.to_thread(fetch_vllm_snapshot, embedding_url)]
    task = asyncio.create_task(operation)  # type: ignore[arg-type]
    try:
        while not task.done():
            await asyncio.sleep(5)
            construction_samples.append(await asyncio.to_thread(fetch_vllm_snapshot, construction_url))
            embedding_samples.append(await asyncio.to_thread(fetch_vllm_snapshot, embedding_url))
        result = await task
    except BaseException:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    construction_samples.append(await asyncio.to_thread(fetch_vllm_snapshot, construction_url))
    embedding_samples.append(await asyncio.to_thread(fetch_vllm_snapshot, embedding_url))
    return result, construction_samples, embedding_samples


async def _close(runtime: object) -> None:
    close = getattr(runtime, "aclose", None)
    if not callable(close):
        close = getattr(getattr(runtime, "graphiti", runtime), "close", None)
    if callable(close):
        value = close()
        if hasattr(value, "__await__"):
            await value


async def _run_blocks(
    *, run_root: Path, plan: Mapping[str, object], workload: Mapping[str, Mapping[str, object]],
    env: Mapping[str, str], execution_identity: str, construction_url: str,
    embedding_url: str, block_indices: Sequence[int], read_runtime: object,
) -> list[int]:
    blocks = plan["blocks"]
    completed: list[int] = []
    for block_index in block_indices:
        block = blocks[block_index]
        block_root = run_root / "blocks" / f"block-{block_index:02d}"
        result_path = block_root / (
            "C8_BLOCK_RESULT.json"
            if plan.get("schema_version") == C8_EXTENSION_SCHEMA
            else "C246_BLOCK_RESULT.json"
        )
        if result_path.exists():
            previous = _read_json(result_path)
            body = {key: value for key, value in previous.items() if key != "payload_sha256"}
            if previous.get("payload_sha256") != payload_sha256(body):
                raise RuntimeError("completed block hash mismatch")
            completed.append(block_index)
            print(f"REUSE block={block_index} method={block['method']} history={block['history_id']}", flush=True)
            continue
        if block_root.exists():
            raise RuntimeError("incomplete block exists; use a fresh run id")
        await _wait_idle(construction_url)
        await _wait_idle(embedding_url)
        salt = (
            cache_salt_for_extension_block(str(plan["run_id"]), block_index)
            if plan.get("schema_version") == C8_EXTENSION_SCHEMA
            else cache_salt_for_block(str(plan["run_id"]), block_index)
        )
        if payload_sha256({"cache_salt": salt}) != block["cache_salt_sha256"]:
            raise RuntimeError("cache salt plan binding mismatch")
        block_env = {**dict(env), "CONSTRUCTION_LLM_BASE_URL": construction_url, "EMBEDDING_BASE_URL": embedding_url, "CONSTRUCTION_CACHE_SALT": salt}
        episodes = workload[str(block["history_id"])] ["episodes"]
        print(f"START block={block_index} method={block['method']} history={block['history_id']} episodes={len(episodes)}", flush=True)
        started_ns = time.monotonic_ns()
        live, samples, embedding_samples = await _sample_until_done(
            construction_url=construction_url,
            embedding_url=embedding_url,
            operation=execute_aligned_live_block(
                verified_plan=plan, block_index=block_index, episodes=episodes,
                env=block_env, block_root=block_root, execution_identity_sha256=execution_identity,
            ),
        )
        inspected = inspect_aligned_block_artifacts(block_root)
        lifecycle = lifecycle_rows_from_events(inspected["events"], method=str(block["method"]), source_count=int(block["source_count"]))
        performance = derive_apc_aligned_performance(lifecycle)
        correctness = await measure_apc_aligned_direct_violations(
            block_root, verified_plan=plan, block_index=block_index,
            driver=read_runtime.graphiti.driver,
            expected_episode_names=tuple(str(getattr(value, "name")) for value in episodes),
        )
        telemetry = reduce_vllm_telemetry(samples)
        embedding_telemetry = reduce_vllm_telemetry(embedding_samples)
        first = samples[0]
        last = samples[-1]
        cold = {
            "first_request_started_at": started_ns,
            "first_request_completed_at": samples[1].timestamp_ns if len(samples) > 1 else last.timestamp_ns,
            "first_request_latency_ns": max(0, (samples[1] if len(samples) > 1 else last).timestamp_ns - started_ns),
            "first_request_prefix_cache_queries_delta": max(0, (samples[1] if len(samples) > 1 else last).values["prefix_cache_queries"] - first.values["prefix_cache_queries"]),
            "first_request_prefix_cache_hits_delta": max(0, (samples[1] if len(samples) > 1 else last).values["prefix_cache_hits"] - first.values["prefix_cache_hits"]),
            "excluded_from_warm_metrics": True,
        }
        body = {
            "schema_version": "membind.paper-eval-v3.c246-baseline-block-result.v1",
            "status": "PASS", "run_id": plan["run_id"], "block_index": block_index,
            "method": block["method"], "history_id": block["history_id"], "namespace": block["namespace"],
            "episode_count": block["source_count"], "plan_payload_sha256": plan["payload_sha256"],
            "cache_isolation": {"mechanism": "REQUEST_CACHE_SALT", "cache_salt_sha256": block["cache_salt_sha256"], "cross_block_prefix_identity_reuse": False, "within_block_prefix_reuse": True},
            "cold_start": cold, "live": live, "performance": performance, "correctness": correctness,
            "vllm_telemetry": telemetry, "vllm_telemetry_samples": [_public_snapshot(v) for v in samples],
            "embedding_vllm_telemetry": embedding_telemetry,
            "embedding_vllm_telemetry_samples": [_public_snapshot(v) for v in embedding_samples],
        }
        body["payload_sha256"] = payload_sha256(body)
        _write(result_path, body)
        completed.append(block_index)
        _write(run_root / "progress.json", {"status": "RUNNING", "run_id": plan["run_id"], "completed_block_indices": completed, "last_block_payload_sha256": body["payload_sha256"]})
        print(f"SEALED block={block_index} method={block['method']} p99_s={performance['p99_freshness_ns']/1e9:.3f} goodput={performance['goodput_episodes_per_second']:.5f} direct_violations={correctness['direct_violations_total']} apc_hit_rate={telemetry['prefix_cache_hit_rate']}", flush=True)
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--phase", choices=("smoke", "full", "c8-extension"), required=True)
    parser.add_argument("--construction-url", default="http://10.87.5.247:8002/v1")
    parser.add_argument("--embedding-url", default="http://10.87.5.247:8003/v1")
    args = parser.parse_args()
    _patch_runtime_endpoint_identities(args.construction_url, args.embedding_url)
    env = _load_env()
    env["CONSTRUCTION_LLM_BASE_URL"] = args.construction_url.rstrip("/")
    env["EMBEDDING_BASE_URL"] = args.embedding_url.rstrip("/")
    workload = _load_workload()
    identity = project_membind_v1_runtime_identity(env)
    base_plan = verify_c246_plan(build_c246_plan(
        run_id=args.run_id, history_source_sha256s=_source_hashes(workload),
        interarrival_ns=41_811_191_012, service_reference_ns=50_173_429_214,
        execution_envelope_sha256=payload_sha256(identity),
        construction_model_identity_sha256=payload_sha256(fetch_vllm_model_identity(args.construction_url)),
        embedding_model_identity_sha256=payload_sha256(fetch_vllm_model_identity(args.embedding_url, expected_model="qwen3-embedding-0.6b", expected_max_model_len=32768)),
    ))
    run_root = RUNS_ROOT / args.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    plan_path = run_root / "PLAN.json"
    if plan_path.exists() and _read_json(plan_path) != base_plan:
        raise SystemExit("existing plan drift")
    if not plan_path.exists() and args.phase != "c8-extension":
        _write(plan_path, base_plan)
    if args.phase == "c8-extension":
        base_result_path = run_root / "PHASE_RESULT.json"
        if not plan_path.exists() or not base_result_path.exists():
            raise SystemExit("full base result required")
        plan = verify_c8_extension_plan(
            build_c8_extension_plan(
                base_plan=_read_json(plan_path),
                full_phase_result=_read_json(base_result_path),
            )
        )
        execution_root = run_root / "c8-extension"
        execution_root.mkdir(parents=True, exist_ok=True)
        extension_plan_path = execution_root / "PLAN.json"
        if extension_plan_path.exists() and _read_json(extension_plan_path) != plan:
            raise SystemExit("existing C8 extension plan drift")
        if not extension_plan_path.exists():
            _write(extension_plan_path, plan)
    else:
        plan = base_plan
        execution_root = run_root
    initial = fetch_vllm_snapshot(args.construction_url)
    embedding_initial = fetch_vllm_snapshot(args.embedding_url)
    preflight = {"schema_version": "membind.paper-eval-v3.c246-preflight.v1", "status": "PASS", "run_id": args.run_id, "construction_url": args.construction_url, "embedding_url": args.embedding_url, "construction_model": fetch_vllm_model_identity(args.construction_url), "embedding_model": fetch_vllm_model_identity(args.embedding_url, expected_model="qwen3-embedding-0.6b", expected_max_model_len=32768), "initial_metrics": _public_snapshot(initial), "embedding_initial_metrics": _public_snapshot(embedding_initial), "cache_probe": probe_vllm_cache_salt(args.construction_url, env.get("CONSTRUCTION_LLM_API_KEY"), cache_salt_for_block(args.run_id, 99)), "embedding_cache_probe": probe_vllm_embedding_cache_salt(args.embedding_url, env.get("EMBEDDING_API_KEY"), cache_salt_for_block(args.run_id, 99)), "implementation_hashes": _implementation_hashes()}
    preflight["payload_sha256"] = payload_sha256(preflight)
    preflight_path = run_root / "PREFLIGHT.json"
    if preflight_path.exists() and _read_json(preflight_path) != preflight:
        raise SystemExit("existing preflight drift")
    if not preflight_path.exists():
        _write(preflight_path, preflight)
    indices = (0, 1, 2) if args.phase == "smoke" else ((tuple(range(4))) if args.phase == "c8-extension" else tuple(range(12)))
    read_runtime = build_graph_quality_runtime(env=env)
    try:
        completed = asyncio.run(_run_blocks(run_root=execution_root, plan=plan, workload=workload, env=env, execution_identity=payload_sha256({"runtime_identity": identity, "implementation_hashes": _implementation_hashes()}), construction_url=args.construction_url, embedding_url=args.embedding_url, block_indices=indices, read_runtime=read_runtime))
    except BaseException as error:
        failure = {"status": "FAILED_STOPPED", "run_id": args.run_id, "error_class": f"{type(error).__module__}.{type(error).__qualname__}", "message": str(error)[:500]}
        failure["payload_sha256"] = payload_sha256(failure)
        _write(execution_root / "FAILURE.json", failure)
        print(json.dumps(failure, sort_keys=True), flush=True)
        return 1
    finally:
        try:
            asyncio.run(_close(read_runtime))
        except Exception:
            pass
    result = {"status": "PASS", "run_id": args.run_id, "phase": args.phase, "completed_block_indices": completed}
    result["payload_sha256"] = payload_sha256(result)
    _write(execution_root / "PHASE_RESULT.json", result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
