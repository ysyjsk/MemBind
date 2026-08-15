"""Checkpointed controller for the one authorized S4 capture/replay smoke."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file
from .s1_controller import ensure_runtime_ready
from .s1_live import load_fixed_history
from .s4_authority import (
    consume_s4_smoke_authority,
    verify_s4_authority_consumption,
    verify_s4_smoke_authority,
)
from .s4_d0_production import S4CachePaths, build_s4_phase_runtime
from .s4_d0_runner import evaluate_s4_smoke, run_s4_phase


ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "paper-eval-v3"
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
DEFAULT_SPLIT = LEGACY / "artifacts/dataset/frozen_split.json"
DEFAULT_AUTHORITY = NATIVE / "S4_SMOKE_AUTHORIZATION.json"
DEFAULT_CONSUMPTION = (
    NATIVE
    / "runs/s4-smoke-20260814-001/S4_SMOKE_AUTHORIZATION_CONSUMPTION.json"
)
DEFAULT_RESULT = NATIVE / "S4_D0_SMOKE_RESULT.json"


def safe_event_sink(event: Mapping[str, Any]) -> None:
    """Print only progress identities and sanitized failure classes."""

    allowed = {
        key: event[key]
        for key in (
            "event_type",
            "source_sequence",
            "timestamp_ns",
            "error_class",
            "failure_stage",
            "completed_count",
            "status",
        )
        if key in event
    }
    print(json.dumps(allowed, sort_keys=True), flush=True)


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def compose_phase_specs(authority: Mapping[str, Any]) -> list[dict[str, str]]:
    """Compose and fully validate runner specs before authority consumption."""

    payload = dict(authority.get("payload", {}))
    if payload.get("execution_order") != ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]:
        raise ValueError("S4 controller execution order drift")
    history = dict(payload.get("history", {}))
    if history != {
        "data_role": "DEVELOPMENT_EXPOSED",
        "episode_count": 49,
        "history_id": "07741c45",
    }:
        raise ValueError("S4 controller history identity drift")
    runs = dict(payload.get("runs", {}))
    expected_fields = {
        "phase",
        "run_id",
        "history_id",
        "namespace",
        "method",
        "mode",
        "cache_id",
    }
    specs = [
        {
            "phase": phase,
            "history_id": history["history_id"],
            **dict(runs.get(phase, {})),
        }
        for phase in payload["execution_order"]
    ]
    expected_identity = [
        ("U0_CAPTURE", "U0", "capture"),
        ("D0_READ_ONLY_REPLAY", "D0", "replay"),
    ]
    for spec, identity in zip(specs, expected_identity, strict=True):
        if (
            set(spec) != expected_fields
            or (spec["phase"], spec["method"], spec["mode"]) != identity
            or not str(spec["namespace"]).startswith("pev3-s4-")
        ):
            raise ValueError("S4 controller phase spec drift")
    return [{key: str(value) for key, value in spec.items()} for spec in specs]


async def orchestrate_s4_smoke(
    *,
    authority: Mapping[str, Any],
    execute_phase: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
    evaluate: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Enforce capture-before-replay and the capture PASS dependency."""

    capture_spec, replay_spec = compose_phase_specs(authority)
    capture = await _await(execute_phase(capture_spec))
    if capture.get("payload", {}).get("status") != "PASS":
        raise RuntimeError("S4 capture did not PASS; replay is forbidden")
    replay = await _await(execute_phase(replay_spec))
    if replay.get("payload", {}).get("status") != "PASS":
        raise RuntimeError("S4 replay did not PASS")
    evaluation = dict(
        evaluate(capture_result=capture, replay_result=replay)
    )
    return {
        "capture_result": capture,
        "replay_result": replay,
        "evaluation": evaluation,
    }


def resolve_private_cache_paths(
    authority: Mapping[str, Any], *, project_root: Path
) -> S4CachePaths:
    payload = dict(authority.get("payload", {}))
    private = dict(payload.get("private_cache", {}))
    project = Path(project_root).resolve()
    allowed = (project / "runtime/private").resolve()

    def resolve(name: str) -> Path:
        raw = private.get(name)
        if not isinstance(raw, str) or not raw:
            raise ValueError("S4 private cache path is missing")
        selected = (project / raw).resolve()
        if not selected.is_relative_to(allowed):
            raise ValueError("S4 private cache path escaped runtime/private")
        return selected

    if private.get("reportable_contents") is not False:
        raise ValueError("S4 private cache reporting policy drift")
    return S4CachePaths(
        prompt=resolve("prompt_relpath"),
        embedding=resolve("embedding_relpath"),
    )


def ensure_authority_consumption(
    *,
    path: Path,
    authority_file_sha256: str,
    authority_payload_sha256: str,
    consume: Callable[[], Mapping[str, Any]],
    verify: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Create once, while allowing only the same exact pipeline to resume."""

    selected_path = Path(path)
    if not selected_path.exists():
        return dict(consume())
    try:
        existing = json.loads(selected_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("S4 authority consumption is unreadable") from error
    selected = dict(verify(existing))
    payload = dict(selected.get("payload", {}))
    if (
        payload.get("consumed_action") != "S4_SMOKE_PIPELINE"
        or payload.get("authority_file_sha256") != authority_file_sha256
        or payload.get("authority_payload_sha256") != authority_payload_sha256
    ):
        raise ValueError("S4 authority consumption does not match this resume")
    return selected


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _legacy_episodes(dataset: Path, split: Path) -> list[Any]:
    source = str(LEGACY / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from dataset import build_episodes

    instance = load_fixed_history(dataset, split)
    episodes = list(build_episodes(instance))
    if len(episodes) != 49 or [item.source_sequence for item in episodes] != list(
        range(49)
    ):
        raise ValueError("S4 controller episode projection drift")
    return episodes


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _verify_authority_sources(authority: Mapping[str, Any]) -> None:
    expected = {
        "authority": sha256_file(PROJECT / "src/paper_eval/s4_authority.py"),
        "controller": sha256_file(PROJECT / "src/paper_eval/s4_controller.py"),
        "production": sha256_file(PROJECT / "src/paper_eval/s4_d0_production.py"),
        "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
        "test": sha256_file(PROJECT / "tests/test_s4_controller.py"),
    }
    if authority["payload"]["source_sha256"] != dict(sorted(expected.items())):
        raise RuntimeError("S4 controller source binding drift")


async def run_controller(args: argparse.Namespace) -> dict[str, Any]:
    authority = verify_s4_smoke_authority(
        json.loads(args.authority.read_text(encoding="utf-8"))
    )
    _verify_authority_sources(authority)
    compose_phase_specs(authority)
    authority_file_sha = sha256_file(args.authority)
    git_commit = _git_commit()
    consumption = ensure_authority_consumption(
        path=args.consumption,
        authority_file_sha256=authority_file_sha,
        authority_payload_sha256=authority["payload_sha256"],
        consume=lambda: consume_s4_smoke_authority(
            authority=authority,
            authority_file_sha256=authority_file_sha,
            output_path=args.consumption,
            git_commit=git_commit,
            run_id="s4-smoke-authority-consumption-20260814-001",
        ),
        verify=verify_s4_authority_consumption,
    )
    episodes = _legacy_episodes(args.dataset, args.split)
    cache_paths = resolve_private_cache_paths(authority, project_root=PROJECT)

    async def execute_phase(spec: Mapping[str, Any]) -> Mapping[str, Any]:
        run_dir = args.artifact_root / str(spec["run_id"])
        resume_capture = (
            spec["mode"] == "capture" and (run_dir / "checkpoint.json").exists()
        )
        runtime = build_s4_phase_runtime(
            spec=spec,
            cache_paths=cache_paths,
            resume_capture=resume_capture,
        )
        await ensure_runtime_ready(SimpleNamespace(graphiti=runtime.graph))
        print(
            json.dumps(
                {
                    "stage": "S4",
                    "phase": spec["phase"],
                    "run_id": spec["run_id"],
                    "namespace": spec["namespace"],
                    "resume": resume_capture or (run_dir / "checkpoint.json").exists(),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return await run_s4_phase(
            spec=spec,
            episodes=episodes,
            graph=runtime.graph,
            episode_kwargs=runtime.episode_kwargs,
            namespace_probe=runtime.namespace_probe,
            graph_exporter=runtime.graph_exporter,
            runtime_evidence=runtime.runtime_evidence,
            cache_evidence=runtime.cache_evidence,
            cleanup_namespace=runtime.cleanup_namespace,
            artifact_root=args.artifact_root,
            expected_episode_count=49,
            git_commit=git_commit,
            event_sink=safe_event_sink,
        )

    result = await orchestrate_s4_smoke(
        authority=authority,
        execute_phase=execute_phase,
        evaluate=evaluate_s4_smoke,
    )
    evaluation = result["evaluation"]
    payload = {
        "schema_version": "membind.paper-eval-v3.s4-d0-smoke-result.v1",
        "stage": "S4",
        "verdict": evaluation["verdict"],
        "authority_file_sha256": authority_file_sha,
        "authority_consumption_file_sha256": sha256_file(args.consumption),
        "capture_result_file_sha256": sha256_file(
            args.artifact_root
            / authority["payload"]["runs"]["U0_CAPTURE"]["run_id"]
            / "phase_result.json"
        ),
        "replay_result_file_sha256": sha256_file(
            args.artifact_root
            / authority["payload"]["runs"]["D0_READ_ONLY_REPLAY"]["run_id"]
            / "phase_result.json"
        ),
        "evaluation": evaluation,
        "authority": {
            "s4_four_history_qualification_authorized": evaluation[
                "s4_four_history_qualification_authorized"
            ],
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        },
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id="s4-d0-smoke-result-20260814-001",
    )
    if args.result.exists():
        existing = json.loads(args.result.read_text(encoding="utf-8"))
        if existing != artifact:
            raise RuntimeError("S4 smoke result already exists with different evidence")
    else:
        _write_exclusive(args.result, artifact)
    print(
        json.dumps(
            {
                "stage": "S4",
                "verdict": evaluation["verdict"],
                "failures": evaluation["failures"],
                "result": str(args.result),
                "result_file_sha256": sha256_file(args.result),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return artifact


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--authority", type=Path, default=DEFAULT_AUTHORITY)
    value.add_argument("--consumption", type=Path, default=DEFAULT_CONSUMPTION)
    value.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    value.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    value.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    value.add_argument(
        "--artifact-root", type=Path, default=NATIVE / "runs"
    )
    return value


def main() -> None:
    artifact = asyncio.run(run_controller(parser().parse_args()))
    raise SystemExit(0 if artifact["payload"]["verdict"] == "PASS" else 2)


if __name__ == "__main__":
    main()
