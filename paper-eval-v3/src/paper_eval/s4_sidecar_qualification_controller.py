"""Sequential controller primitives for the remaining S4 sidecar blocks.

The module is intentionally separate from the sealed retry controller.  Its
live entry point refuses to start without a finalized fixed-three authority;
the pure helpers are covered offline before any authority is created.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .s4_candidate_projection import PROJECTION_SCHEMA_SHA256
from .s4_d0_production import S4CachePaths, S4CandidateSidecarConfig
from .s4_sidecar_qualification_authority import (
    verify_s4_sidecar_qualification_authority,
)
from .s4_sidecar_qualification_data import (
    LIVE_HISTORY_IDS,
    build_s4_qualification_episode_manifest,
)


ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "paper-eval-v3"
NATIVE = PROJECT / "artifacts/paper_eval/native"
DEFAULT_AUTHORITY = (
    NATIVE / "S4_D0_QUALIFICATION_EXECUTION_AUTHORITY_SIDECAR_V1.json"
)
DEFAULT_RESULT = NATIVE / "S4_D0_FIXED_THREE_RESULT_SIDECAR_V1.json"


@dataclass(frozen=True)
class QualificationPhaseDependencies:
    """Inject live construction only after authority consumption."""

    build_runtime: Callable[..., Any]
    run_phase: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]]
    ensure_runtime_ready: Callable[[Any], Awaitable[Any] | Any]
    edge_operations_module: Any


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def compose_qualification_phase_specs(
    block: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Compose the exact capture/replay specs embedded in one authority block."""

    selected = _mapping(block, label="S4 qualification block")
    history = _mapping(selected.get("history"), label="qualification history")
    plan_block = _mapping(selected.get("plan_block"), label="qualification plan block")
    history_id = history.get("history_id")
    if history_id not in LIVE_HISTORY_IDS or plan_block.get("history_id") != history_id:
        raise ValueError("S4 qualification block history drift")
    runs = _mapping(plan_block.get("runs"), label="qualification runs")
    expected_fields = {
        "phase",
        "run_id",
        "history_id",
        "namespace",
        "method",
        "mode",
        "cache_id",
    }
    result: list[dict[str, str]] = []
    for phase, method, mode in (
        ("U0_CAPTURE", "U0", "capture"),
        ("D0_READ_ONLY_REPLAY", "D0", "replay"),
    ):
        run = _mapping(runs.get(phase), label=f"{phase} run")
        spec = {"phase": phase, "history_id": history_id, **run}
        if (
            set(spec) != expected_fields
            or spec.get("method") != method
            or spec.get("mode") != mode
            or spec.get("cache_id") != plan_block.get("cache_id")
            or not str(spec.get("namespace", "")).startswith("pev3-s4-")
        ):
            raise ValueError("S4 qualification phase spec drift")
        result.append({key: str(value) for key, value in spec.items()})
    return result


def qualification_sidecar_config(
    *,
    block: Mapping[str, Any],
    spec: Mapping[str, Any],
    episodes: Sequence[Any],
    edge_operations_module: Any,
    project_root: Path = PROJECT,
) -> S4CandidateSidecarConfig:
    """Build a history-parameterized sidecar config without smoke assumptions."""

    selected = _mapping(block, label="S4 qualification block")
    history = _mapping(selected.get("history"), label="qualification history")
    expected_specs = compose_qualification_phase_specs(selected)
    phase = spec.get("phase")
    expected = next((item for item in expected_specs if item["phase"] == phase), None)
    if expected is None or dict(spec) != expected:
        raise ValueError("S4 qualification sidecar spec is outside authority")
    manifest, manifest_sha = build_s4_qualification_episode_manifest(episodes)
    del manifest
    if (
        len(episodes) != history.get("episode_count")
        or manifest_sha != history.get("episode_manifest_sha256")
    ):
        raise ValueError("S4 qualification episode manifest drift")
    private = _mapping(selected.get("private_cache"), label="qualification cache")
    if private.get("reportable_contents") is not False:
        raise ValueError("S4 qualification private cache policy drift")
    project = Path(project_root).resolve()
    allowed = (project / "runtime/private").resolve()
    raw_path = private.get("candidate_sidecar_relpath")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("S4 qualification candidate sidecar path is missing")
    path = (project / raw_path).resolve()
    if not path.is_relative_to(allowed):
        raise ValueError("S4 qualification candidate sidecar path escaped runtime/private")
    history_id = str(history["history_id"])
    return S4CandidateSidecarConfig(
        path=path,
        identity={
            "attempt_id": f"s4q-{history_id}-001",
            "cache_id": str(spec["cache_id"]),
            "episode_manifest_sha256": manifest_sha,
            "history_id": history_id,
            "projection_schema_sha256": PROJECTION_SCHEMA_SHA256,
        },
        episodes=episodes,
        edge_operations_module=edge_operations_module,
        namespace=str(spec["namespace"]),
    )


def qualification_cache_paths(
    block: Mapping[str, Any], *, project_root: Path = PROJECT
) -> S4CachePaths:
    """Resolve only authority-declared prompt/embedding paths under private root."""

    selected = _mapping(block, label="S4 qualification block")
    private = _mapping(selected.get("private_cache"), label="qualification cache")
    if private.get("reportable_contents") is not False:
        raise ValueError("S4 qualification private cache policy drift")
    project = Path(project_root).resolve()
    allowed = (project / "runtime/private").resolve()

    def resolve(field: str) -> Path:
        raw = private.get(field)
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"S4 qualification {field} is missing")
        path = (project / raw).resolve()
        if not path.is_relative_to(allowed):
            raise ValueError(f"S4 qualification {field} escaped runtime/private")
        return path

    return S4CachePaths(
        prompt=resolve("prompt_relpath"),
        embedding=resolve("embedding_relpath"),
    )


async def execute_qualification_phase(
    *,
    block: Mapping[str, Any],
    spec: Mapping[str, Any],
    episodes: Sequence[Any],
    project_root: Path,
    artifact_root: Path,
    git_commit: str,
    dependencies: QualificationPhaseDependencies,
    event_sink: Callable[[Mapping[str, Any]], Any] | None = None,
) -> Mapping[str, Any]:
    """Wire one authority-bound phase into the already-qualified runtime/runner."""

    selected = _mapping(block, label="S4 qualification block")
    history = _mapping(selected.get("history"), label="qualification history")
    expected_count = history.get("episode_count")
    if not isinstance(expected_count, int) or isinstance(expected_count, bool):
        raise ValueError("S4 qualification episode count is invalid")
    cache_paths = qualification_cache_paths(selected, project_root=project_root)
    sidecar = qualification_sidecar_config(
        block=selected,
        spec=spec,
        episodes=episodes,
        edge_operations_module=dependencies.edge_operations_module,
        project_root=project_root,
    )
    run_dir = Path(artifact_root) / str(spec["run_id"])
    checkpoint_exists = (run_dir / "checkpoint.json").exists()
    runtime = dependencies.build_runtime(
        spec=spec,
        cache_paths=cache_paths,
        resume_capture=spec["mode"] == "capture" and checkpoint_exists,
        sidecar=sidecar,
    )
    await _await(dependencies.ensure_runtime_ready(runtime.graph))
    return await _await(
        dependencies.run_phase(
            spec=spec,
            episodes=episodes,
            graph=runtime.graph,
            episode_kwargs=runtime.episode_kwargs,
            namespace_probe=runtime.namespace_probe,
            graph_exporter=runtime.graph_exporter,
            runtime_evidence=runtime.runtime_evidence,
            cache_evidence=runtime.cache_evidence,
            cleanup_namespace=runtime.cleanup_namespace,
            artifact_root=Path(artifact_root),
            expected_episode_count=expected_count,
            git_commit=str(git_commit),
            event_sink=event_sink,
            episode_scope=lambda _item: runtime.phase_context(),
            restore_prefix=runtime.restore_sidecar_prefix,
            pre_cleanup_finalize=lambda: runtime.pre_finalize_sidecar(
                runtime.cache_evidence()
            ),
        )
    )


def select_next_qualification_block(
    authority: Mapping[str, Any],
    completed_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return only the first incomplete block after a contiguous strict PASS prefix."""

    payload = _mapping(authority.get("payload"), label="qualification authority payload")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != len(LIVE_HISTORY_IDS):
        raise ValueError("S4 qualification authority block inventory drift")
    extras = set(completed_results) - set(LIVE_HISTORY_IDS)
    if extras:
        raise ValueError("S4 qualification result inventory drift")
    seen_missing = False
    for index, (history_id, block) in enumerate(
        zip(LIVE_HISTORY_IDS, blocks, strict=True)
    ):
        result = completed_results.get(history_id)
        if result is None:
            seen_missing = True
            if any(later in completed_results for later in LIVE_HISTORY_IDS[index + 1 :]):
                raise ValueError("S4 qualification result prefix is non-contiguous")
            return deepcopy(dict(block))
        if seen_missing:
            raise ValueError("S4 qualification result prefix is non-contiguous")
        result_payload = _mapping(result.get("payload"), label="block result payload")
        if (
            result_payload.get("history_id") != history_id
            or result_payload.get("block_index") != index
            or result_payload.get("verdict") != "PASS"
            or result_payload.get("next_block_authorized")
            is not (index < len(LIVE_HISTORY_IDS) - 1)
            or result_payload.get("s5_authorized") is not False
        ):
            raise ValueError("S4 qualification prior block is not a strict PASS")
    return None


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def orchestrate_qualification_block(
    *,
    block: Mapping[str, Any],
    execute_phase: Callable[
        [Mapping[str, Any]], Awaitable[Mapping[str, Any]] | Mapping[str, Any]
    ],
    evaluate: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Enforce capture PASS before replay and evaluate only two PASS phases."""

    capture_spec, replay_spec = compose_qualification_phase_specs(block)
    capture = await _await(execute_phase(capture_spec))
    if capture.get("payload", {}).get("status") != "PASS":
        raise RuntimeError("S4 qualification capture did not PASS; replay is forbidden")
    replay = await _await(execute_phase(replay_spec))
    if replay.get("payload", {}).get("status") != "PASS":
        raise RuntimeError("S4 qualification replay did not PASS")
    history = _mapping(block.get("history"), label="qualification history")
    evaluation = dict(
        evaluate(
            capture_result=capture,
            replay_result=replay,
            history_id=history["history_id"],
            expected_episode_count=history["episode_count"],
        )
    )
    if evaluation.get("verdict") != "PASS":
        raise RuntimeError("S4 qualification block evaluation did not PASS")
    return {
        "capture_result": capture,
        "replay_result": replay,
        "evaluation": evaluation,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--authority", type=Path, default=DEFAULT_AUTHORITY)
    value.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    return value


def main() -> None:
    """Fail before live I/O until an authority exists and the live driver is sealed."""

    args = parser().parse_args()
    if not args.authority.is_file():
        raise SystemExit(f"missing fixed-three authority: {args.authority}")
    authority = verify_s4_sidecar_qualification_authority(
        json.loads(args.authority.read_text(encoding="utf-8"))
    )
    del authority
    raise SystemExit(
        "fixed-three authority verified, but live controller finalization is not installed"
    )


if __name__ == "__main__":
    main()
