#!/usr/bin/env python3
"""Recover DVSR ready/need window fields from an existing sealed V6 block."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "saturated_fixed_work_baseline_v1_3/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_window import (  # noqa: E402
    recover_frozen_v6_window_fields,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("ascii")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    block = args.block.resolve()
    paths = {
        "frontier.jsonl": block / "frontier.jsonl",
        "native_trace.jsonl": block / "native_trace.jsonl",
        "raw_events.jsonl": block / "raw_events.jsonl",
        "construction_seal.json": block / "construction_seal.json",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"sealed V6 block is missing: {','.join(missing)}")
    result = recover_frozen_v6_window_fields(
        frontier_events=_jsonl(paths["frontier.jsonl"]),
        native_trace_envelopes=_jsonl(paths["native_trace.jsonl"]),
        raw_events=_jsonl(paths["raw_events.jsonl"]),
    )
    artifact = {
        **result,
        "evidence_role": "EXISTING_SEALED_FROZEN_V6_FIELD_RECOVERY",
        "input_provenance": {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in paths.items()
        },
        "phase3_scaling_authorized": False,
    }
    _write(args.output.resolve(), artifact)
    print(
        json.dumps(
            {
                "status": artifact["status"],
                "source_pair_count": artifact["source_pair_count"],
                "complete_pair_count": artifact["complete_pair_count"],
                "cross_snapshot_launch_eligible_count": artifact[
                    "cross_snapshot_launch_eligible_count"
                ],
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
