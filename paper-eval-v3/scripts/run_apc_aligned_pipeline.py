#!/usr/bin/env python3
"""Wait for the exact smoke gate, then run full baselines, Quality v1, report."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
RUNS = PROJECT / "artifacts/paper_eval/apc_aligned_baseline/runs"


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact invalid: {path}")
    return value


def _payload_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _smoke_correctness(
    *, root: Path, run_id: str, block_index: int, block: dict[str, object]
) -> dict[str, object]:
    """Prefer a hash-bound post-crash checker amendment when one exists."""

    path = root / "CORRECTNESS_REMEASUREMENT.json"
    if not path.exists():
        value = block.get("correctness")
        if not isinstance(value, dict):
            raise ValueError("smoke correctness missing")
        return value
    amendment = _read(path)
    body = {key: value for key, value in amendment.items() if key != "payload_sha256"}
    if (
        amendment.get("payload_sha256") != _payload_sha256(body)
        or amendment.get("status") != "PASS"
        or amendment.get("run_id") != run_id
    ):
        raise ValueError("smoke correctness remeasurement invalid")
    entries = amendment.get("entries")
    if not isinstance(entries, list) or block_index >= len(entries):
        raise ValueError("smoke correctness remeasurement incomplete")
    entry = entries[block_index]
    if (
        not isinstance(entry, dict)
        or entry.get("block_index") != block_index
        or entry.get("method") != block.get("method")
        or entry.get("source_block_payload_sha256") != block.get("payload_sha256")
        or not isinstance(entry.get("correctness"), dict)
    ):
        raise ValueError("smoke correctness remeasurement binding invalid")
    return entry["correctness"]


def _wait_for_smoke(run_id: str) -> None:
    root = RUNS / run_id
    while True:
        failure = root / "FAILURE.json"
        disposition = root / "ATTEMPT_DISPOSITION.json"
        result = root / "PHASE_RESULT.json"
        if failure.exists() or disposition.exists():
            raise ValueError("smoke failed or was invalidated")
        if result.exists():
            phase = _read(result)
            if phase.get("status") != "PASS" or phase.get("phase") != "smoke":
                raise ValueError("smoke phase did not pass")
            break
        print(f"WAIT smoke_run_id={run_id}", flush=True)
        time.sleep(60)
    expected = ("U0-aligned", "A0-aligned", "P(C=2)-aligned")
    for index, method in enumerate(expected):
        block = _read(
            root / "blocks" / f"block-{index:02d}" / "APC_ALIGNED_BLOCK_RESULT.json"
        )
        correctness = _smoke_correctness(
            root=root, run_id=run_id, block_index=index, block=block
        )
        embedding = block.get("embedding_vllm_telemetry")
        if (
            block.get("status") != "PASS"
            or block.get("method") != method
            or not isinstance(correctness, dict)
            or correctness.get("checker_status") != "MEASURED"
            or not isinstance(embedding, dict)
            or int(embedding.get("sample_count", 0)) < 2
        ):
            raise ValueError(f"smoke gate failed for {method}")
    print(f"SMOKE_GATE_PASS run_id={run_id}", flush=True)


def _run(command: list[str]) -> None:
    print("EXEC " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-run-id", required=True)
    parser.add_argument("--full-run-id", required=True)
    parser.add_argument("--quality-run-id", required=True)
    args = parser.parse_args()
    python = str(ROOT / "membind-validation/.venv/bin/python")
    _wait_for_smoke(args.smoke_run_id)
    _run(
        [
            python,
            str(PROJECT / "scripts/run_apc_aligned_baselines.py"),
            args.full_run_id,
            "--phase",
            "full",
        ]
    )
    targets = RUNS / args.full_run_id / "QUALITY_TARGETS.json"
    _run(
        [
            python,
            str(PROJECT / "scripts/run_quality_evaluation_v1.py"),
            args.quality_run_id,
            "--target-manifest",
            str(targets),
        ]
    )
    _run(
        [
            python,
            str(PROJECT / "scripts/write_apc_aligned_report.py"),
            args.full_run_id,
            "--quality-run-id",
            args.quality_run_id,
        ]
    )
    print(
        f"PIPELINE_PASS baseline={args.full_run_id} quality={args.quality_run_id}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as error:
        print(
            f"PIPELINE_STOP error_class={type(error).__module__}.{type(error).__qualname__} "
            f"message={str(error)[:500]}",
            flush=True,
        )
        raise
