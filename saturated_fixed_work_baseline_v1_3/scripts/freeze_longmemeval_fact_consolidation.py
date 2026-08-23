#!/usr/bin/env python3
"""Freeze the offline LongMemEval-S FactConsolidation operation cohort.

The command only reads the pinned dataset and inventories existing canonical
graph *paths*.  It never starts a service and never opens a graph payload,
baseline result, Reader, Judge, or execution trace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from saturated_fixed_work_baseline_v1_3.longmemeval_fact_consolidation import (
    EXPECTED_NON_ABSTENTION_COUNT,
    EXPECTED_RAW_SHA256,
    RAW_LONGMEMEVAL_PATH,
    build_operation_manifest,
    discover_completed_graph_coverage,
    load_longmemeval_records,
    select_longmemeval_cases,
    write_operation_artifact,
)


def _write_text_new(path: Path, content: str) -> None:
    if path.exists():
        raise RuntimeError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _manifest_markdown(manifest: Mapping[str, Any]) -> str:
    source = manifest["source_dataset"]
    provenance = manifest["literature_provenance"]
    cases = manifest["cases"]
    coverage = manifest["completed_graph_coverage"]
    lines = [
        "# LongMemEval-S FactConsolidation Operation Freeze",
        "",
        "Status: `OFFLINE_OPERATION_FROZEN`",
        "",
        "This is an append-only, gold-only operation manifest. It does not run",
        "Graphiti, an LLM, an embedding service, Neo4j, Reader, or Judge.",
        "",
        "## Literature Basis",
        "",
        f"Primary precedent: **{provenance['title']}**, {provenance['venue']} ({provenance['arxiv']}).",
        "The paper's Selective Forgetting / FactConsolidation protocol injects",
        "facts incrementally, treats larger serial numbers as newer, resolves",
        "contradictions in favor of the newest fact, queries after all injection,",
        "and uses SubEM-compatible final-answer scoring.",
        "",
        "This lane is explicitly a `LONGMEMEVAL_OPERATIONALIZATION`, not an exact",
        "MemoryAgentBench FactConsolidation reproduction: LongMemEval-S exposes",
        "two official answer-session anchors and a final answer, but no structured",
        "`old_value`/`new_value`. Old/new values therefore remain",
        "`OPAQUE_UNLESS_PROVABLE` and are never inferred from a model result.",
        "",
        "## Frozen Source",
        "",
        f"- dataset: `{source['path']}`",
        f"- file SHA-256: `{source['sha256']}`",
        f"- records: `{source['record_count']}`",
        f"- knowledge-update: `{source['knowledge_update_count']}`",
        f"- `_abs` excluded: `{source['abstention_excluded_count']}`",
        f"- selected non-abstention operations: `{source['non_abstention_cohort_count']}`",
        "- selection reads B0/B1 results: `false`",
        "- selection reads execution outcomes: `false`",
        "",
        "## Reference-Time Policy",
        "",
        "Construction reference times are a fixed monotonic source-order mapping",
        "(`2000-01-01T00:00:00Z + source_sequence * 60s`). Raw LongMemEval dates",
        "are retained and hashed for provenance but are not used to encode gold",
        "answers or to schedule construction. The mapping is identical for B0/B1.",
        "",
        "## Existing Graph Coverage (Inventory Only)",
        "",
        f"The raw operation freeze covers `{source['non_abstention_cohort_count']}` histories.",
        f"Existing paired v1.3 canonical graph paths cover `{coverage.get('completed_graph_coverage', 0)}` histories:",
        f"`{', '.join(coverage.get('paired_history_ids', [])) or 'none'}`.",
        "This inventory did not read graph payloads and does not change the frozen",
        "LongMemEval cohort.",
        "",
        "## Frozen Cases",
        "",
        "| # | question_id | old anchor (segment) | new anchor (segment) | source count | raw date order | official current answer |",
        "|---:|---|---|---|---:|---|---|",
    ]
    for index, case in enumerate(cases, 1):
        answer = _md(case.get("gold_current_answer"))
        lines.append(
            "| %d | `%s` | `%s` (%s) | `%s` (%s) | %s | `%s` | %s |"
            % (
                index,
                _md(case["question_id"]),
                _md(case["old_session_id"]),
                case["old_segment_index"],
                _md(case["new_session_id"]),
                case["new_segment_index"],
                case["source_count"],
                _md(case["raw_date_order_status"]),
                answer,
            )
        )
    lines.extend(
        [
            "",
            "## Later QA Boundary",
            "",
            "A later read-only lane may query each completed graph after all source",
            "episodes are durable. Its primary evidence surface must be graph facts",
            "and temporal fields only; it must not include source-local sessions or",
            "the full gold conversation. Retrieval success alone is not Selective",
            "Forgetting: the semantic predicate must require the official current",
            "state and reject a stale value retained as current. Cases whose old value",
            "cannot be proven from official gold remain stale-exclusion `NOT_PROVABLE`.",
            "",
            "No B1 or V5 run is authorized by this artifact.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "artifacts/sfwb-v1-3-longmemeval-sf-operation-20260823-001",
    )
    parser.add_argument(
        "--formal-root",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "artifacts/sfwb-v1-3-formal-baseline-20260822-002",
    )
    args = parser.parse_args()
    output = args.output
    if output.exists():
        raise SystemExit(f"ARTIFACT_ALREADY_EXISTS:{output}")

    records = load_longmemeval_records(RAW_LONGMEMEVAL_PATH)
    cases = select_longmemeval_cases(records)
    if len(cases) != EXPECTED_NON_ABSTENTION_COUNT:
        raise SystemExit("LONGMEMEVAL_OPERATION_COHORT_COUNT_INVALID")
    coverage = discover_completed_graph_coverage(args.formal_root)
    manifest = build_operation_manifest(
        cases,
        raw_file_sha256=EXPECTED_RAW_SHA256,
        completed_graph_coverage=coverage,
    )
    output.mkdir(parents=True, exist_ok=False)
    write_operation_artifact(output / "operation_manifest.json", manifest)
    write_operation_artifact(output / "completed_graph_coverage.json", coverage)
    gate = {
        "schema_version": "sfwb.v1.3.longmemeval-sf-offline-gate.v1",
        "status": "PASS",
        "decision": "OFFLINE_OPERATION_FROZEN",
        "dataset_sha256": EXPECTED_RAW_SHA256,
        "record_count": len(records),
        "selected_case_count": len(cases),
        "selection_gold_only": True,
        "b0_b1_results_used_for_selection": False,
        "live_service_started": False,
        "b1_authorized": False,
        "v5_authorized": False,
        "completed_graph_coverage": coverage.get("completed_graph_coverage", 0),
    }
    gate["payload_sha256"] = hashlib.sha256(
        json.dumps(gate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    write_operation_artifact(output / "offline_gate.json", gate)
    _write_text_new(output / "operation_manifest.md", _manifest_markdown(manifest))

    files = {
        name: _file_sha256(output / name)
        for name in ("operation_manifest.json", "completed_graph_coverage.json", "offline_gate.json", "operation_manifest.md")
    }
    seal = {
        "schema_version": "sfwb.v1.3.longmemeval-sf-operation-seal.v1",
        "status": "OFFLINE_OPERATION_SEALED",
        "artifact_directory": str(output),
        "dataset_sha256": EXPECTED_RAW_SHA256,
        "selected_case_count": len(cases),
        "files": files,
        "live_service_started": False,
        "b1_authorized": False,
        "v5_authorized": False,
    }
    seal["payload_sha256"] = hashlib.sha256(
        json.dumps(seal, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    write_operation_artifact(output / "seal.json", seal)
    print(json.dumps({"status": "PASS", "output": str(output), "selected_case_count": len(cases), "completed_graph_coverage": coverage.get("completed_graph_coverage", 0)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
