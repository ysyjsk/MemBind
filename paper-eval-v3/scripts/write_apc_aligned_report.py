#!/usr/bin/env python3
"""Reduce sealed APC baseline blocks and optional frozen Quality v1 output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.apc_aligned_report import (
    reduce_apc_aligned_results,
    render_apc_aligned_markdown,
)
from paper_eval.artifacts import atomic_write_json, payload_sha256


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact invalid: {path}")
    digest = value.get("payload_sha256")
    if digest is not None and digest != payload_sha256(
        {key: item for key, item in value.items() if key != "payload_sha256"}
    ):
        raise ValueError(f"artifact hash mismatch: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_run_id")
    parser.add_argument("--quality-run-id")
    args = parser.parse_args()
    run_root = (
        PROJECT
        / "artifacts/paper_eval/apc_aligned_baseline/runs"
        / args.baseline_run_id
    )
    blocks = [
        _read(
            run_root
            / "blocks"
            / f"block-{index:02d}"
            / "APC_ALIGNED_BLOCK_RESULT.json"
        )
        for index in range(12)
    ]
    quality = None
    if args.quality_run_id:
        quality = _read(
            PROJECT
            / "artifacts/paper_eval/quality_evaluation_v1/runs"
            / args.quality_run_id
            / "QUALITY_EVALUATION_V1_RESULTS.json"
        )
    report = reduce_apc_aligned_results(blocks=blocks, quality_report=quality)
    report = {**report, "baseline_run_id": args.baseline_run_id}
    if args.quality_run_id:
        report["quality_run_id"] = args.quality_run_id
    report["payload_sha256"] = payload_sha256(report)
    atomic_write_json(run_root / "APC_ALIGNED_BASELINE_REPORT.json", report)
    markdown = render_apc_aligned_markdown(report)
    (run_root / "APC_ALIGNED_BASELINE_REPORT.md").write_text(
        markdown, encoding="utf-8"
    )
    (ROOT / "APC_ALIGNED_THREE_BASELINE_REPORT.md").write_text(
        markdown
        + "\nArtifacts\n\n"
        + f"- Baseline run: `{run_root}`\n"
        + (
            f"- Quality run: `paper-eval-v3/artifacts/paper_eval/quality_evaluation_v1/runs/{args.quality_run_id}`\n"
            if args.quality_run_id
            else "- Quality run: pending\n"
        ),
        encoding="utf-8",
    )
    print(json.dumps({"status": report["status"], "payload_sha256": report["payload_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
