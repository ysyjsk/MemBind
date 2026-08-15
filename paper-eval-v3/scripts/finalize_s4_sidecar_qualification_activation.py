#!/usr/bin/env python3
"""Strictly verify retry-008 PASS and activate the sealed fixed-four plan."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_sidecar_qualification_activation import (
    build_s4_sidecar_qualification_activation,
    finalize_s4_sidecar_qualification_activation,
    verify_s4_sidecar_qualification_activation_external,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
PLAN = NATIVE / "S4_D0_QUALIFICATION_PLAN.json"
SMOKE = NATIVE / "S4_D0_SIDECAR_SMOKE_RESULT_RETRY_008.json"
AUTHORITY = NATIVE / "S4_SIDECAR_SMOKE_AUTHORIZATION_RETRY_008.json"
CONSUMPTION = (
    NATIVE
    / "runs/s4-sidecar-smoke-retry-008/S4_SIDECAR_AUTHORITY_CONSUMPTION.json"
)
CAPTURE_RUN = NATIVE / "runs/s4-d0-capture-20260815-008"
REPLAY_RUN = NATIVE / "runs/s4-d0-replay-20260815-008"
CAPTURE = CAPTURE_RUN / "phase_result.json"
REPLAY = REPLAY_RUN / "phase_result.json"
CAPTURE_CHECKPOINT = CAPTURE_RUN / "checkpoint.json"
CAPTURE_EVENTS = CAPTURE_RUN / "events.jsonl"
REPLAY_CHECKPOINT = REPLAY_RUN / "checkpoint.json"
REPLAY_EVENTS = REPLAY_RUN / "events.jsonl"
PRIVATE_CACHE = (
    PROJECT / "runtime/private/s4-d0-sidecar-07741c45-20260815-008"
)
SIDECAR = PRIVATE_CACHE / "candidate-sidecar.jsonl"
PROMPT_CACHE = PRIVATE_CACHE / "prompt.jsonl"
EMBEDDING_CACHE = PRIVATE_CACHE / "embedding.jsonl"
OUTPUT = NATIVE / "S4_D0_QUALIFICATION_ACTIVATION_SIDECAR_V3.json"
SOURCES = {
    "activation": PROJECT
    / "src/paper_eval/s4_sidecar_qualification_activation.py",
    "finalizer": PROJECT
    / "scripts/finalize_s4_sidecar_qualification_activation.py",
    "smoke_result_verifier": PROJECT
    / "src/paper_eval/s4_sidecar_smoke_result_verifier.py",
    "test": PROJECT / "tests/test_s4_sidecar_qualification_activation.py",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = build_s4_sidecar_qualification_activation(
        qualification_plan=_load(PLAN),
        qualification_plan_file_sha256=sha256_file(PLAN),
        smoke_result=_load(SMOKE),
        smoke_result_file_sha256=sha256_file(SMOKE),
        authority=_load(AUTHORITY),
        authority_file_sha256=sha256_file(AUTHORITY),
        consumption=_load(CONSUMPTION),
        consumption_file_sha256=sha256_file(CONSUMPTION),
        capture_result=_load(CAPTURE),
        capture_result_file_sha256=sha256_file(CAPTURE),
        replay_result=_load(REPLAY),
        replay_result_file_sha256=sha256_file(REPLAY),
        candidate_sidecar_file_sha256=sha256_file(SIDECAR),
        source_sha256={
            name: sha256_file(path) for name, path in SOURCES.items()
        },
        git_commit=git_commit,
    )
    verified = verify_s4_sidecar_qualification_activation_external(
        value=artifact,
        qualification_plan_path=PLAN,
        smoke_result_path=SMOKE,
        authority_path=AUTHORITY,
        consumption_path=CONSUMPTION,
        capture_result_path=CAPTURE,
        replay_result_path=REPLAY,
        candidate_sidecar_path=SIDECAR,
        prompt_cache_path=PROMPT_CACHE,
        embedding_cache_path=EMBEDDING_CACHE,
        capture_checkpoint_path=CAPTURE_CHECKPOINT,
        capture_events_path=CAPTURE_EVENTS,
        replay_checkpoint_path=REPLAY_CHECKPOINT,
        replay_events_path=REPLAY_EVENTS,
        source_paths=SOURCES,
    )
    finalized = finalize_s4_sidecar_qualification_activation(
        path=OUTPUT,
        artifact=verified,
    )
    print(
        json.dumps(
            {
                "path": str(OUTPUT),
                "file_sha256": sha256_file(OUTPUT),
                "payload_sha256": finalized["payload_sha256"],
                "authority": finalized["payload"]["authority"],
                "live_history_ids": finalized["payload"][
                    "activated_projection"
                ]["live_history_ids"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
