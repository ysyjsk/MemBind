#!/usr/bin/env python3
"""Deterministically upgrade pre-contract Native Level-1 phase counts.

The command reads only one fixed Native attempt directory and rewrites the
derived ``per_episode_metrics.jsonl`` from its immutable span counts.  It does
not contact Graphiti, model services, or Neo4j, and it records before/after
hashes so the migration remains auditable.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from paper_eval.artifacts import atomic_write_json, canonical_bytes, payload_sha256, sha256_file
from paper_eval.native_baseline_runner import (
    build_native_baseline_plan,
    upgrade_episode_phase_span_counts,
    verify_checkpoint,
)


PROJECT = Path(__file__).resolve().parents[1]
RUN_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"artifact_not_object:{path.name}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="ascii").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(f"jsonl_row_not_object:{path.name}")
        rows.append(value)
    return rows


def _atomic_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    payload = b"".join(canonical_bytes(dict(row)) + b"\n" for row in rows)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--history-id", required=True)
    args = parser.parse_args()
    plan = build_native_baseline_plan(args.run_id)
    matches = [item for item in plan.histories if item.history_id == args.history_id]
    if len(matches) != 1:
        raise SystemExit("--history-id is not in the fixed development plan")
    history = matches[0]
    root = RUN_ROOT / args.run_id / history.history_id
    checkpoint = verify_checkpoint(_json(root / "checkpoint.json"))
    if (
        checkpoint["namespace"] != history.namespace
        or checkpoint["completed_sequences"] != checkpoint["expected_sequences"]
    ):
        raise RuntimeError("level1_upgrade_requires_complete_exact_prefix")

    metrics_path = root / "per_episode_metrics.jsonl"
    spans_path = root / "spans.jsonl"
    evidence_path = root / "LEVEL1_UPGRADE.json"
    if evidence_path.exists():
        raise RuntimeError("level1_upgrade_evidence_already_exists")
    before_hash = sha256_file(metrics_path)
    spans_hash = sha256_file(spans_path)
    upgraded = upgrade_episode_phase_span_counts(
        _jsonl(metrics_path),
        _jsonl(spans_path),
    )
    _atomic_jsonl(metrics_path, upgraded)
    body = {
        "schema_version": "membind.paper-eval-v3.native-level1-upgrade.v1",
        "run_id": args.run_id,
        "history_id": history.history_id,
        "namespace": history.namespace,
        "status": "completed",
        "operation": "derive_phase_span_count_from_level0_spans",
        "model_or_database_calls": 0,
        "row_count": len(upgraded),
        "source_spans_sha256": spans_hash,
        "per_episode_before_sha256": before_hash,
        "per_episode_after_sha256": sha256_file(metrics_path),
    }
    body["payload_sha256"] = payload_sha256(body)
    atomic_write_json(evidence_path, body)
    print(json.dumps(body, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
