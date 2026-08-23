#!/usr/bin/env python3
"""Bounded read-only Zep-shaped graph-native QA over current formal graphs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from run_longmemeval_session_value_qa import (
    CHAT_BASE_URL,
    EMBEDDING_BASE_URL,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    HISTORIES,
    METHODS,
    MODEL,
    build_runtime,
    graph_namespace,
    load_coverage,
    read_json,
    write_new_json,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPERATION_ROOT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-sf-operation-20260823-001"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-graph-native-qa-20260823-001"


def _hash(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


async def _episode_mapping(graphiti: Any, namespace: str, case: Any) -> tuple[dict[str, str], int]:
    from paper_eval.s2_retrieval_probe import ProbeCounters, _read_only_query_guard

    expected = {f"{case.history_id}::episode::{segment.source_sequence:04d}": segment.session_id for segment in case.segments}
    query = """
    MATCH (e:Episodic) WHERE e.group_id = $group_id
    RETURN e.uuid AS uuid, e.name AS name, e.group_id AS group_id
    """
    counters = ProbeCounters()
    with _read_only_query_guard(graphiti.driver, counters):
        result = await graphiti.driver.execute_query(query, params={"group_id": namespace}, routing_="r")
    records = getattr(result, "records", result[0] if isinstance(result, tuple) else result)
    if not isinstance(records, list):
        raise RuntimeError("EPISODE_MAPPING_RESULT_INVALID")
    mapping: dict[str, str] = {}
    for record in records:
        row = record if isinstance(record, Mapping) else dict(record)
        name = str(row.get("name") or "")
        uuid = str(row.get("uuid") or "")
        if name not in expected or not uuid or str(row.get("group_id") or "") != namespace:
            raise RuntimeError("EPISODE_MAPPING_FOREIGN")
        mapping[uuid] = expected[name]
    if len(mapping) != len(expected):
        raise RuntimeError("EPISODE_MAPPING_INCOMPLETE")
    return mapping, counters.neo4j_read_requests


async def run(args: argparse.Namespace) -> int:
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_3.longmemeval_answer_qa import evaluate_official_answer, paired_answer_outcome
    from saturated_fixed_work_baseline_v1_3.longmemeval_fact_consolidation import load_longmemeval_records, select_longmemeval_cases
    from saturated_fixed_work_baseline_v1_3.longmemeval_state_qa import inspect_longmemeval_current_state
    from paper_eval.graph_quality_judge import build_graph_quality_qwen_judge
    from paper_eval.graph_quality_transport import GraphQualityTransport
    from paper_eval.graphiti_longmemeval_quality import retrieve_graph_quality_evidence
    from paper_eval.temporal_fact_reader import TemporalFactReader

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
    write_new_json(output_root / "graph_native_manifest.json", {
        "schema_version": "sfwb.v1.3.longmemeval-graph-native-qa-manifest.v1",
        "status": "READ_ONLY_GRAPH_NATIVE_QA_AUTHORIZED",
        "operation_root": str(operation_root),
        "history_ids": list(HISTORIES),
        "methods": list(METHODS),
        "evidence_surface": "TOP_20_TEMPORAL_FACTS_PLUS_TOP_20_ENTITY_SUMMARIES",
        "literature_alignment": {
            "repository": "getzep/zep",
            "commit": "be263ee23085410185835e0d8508b47fd35e9abb",
            "source": "benchmarks/longmemeval/zep_longmem_eval.py",
            "selection_gold_blind": True,
        },
        "retrieval_embedding_base_url": EMBEDDING_BASE_URL,
        "reader_base_url": CHAT_BASE_URL,
        "judge_base_url": CHAT_BASE_URL,
        "headline_authority": "DIAGNOSTIC_ONLY_NOT_SESSION_VALUE_HEADLINE",
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    })
    runtime = await asyncio.to_thread(build_runtime, env)
    transport = GraphQualityTransport(model=MODEL, base_url=CHAT_BASE_URL, api_key=chat_key, timeout_seconds=180.0)
    reader = TemporalFactReader(model=MODEL, transport=transport)
    judge = build_graph_quality_qwen_judge(base_url=CHAT_BASE_URL, api_key=chat_key)
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
            mapping, mapping_reads = await _episode_mapping(runtime.graphiti, namespace, case)
            evidence = await retrieve_graph_quality_evidence(
                graph=runtime.graphiti,
                query=case.question,
                namespace=namespace,
                episode_uuid_to_session_id=mapping,
            )
            response = await reader.answer(evidence.facts, evidence.entities, question_date=case.question_date, question=case.question)
            inputs = SimpleNamespace(run_id=output_root.name, history_id=history, question_type=case.question_type, question=case.question, reference_answer=str(case.gold_current_answer))
            judge_result = await judge.evaluate(hypothesis=response.answer, inputs=inputs)
            raw = str(judge_result.pop("raw_output", ""))
            judge_result["output_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            judge_result["raw_output_persisted"] = False
            answer = evaluate_official_answer(expected_answer=case.gold_current_answer, reader_answer=response.answer, judge=judge_result)
            state = inspect_longmemeval_current_state(graph, expected_answer=case.gold_current_answer, observation_time=case.question_date, question=case.question)
            rows.append({
                "history_id": history,
                "method": method,
                "namespace": namespace,
                "canonical_graph_path": str(graph_path),
                "question": case.question,
                "reader_answer": response.answer,
                "reader": response.to_public_artifact(),
                "official_judge": judge_result,
                "answer_evaluation": answer,
                "state_diagnostic": state,
                "evidence": {
                    "fact_count": len(evidence.facts),
                    "entity_count": len(evidence.entities),
                    "graphiti_search_calls": evidence.graphiti_search_calls,
                    "neo4j_read_requests": evidence.neo4j_read_requests + mapping_reads,
                    "search_config_sha256": evidence.search_config_sha256,
                    "fact_fingerprints": [_hash({"fact": fact.fact, "valid_at": fact.valid_at, "invalid_at": fact.invalid_at, "expired_at": fact.expired_at}) for fact in evidence.facts],
                    "entity_fingerprints": [_hash({"name": entity.name, "summary": entity.summary}) for entity in evidence.entities],
                },
            })
    finally:
        await judge.aclose()
        await transport.aclose()
        await runtime.aclose()
    by_key = {(row["history_id"], row["method"]): row for row in rows}
    paired = []
    for history in HISTORIES:
        b0 = by_key[(history, METHODS[0])]
        b1 = by_key[(history, METHODS[1])]
        paired.append({"history_id": history, **paired_answer_outcome(b0["answer_evaluation"], b1["answer_evaluation"]), "state_status_b0": b0["state_diagnostic"]["status"], "state_status_b1": b1["state_diagnostic"]["status"]})
    result = {
        "schema_version": "sfwb.v1.3.longmemeval-graph-native-qa-results.v1",
        "status": "READ_ONLY_GRAPH_NATIVE_QA_COMPLETE",
        "headline_authority": "DIAGNOSTIC_ONLY_NOT_SESSION_VALUE_HEADLINE",
        "rows": rows,
        "paired": paired,
        "answer_accuracy": {
            "authority": "OFFICIAL_LONGMEMEVAL_JUDGE_ONLY",
            "b0_pass": sum(row["answer_evaluation"]["status"] == "PASS" for row in rows if row["method"] == METHODS[0]),
            "b1_pass": sum(row["answer_evaluation"]["status"] == "PASS" for row in rows if row["method"] == METHODS[1]),
            "scored": len(rows),
        },
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    }
    write_new_json(output_root / "graph_native_results.json", result)
    write_new_json(output_root / "graph_native_summary.json", {
        "status": result["status"],
        "b0_accuracy": result["answer_accuracy"]["b0_pass"] / 4,
        "b1_accuracy": result["answer_accuracy"]["b1_pass"] / 4,
        "b0_b1_answer_divergence": [row["history_id"] for row in paired if row["answer_divergence"]],
        "b0_b1_state_status_pairs": [{"history_id": row["history_id"], "b0": row["state_status_b0"], "b1": row["state_status_b1"]} for row in paired],
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    })
    print(json.dumps({"status": result["status"], "output": str(output_root), "b0_pass": result["answer_accuracy"]["b0_pass"], "b1_pass": result["answer_accuracy"]["b1_pass"]}, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation-root", type=Path, default=DEFAULT_OPERATION_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT.parent)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
