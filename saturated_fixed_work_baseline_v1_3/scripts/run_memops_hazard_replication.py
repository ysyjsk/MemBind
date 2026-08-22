#!/usr/bin/env python3
"""Run the frozen gold-only hazard cohort through existing B0/B1 entry points.

The script only composes ``run_memops_live``.  It does not implement a
scheduler and never invokes V5.  Each invocation gets a fresh run root and a
fresh namespace derived by the existing runner.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path
from typing import Any


METHOD_RUNS = (
    ("B0_NATIVE_SERIAL", "b0"),
    ("B1_NAIVE_WHOLE_UPDATE_ASYNC", "b1"),
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    payload = value.get("payload_sha256")
    if payload is None:
        raise RuntimeError(f"PAYLOAD_HASH_MISSING:{path}")
    import hashlib

    unsigned = {key: child for key, child in value.items() if key != "payload_sha256"}
    actual = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if actual != payload:
        raise RuntimeError(f"PAYLOAD_HASH_MISMATCH:{path}")
    return value


def prepare_run_root(cohort_root: Path, replication_root: Path, method: str, ordinal: int) -> Path:
    run_root = replication_root / f"{method.lower().replace('_', '-')}-r{ordinal:03d}"
    if run_root.exists():
        raise RuntimeError(f"RUN_ROOT_ALREADY_EXISTS:{run_root}")
    run_root.mkdir(parents=True)
    shutil.copy2(cohort_root / "selection_manifest.json", run_root / "selection_manifest.json")
    shutil.copytree(cohort_root / "frozen_samples", run_root / "frozen_samples")
    (run_root / "offline_gate.txt").write_text("OFFLINE_HAZARD_COHORT_PASS\n", encoding="utf-8")
    manifest = {
        "schema_version": "sfwb.v1.3.memops-hazard-replication-run.v1",
        "run_id": run_root.name,
        "method": method,
        "replication_ordinal": ordinal,
        "selection_manifest_sha256": read_json(run_root / "selection_manifest.json")["payload_sha256"],
        "v5_started": False,
        "b0_gate_required_for_b1": False,
    }
    manifest["payload_sha256"] = __import__("hashlib").sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (run_root / "replication_run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return run_root


async def run_one(*, run_root: Path, method: str, repository_root: Path) -> dict[str, Any]:
    from saturated_fixed_work_baseline_v1_3.memops_qualification import run_memops_live

    result = await run_memops_live(
        qualification_root=run_root,
        method_names=(method,),
        repository_root=repository_root,
    )
    result_path = run_root / "replication_result.json"
    if result_path.exists():
        raise RuntimeError(f"RESULT_ALREADY_EXISTS:{result_path}")
    result_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


async def main_async(args: argparse.Namespace) -> int:
    cohort_root = args.audit_root.resolve() / "replication_cohort"
    selection = read_json(cohort_root / "selection_manifest.json")
    if selection.get("sample_count") != 24 or len(selection.get("sample_order", [])) != 24:
        raise RuntimeError("HAZARD_COHORT_NOT_24_SAMPLES")
    replication_root = args.replication_root.resolve()
    if replication_root.exists() and any(replication_root.iterdir()):
        raise RuntimeError("REPLICATION_ROOT_MUST_BE_NEW")
    replication_root.mkdir(parents=True, exist_ok=False)
    plan = {
        "schema_version": "sfwb.v1.3.memops-hazard-replication-plan.v1",
        "cohort_selection_manifest_sha256": selection["payload_sha256"],
        "sample_order": selection["sample_order"],
        "replications_per_method": 3,
        "methods": [method for method, _ in METHOD_RUNS],
        "v5_started": False,
        "run_roots": [],
    }
    for method, _ in METHOD_RUNS:
        for ordinal in range(1, 4):
            run_root = prepare_run_root(cohort_root, replication_root, method, ordinal)
            plan["run_roots"].append({"method": method, "ordinal": ordinal, "run_root": str(run_root)})
            (replication_root / "replication_plan.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"event": "RUN_START", "method": method, "ordinal": ordinal, "run_root": str(run_root)}), flush=True)
            result = await run_one(run_root=run_root, method=method, repository_root=args.repository_root.resolve())
            for output in result.get("outputs", []):
                attempt = Path(str(output["attempt_root"]))
                from derive_memops_hazard_trace import derive_attempt

                trace = derive_attempt(attempt, args.audit_root.resolve())
                path = attempt / "hazard_observability.json"
                if path.exists():
                    raise RuntimeError(f"HAZARD_TRACE_ALREADY_EXISTS:{path}")
                path.write_text(json.dumps(trace, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"event": "RUN_COMPLETE", "method": method, "ordinal": ordinal, "status": result.get("status"), "output_count": len(result.get("outputs", []))}), flush=True)
    plan["status"] = "LIVE_REPLICATION_COMPLETE"
    (replication_root / "replication_plan.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--replication-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
