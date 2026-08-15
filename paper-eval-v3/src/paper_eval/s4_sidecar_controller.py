"""Checkpointed controller for an authorized S4 bilateral-sidecar retry."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, sha256_file
from .s1_controller import ensure_runtime_ready
from .s4_candidate_projection import PROJECTION_SCHEMA_SHA256
from .s4_controller import (
    _legacy_episodes,
    compose_phase_specs,
    orchestrate_s4_smoke,
    resolve_private_cache_paths,
)
from .s4_d0_production import (
    S4CandidateSidecarConfig,
    build_s4_phase_runtime,
)
from .s4_d0_runner import run_s4_phase
from .s4_edge_identity_diagnosis_production import build_episode_manifest
from .s4_sidecar_authority import (
    consume_s4_sidecar_authority,
    verify_s4_sidecar_authority,
    verify_s4_sidecar_authority_consumption,
)
from .s4_sidecar_result import evaluate_s4_sidecar_smoke


ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "paper-eval-v3"
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
DEFAULT_SPLIT = LEGACY / "artifacts/dataset/frozen_split.json"
DEFAULT_AUTHORITY = NATIVE / "S4_SIDECAR_SMOKE_AUTHORIZATION_RETRY_006.json"
DEFAULT_CONSUMPTION = (
    NATIVE
    / "runs/s4-sidecar-smoke-retry-006/S4_SIDECAR_AUTHORITY_CONSUMPTION.json"
)
DEFAULT_RESULT = NATIVE / "S4_D0_SIDECAR_SMOKE_RESULT.json"


def _sha(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} is not a SHA256")
    return value


def safe_event_sink(event: Mapping[str, Any]) -> None:
    """Print only progress identity and fixed failure classifications."""

    allowed = {
        key: event[key]
        for key in (
            "event_type",
            "source_sequence",
            "timestamp_ns",
            "error_class",
            "error_code",
            "failure_stage",
            "completed_count",
            "status",
        )
        if key in event
    }
    print(json.dumps(allowed, sort_keys=True), flush=True)


def _attempt_from_runs(runs: Mapping[str, Any]) -> str:
    selected = dict(runs)
    attempts: set[str] = set()
    for phase, prefix in (
        ("U0_CAPTURE", "s4-d0-capture-20260815-"),
        ("D0_READ_ONLY_REPLAY", "s4-d0-replay-20260815-"),
    ):
        run = selected.get(phase)
        run_id = run.get("run_id") if isinstance(run, Mapping) else None
        if not isinstance(run_id, str) or not run_id.startswith(prefix):
            raise ValueError("S4 sidecar run attempt identity drift")
        attempt = run_id.removeprefix(prefix)
        if re.fullmatch(r"\d{3}", attempt) is None or int(attempt) < 6:
            raise ValueError("S4 sidecar run attempt identity drift")
        attempts.add(attempt)
    if len(attempts) != 1:
        raise ValueError("S4 sidecar phase attempts disagree")
    return attempts.pop()


def _verify_authority_sources(authority: Mapping[str, Any]) -> None:
    payload = authority.get("payload", {})
    attempt = _attempt_from_runs(payload.get("runs", {}))
    expected = {
        "authority": sha256_file(PROJECT / "src/paper_eval/s4_sidecar_authority.py"),
        "candidate_oracle": sha256_file(
            PROJECT / "src/paper_eval/s4_candidate_oracle.py"
        ),
        "candidate_projection": sha256_file(
            PROJECT / "src/paper_eval/s4_candidate_projection.py"
        ),
        "candidate_sidecar": sha256_file(
            PROJECT / "src/paper_eval/s4_candidate_sidecar.py"
        ),
        "candidate_sidecar_runtime": sha256_file(
            PROJECT / "src/paper_eval/s4_candidate_sidecar_runtime.py"
        ),
        "controller": sha256_file(
            PROJECT / "src/paper_eval/s4_sidecar_controller.py"
        ),
        "production": sha256_file(PROJECT / "src/paper_eval/s4_d0_production.py"),
        "result": sha256_file(PROJECT / "src/paper_eval/s4_sidecar_result.py"),
        "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
        "test": sha256_file(PROJECT / "tests/test_s4_sidecar_controller.py"),
    }
    if int(attempt) >= 7:
        expected["edge_identity"] = sha256_file(
            PROJECT / "src/paper_eval/s4_edge_identity_diagnosis.py"
        )
    if payload.get("source_sha256") != dict(sorted(expected.items())):
        raise RuntimeError("S4 sidecar controller source binding drift")


def _sidecar_config(
    *,
    authority: Mapping[str, Any],
    spec: Mapping[str, Any],
    episodes: Sequence[Any],
    edge_operations_module: Any,
    project_root: Path = PROJECT,
) -> S4CandidateSidecarConfig:
    payload = dict(authority.get("payload", {}))
    history = dict(payload.get("history", {}))
    private = dict(payload.get("private_cache", {}))
    runs = dict(payload.get("runs", {}))
    attempt = _attempt_from_runs(runs)
    phase = spec.get("phase")
    expected_run = runs.get(phase) if isinstance(phase, str) else None
    if not isinstance(expected_run, Mapping) or any(
        spec.get(field) != expected
        for field, expected in expected_run.items()
    ):
        raise ValueError("S4 sidecar phase spec is outside authority")
    if (
        history
        != {
            "data_role": "DEVELOPMENT_EXPOSED",
            "episode_count": 49,
            "history_id": "07741c45",
        }
        or payload.get("projection_schema_sha256") != PROJECTION_SCHEMA_SHA256
        or private.get("reportable_contents") is not False
    ):
        raise ValueError("S4 sidecar authority identity drift")
    _, manifest_sha = build_episode_manifest(episodes)
    project = Path(project_root).resolve()
    allowed = (project / "runtime/private").resolve()
    raw_path = private.get("candidate_sidecar_relpath")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("S4 candidate sidecar path is missing")
    selected_path = (project / raw_path).resolve()
    if not selected_path.is_relative_to(allowed):
        raise ValueError("S4 candidate sidecar path escaped runtime/private")
    return S4CandidateSidecarConfig(
        path=selected_path,
        identity={
            "attempt_id": attempt,
            "cache_id": str(spec["cache_id"]),
            "episode_manifest_sha256": manifest_sha,
            "history_id": "07741c45",
            "projection_schema_sha256": PROJECTION_SCHEMA_SHA256,
        },
        episodes=episodes,
        edge_operations_module=edge_operations_module,
        namespace=str(spec["namespace"]),
    )


def build_sidecar_result_payload(
    *,
    evaluation: Mapping[str, Any],
    authority_file_sha256: str,
    authority_consumption_file_sha256: str,
    capture_result_file_sha256: str,
    replay_result_file_sha256: str,
    candidate_sidecar_file_sha256: str,
) -> dict[str, Any]:
    selected = dict(evaluation)
    if (
        selected.get("verdict") != "PASS"
        or selected.get("failures") != []
        or selected.get("canonical_graph_parity") is not True
        or selected.get("cache_and_sidecar_mutation_during_replay") is not False
        or selected.get("sidecar_consumption_exact") is not True
        or selected.get("edge_sidecar_resolution_accounting") is not True
        or selected.get("s4_four_history_qualification_authorized") is not True
        or selected.get("s5_authorized") is not False
    ):
        raise ValueError("S4 sidecar result evaluation is not a complete PASS")
    return {
        "schema_version": "membind.paper-eval-v3.s4-d0-sidecar-smoke-result.v3",
        "stage": "S4",
        "verdict": "PASS",
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_consumption_file_sha256": _sha(
            authority_consumption_file_sha256, field="authority consumption"
        ),
        "capture_result_file_sha256": _sha(
            capture_result_file_sha256, field="capture result"
        ),
        "replay_result_file_sha256": _sha(
            replay_result_file_sha256, field="replay result"
        ),
        "candidate_sidecar_file_sha256": _sha(
            candidate_sidecar_file_sha256, field="candidate sidecar"
        ),
        "evaluation": selected,
        "authority": {
            "s4_four_history_qualification_authorized": True,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        },
    }


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _consume_or_resume(
    *,
    authority: Mapping[str, Any],
    authority_path: Path,
    consumption_path: Path,
    git_commit: str,
) -> dict[str, Any]:
    authority_file_sha = sha256_file(authority_path)
    attempt = _attempt_from_runs(authority["payload"]["runs"])
    if not consumption_path.exists():
        return consume_s4_sidecar_authority(
            authority=authority,
            authority_file_sha256=authority_file_sha,
            output_path=consumption_path,
            git_commit=git_commit,
            run_id=f"s4-sidecar-authority-consumption-20260815-{attempt}",
        )
    existing = verify_s4_sidecar_authority_consumption(
        json.loads(consumption_path.read_text(encoding="utf-8"))
    )
    payload = existing["payload"]
    if (
        payload["authority_file_sha256"] != authority_file_sha
        or payload["authority_payload_sha256"] != authority["payload_sha256"]
    ):
        raise ValueError("S4 sidecar consumption does not match this resume")
    return existing


async def run_controller(args: argparse.Namespace) -> dict[str, Any]:
    authority = verify_s4_sidecar_authority(
        json.loads(args.authority.read_text(encoding="utf-8"))
    )
    _verify_authority_sources(authority)
    compose_phase_specs(authority)
    attempt = _attempt_from_runs(authority["payload"]["runs"])
    git_commit = _git_commit()
    _consume_or_resume(
        authority=authority,
        authority_path=args.authority,
        consumption_path=args.consumption,
        git_commit=git_commit,
    )
    episodes = _legacy_episodes(args.dataset, args.split)
    cache_paths = resolve_private_cache_paths(authority, project_root=PROJECT)
    from graphiti_core.utils.maintenance import edge_operations

    async def execute_phase(spec: Mapping[str, Any]) -> Mapping[str, Any]:
        run_dir = args.artifact_root / str(spec["run_id"])
        checkpoint_exists = (run_dir / "checkpoint.json").exists()
        runtime = build_s4_phase_runtime(
            spec=spec,
            cache_paths=cache_paths,
            resume_capture=spec["mode"] == "capture" and checkpoint_exists,
            sidecar=_sidecar_config(
                authority=authority,
                spec=spec,
                episodes=episodes,
                edge_operations_module=edge_operations,
            ),
        )
        await ensure_runtime_ready(SimpleNamespace(graphiti=runtime.graph))
        print(
            json.dumps(
                {
                    "stage": "S4_BILATERAL_SIDECAR_SMOKE",
                    "phase": spec["phase"],
                    "run_id": spec["run_id"],
                    "namespace": spec["namespace"],
                    "resume": checkpoint_exists,
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
            episode_scope=lambda _item: runtime.phase_context(),
            restore_prefix=runtime.restore_sidecar_prefix,
            pre_cleanup_finalize=lambda: runtime.pre_finalize_sidecar(
                runtime.cache_evidence()
            ),
        )

    result = await orchestrate_s4_smoke(
        authority=authority,
        execute_phase=execute_phase,
        evaluate=evaluate_s4_sidecar_smoke,
    )
    runs = authority["payload"]["runs"]
    capture_run = runs["U0_CAPTURE"]["run_id"]
    replay_run = runs["D0_READ_ONLY_REPLAY"]["run_id"]
    sidecar_path = _sidecar_config(
        authority=authority,
        spec={
            "phase": "D0_READ_ONLY_REPLAY",
            **runs["D0_READ_ONLY_REPLAY"],
        },
        episodes=episodes,
        edge_operations_module=edge_operations,
    ).path
    payload = build_sidecar_result_payload(
        evaluation=result["evaluation"],
        authority_file_sha256=sha256_file(args.authority),
        authority_consumption_file_sha256=sha256_file(args.consumption),
        capture_result_file_sha256=sha256_file(
            args.artifact_root / capture_run / "phase_result.json"
        ),
        replay_result_file_sha256=sha256_file(
            args.artifact_root / replay_run / "phase_result.json"
        ),
        candidate_sidecar_file_sha256=sha256_file(sidecar_path),
    )
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=f"s4-d0-sidecar-smoke-result-20260815-{attempt}",
    )
    if args.result.exists():
        if json.loads(args.result.read_text(encoding="utf-8")) != artifact:
            raise RuntimeError("S4 sidecar result exists with different evidence")
    else:
        _write_exclusive(args.result, artifact)
    print(
        json.dumps(
            {
                "stage": "S4_BILATERAL_SIDECAR_SMOKE",
                "verdict": payload["verdict"],
                "sidecar_record_count": payload["evaluation"][
                    "sidecar_record_count"
                ],
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
    value.add_argument("--artifact-root", type=Path, default=NATIVE / "runs")
    return value


def main() -> None:
    artifact = asyncio.run(run_controller(parser().parse_args()))
    raise SystemExit(0 if artifact["payload"]["verdict"] == "PASS" else 2)


if __name__ == "__main__":
    main()
