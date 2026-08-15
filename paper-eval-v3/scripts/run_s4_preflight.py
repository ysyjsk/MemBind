#!/usr/bin/env python3
"""Run and seal the one bounded, read-only S4 service preflight."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s3_native_v2_freeze import verify_native_baseline_v2_freeze
from paper_eval.s4_d0_contract import verify_s4_d0_contract
from paper_eval.s4_preflight import finalize_s4_preflight
from paper_eval.s4_preflight_production import (
    execute_production_preflight,
    load_s4_preflight_env,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


async def _run(args: argparse.Namespace) -> int:
    contract = verify_s4_d0_contract(_load(args.contract))
    freeze = verify_native_baseline_v2_freeze(_load(args.native_freeze))
    if sha256_file(args.native_freeze) != contract[
        "native_baseline_v2_freeze_file_sha256"
    ]:
        raise RuntimeError("S4 preflight Native-v2 freeze binding drift")
    checkpoint_sha = sha256_file(args.s1_checkpoint)
    if checkpoint_sha != freeze["payload"]["native_construction"][
        "s1_checkpoint_sha256"
    ]:
        raise RuntimeError("S4 preflight S1 checkpoint binding drift")

    evaluation = await execute_production_preflight(
        env=load_s4_preflight_env(args.env),
        s1_checkpoint_path=args.s1_checkpoint,
    )
    print(
        json.dumps(
            {
                "stage": "S4_PREFLIGHT",
                "verdict": evaluation["verdict"],
                "failures": evaluation["failures"],
                "construction": evaluation["construction"],
                "embedding": evaluation["embedding"],
                "neo4j_connectivity": evaluation["neo4j_connectivity"],
                "namespace_checks": evaluation["namespace_checks"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if evaluation["verdict"] != "PASS":
        return 2
    artifact = finalize_s4_preflight(
        output_path=args.output,
        evaluation=evaluation,
        s4_contract_file_sha256=sha256_file(args.contract),
        s4_contract_sha256=contract["contract_sha256"],
        s1_checkpoint_file_sha256=checkpoint_sha,
        source_sha256={
            "preflight": sha256_file(PROJECT / "src/paper_eval/s4_preflight.py"),
            "production": sha256_file(
                PROJECT / "src/paper_eval/s4_preflight_production.py"
            ),
            "test": sha256_file(PROJECT / "tests/test_s4_preflight.py"),
        },
        git_commit=_git_commit(),
        run_id=args.run_id,
    )
    print(
        json.dumps(
            {
                "artifact": str(args.output),
                "artifact_file_sha256": sha256_file(args.output),
                "payload_sha256": artifact["payload_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument(
        "--env", type=Path, default=LEGACY / ".env"
    )
    value.add_argument(
        "--contract", type=Path, default=NATIVE / "S4_D0_CONTRACT.json"
    )
    value.add_argument(
        "--native-freeze",
        type=Path,
        default=NATIVE / "NATIVE_BASELINE_V2_FREEZE.json",
    )
    value.add_argument(
        "--s1-checkpoint",
        type=Path,
        default=NATIVE / "runs/s1-20260814-001/checkpoint.json",
    )
    value.add_argument(
        "--output", type=Path, default=NATIVE / "S4_PREFLIGHT.json"
    )
    value.add_argument("--run-id", default="s4-preflight-20260814-001")
    return value


def main() -> None:
    raise SystemExit(asyncio.run(_run(parser().parse_args())))


if __name__ == "__main__":
    main()
