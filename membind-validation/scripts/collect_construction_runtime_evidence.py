#!/usr/bin/env python3
"""Collect checkpoint hashes and non-secret vLLM argv on the model host."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from construction_identity import (  # noqa: E402
    collect_directory_manifest,
    collect_vllm_process_evidence,
    compare_deployment_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hash the frozen construction checkpoint and vLLM launch contract."
    )
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--expected-manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected = json.loads(args.expected_manifest.read_text(encoding="utf-8"))
    actual = collect_directory_manifest(
        args.model_root,
        expected_paths=[item["path"] for item in expected["files"]],
    )
    comparison = compare_deployment_manifest(expected["files"], actual["files"])
    try:
        vllm_version = importlib.metadata.version("vllm")
    except importlib.metadata.PackageNotFoundError:
        vllm_version = None
    result = {
        "schema_version": "membind.construction_runtime_evidence.v1",
        "expected_repository": expected["repository"],
        "expected_revision": expected["revision"],
        "expected_manifest_fingerprint": expected["manifest_fingerprint"],
        "model_root_realpath": actual["root_realpath"],
        "actual_files": actual["files"],
        "actual_manifest_fingerprint": actual["manifest_fingerprint"],
        "missing_paths": actual["missing_paths"],
        "comparison": comparison,
        "vllm_version": vllm_version,
        "vllm_processes": collect_vllm_process_evidence(
            args.proc_root,
            port=args.port,
        ),
        "secrets_persisted": False,
    }
    encoded = json.dumps(
        result,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(encoded)
    sys.stdout.write(encoded)
    return 0 if comparison["exact_match"] and result["vllm_processes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
