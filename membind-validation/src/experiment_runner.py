"""Isolated, artifact-producing execution for one frozen experiment run."""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from dataset import build_episodes
from embedding_cache import embedding_metrics
from graphiti_membind import M2_MEMBIND_GO_C8, run_membind_go
from graphiti_native import (
    M0_NATIVE_SERIAL,
    M1_WHOLE_PARALLEL_C8,
    build_qwen_graphiti_from_env,
    llm_failure_records,
    llm_metrics,
    load_env_file,
    run_native_serial,
    run_whole_parallel,
    unexpected_prompt_records,
)
from instrumentation import install_driver_instrumentation
from live_outputs import evaluate_retrieval, export_canonical_graph
from live_runtime import clear_database, close, count_nodes, prepare_clean_graph
from response_cache import PromptCache
from search_forensics import search_forensic_payload
from tracing import JsonlTraceWriter


class RunArtifactExists(RuntimeError):
    pass


class ExperimentRunFailed(RuntimeError):
    def __init__(self, status: dict[str, Any]):
        super().__init__(f"experiment run {status['run_id']} failed: {status.get('error')}")
        self.status = status


def cache_for_spec(spec: dict[str, Any], artifacts: Path) -> PromptCache | None:
    mode = str(spec.get("mode", "live"))
    if mode == "live":
        return None
    cache_id = str(spec.get("cache_id") or spec["question_id"])
    path = artifacts / "prompt_cache" / f"{cache_id}.jsonl"
    if mode == "capture":
        if path.exists():
            raise RunArtifactExists(f"capture cache already exists: {path}")
        return PromptCache(path, read_only=False)
    if mode == "replay":
        if not path.exists():
            raise FileNotFoundError(f"missing correctness capture cache: {path}")
        return PromptCache(path, read_only=True)
    raise ValueError(f"unknown run mode: {mode}")


async def run_experiment(
    spec: dict[str, Any],
    instance: dict[str, Any],
    arrival_interval_ms: int,
    *,
    artifacts: str | Path,
    graphiti_factory: Callable[..., Any] = build_qwen_graphiti_from_env,
    method_runners: dict[str, Callable[..., Awaitable[Any]]] | None = None,
    service_checker: Callable[[], Awaitable[Any]] | None = None,
    graph_exporter: Callable[..., Awaitable[dict[str, Any]]] = export_canonical_graph,
    retrieval_evaluator: Callable[..., Awaitable[dict[str, Any]]] = evaluate_retrieval,
    collect_outputs: bool = True,
) -> dict[str, Any]:
    artifacts = Path(artifacts)
    run_id = str(spec["run_id"])
    paths = {
        "status": artifacts / "runs" / f"{run_id}.json",
        "trace": artifacts / "traces" / f"{run_id}.jsonl",
        "graph": artifacts / "graphs" / f"{run_id}.canonical.json",
        "retrieval": artifacts / "retrieval" / f"{run_id}.json",
        "llm_failures": artifacts / "llm_failures" / f"{run_id}.json",
        "unexpected_prompts": artifacts / "unexpected_prompts" / f"{run_id}.json",
        "search_forensics": artifacts / "search_forensics" / f"{run_id}.json",
    }
    _assert_fresh(paths.values())

    started_wall = datetime.now(timezone.utc)
    started_ns = time.monotonic_ns()
    status: dict[str, Any] = {
        **spec,
        "run_id": run_id,
        "status": "running",
        "started_at": started_wall.isoformat(),
        "arrival_interval_ms": int(arrival_interval_ms),
        "artifact_paths": {key: str(path) for key, path in paths.items()},
    }
    _write_json(paths["status"], status)

    graphiti = None
    failure: Exception | None = None
    cleanup_errors: list[str] = []
    try:
        prompt_cache = cache_for_spec(spec, artifacts)
        await (service_checker or wait_for_model_services)()
        graphiti = graphiti_factory(prompt_cache=prompt_cache)
        install_driver_instrumentation(graphiti)
        await prepare_clean_graph(graphiti, _warm_up_episode)

        episodes = build_episodes(instance)
        trace_writer = JsonlTraceWriter(paths["trace"])
        await _run_method(
            spec,
            graphiti,
            episodes,
            int(arrival_interval_ms),
            trace_writer,
            method_runners,
        )

        status["episode_count"] = len(episodes)
        status["llm_metrics"] = llm_metrics(graphiti.llm_client)
        status["embedding_metrics"] = embedding_metrics(graphiti.embedder)
        if collect_outputs:
            graph = await graph_exporter(graphiti, episodes, str(spec["question_id"]))
            retrieval = await retrieval_evaluator(graphiti, instance, episodes)
            _write_json(paths["graph"], graph)
            _write_json(paths["retrieval"], retrieval)
            status["canonical_graph_hash"] = graph.get("canonical_graph_hash")
            status["retrieval_metrics"] = retrieval.get("metrics", {})
    except Exception as exc:
        failure = exc
        status["error"] = repr(exc)
        status["traceback_tail"] = traceback.format_exc().splitlines()[-30:]
    finally:
        if graphiti is not None:
            status.setdefault("llm_metrics", llm_metrics(graphiti.llm_client))
            status.setdefault("embedding_metrics", embedding_metrics(graphiti.embedder))
            forensic_payload = search_forensic_payload(graphiti.driver)
            if any(forensic_payload.values()):
                _write_json(paths["search_forensics"], forensic_payload)
                status["search_forensics_path"] = str(paths["search_forensics"])
            structured_failures = llm_failure_records(graphiti.llm_client)
            if structured_failures:
                _write_json(paths["llm_failures"], structured_failures)
                status["structured_failure_count"] = len(structured_failures)
            prompt_diagnostics = unexpected_prompt_records(graphiti.llm_client)
            if prompt_diagnostics:
                _write_json(
                    paths["unexpected_prompts"],
                    {"run_id": run_id, "diagnostics": prompt_diagnostics},
                )
                status["unexpected_prompt_count"] = len(prompt_diagnostics)
                status["unexpected_prompt_diagnostics_path"] = str(
                    paths["unexpected_prompts"]
                )
            try:
                await clear_database(graphiti)
                remaining = await count_nodes(graphiti)
                if remaining != 0:
                    raise RuntimeError(f"database isolation failure: {remaining} nodes remain")
                status["post_run_node_count"] = remaining
            except Exception as exc:
                cleanup_errors.append(repr(exc))
                if failure is None:
                    failure = exc
            try:
                await close(graphiti)
            except Exception as exc:
                cleanup_errors.append(repr(exc))
                if failure is None:
                    failure = exc

    status["finished_at"] = datetime.now(timezone.utc).isoformat()
    status["elapsed_ms"] = (time.monotonic_ns() - started_ns) / 1_000_000
    if cleanup_errors:
        status["cleanup_errors"] = cleanup_errors
    status["status"] = "failed" if failure is not None else "success"
    if failure is not None and "error" not in status:
        status["error"] = repr(failure)
    _write_json(paths["status"], status)
    if failure is not None:
        raise ExperimentRunFailed(status) from failure
    return status


async def _run_method(
    spec: dict[str, Any],
    graphiti: Any,
    episodes: list[Any],
    arrival_interval_ms: int,
    trace_writer: JsonlTraceWriter,
    method_runners: dict[str, Callable[..., Awaitable[Any]]] | None,
) -> None:
    method = str(spec["method"])
    runner = (method_runners or {}).get(method)
    common = (
        graphiti,
        episodes,
        str(spec["run_id"]),
        int(spec.get("repeat", 0)),
        arrival_interval_ms,
        trace_writer,
    )
    if runner is not None:
        await runner(*common)
    elif method == M0_NATIVE_SERIAL:
        await run_native_serial(*common)
    elif method == M1_WHOLE_PARALLEL_C8:
        await run_whole_parallel(*common, 8)
    elif method == M2_MEMBIND_GO_C8:
        await run_membind_go(*common, 8)
    else:
        raise ValueError(f"unknown method {method}")


async def _warm_up_episode(graphiti: Any) -> None:
    from graphiti_core.nodes import EpisodeType

    await graphiti.add_episode(
        name="membind-warmup-0000",
        episode_body="[USER] Warm-up user likes tea.\n[ASSISTANT] I will remember that.",
        source_description="MemBind unmeasured per-run warm-up",
        reference_time=datetime(2026, 8, 6, tzinfo=timezone.utc),
        source=EpisodeType.message,
        group_id="_membind_warmup",
    )


async def wait_for_model_services(
    timeout_seconds: int = 60,
    required_stable_checks: int = 2,
) -> None:
    load_env_file()
    deadline = time.monotonic() + timeout_seconds
    stable = 0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            await asyncio.to_thread(_check_model_services_once)
            stable += 1
            if stable >= required_stable_checks:
                return
        except Exception as exc:
            last_error = exc
            stable = 0
        await asyncio.sleep(1)
    raise RuntimeError(f"model services did not remain healthy: {last_error!r}")


def _check_model_services_once() -> None:
    checks = [
        (
            os.environ.get("CONSTRUCTION_LLM_BASE_URL", "http://10.87.5.247:8000/v1/"),
            os.environ.get("CONSTRUCTION_LLM_API_KEY") or os.environ.get("VLLM_API_KEY"),
            os.environ.get("CONSTRUCTION_LLM_MODEL"),
        ),
        (
            os.environ.get("EMBEDDING_BASE_URL", "http://10.87.5.247:8001/v1"),
            os.environ.get("EMBEDDING_API_KEY") or os.environ.get("VLLM_API_KEY"),
            os.environ.get("EMBEDDING_MODEL", "qwen3-embedding-0.6b"),
        ),
    ]
    for base_url, api_key, expected_model in checks:
        if not api_key:
            raise RuntimeError("model service API key is missing")
        request = urllib.request.Request(
            base_url.rstrip("/") + "/models",
            headers={"Authorization": "Bearer " + api_key},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = {str(item.get("id")) for item in payload.get("data", [])}
        if expected_model and expected_model not in models:
            raise RuntimeError(f"expected model is not served at {base_url}")


def _assert_fresh(paths: Any) -> None:
    existing = [str(path) for path in paths if Path(path).exists()]
    if existing:
        raise RunArtifactExists("run artifacts already exist: " + ", ".join(existing))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
