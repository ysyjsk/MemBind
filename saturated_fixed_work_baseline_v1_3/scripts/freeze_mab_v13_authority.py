#!/usr/bin/env python3
"""Materialize the immutable A0 MAB v1.3 authority and manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
MAB_SRC = REPO / "mab_quality_v2_final_qa" / "src"
DATASET = REPO / "mab_quality_v2_final_qa" / "data" / "official_5_contexts.json"
if str(MAB_SRC) not in sys.path:
    sys.path.insert(0, str(MAB_SRC))

from mab_quality_v2_final_qa.mab_main_dataset import (  # noqa: E402
    DATASET_REVISION,
    EXPECTED_QA_TYPE_COUNTS,
    EXPECTED_SESSION_COUNTS,
    SOURCE_FILTER,
    authority_artifact,
    build_authority,
    build_qa_manifest,
    build_workload_manifest,
)
from mab_quality_v2_final_qa.contracts import canonical_sha256  # noqa: E402


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command(*args: str) -> str:
    try:
        return subprocess.check_output(args, cwd=REPO, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        return f"COMMAND_FAILED:{exc.returncode}:{exc.output.strip()}"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def freeze(output_root: Path) -> dict[str, Any]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"output root is not fresh: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    authority = build_authority(DATASET)
    _write_json(output_root / "dataset_authority.json", authority_artifact(authority))
    contexts = authority["contexts"]
    for index, context in enumerate(contexts):
        context_root = output_root / "contexts" / f"context-{index}"
        manifest = build_workload_manifest(context, authority)
        manifest_path = context_root / "workload_manifest.jsonl"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(manifest.jsonl(), encoding="utf-8")
        _write_json(context_root / "workload_manifest.json", manifest.to_dict())
        (context_root / "workload_manifest.sha256").write_text(_sha(manifest_path) + "\n", encoding="utf-8")
        for scope in ("SMOKE", "FULL"):
            qa_path = context_root / f"qa_manifest_{scope.lower()}.jsonl"
            qa_rows = build_qa_manifest(context, scope=scope)
            qa_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in qa_rows), encoding="utf-8")
            _write_json(context_root / f"qa_manifest_{scope.lower()}.json", {
                "schema_version": "membind.v1.3.qa-manifest.v1",
                "context_id": context.context_id,
                "scope": scope,
                "count": len(qa_rows),
                "rows_sha256": _sha(qa_path),
                "rows": qa_rows,
            })
    frozen_config = {
        "schema_version": "membind.v1.3.frozen-config.v1",
        "dataset_revision": DATASET_REVISION,
        "source_filter": SOURCE_FILTER,
        "dataset_file_sha256": authority["local_file_sha256"],
        "renderer_sha256": _sha(MAB_SRC / "mab_quality_v2_final_qa" / "workload_contract.py"),
        "renderer": "mab_quality_v2_final_qa.workload_contract.canonical_episode_body",
        "methods": {
            "B0": {"label": "B0 Native Serial", "semantic_class": "ORDERED_REFERENCE"},
            "B1": {"label": "B1 Naive Whole-Update Async", "semantic_class": "RELAXED_ORDER_REFERENCE"},
            "V6": {"label": "MemBind V6", "semantic_class": "ORDERED_REFINEMENT"},
        },
        "arrival": {"mode": "SATURATED", "arrival_offset_s": 0.0},
        "formal": {"context_indices": [0, 1, 2, 3, 4], "repeats": 3, "qa_per_context": 60},
        "arm_orders": [["B0", "B1", "V6"], ["B1", "V6", "B0"], ["V6", "B0", "B1"]],
        "primary_endpoint": "last PUBLICATION_DURABLE - FORMAL_START",
        "qa_role": "OFFICIAL_ENDPOINT_QUALITY_GUARD_ONLY",
    }
    frozen_config["config_sha256"] = canonical_sha256(frozen_config)
    _write_json(output_root / "frozen_config.json", frozen_config)
    charter = {
        "schema_version": "membind.v1.3.experiment-charter.v1",
        "question": "Compare B0 Native Serial, B1 Naive Whole-Update Async, and MemBind V6 on one frozen MAB workload.",
        "dataset": "MemoryAgentBench Accurate Retrieval longmemeval_s* full five-context component",
        "context_count": 5,
        "session_counts": list(EXPECTED_SESSION_COUNTS),
        "qa_count": 300,
        "qa_type_counts": EXPECTED_QA_TYPE_COUNTS,
        "formal_blocks": 45,
        "claim_boundary": "QA is a sealed-state quality guard and never substitutes for trace correctness.",
        "known_issue": "0ddfec37_abs is retained with PARTIAL_GOLD_MAPPING; evidence metrics are null.",
    }
    charter["charter_sha256"] = canonical_sha256(charter)
    _write_json(output_root / "experiment_charter.json", charter)
    environment = {
        "schema_version": "membind.v1.3.environment.v1",
        "repo_root": str(REPO),
        "head": _command("git", "rev-parse", "HEAD"),
        "status_short": _command("git", "status", "--short"),
        "tracked_diff_sha256": hashlib.sha256(_command("git", "diff", "--no-ext-diff").encode()).hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {
            "mab_quality_v2_final_qa": "local-src",
            "saturated_fixed_work_baseline_v1_3": "local-src",
        },
        "graphiti": "recorded at live preflight; no live call in A0",
        "endpoint_identity": "recorded at live preflight; no live call in A0",
    }
    _write_json(output_root / "environment.json", environment)
    return {
        "status": "DATASET_FROZEN",
        "output_root": str(output_root.resolve()),
        "authority_sha256": authority_artifact(authority)["authority_sha256"],
        "context_count": len(contexts),
        "total_sessions": sum(len(context.sessions) for context in contexts),
        "total_qa": sum(len(context.qa_items) for context in contexts),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.output_root), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
