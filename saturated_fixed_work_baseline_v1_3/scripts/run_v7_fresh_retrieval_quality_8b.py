#!/usr/bin/env python3
"""Run a read-only retrieval quality diagnostic for a V7-FRESH namespace."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for source in (ROOT / "mab_quality_v2_final_qa/src", ROOT / "saturated_fixed_work_baseline_v1_3/src", ROOT / "paper-eval-v3/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab_main_dataset import build_authority  # noqa: E402
from paper_eval import graph_quality_live  # noqa: E402
from paper_eval.graphiti_longmemeval_quality import retrieve_graph_quality_evidence  # noqa: E402
from retrieval_eval import retrieval_metrics  # noqa: E402


def _records(result: Any) -> list[dict[str, Any]]:
    values = getattr(result, "records", None)
    if values is None and isinstance(result, tuple) and result:
        values = result[0]
    if values is None and isinstance(result, list):
        values = result
    return [value if isinstance(value, dict) else dict(value) for value in (values or [])]


async def _run(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    root = args.run_root.resolve()
    result = json.loads((root / "RESULT.json").read_text(encoding="utf-8"))
    if result.get("method") != "V7_FRESH" or result.get("status") != "PASS":
        raise RuntimeError("V7-FRESH result is not complete")
    authority = build_authority(ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json")
    context = authority["contexts"][int(json.loads((root / "RUN_MANIFEST_FINAL.json").read_text(encoding="utf-8")).get("context_index", 0))]
    limit = int(result["source_count"])
    prefix_ids = {session.session_id for session in context.sessions[:limit]}
    questions = [qa for qa in context.qa_items if qa.gold_session_ids and set(qa.gold_session_ids) <= prefix_ids]
    if not questions:
        raise RuntimeError(f"no complete-gold questions addressable by prefix-{limit}")
    query = """MATCH (e:Episodic) WHERE e.group_id = $group_id RETURN e.uuid AS uuid, e.name AS name ORDER BY e.name"""
    mapping_rows = _records(await runtime.graphiti.driver.execute_query(query, params={"group_id": result["namespace"]}, routing_="r"))
    by_name = {f"{context.context_id}::episode::{s.source_sequence:04d}": s.session_id for s in context.sessions[:limit]}
    mapping = {str(row["uuid"]): by_name[str(row["name"])] for row in mapping_rows if str(row.get("name")) in by_name}
    rows = []
    for qa in questions:
        evidence = await retrieve_graph_quality_evidence(
            graph=runtime.graphiti,
            query=qa.question,
            namespace=result["namespace"],
            episode_uuid_to_session_id=mapping,
        )
        retrieved = []
        for fact in evidence.facts:
            for session_id in fact.source_session_ids:
                if session_id not in retrieved:
                    retrieved.append(session_id)
        rows.append({
            "question_id": qa.question_id,
            "question_type": qa.question_type,
            "gold_session_ids": list(qa.gold_session_ids),
            "retrieved_session_ids": retrieved[:10],
            "metrics": retrieval_metrics(retrieved, qa.gold_session_ids),
            "fact_count": len(evidence.facts),
            "entity_count": len(evidence.entities),
            "neo4j_read_requests": evidence.neo4j_read_requests,
            "graphiti_search_calls": evidence.graphiti_search_calls,
        })
    recall5 = [row["metrics"]["evidence_recall_at_5"] for row in rows]
    recall10 = [row["metrics"]["evidence_recall_at_10"] for row in rows]
    return {
        "schema_version": "membind.v7b.retrieval-quality-diagnostic.v1",
        "status": "PASS",
        "quality_scope": "READ_ONLY_GOLD_BLIND_RETRIEVAL_DIAGNOSTIC",
        "downstream_reader_judge": "NOT_RUN",
        "run_id": result["run_id"],
        "namespace": result["namespace"],
        "prefix_episode_count": limit,
        "question_count": len(rows),
        "mean_evidence_recall_at_5": sum(recall5) / len(recall5),
        "mean_evidence_recall_at_10": sum(recall10) / len(recall10),
        "rows": rows,
        "construction_latency_excluded": True,
        "llm_calls": 0,
        "embedding_calls": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-name", default="retrieval_quality_diagnostic.json")
    args = parser.parse_args()
    graph_quality_live.NEO4J_URI = os.environ["NEO4J_URI"]
    graph_quality_live.EMBEDDING_BASE_URL = os.environ["EMBEDDING_BASE_URL"].rstrip("/")
    graph_quality_live.EMBEDDING_MODEL = os.environ["EMBEDDING_MODEL"]
    graph_quality_live.EMBEDDING_DIMENSION = int(os.environ["EMBEDDING_DIM"])
    runtime = graph_quality_live.build_graph_quality_runtime(env=dict(os.environ))
    try:
        result = asyncio.run(_run(args, runtime))
    except BaseException as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)[:300]}, ensure_ascii=True))
        return 2
    finally:
        try:
            asyncio.run(runtime.aclose())
        except BaseException:
            pass
    output = args.run_root.resolve() / args.output_name
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing artifact: {output}")
    output.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="ascii")
    print(json.dumps({"status": result["status"], "question_count": result["question_count"], "mean_recall10": result["mean_evidence_recall_at_10"], "output": str(output)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
