#!/usr/bin/env python3
"""Resume a partial MemOps replication at the sealed-sample boundary.

This is an orchestration/recovery helper.  It invokes the existing
sample-level runner and never changes B0/B1 construction semantics.  Existing
sealed outputs are copied into a new aggregate result; incomplete samples are
run in fresh namespaces under the recovery root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def payload(value: dict[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body["payload_sha256"] = hashlib.sha256(canonical_bytes(body)).hexdigest()
    return body


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def write_checkpoint(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--recovery-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--method", choices=("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC"), required=True)
    parser.add_argument("--ordinal", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--sample-timeout-s", type=float, default=180.0)
    args = parser.parse_args()

    audit_root = args.audit_root.resolve()
    source_root = args.source_root.resolve()
    recovery_root = args.recovery_root.resolve()
    source_result_path = source_root / "replication_result.json"
    if not source_result_path.is_file():
        raise RuntimeError(f"SOURCE_REPLICATION_RESULT_MISSING:{source_result_path}")
    source_result = read_json(source_result_path)
    if source_result.get("method") != args.method or int(source_result.get("replication_ordinal", -1)) != args.ordinal:
        raise RuntimeError("SOURCE_REPLICATION_IDENTITY_MISMATCH")

    cohort_manifest = read_json(audit_root / "replication_cohort" / "selection_manifest.json")
    rows = cohort_manifest.get("samples")
    if not isinstance(rows, list) or len(rows) != 24:
        raise RuntimeError("HAZARD_COHORT_NOT_24_SAMPLES")
    sample_ids = [str(row["sample_id"]) for row in rows]
    source_outputs = [row for row in source_result.get("outputs", []) if isinstance(row, dict)]
    source_output_ids = {str(row.get("sample_id")) for row in source_outputs}

    recovery_root.mkdir(parents=True, exist_ok=True)
    checkpoint_path = recovery_root / "checkpoint_state.json"
    if checkpoint_path.is_file():
        checkpoint = read_json(checkpoint_path)
    else:
        checkpoint = payload(
            {
                "schema_version": "sfwb.v1.3.memops-hazard-recovery-checkpoint.v1",
                "method": args.method,
                "replication_ordinal": args.ordinal,
                "source_root": str(source_root),
                "sample_ids": sample_ids,
                "reused_source_sample_ids": sorted(source_output_ids),
                "attempts": [],
                "v5_started": False,
            }
        )
        write_checkpoint(checkpoint_path, checkpoint)

    completed_recovery_ids = {
        str(row.get("sample_id"))
        for row in checkpoint.get("attempts", [])
        if isinstance(row, dict) and row.get("status") in {"LIVE_COMPLETE", "LIVE_PARTIAL", "FAILED"}
    }
    runner = args.repository_root.resolve() / "saturated_fixed_work_baseline_v1_3/scripts/run_memops_hazard_sample_replication.py"
    python_bin = args.repository_root.resolve() / "membind-validation/.venv/bin/python"
    pythonpath = os.pathsep.join(
        [
            str(args.repository_root.resolve() / "saturated_fixed_work_baseline_v1_3/src"),
            str(args.repository_root.resolve() / "saturated_fixed_work_baseline_v1_2/src"),
            str(args.repository_root.resolve() / "saturated_fixed_work_baseline_v1_3/scripts"),
        ]
    )
    recovery_outputs: list[dict[str, Any]] = []
    recovery_failures: list[dict[str, Any]] = []

    missing_ids = [sample_id for sample_id in sample_ids if sample_id not in source_output_ids]
    for sample_order, sample_id in enumerate(missing_ids, 1):
        if sample_id in completed_recovery_ids:
            root = recovery_root / f"s{sample_order:03d}"
            result_path = root / "replication_result.json"
            if result_path.is_file():
                previous = read_json(result_path)
                recovery_outputs.extend(row for row in previous.get("outputs", []) if isinstance(row, dict))
                recovery_failures.extend(row for row in previous.get("failures", []) if isinstance(row, dict))
            continue

        sample_root = recovery_root / f"s{sample_order:03d}"
        command = [
            str(python_bin),
            str(runner),
            "--audit-root",
            str(audit_root),
            "--replication-root",
            str(sample_root),
            "--repository-root",
            str(args.repository_root.resolve()),
            "--method",
            args.method,
            "--ordinal",
            str(args.ordinal),
            "--sample-id",
            sample_id,
            "--sample-timeout-s",
            str(args.sample_timeout_s),
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = pythonpath
        print(json.dumps({"event": "RECOVERY_SAMPLE_START", "sample_id": sample_id, "root": str(sample_root)}), flush=True)
        try:
            completed = subprocess.run(command, cwd=str(args.repository_root.resolve()), env=env, check=False)
            result_path = sample_root / "replication_result.json"
            if not result_path.is_file():
                raise RuntimeError(f"RECOVERY_RESULT_MISSING:rc={completed.returncode}")
            result = read_json(result_path)
            recovery_outputs.extend(row for row in result.get("outputs", []) if isinstance(row, dict))
            recovery_failures.extend(row for row in result.get("failures", []) if isinstance(row, dict))
            status = str(result.get("status") or "LIVE_PARTIAL")
        except Exception as exc:
            recovery_failures.append({"sample_id": sample_id, "recovery_root": str(sample_root), "error_type": type(exc).__name__, "error": str(exc)})
            status = "FAILED"
        checkpoint.setdefault("attempts", []).append({"sample_id": sample_id, "root": str(sample_root), "status": status})
        checkpoint = payload(checkpoint)
        write_checkpoint(checkpoint_path, checkpoint)
        print(json.dumps({"event": "RECOVERY_SAMPLE_DONE", "sample_id": sample_id, "status": status}), flush=True)

    combined_by_sample: dict[str, dict[str, Any]] = {str(row.get("sample_id")): row for row in source_outputs}
    combined_by_sample.update({str(row.get("sample_id")): row for row in recovery_outputs})
    combined_outputs = [combined_by_sample[sample_id] for sample_id in sample_ids if sample_id in combined_by_sample]
    combined_output_ids = {str(row.get("sample_id")) for row in combined_outputs}
    failures_by_sample: dict[str, dict[str, Any]] = {}
    for row in source_result.get("failures", []):
        if isinstance(row, dict):
            failures_by_sample.setdefault(str(row.get("sample_id")), row)
    for row in recovery_failures:
        if isinstance(row, dict):
            failures_by_sample[str(row.get("sample_id"))] = row
    combined_failures = [failures_by_sample[sample_id] for sample_id in sample_ids if sample_id not in combined_output_ids and sample_id in failures_by_sample]
    combined = payload(
        {
            "schema_version": "sfwb.v1.3.memops-hazard-recovered-replication-result.v1",
            "status": "LIVE_COMPLETE" if len(combined_outputs) == len(sample_ids) and not combined_failures else "LIVE_PARTIAL",
            "method": args.method,
            "replication_ordinal": args.ordinal,
            "sample_ids": sample_ids,
            "outputs": combined_outputs,
            "failures": combined_failures,
            "sample_count": len(sample_ids),
            "completed_output_count": len(combined_outputs),
            "source_replication_root": str(source_root),
            "recovery_root": str(recovery_root),
            "reused_source_sample_count": len(source_output_ids),
            "recovered_sample_count": len(recovery_outputs),
            "v5_started": False,
        }
    )
    (recovery_root / "recovered_replication_result.json").write_text(json.dumps(combined, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "RECOVERY_COMPLETE", "status": combined["status"], "completed_output_count": len(combined_outputs), "failure_count": len(combined_failures)}), flush=True)
    return 0 if not combined_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
