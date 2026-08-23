#!/usr/bin/env python3
"""Run the pinned official LongMemEval Judge on existing Reader outputs.

This is a read-only answer-scoring step.  It does not open Graphiti search,
does not call ``add_episode``, and does not write Neo4j.  Raw Judge text is
discarded after hashing; only the qualified status/label projection is
written for the offline layered analyzer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-state-qa-20260823-004/state_qa_results.json"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-state-qa-v2-judge-20260823-001"
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
METHODS = ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")
BASE_URL = "http://10.87.5.247:8002/v1"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(value)
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    body["payload_sha256"] = hashlib.sha256(canonical).hexdigest()
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def run(args: argparse.Namespace) -> int:
    source_path = args.source_results.resolve()
    source = read_json(source_path)
    rows = source.get("rows")
    if not isinstance(rows, list) or len(rows) != 8:
        raise RuntimeError("STATE_QA_SOURCE_COVERAGE_INVALID")
    by_key = {(str(row.get("history_id")), str(row.get("method"))): dict(row) for row in rows if isinstance(row, Mapping)}
    expected = {(history, method) for history in HISTORIES for method in METHODS}
    if set(by_key) != expected:
        raise RuntimeError("STATE_QA_SOURCE_METHOD_HISTORY_COVERAGE_INVALID")

    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_3.longmemeval_fact_consolidation import (
        load_longmemeval_records,
        select_longmemeval_cases,
    )
    from paper_eval.graph_quality_judge import build_graph_quality_qwen_judge

    raw_cases = {
        case.question_id: case
        for case in select_longmemeval_cases(load_longmemeval_records())
    }
    env = dict(_load_env(args.repository_root.resolve()))
    api_key = str(env.get("CONSTRUCTION_LLM_API_KEY") or env.get("OPENAI_API_KEY") or "")
    if not api_key:
        raise RuntimeError("JUDGE_API_KEY_MISSING")
    judge = build_graph_quality_qwen_judge(base_url=args.base_url, api_key=api_key)
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"OUTPUT_ROOT_MUST_BE_NEW:{output_root}")
    output_root.mkdir(parents=True, exist_ok=False)
    projections: dict[str, Any] = {}
    try:
        for history in HISTORIES:
            case = raw_cases.get(history)
            if case is None:
                raise RuntimeError(f"LONGMEMEVAL_CASE_MISSING:{history}")
            for method in METHODS:
                row = by_key[(history, method)]
                inputs = SimpleNamespace(
                    run_id=output_root.name,
                    history_id=history,
                    question_type=case.question_type,
                    question=str(row.get("question") or case.question),
                    reference_answer=str(row.get("official_current_answer") or case.gold_current_answer),
                )
                result = await judge.evaluate(
                    hypothesis=str(row.get("reader_answer") or ""), inputs=inputs
                )
                raw_output = str(result.pop("raw_output", ""))
                result["output_sha256"] = sha256_text(raw_output)
                result["raw_output_persisted"] = False
                path = output_root / history / f"{method}.json"
                write_new_json(path, result)
                projections[f"{history}:{method}"] = {
                    "status": result.get("status"),
                    "label": result.get("label"),
                    "parse_status": result.get("parse_status"),
                    "output_sha256": result.get("output_sha256"),
                    "config_sha256": result.get("config_sha256"),
                }
    finally:
        await judge.aclose()
    write_new_json(
        output_root / "judge_manifest.json",
        {
            "schema_version": "sfwb.v1.3.longmemeval-state-qa-v2-judge-manifest.v1",
            "status": "OFFICIAL_LONGMEMEVAL_JUDGE_COMPLETE",
            "source_results": str(source_path),
            "base_url": args.base_url,
            "model": "qwen3-32b-fp8",
            "rows": 8,
            "construction_calls": 0,
            "graph_writes": 0,
            "raw_output_persisted": False,
            "projections": projections,
        },
    )
    print(json.dumps({"status": "OFFICIAL_LONGMEMEVAL_JUDGE_COMPLETE", "output": str(output_root), "rows": 8}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-results", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT.parent)
    parser.add_argument("--base-url", default=BASE_URL)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
