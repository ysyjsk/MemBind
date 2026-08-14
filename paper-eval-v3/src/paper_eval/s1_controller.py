"""Executable S1 controller over the isolated durable runner."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import atomic_write_json
from .s1_live import EXPECTED_S1_HISTORY_ID, S1LiveAdapter, load_fixed_history
from .s1_summary import finalize_s1_summary
from .s1_u0_smoke import DurableRun, RunResult


ROOT = Path(__file__).resolve().parents[3]
LEGACY = ROOT / "membind-validation"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
DEFAULT_SPLIT = LEGACY / "artifacts/dataset/frozen_split.json"
DEFAULT_RUN_ROOT = ROOT / "paper-eval-v3/artifacts/paper_eval/native/runs"
DEFAULT_FINAL = ROOT / "paper-eval-v3/artifacts/paper_eval/native/U0_SMOKE.json"


async def ensure_runtime_ready(runtime: Any) -> None:
    """Wait for Graphiti's Neo4j initialization before the first live probe."""

    graphiti = getattr(runtime, "graphiti", None)
    driver = getattr(graphiti, "driver", None)
    init_task = getattr(driver, "_init_task", None)
    if init_task is not None:
        await init_task
        return
    for method_name in ("build_indices_and_constraints", "init"):
        readiness = getattr(driver, method_name, None)
        if callable(readiness):
            value = readiness()
            if inspect.isawaitable(value):
                await value
            return


async def _close_graphiti(graphiti: Any) -> None:
    close = getattr(graphiti, "close", None)
    if callable(close):
        value = close()
        if inspect.isawaitable(value):
            await value


def safe_event_sink(event: Mapping[str, Any]) -> None:
    allowed = {
        key: event[key]
        for key in (
            "event_type",
            "source_sequence",
            "timestamp_ns",
            "error_class",
            "failure_stage",
        )
        if key in event
    }
    print(json.dumps(allowed, sort_keys=True), flush=True)


async def run_s1(
    *,
    run_id: str,
    namespace: str,
    artifact_root: Path,
    final_output: Path,
    git_commit: str,
    instance: Mapping[str, Any],
    episodes: Sequence[Any],
    runtime: Any,
    kwargs_builder: Callable[[Any], Mapping[str, Any]],
    namespace_probe: Callable[[Any], Any],
    expected_episode_count: int = 49,
    event_sink: Callable[[Mapping[str, Any]], Any] = safe_event_sink,
) -> RunResult:
    if str(instance.get("question_id")) != EXPECTED_S1_HISTORY_ID:
        raise ValueError("S1 history identity drift")
    if len(episodes) != expected_episode_count:
        raise ValueError(
            f"S1 episode count drift: expected {expected_episode_count}, got {len(episodes)}"
        )
    adapter = S1LiveAdapter(namespace, kwargs_builder=kwargs_builder)

    async def probe() -> Mapping[str, Any]:
        value = namespace_probe(runtime.graphiti.driver)
        if asyncio.iscoroutine(value):
            return dict(await value)
        if value is None:
            return {}
        return dict(value)

    durable = DurableRun(
        artifact_root,
        run_id,
        EXPECTED_S1_HISTORY_ID,
        namespace,
        episode_kwargs=adapter.episode_kwargs,
        namespace_probe=probe,
        event_sink=event_sink,
    )
    execute_entered = False
    try:
        await ensure_runtime_ready(runtime)
        execute_entered = True
        result = await durable.execute(
            runtime.graphiti,
            list(episodes),
            query=str(instance["question"]),
        )
    except Exception as error:
        attempt = {
            "run_id": run_id,
            "history_id": EXPECTED_S1_HISTORY_ID,
            "namespace": namespace,
            "status": "incomplete",
            "completed_episode_count": 0,
            "expected_episode_count": expected_episode_count,
            "error_class": type(error).__name__,
        }
        checkpoint_path = artifact_root / run_id / "checkpoint.json"
        if checkpoint_path.is_file():
            try:
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                attempt["completed_episode_count"] = len(
                    checkpoint.get("completed_source_sequences", [])
                )
            except (OSError, ValueError, TypeError):
                pass
        atomic_write_json(artifact_root / run_id / "attempt_summary.json", attempt)
        if not execute_entered:
            await _close_graphiti(runtime.graphiti)
        raise
    attempt = {
        "run_id": run_id,
        "history_id": EXPECTED_S1_HISTORY_ID,
        "namespace": namespace,
        "status": result.status,
        "completed_episode_count": len(result.completed_source_sequences),
        "expected_episode_count": expected_episode_count,
        "error_class": result.error_class,
    }
    atomic_write_json(artifact_root / run_id / "attempt_summary.json", attempt)
    if result.status == "completed":
        final = finalize_s1_summary(
            run_dir=artifact_root / run_id,
            output_path=final_output,
            expected_episode_count=expected_episode_count,
            git_commit=git_commit,
        )
        if final["payload"]["verdict"] != "PASS":
            raise RuntimeError("S1 finalization failed its own coverage gate")
    return result


def _legacy_imports() -> tuple[Any, Callable[[dict[str, Any]], Sequence[Any]], Callable[[Any], Mapping[str, Any]]]:
    source = str(LEGACY / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from dataset import build_episodes
    from graphiti_native import graphiti_episode_kwargs, load_env_file
    from native_characterization_runtime import build_u0_graphiti_from_env

    runtime = build_u0_graphiti_from_env(
        authorization_checker=lambda _action: None,
        env_loader=lambda: load_env_file(LEGACY / ".env"),
    )
    return runtime, build_episodes, graphiti_episode_kwargs


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


async def _main(args: argparse.Namespace) -> int:
    instance = load_fixed_history(args.dataset, args.split)
    runtime, episode_builder, kwargs_builder = _legacy_imports()
    episodes = list(episode_builder(instance))
    adapter = S1LiveAdapter(args.namespace)
    result = await run_s1(
        run_id=args.run_id,
        namespace=args.namespace,
        artifact_root=args.artifact_root,
        final_output=args.final_output,
        git_commit=_git_commit(),
        instance=instance,
        episodes=episodes,
        runtime=runtime,
        kwargs_builder=kwargs_builder,
        namespace_probe=adapter.namespace_state,
    )
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "namespace": args.namespace,
                "status": result.status,
                "completed_episode_count": len(result.completed_source_sequences),
                "error_class": result.error_class,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if result.status == "completed" else 2


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--run-id", required=True)
    value.add_argument("--namespace", required=True)
    value.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    value.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    value.add_argument("--artifact-root", type=Path, default=DEFAULT_RUN_ROOT)
    value.add_argument("--final-output", type=Path, default=DEFAULT_FINAL)
    return value


def main() -> None:
    raise SystemExit(asyncio.run(_main(parser().parse_args())))


if __name__ == "__main__":
    main()
