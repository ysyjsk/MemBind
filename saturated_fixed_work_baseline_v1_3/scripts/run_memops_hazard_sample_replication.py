#!/usr/bin/env python3
"""Run one frozen hazard sample at a time through the existing live entry point.

The current qualification helper deliberately short-circuits a B0 method after
its first diagnostic in a multi-sample root.  This wrapper keeps the existing
entry point and policy unchanged, but gives every sample its own fresh root so
one B0 diagnostic cannot suppress the remaining gold-defined samples or B1.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
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


def prepare_sample_root(
    *, cohort_root: Path, parent_root: Path, sample_row: dict[str, Any], sample_order: int, method: str
) -> Path:
    cohort_id = str(sample_row["sample_id"])
    run_prefix = f"{parent_root.parent.name}-{parent_root.name}".lower().replace("_", "-")
    root = parent_root / f"{run_prefix}-sample-{sample_order:03d}-{cohort_id.lower().replace('_', '-') }"
    if root.exists():
        raise RuntimeError(f"SAMPLE_ROOT_ALREADY_EXISTS:{root}")
    root.mkdir(parents=True)
    source_manifest = read_json(cohort_root / "selection_manifest.json")
    selection = {
        "schema_version": "sfwb.v1.3.memops-hazard-single-sample-selection.v1",
        "status": "OFFLINE_HAZARD_COHORT_FROZEN",
        "benchmark": "MemOps",
        "pilot_policy_set": [method],
        "sample_order": [cohort_id],
        "sample_count": 1,
        "samples": [sample_row],
        "official_qa_evaluator": source_manifest["official_qa_evaluator"],
        "selection_basis": source_manifest["selection_basis"],
        "parent_cohort_selection_manifest_sha256": source_manifest["payload_sha256"],
        "replication_contract": {
            "single_sample_subroot": True,
            "same_source_order": True,
            "b0_failure_does_not_gate_next_sample_or_b1": True,
            "v5_started": False,
        },
    }
    selection = payload(selection)
    (root / "selection_manifest.json").write_text(json.dumps(selection, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "frozen_samples").mkdir()
    shutil.copy2(cohort_root / "frozen_samples" / f"{cohort_id}.json", root / "frozen_samples" / f"{cohort_id}.json")
    (root / "offline_gate.txt").write_text("OFFLINE_HAZARD_COHORT_PASS\n", encoding="utf-8")
    run_manifest = payload(
        {
            "schema_version": "sfwb.v1.3.memops-hazard-single-sample-run.v1",
            "run_id": root.name,
            "method": method,
            "sample_order": sample_order,
            "sample_id": cohort_id,
            "selection_manifest_sha256": selection["payload_sha256"],
            "v5_started": False,
        }
    )
    (root / "replication_run_manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return root


async def main_async(args: argparse.Namespace) -> int:
    from saturated_fixed_work_baseline_v1_3.memops_qualification import run_memops_live
    from derive_memops_hazard_trace import derive_attempt

    cohort_root = args.audit_root.resolve() / "replication_cohort"
    source_manifest = read_json(cohort_root / "selection_manifest.json")
    rows = source_manifest.get("samples")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("HAZARD_COHORT_EMPTY")
    if args.sample_id is not None:
        selected = [row for row in rows if isinstance(row, dict) and row.get("sample_id") == args.sample_id]
        if len(selected) != 1:
            raise RuntimeError(f"HAZARD_SAMPLE_ID_NOT_UNIQUE:{args.sample_id}")
        rows = selected
    elif len(rows) != 24:
        raise RuntimeError("HAZARD_COHORT_NOT_24_SAMPLES")
    parent_root = args.replication_root.resolve()
    if parent_root.exists() and any(parent_root.iterdir()):
        raise RuntimeError(f"REPLICATION_ROOT_MUST_BE_NEW:{parent_root}")
    parent_root.mkdir(parents=True, exist_ok=False)
    method = args.method
    parent_plan = payload(
        {
            "schema_version": "sfwb.v1.3.memops-hazard-sample-replication.v1",
            "method": method,
            "replication_ordinal": args.ordinal,
            "sample_order": [row["sample_id"] for row in rows],
            "sample_count": len(rows),
            "parent_cohort_selection_manifest_sha256": source_manifest["payload_sha256"],
            "v5_started": False,
            "subroots": [],
        }
    )
    (parent_root / "replication_plan.json").write_text(json.dumps(parent_plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    outputs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        subroot = prepare_sample_root(cohort_root=cohort_root, parent_root=parent_root, sample_row=row, sample_order=index, method=method)
        parent_plan["subroots"].append({"sample_id": row["sample_id"], "subroot": str(subroot)})
        (parent_root / "replication_plan.json").write_text(json.dumps(parent_plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"event": "SAMPLE_START", "method": method, "ordinal": args.ordinal, "sample_order": index, "sample_id": row["sample_id"]}), flush=True)
        try:
            result = await asyncio.wait_for(
                run_memops_live(
                    qualification_root=subroot,
                    method_names=(method,),
                    repository_root=args.repository_root.resolve(),
                ),
                timeout=args.sample_timeout_s,
            )
            for output in result.get("outputs", []):
                attempt = Path(str(output["attempt_root"]))
                trace = derive_attempt(attempt, args.audit_root.resolve())
                path = attempt / "hazard_observability.json"
                if path.exists():
                    raise RuntimeError(f"HAZARD_TRACE_ALREADY_EXISTS:{path}")
                path.write_text(json.dumps(trace, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                outputs.append(output)
            print(json.dumps({"event": "SAMPLE_COMPLETE", "method": method, "ordinal": args.ordinal, "sample_order": index, "sample_id": row["sample_id"], "output_count": len(result.get("outputs", [])), "status": result.get("status")}), flush=True)
        except Exception as exc:
            failures.append({"sample_id": row["sample_id"], "subroot": str(subroot), "error_type": type(exc).__name__, "error": str(exc)})
            print(json.dumps({"event": "SAMPLE_FAILURE", "method": method, "ordinal": args.ordinal, "sample_order": index, "sample_id": row["sample_id"], "error_type": type(exc).__name__, "error": str(exc)}), flush=True)
    result = payload(
        {
            "schema_version": "sfwb.v1.3.memops-hazard-sample-replication-result.v1",
            "status": "LIVE_COMPLETE" if not failures else "LIVE_PARTIAL",
            "method": method,
            "replication_ordinal": args.ordinal,
            "sample_ids": [row["sample_id"] for row in rows],
            "outputs": outputs,
            "failures": failures,
            "sample_count": len(rows),
            "completed_output_count": len(outputs),
            "parent_cohort_selection_manifest_sha256": source_manifest["payload_sha256"],
            "v5_started": False,
        }
    )
    (parent_root / "replication_result.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "REPLICATION_COMPLETE", "method": method, "ordinal": args.ordinal, "status": result["status"], "completed_output_count": len(outputs), "failure_count": len(failures)}), flush=True)
    return 0 if not failures else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--replication-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--method", choices=("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC"), required=True)
    parser.add_argument("--ordinal", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--sample-id", default=None, help="Run exactly one already-frozen sample for checkpoint recovery.")
    parser.add_argument("--sample-timeout-s", type=float, default=180.0)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
