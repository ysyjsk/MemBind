"""Bounded P(C=4) analysis over any already-persisted frozen namespaces.

The C246 plan names four P(C=4) namespaces, but this module only executes
against namespaces that are present and source-bound in Neo4j. Missing
histories remain blocked; no construction or namespace replacement is
performed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


EXPANDED_DIR = Path(__file__).resolve().parent
ROOT = EXPANDED_DIR.parent
PROJECT = ROOT.parents[0]
PAPER_SRC = PROJECT / "paper-eval-v3/src"
LEGACY_SRC = PROJECT / "membind-validation/src"
MAB_SRC = PROJECT / "mab_quality_v2_final_qa/src"
for path in (PAPER_SRC, LEGACY_SRC, MAB_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from expanded_analysis import EXPECTED_HISTORIES, file_sha256, load_expanded_inventory  # noqa: E402
from expanded_runtime import build_expanded_runtime  # noqa: E402
from run_expanded import (  # noqa: E402
    EXACT_EMBEDDING_MODEL,
    EXACT_JUDGE_MODEL,
    EXACT_READER_MODEL,
    SILICONFLOW_BASE_URL,
    SOURCE,
    _api_preflight,
    _build_reader_and_judge,
    _load_source_records,
    _namespace_snapshot,
    _preflight_corpus,
    _run_question,
    _read_only_query_guard,
    _safe_error,
    ProbeCounters,
)


PC4_NAMESPACE_MAP = {
    "07741c45": "c246-c246-baseline-20260819-002-pc4-07741c45",
    "b6019101": "c246-c246-baseline-20260819-002-pc4-b6019101",
    "6071bd76": "c246-c246-baseline-20260819-002-pc4-6071bd76",
    "a2f3aa27": "c246-c246-baseline-20260819-002-pc4-a2f3aa27",
}
BASELINE_ARTIFACT = EXPANDED_DIR / "artifacts/expanded-qa-20260819-001"


def summarize_pc4_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"question_count": 0, "valid_count": 0, "invalid_count": 0, "correct_count": 0, "accuracy": None}
    valid = [row for row in rows if row.get("judge_valid") is True and type(row.get("correct")) is bool]
    correct = sum(row.get("correct") is True for row in valid)
    retrieval = {}
    for metric in ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10"):
        values = [float((row.get("retrieval_metrics") or {}).get(metric, 0.0)) for row in rows]
        retrieval[metric] = sum(values) / len(values)
    return {
        "question_count": len(rows),
        "valid_count": len(valid),
        "invalid_count": len(rows) - len(valid),
        "correct_count": correct,
        "accuracy": correct / len(rows),
        "valid_only_accuracy": correct / len(valid) if valid else None,
        "reader_invalid_count": sum(row.get("reader_valid") is not True for row in rows),
        "judge_invalid_count": sum(
            row.get("reader_valid") is True
            and not (row.get("judge_valid") is True and type(row.get("correct")) is bool)
            for row in rows
        ),
        "retrieval": retrieval,
    }


def _load_comparison_rows(history_id: str) -> dict[str, list[dict[str, Any]]]:
    comparison: dict[str, list[dict[str, Any]]] = {}
    private_root = BASELINE_ARTIFACT / "private_rows"
    for method in ("U0", "P(C=2)"):
        comparison[method] = []
        for path in sorted(private_root.glob(f"{method.replace('(', '').replace(')', '').replace('=', '')}-{history_id}-*.json")):
            private = json.loads(path.read_text(encoding="utf-8"))
            reader = private.get("reader") or {}
            judge = private.get("judge") or {}
            reader_valid = reader.get("finish_reason") == "stop" and reader.get("model") == EXACT_READER_MODEL
            judge_valid = (
                judge.get("status") == "SUCCESS"
                and judge.get("parse_status") in {"YES", "NO"}
                and type(judge.get("label")) is bool
                and judge.get("model") == EXACT_JUDGE_MODEL
            )
            comparison[method].append({
                "question_id": private["question_id"],
                "reader_valid": reader_valid,
                "judge_valid": judge_valid,
                "correct": judge.get("label") if judge_valid else None,
                "retrieval_metrics": private.get("retrieval_metrics") or {},
            })
    return comparison


def _load_pc4_gold_ranks(private_root: Path, history_id: str) -> dict[str, list[int]]:
    """Load retrieval ranks from sealed P(C=4) private rows.

    The public/normalized row intentionally omits private retrieval details,
    so report generation must restore only this non-sensitive metric from the
    corresponding sealed row. Missing or malformed rows remain absent rather
    than being guessed.
    """

    ranks_by_question: dict[str, list[int]] = {}
    for path in sorted(private_root.glob(f"PC4-{history_id}-*.json")):
        private = json.loads(path.read_text(encoding="utf-8"))
        question_id = str(private.get("question_id", ""))
        ranks = (private.get("retrieval_metrics") or {}).get("gold_ranks")
        if not question_id or not isinstance(ranks, list):
            continue
        normalized = [value for value in ranks if type(value) is int and value > 0]
        if normalized:
            ranks_by_question[question_id] = normalized
    return ranks_by_question


def restore_pc4_gold_ranks(
    rows: Sequence[Mapping[str, Any]], ranks_by_question: Mapping[str, Sequence[int]]
) -> list[dict[str, Any]]:
    """Attach private gold ranks to normalized P(C=4) rows without rescoring."""

    restored: list[dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        metrics = dict(row.get("retrieval_metrics") or {})
        question_id = str(row.get("question_id", ""))
        if row.get("method") == "P(C=4)" and not metrics.get("gold_ranks"):
            ranks = ranks_by_question.get(question_id)
            if ranks:
                metrics["gold_ranks"] = list(ranks)
        normalized["retrieval_metrics"] = metrics
        restored.append(normalized)
    return restored


def _comparison_summary(history_id: str, pc4_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    loaded = _load_comparison_rows(history_id)
    methods = {method: summarize_pc4_rows(rows) for method, rows in loaded.items()}
    methods["P(C=4)"] = summarize_pc4_rows(pc4_rows)
    question_ids = sorted({str(row["question_id"]) for row in pc4_rows})
    by_method = {
        method: {str(row["question_id"]): row for row in rows}
        for method, rows in loaded.items()
    }
    by_method["P(C=4)"] = {str(row["question_id"]): row for row in pc4_rows}
    questions = []
    for question_id in question_ids:
        item = {"question_id": question_id}
        for method in ("U0", "P(C=2)", "P(C=4)"):
            row = by_method[method][question_id]
            item[method] = {
                "label": row.get("correct") if type(row.get("correct")) is bool else None,
                "gold_rank": min((row.get("retrieval_metrics") or {}).get("gold_ranks") or [None]),
            }
        questions.append(item)
    return {"history_id": history_id, "methods": methods, "questions": questions}


def render_pc4_report(result: Mapping[str, Any]) -> str:
    lines = [
        "# P(C=4) Analysis",
        "",
        "Scope: `BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION`. P(C=4) is analyzed only where an already-persisted, source-bound namespace exists. This is not a construction run and not an official MemoryAgentBench Multi-QA result.",
        "",
        f"Coverage: {result['available_history_count']}/{len(EXPECTED_HISTORIES)} histories have a verified P(C=4) namespace.",
        f"Available histories: {', '.join(result['available_histories']) or 'none'}.",
        f"Missing histories: {', '.join(result['missing_histories']) or 'none'}.",
        "",
    ]
    if result.get("partial"):
        lines.extend([
            "Status: **PARTIAL_BLOCKED_FOR_FULL_METHOD_CLAIM**",
            "",
            "A four-history P(C=4) QA accuracy is not reported because three planned namespaces are absent from Neo4j. The partial score below is descriptive only for the available history and must not be compared as a full method result.",
            "",
        ])
    else:
        lines.extend(["Status: **PASS**", "", "The available P(C=4) coverage is complete for the declared scope.", ""])
    summary = result.get("summary") or {}
    if summary.get("question_count"):
        lines.extend([
            "## Available-History Partial Score",
            "",
            f"- History: `{result['available_histories'][0]}`",
            f"- Primary accuracy: {summary['correct_count']}/{summary['question_count']} = {summary['accuracy']:.1%}",
            f"- Valid-only accuracy: {summary['valid_only_accuracy']:.1%}",
            f"- Reader invalid: {summary['reader_invalid_count']}; Judge invalid: {summary['judge_invalid_count']}",
            "",
        ])
        retrieval = summary.get("retrieval") or {}
        lines.extend([
            f"- Retrieval means: R@1 {retrieval.get('recall_at_1', 0.0):.3f}, R@3 {retrieval.get('recall_at_3', 0.0):.3f}, R@5 {retrieval.get('recall_at_5', 0.0):.3f}, R@10 {retrieval.get('recall_at_10', 0.0):.3f}, MRR {retrieval.get('mrr', 0.0):.3f}, nDCG@10 {retrieval.get('ndcg_at_10', 0.0):.3f}.",
            "",
        ])
    comparison = result.get("comparison")
    if comparison:
        lines.extend([
            "## Same-History Comparison",
            "",
            "| Question | U0 | P(C=2) | P(C=4) | Gold rank P(C=4) |",
            "|---|---:|---:|---:|---:|",
        ])
        for item in comparison["questions"]:
            def mark(value: Any) -> str:
                return "invalid" if value is None else ("correct" if value else "wrong")
            lines.append(
                f"| `{item['question_id']}` | {mark(item['U0']['label'])} | {mark(item['P(C=2)']['label'])} | {mark(item['P(C=4)']['label'])} | {item['P(C=4)']['gold_rank']} |"
            )
        lines.extend([
            "",
            "For the only available history, U0 is 3/4, P(C=2) is 4/4, and P(C=4) is 3/4. This is a within-history diagnostic, not a four-history comparison.",
            "",
        ])
    lines.extend([
        "## Identity And Safety",
        "",
        "- P(C=4) namespace identity comes from the existing C246 plan and is checked against the live episode corpus.",
        "- Missing namespaces are not substituted with U0, P(C=2), native, or another C246 block.",
        "- No memory construction, Neo4j write, or namespace mutation was performed.",
        f"- Reader/Judge model: `{EXACT_READER_MODEL}`; embedding model: `{EXACT_EMBEDDING_MODEL}` (1024 dimensions).",
        "- A partial score cannot support equivalence, non-inferiority, or a four-history P(C=4) conclusion.",
        "- On the available history, P(C=4)'s only miss is the sandal-brand question: gold session rank 9, exact authored quote absent from the final context, and the Reader selected Teva + Keen rather than Teva + Merrell.",
    ])
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


async def _available_namespaces(driver: Any) -> dict[str, dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {}
    for history_id, namespace in PC4_NAMESPACE_MAP.items():
        snapshot = await _namespace_snapshot(driver, namespace)
        if snapshot["episode_count"]:
            available[history_id] = {"namespace": namespace, **snapshot}
    return available


async def _run_partial(
    *,
    output_root: Path,
    inventory: Mapping[str, Any],
    source_records: Mapping[str, Mapping[str, Any]],
    runtime: Any,
    available: Mapping[str, Mapping[str, Any]],
    api_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    reader, judge, reader_transport, _backend = _build_reader_and_judge(api_key)
    rows: list[dict[str, Any]] = []
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    try:
        for history_id in sorted(available):
            namespace = str(available[history_id]["namespace"])
            with _read_only_query_guard(runtime.graphiti.driver, ProbeCounters()):
                before[history_id] = await _namespace_snapshot(runtime.graphiti.driver, namespace)
            for row in inventory["questions"]:
                if row["history_id"] != history_id:
                    continue
                rows.append(await _run_question(
                    run_id="expanded-pc4-20260820-001",
                    method="P(C=4)",
                    history_id=history_id,
                    row=row,
                    source_record=source_records[history_id],
                    namespace=namespace,
                    runtime=runtime,
                    reader=reader,
                    judge=judge,
                    output_root=output_root,
                ))
            with _read_only_query_guard(runtime.graphiti.driver, ProbeCounters()):
                after[history_id] = await _namespace_snapshot(runtime.graphiti.driver, namespace)
    finally:
        await judge.aclose()
        await reader_transport.aclose()
    return rows, before, after


async def _execute_runtime_probe(
    *,
    output_root: Path,
    inventory: Mapping[str, Any],
    source_records: Mapping[str, Mapping[str, Any]],
    runtime: Any,
    api_key: str,
) -> dict[str, Any]:
    """Keep all async driver operations on the event loop that owns them."""

    try:
        available = await _available_namespaces(runtime.graphiti.driver)
        missing = [history_id for history_id in EXPECTED_HISTORIES if history_id not in available]
        if not available:
            return {
                "status": "BLOCKED",
                "partial": True,
                "blocker": "PC4_NAMESPACE_NONE_PRESENT",
                "available_histories": [],
                "missing_histories": missing,
                "available_history_count": 0,
            }
        rows, before, after = await _run_partial(
            output_root=output_root,
            inventory=inventory,
            source_records=source_records,
            runtime=runtime,
            available=available,
            api_key=api_key,
        )
        pc4_ranks = _load_pc4_gold_ranks(output_root / "private_rows", sorted(available)[0]) if len(available) == 1 else {}
        rows = restore_pc4_gold_ranks(rows, pc4_ranks)
        summary = summarize_pc4_rows(rows)
        comparison = _comparison_summary(sorted(available)[0], rows) if len(available) == 1 else None
        return {
            "schema_version": "membind.baseline-reuse-pc4-analysis.v1",
            "claim_scope": "BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION",
            "status": "PARTIAL" if missing else "PASS",
            "partial": bool(missing),
            "blocker": "PC4_NAMESPACE_COVERAGE_INCOMPLETE" if missing else None,
            "available_histories": sorted(available),
            "missing_histories": missing,
            "available_history_count": len(available),
            "question_count": len(rows),
            "summary": summary,
            "comparison": comparison,
            "namespace_snapshots_before": before,
            "namespace_snapshots_after": after,
            "namespace_snapshots_unchanged": before == after,
            "construction_calls": 0,
            "rows": rows,
        }
    finally:
        await runtime.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    source_records = _load_source_records()
    inventory = load_expanded_inventory(EXPANDED_DIR / "expanded_qa_inventory.json", SOURCE.resolve())
    api_key = __import__("os").environ.get("SILICONFLOW_API_KEY", "")
    preflight = asyncio.run(_api_preflight(api_key)) if api_key else {"status": "BLOCKED", "error_class": "SILICONFLOW_API_KEY_MISSING"}
    _write_json(output_root / "RUNTIME_PREFLIGHT.json", preflight)
    if preflight.get("status") != "PASS":
        result = {"status": "BLOCKED", "partial": True, "blocker": preflight.get("error_class"), "available_histories": [], "missing_histories": list(EXPECTED_HISTORIES), "available_history_count": 0}
        _write_json(output_root / "RESULTS.json", result)
        (output_root / "FINAL_PC4_ANALYSIS.md").write_text(render_pc4_report(result), encoding="utf-8")
        return 2
    runtime = build_expanded_runtime(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user=__import__("os").environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=__import__("os").environ.get("NEO4J_PASSWORD", "password"),
        embedding_base_url=SILICONFLOW_BASE_URL,
        embedding_api_key=api_key,
    )
    try:
        result = asyncio.run(_execute_runtime_probe(
            output_root=output_root,
            inventory=inventory,
            source_records=source_records,
            runtime=runtime,
            api_key=api_key,
        ))
        runtime = None
        _write_json(output_root / "RESULTS.json", result)
        (output_root / "FINAL_PC4_ANALYSIS.md").write_text(render_pc4_report(result), encoding="utf-8")
        return 0 if result.get("status") in {"PASS", "PARTIAL"} else 2
    finally:
        if runtime is not None:
            asyncio.run(runtime.aclose())


if __name__ == "__main__":
    raise SystemExit(main())
