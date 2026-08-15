"""Checkpointed controller for the authorized S4 candidate-remap smoke."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, sha256_file
from .s1_controller import ensure_runtime_ready
from .s4_controller import (
    _legacy_episodes,
    compose_phase_specs,
    orchestrate_s4_smoke,
    resolve_private_cache_paths,
)
from .s4_d0_production import build_s4_phase_runtime
from .s4_d0_runner import evaluate_s4_smoke, run_s4_phase
from .s4_remap_authority import (
    consume_s4_remap_authority,
    verify_s4_remap_authority,
    verify_s4_remap_authority_consumption,
)


ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "paper-eval-v3"
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
DEFAULT_SPLIT = LEGACY / "artifacts/dataset/frozen_split.json"
DEFAULT_AUTHORITY = NATIVE / "S4_REMAP_SMOKE_AUTHORIZATION_RETRY_005.json"
DEFAULT_CONSUMPTION = (
    NATIVE
    / "runs/s4-remap-smoke-retry-005/S4_REMAP_AUTHORITY_CONSUMPTION.json"
)
DEFAULT_RESULT = NATIVE / "S4_D0_REMAP_SMOKE_RESULT.json"


def safe_event_sink(event: Mapping[str, Any]) -> None:
    """Print progress identity and fixed failure classification only."""

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


def _verify_authority_sources(authority: Mapping[str, Any]) -> None:
    expected = {
        "authority": sha256_file(PROJECT / "src/paper_eval/s4_remap_authority.py"),
        "candidate_oracle": sha256_file(
            PROJECT / "src/paper_eval/s4_candidate_oracle.py"
        ),
        "controller": sha256_file(
            PROJECT / "src/paper_eval/s4_remap_controller.py"
        ),
        "production": sha256_file(PROJECT / "src/paper_eval/s4_d0_production.py"),
        "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
        "test": sha256_file(PROJECT / "tests/test_s4_remap_controller.py"),
    }
    payload = dict(authority.get("payload", {}))
    if payload.get("source_sha256") != dict(sorted(expected.items())):
        raise RuntimeError("S4 remap controller source binding drift")


def _sha(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} is not a SHA256")
    return value


def build_remap_result_payload(
    *,
    evaluation: Mapping[str, Any],
    authority_file_sha256: str,
    authority_consumption_file_sha256: str,
    capture_result_file_sha256: str,
    replay_result_file_sha256: str,
) -> dict[str, Any]:
    selected = dict(evaluation)
    remap_count = selected.get("candidate_remap_hit_count")
    if (
        selected.get("verdict") != "PASS"
        or selected.get("failures") != []
        or selected.get("canonical_graph_parity") is not True
        or selected.get("cache_mutation_during_replay") is not False
        or selected.get("candidate_oracle_resolution_accounting") is not True
        or not isinstance(remap_count, int)
        or isinstance(remap_count, bool)
        or remap_count < 0
        or selected.get("candidate_remap_used") is not (remap_count > 0)
        or selected.get("s4_four_history_qualification_authorized") is not True
        or selected.get("s5_authorized") is not False
    ):
        raise ValueError("S4 remap result evaluation is not a complete PASS")
    return {
        "schema_version": "membind.paper-eval-v3.s4-d0-remap-smoke-result.v2",
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
    if not consumption_path.exists():
        return consume_s4_remap_authority(
            authority=authority,
            authority_file_sha256=authority_file_sha,
            output_path=consumption_path,
            git_commit=git_commit,
            run_id="s4-remap-authority-consumption-20260815-005",
        )
    existing = verify_s4_remap_authority_consumption(
        json.loads(consumption_path.read_text(encoding="utf-8"))
    )
    payload = existing["payload"]
    if (
        payload["authority_file_sha256"] != authority_file_sha
        or payload["authority_payload_sha256"] != authority["payload_sha256"]
    ):
        raise ValueError("S4 remap consumption does not match this resume")
    return existing


async def run_controller(args: argparse.Namespace) -> dict[str, Any]:
    authority = verify_s4_remap_authority(
        json.loads(args.authority.read_text(encoding="utf-8"))
    )
    _verify_authority_sources(authority)
    compose_phase_specs(authority)
    git_commit = _git_commit()
    _consume_or_resume(
        authority=authority,
        authority_path=args.authority,
        consumption_path=args.consumption,
        git_commit=git_commit,
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
                    "stage": "S4_REMAP_SMOKE",
                    "phase": spec["phase"],
                    "run_id": spec["run_id"],
                    "namespace": spec["namespace"],
                    "resume": resume_capture
                    or (run_dir / "checkpoint.json").exists(),
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
    capture_run = authority["payload"]["runs"]["U0_CAPTURE"]["run_id"]
    replay_run = authority["payload"]["runs"]["D0_READ_ONLY_REPLAY"]["run_id"]
    payload = build_remap_result_payload(
        evaluation=result["evaluation"],
        authority_file_sha256=sha256_file(args.authority),
        authority_consumption_file_sha256=sha256_file(args.consumption),
        capture_result_file_sha256=sha256_file(
            args.artifact_root / capture_run / "phase_result.json"
        ),
        replay_result_file_sha256=sha256_file(
            args.artifact_root / replay_run / "phase_result.json"
        ),
    )
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id="s4-d0-remap-smoke-result-20260815-005",
    )
    if args.result.exists():
        existing = json.loads(args.result.read_text(encoding="utf-8"))
        if existing != artifact:
            raise RuntimeError("S4 remap result exists with different evidence")
    else:
        _write_exclusive(args.result, artifact)
    print(
        json.dumps(
            {
                "stage": "S4_REMAP_SMOKE",
                "verdict": payload["verdict"],
                "candidate_remap_hit_count": payload["evaluation"][
                    "candidate_remap_hit_count"
                ],
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
    value.add_argument("--artifact-root", type=Path, default=NATIVE / "runs")
    return value


def main() -> None:
    artifact = asyncio.run(run_controller(parser().parse_args()))
    raise SystemExit(0 if artifact["payload"]["verdict"] == "PASS" else 2)


if __name__ == "__main__":
    main()
