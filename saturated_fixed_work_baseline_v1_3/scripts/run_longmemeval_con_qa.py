#!/usr/bin/env python3
"""Bounded read-only LongMemEval JSON + Chain-of-Note QA lane."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from run_longmemeval_session_value_qa import (
    CHAT_BASE_URL,
    DEFAULT_OPERATION_ROOT,
    EMBEDDING_BASE_URL,
    HISTORIES,
    METHODS,
    MODEL,
    TOP_K,
    build_runtime,
    graph_namespace,
    load_coverage,
    public_session_metadata,
    read_episode_rows,
    read_json,
    write_new_json,
    _episode_mapping_for_retrieval,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-con-qa-20260823-001"


def _sha(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


async def run(args: argparse.Namespace) -> int:
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_3.longmemeval_answer_qa import evaluate_official_answer, paired_answer_outcome
    from saturated_fixed_work_baseline_v1_3.longmemeval_con_reader import ChainOfNoteReader
    from saturated_fixed_work_baseline_v1_3.longmemeval_fact_consolidation import load_longmemeval_records, select_longmemeval_cases
    from saturated_fixed_work_baseline_v1_3.longmemeval_session_qa import materialize_retrieved_sessions, persisted_episode_identity
    from paper_eval.graph_quality_judge import build_graph_quality_qwen_judge
    from paper_eval.graph_quality_transport import GraphQualityTransport
    from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1

    operation_root = args.operation_root.resolve()
    coverage = load_coverage(operation_root)
    cases = {case.question_id: case for case in select_longmemeval_cases(load_longmemeval_records())}
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"OUTPUT_ROOT_MUST_BE_NEW:{output_root}")
    output_root.mkdir(parents=True, exist_ok=False)
    env = dict(_load_env(args.repository_root.resolve()))
    embedding_key = str(env.get("EMBEDDING_API_KEY") or "")
    chat_key = str(env.get("CONSTRUCTION_LLM_API_KEY") or env.get("OPENAI_API_KEY") or "")
    if not embedding_key or not chat_key or not env.get("NEO4J_URI"):
        raise RuntimeError("READ_ONLY_RUNTIME_CONFIG_MISSING")
    runtime = await asyncio.to_thread(build_runtime, env)
    transport = GraphQualityTransport(model=MODEL, base_url=CHAT_BASE_URL, api_key=chat_key, timeout_seconds=180.0)
    reader = ChainOfNoteReader(model=MODEL, transport=transport)
    judge = build_graph_quality_qwen_judge(base_url=CHAT_BASE_URL, api_key=chat_key)
    write_new_json(output_root / "con_manifest.json", {
        "schema_version": "sfwb.v1.3.longmemeval-con-qa-manifest.v1",
        "status": "READ_ONLY_CON_QA_AUTHORIZED",
        "operation_root": str(operation_root),
        "history_ids": list(HISTORIES),
        "methods": list(METHODS),
        "retrieval": "EXISTING_QUALITY_V1_TOP20_RRF_GOLD_BLIND",
        "reader": reader.public_config,
        "reader_text_source": "READ_ONLY_NEO4J_EPISODIC_CONTENT_ONLY",
        "judge": {"implementation": "existing_graph_quality_private_longmemeval_judge", "base_url": CHAT_BASE_URL, "raw_output_persisted": False},
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    })
    rows: list[dict[str, Any]] = []
    try:
        for item in coverage:
            history = str(item["history_id"])
            method = str(item["method"])
            graph_path = Path(str(item["canonical_graph_path"])).resolve()
            graph = read_json(graph_path)
            if not isinstance(graph, Mapping):
                raise RuntimeError("CANONICAL_GRAPH_INVALID")
            namespace = graph_namespace(graph)
            case = cases[history]
            mapping = await _episode_mapping_for_retrieval(runtime.graphiti, namespace, case)
            retrieval = await retrieve_quality_v1(graph=runtime.graphiti, query=case.question, namespace=namespace, episode_uuid_to_session_id=mapping)
            episode_uuids = [episode.episode_uuid for episode in retrieval.episodes]
            episodic_rows, row_reads = await read_episode_rows(graphiti=runtime.graphiti, namespace=namespace, uuids=episode_uuids)
            sessions = materialize_retrieved_sessions(history_id=history, retrieved_episodes=retrieval.episodes, episodic_rows=episodic_rows, public_session_metadata=public_session_metadata(case), top_k=TOP_K)
            con_result = await reader.answer(sessions, question_date=case.question_date, question=case.question)
            judge_inputs = SimpleNamespace(run_id=output_root.name, history_id=history, question_type=case.question_type, question=case.question, reference_answer=str(case.gold_current_answer))
            judge_result = await judge.evaluate(hypothesis=con_result.answer, inputs=judge_inputs)
            raw = str(judge_result.pop("raw_output", ""))
            judge_result["output_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            judge_result["raw_output_persisted"] = False
            answer = evaluate_official_answer(expected_answer=case.gold_current_answer, reader_answer=con_result.answer, judge=judge_result)
            rows.append({
                "history_id": history,
                "method": method,
                "namespace": namespace,
                "canonical_graph_path": str(graph_path),
                "question": case.question,
                "question_date": case.question_date,
                "evidence_surface": "EPISODIC_CONTENT_FROM_NEO4J",
                "retrieval": {
                    "episode_count": len(retrieval.episodes),
                    "fact_count": len(retrieval.facts),
                    "graphiti_search_calls": retrieval.graphiti_search_calls,
                    "neo4j_read_requests": retrieval.neo4j_read_requests + row_reads,
                    "search_config_sha256": retrieval.search_config_sha256,
                    "session_content_identity_sha256": persisted_episode_identity(history_id=history, episodic_rows=episodic_rows),
                },
                "reader": con_result.to_artifact(),
                "reader_answer": con_result.answer,
                "official_judge": judge_result,
                "answer_evaluation": answer,
            })
    finally:
        await judge.aclose()
        await transport.aclose()
        await runtime.aclose()
    by_key = {(row["history_id"], row["method"]): row for row in rows}
    paired = []
    for history in HISTORIES:
        b0 = by_key[(history, METHODS[0])]["answer_evaluation"]
        b1 = by_key[(history, METHODS[1])]["answer_evaluation"]
        paired.append({"history_id": history, **paired_answer_outcome(b0, b1)})
    b0_pass = sum(row["answer_evaluation"]["status"] == "PASS" for row in rows if row["method"] == METHODS[0])
    b1_pass = sum(row["answer_evaluation"]["status"] == "PASS" for row in rows if row["method"] == METHODS[1])
    result = {
        "schema_version": "sfwb.v1.3.longmemeval-con-qa-results.v1",
        "status": "READ_ONLY_CON_QA_COMPLETE",
        "reader_authority": "LITERATURE_ALIGNED_JSON_CHAIN_OF_NOTE",
        "rows": rows,
        "paired": paired,
        "answer_accuracy": {"authority": "OFFICIAL_LONGMEMEVAL_JUDGE_ONLY", "b0_pass": b0_pass, "b1_pass": b1_pass, "b0_accuracy": b0_pass / 4, "b1_accuracy": b1_pass / 4, "scored": len(rows)},
        "b0_pass_b1_fail": [row["history_id"] for row in paired if row["concrete_b1_answer_failure"]],
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    }
    write_new_json(output_root / "con_results.json", result)
    write_new_json(output_root / "con_summary.json", {
        "status": result["status"],
        "b0_accuracy": result["answer_accuracy"]["b0_accuracy"],
        "b1_accuracy": result["answer_accuracy"]["b1_accuracy"],
        "b0_pass_b1_fail": result["b0_pass_b1_fail"],
        "paired_count": len(paired),
        "note_calls_total": sum(int(row["reader"].get("note_calls", 0)) for row in rows),
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    })
    print(json.dumps({"status": result["status"], "output": str(output_root), "b0_pass": b0_pass, "b1_pass": b1_pass}, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation-root", type=Path, default=DEFAULT_OPERATION_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT.parent)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
