#!/usr/bin/env python3
"""Bounded, read-only LongMemEval session-value QA autoresearch lane.

The runner reuses the qualified Graphiti retrieval, official LongMemEval
session Reader, and official Judge.  It never calls ``add_episode`` and never
writes Neo4j.  Only persisted ``EpisodicNode.content`` is placed in the
Reader prompt; source records are used for question/public session metadata
and post-hoc session-recall metrics only.
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
DEFAULT_OPERATION_ROOT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-sf-operation-20260823-001"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-session-value-qa-20260823-001"
MODEL = "qwen3-32b-fp8"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
EMBEDDING_DIM = 1024
CHAT_BASE_URL = "http://10.87.5.247:8002/v1"
EMBEDDING_BASE_URL = "http://10.87.5.247:8003/v1"
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
METHODS = ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")
TOP_K = 10


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError(f"JSON_UNREADABLE:{path}") from None


def write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(value)
    body["payload_sha256"] = _sha(body)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


def graph_namespace(graph: Mapping[str, Any]) -> str:
    groups: set[str] = set()
    for field in ("entities", "edges"):
        rows = graph.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping) and row.get("group_id"):
                groups.add(str(row["group_id"]))
    if len(groups) != 1:
        raise RuntimeError(f"GRAPH_NAMESPACE_INVALID:{sorted(groups)}")
    return next(iter(groups))


def load_coverage(operation_root: Path) -> list[dict[str, Any]]:
    coverage = read_json(operation_root / "completed_graph_coverage.json")
    rows = coverage.get("rows") if isinstance(coverage, Mapping) else None
    if not isinstance(rows, list):
        raise RuntimeError("OPERATION_COVERAGE_INVALID")
    selected = [dict(row) for row in rows if isinstance(row, Mapping) and str(row.get("history_id")) in HISTORIES and str(row.get("method")) in METHODS]
    expected = {(history, method) for history in HISTORIES for method in METHODS}
    actual = {(str(row.get("history_id")), str(row.get("method"))) for row in selected}
    if actual != expected or len(selected) != 8:
        raise RuntimeError(f"OPERATION_PAIRED_COVERAGE_INVALID:{sorted(actual)}")
    return sorted(selected, key=lambda row: (HISTORIES.index(str(row["history_id"])), METHODS.index(str(row["method"]))))


def build_runtime(env: dict[str, str]) -> Any:
    from paper_eval import graph_quality_live

    graph_quality_live.EMBEDDING_BASE_URL = EMBEDDING_BASE_URL
    alternate = dict(env)
    alternate["EMBEDDING_BASE_URL"] = EMBEDDING_BASE_URL
    alternate["EMBEDDING_MODEL"] = EMBEDDING_MODEL
    alternate["EMBEDDING_DIM"] = str(EMBEDDING_DIM)
    return graph_quality_live.build_graph_quality_runtime(env=alternate)


async def read_episode_rows(*, graphiti: Any, namespace: str, uuids: list[str]) -> tuple[dict[str, dict[str, Any]], int]:
    from paper_eval.s2_retrieval_probe import ProbeCounters, _read_only_query_guard

    if not uuids or len(set(uuids)) != len(uuids):
        raise RuntimeError("EPISODE_UUIDS_INVALID")
    query = """
    MATCH (e:Episodic)
    WHERE e.group_id = $group_id AND e.uuid IN $episode_uuids
    RETURN e.uuid AS uuid, e.name AS name, e.group_id AS group_id, e.content AS content
    ORDER BY e.name, e.uuid
    """
    counters = ProbeCounters()
    with _read_only_query_guard(graphiti.driver, counters):
        result = await graphiti.driver.execute_query(
            query,
            params={"group_id": namespace, "episode_uuids": uuids},
            routing_="r",
        )
    records = getattr(result, "records", result[0] if isinstance(result, tuple) else result)
    if not isinstance(records, list):
        raise RuntimeError("EPISODE_ROWS_RESULT_INVALID")
    rows: dict[str, dict[str, Any]] = {}
    for record in records:
        row = record if isinstance(record, Mapping) else dict(record)
        uuid = str(row.get("uuid") or "")
        if uuid not in uuids or uuid in rows or str(row.get("group_id") or "") != namespace:
            raise RuntimeError("EPISODE_ROW_NAMESPACE_OR_ID_INVALID")
        if not isinstance(row.get("name"), str) or not isinstance(row.get("content"), str):
            raise RuntimeError("EPISODE_ROW_CONTENT_INVALID")
        rows[uuid] = {"name": row["name"], "content": row["content"]}
    if set(rows) != set(uuids):
        raise RuntimeError(f"EPISODE_ROWS_INCOMPLETE:{len(rows)}:{len(uuids)}")
    return rows, counters.neo4j_read_requests


def public_session_metadata(case: Any) -> dict[int, dict[str, str]]:
    """Expose only identifiers/dates, never source bodies, to the adapter."""

    return {
        int(segment.source_sequence): {
            "session_id": str(segment.session_id),
            "session_date": str(segment.original_date),
        }
        for segment in case.segments
    }


async def run(args: argparse.Namespace) -> int:
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_3.longmemeval_answer_qa import evaluate_official_answer, paired_answer_outcome
    from saturated_fixed_work_baseline_v1_3.longmemeval_fact_consolidation import load_longmemeval_records, select_longmemeval_cases
    from saturated_fixed_work_baseline_v1_3.longmemeval_session_qa import materialize_retrieved_sessions, persisted_episode_identity
    from paper_eval.graph_quality_judge import build_graph_quality_qwen_judge
    from paper_eval.graph_quality_transport import GraphQualityTransport
    from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1
    from paper_eval.s2_session_reader import OfficialSessionReader

    operation_root = args.operation_root.resolve()
    coverage_rows = load_coverage(operation_root)
    cases = {case.question_id: case for case in select_longmemeval_cases(load_longmemeval_records())}
    if set(cases).intersection(HISTORIES) != set(HISTORIES):
        raise RuntimeError("LONGMEMEVAL_CASE_COVERAGE_INVALID")

    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"OUTPUT_ROOT_MUST_BE_NEW:{output_root}")
    output_root.mkdir(parents=True, exist_ok=False)
    env = dict(_load_env(args.repository_root.resolve()))
    embedding_key = str(env.get("EMBEDDING_API_KEY") or "")
    chat_key = str(env.get("CONSTRUCTION_LLM_API_KEY") or env.get("OPENAI_API_KEY") or "")
    if not embedding_key or not chat_key or not env.get("NEO4J_URI"):
        raise RuntimeError("READ_ONLY_RUNTIME_CONFIG_MISSING")

    manifest = {
        "schema_version": "sfwb.v1.3.longmemeval-session-value-qa-manifest.v1",
        "status": "READ_ONLY_SESSION_VALUE_QA_AUTHORIZED",
        "operation_root": str(operation_root),
        "history_ids": list(HISTORIES),
        "methods": list(METHODS),
        "top_k_context": TOP_K,
        "retrieval": {
            "implementation": "existing_quality_v1_retrieve_quality_v1",
            "graphiti_search_limit": 20,
            "edge_and_episode_rrf": True,
            "embedding_base_url": EMBEDDING_BASE_URL,
            "gold_inputs_during_selection": False,
        },
        "reader": {
            "implementation": "paper_eval.s2_session_reader.OfficialSessionReader",
            "base_url": CHAT_BASE_URL,
            "model": MODEL,
            "official_session_value_contract": True,
            "source_text": "READ_ONLY_NEO4J_EPISODIC_CONTENT_ONLY",
            "raw_source_bodies_in_reader_context": False,
        },
        "judge": {
            "implementation": "existing_graph_quality_private_longmemeval_judge",
            "base_url": CHAT_BASE_URL,
            "raw_output_persisted": False,
        },
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    }
    write_new_json(output_root / "session_value_manifest.json", manifest)

    runtime = await asyncio.to_thread(build_runtime, env)
    transport = GraphQualityTransport(model=MODEL, base_url=CHAT_BASE_URL, api_key=chat_key, timeout_seconds=180.0)
    reader = OfficialSessionReader(model=MODEL, transport=transport)
    judge = build_graph_quality_qwen_judge(base_url=CHAT_BASE_URL, api_key=chat_key)
    rows: list[dict[str, Any]] = []
    try:
        for coverage in coverage_rows:
            history_id = str(coverage["history_id"])
            method = str(coverage["method"])
            graph_path = Path(str(coverage["canonical_graph_path"])).resolve()
            graph = read_json(graph_path)
            if not isinstance(graph, Mapping):
                raise RuntimeError(f"CANONICAL_GRAPH_INVALID:{graph_path}")
            namespace = graph_namespace(graph)
            case = cases[history_id]
            retrieval = await retrieve_quality_v1(
                graph=runtime.graphiti,
                query=case.question,
                namespace=namespace,
                episode_uuid_to_session_id=await _episode_mapping_for_retrieval(runtime.graphiti, namespace, case),
            )
            episode_uuids = [episode.episode_uuid for episode in retrieval.episodes]
            episodic_rows, row_reads = await read_episode_rows(graphiti=runtime.graphiti, namespace=namespace, uuids=episode_uuids)
            sessions = materialize_retrieved_sessions(
                history_id=history_id,
                retrieved_episodes=retrieval.episodes,
                episodic_rows=episodic_rows,
                public_session_metadata=public_session_metadata(case),
                top_k=TOP_K,
            )
            ranked_session_ids = [item.session_id for item in sorted(sessions, key=lambda item: item.retrieval_rank)]
            reader_result = await reader.answer(sessions, question_date=case.question_date, question=case.question)
            inputs = SimpleNamespace(
                run_id=output_root.name,
                history_id=history_id,
                question_type=case.question_type,
                question=case.question,
                reference_answer=str(case.gold_current_answer),
            )
            judge_result = await judge.evaluate(hypothesis=reader_result.answer, inputs=inputs)
            raw_judge = str(judge_result.pop("raw_output", ""))
            judge_result["output_sha256"] = hashlib.sha256(raw_judge.encode("utf-8")).hexdigest()
            judge_result["raw_output_persisted"] = False
            answer = evaluate_official_answer(expected_answer=case.gold_current_answer, reader_answer=reader_result.answer, judge=judge_result)
            reader_artifact = reader_result.to_artifact()
            rows.append({
                "history_id": history_id,
                "method": method,
                "namespace": namespace,
                "canonical_graph_path": str(graph_path),
                "question": case.question,
                "question_date": case.question_date,
                "evidence_surface": "EPISODIC_CONTENT_FROM_NEO4J",
                "retrieval": {
                    "graphiti_search_calls": retrieval.graphiti_search_calls,
                    "neo4j_read_requests": retrieval.neo4j_read_requests + row_reads,
                    "fact_count": len(retrieval.facts),
                    "episode_count": len(retrieval.episodes),
                    "top_k_context": TOP_K,
                    "ranked_session_ids": ranked_session_ids,
                    "session_content_identity_sha256": persisted_episode_identity(history_id=history_id, episodic_rows=episodic_rows),
                    "search_config_sha256": retrieval.search_config_sha256,
                },
                "session_materialization": [
                    {
                        "retrieval_rank": item.retrieval_rank,
                        "session_id": item.session_id,
                        "turn_count": len(item.turns),
                        "session_date": item.session_date,
                        "source_sequence": int(str(episodic_rows[retrieval.episodes[item.retrieval_rank - 1].episode_uuid]["name"]).rsplit("::", 1)[-1]),
                        "content_sha256": hashlib.sha256(str(episodic_rows[retrieval.episodes[item.retrieval_rank - 1].episode_uuid]["content"]).encode("utf-8")).hexdigest(),
                    }
                    for item in sessions
                ],
                "reader": reader_artifact,
                "reader_answer": reader_result.answer,
                "official_judge": judge_result,
                "answer_evaluation": answer,
                "session_recall_posthoc": _session_recall(ranked_session_ids, case.answer_session_ids),
            })
    finally:
        await judge.aclose()
        await transport.aclose()
        await runtime.aclose()

    paired: list[dict[str, Any]] = []
    by_key = {(row["history_id"], row["method"]): row for row in rows}
    for history in HISTORIES:
        b0 = by_key[(history, METHODS[0])]["answer_evaluation"]
        b1 = by_key[(history, METHODS[1])]["answer_evaluation"]
        paired.append({"history_id": history, **paired_answer_outcome(b0, b1)})
    b0 = [row for row in rows if row["method"] == METHODS[0]]
    b1 = [row for row in rows if row["method"] == METHODS[1]]
    result = {
        "schema_version": "sfwb.v1.3.longmemeval-session-value-qa-results.v1",
        "status": "READ_ONLY_SESSION_VALUE_QA_COMPLETE",
        "rows": rows,
        "paired": paired,
        "answer_accuracy": {
            "authority": "OFFICIAL_LONGMEMEVAL_JUDGE_ONLY",
            "b0_pass": sum(row["answer_evaluation"]["status"] == "PASS" for row in b0),
            "b0_scored": len(b0),
            "b1_pass": sum(row["answer_evaluation"]["status"] == "PASS" for row in b1),
            "b1_scored": len(b1),
        },
        "b0_b1_concrete_answer_divergence": [row["history_id"] for row in paired if row["concrete_b1_answer_failure"]],
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    }
    write_new_json(output_root / "session_value_results.json", result)
    write_new_json(output_root / "session_value_summary.json", {
        "status": result["status"],
        "b0_accuracy": result["answer_accuracy"]["b0_pass"] / result["answer_accuracy"]["b0_scored"],
        "b1_accuracy": result["answer_accuracy"]["b1_pass"] / result["answer_accuracy"]["b1_scored"],
        "b0_b1_concrete_answer_divergence": result["b0_b1_concrete_answer_divergence"],
        "paired_count": len(paired),
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    })
    print(json.dumps({"status": result["status"], "output": str(output_root), "b0_pass": result["answer_accuracy"]["b0_pass"], "b1_pass": result["answer_accuracy"]["b1_pass"]}, ensure_ascii=False), flush=True)
    return 0


async def _episode_mapping_for_retrieval(graphiti: Any, namespace: str, case: Any) -> dict[str, str]:
    """Build the required UUID provenance map without exposing bodies."""

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
        raise RuntimeError(f"EPISODE_MAPPING_INCOMPLETE:{len(mapping)}:{len(expected)}")
    return mapping


def _session_recall(ranked: list[str], gold: tuple[str, str]) -> dict[str, Any]:
    gold_set = set(gold)
    ranks = [index for index, value in enumerate(ranked, start=1) if value in gold_set]
    return {
        "gold_session_count": len(gold_set),
        "gold_hits_at_10": len(ranks),
        "gold_ranks": ranks,
        "recall_at_10": len(ranks) / len(gold_set) if gold_set else 0.0,
        "authority": "POST_HOC_PROVENANCE_METRIC_ONLY",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation-root", type=Path, default=DEFAULT_OPERATION_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT.parent)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
