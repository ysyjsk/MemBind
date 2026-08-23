#!/usr/bin/env python3
"""Read-only graph-state QA over the four completed LongMemEval graph pairs.

This runner never calls ``add_episode`` and never writes Neo4j.  It reads only
the append-only LongMemEval operation freeze and the existing canonical graph
files, then uses Graphiti's guarded search path with embedding endpoint 8003
and the qualified graph-fact Reader on endpoint 8002.  The direct temporal
graph predicate is authoritative; Reader answers are diagnostic only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPERATION_ROOT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-sf-operation-20260823-001"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-state-qa-20260823-001"
MODEL = "qwen3-32b-fp8"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
EMBEDDING_DIM = 1024
ALT_CHAT_BASE_URL = "http://10.87.5.247:8002/v1"
ALT_EMBEDDING_BASE_URL = "http://10.87.5.247:8003/v1"
PAIRED_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
METHODS = ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError(f"JSON_UNREADABLE:{path}") from None
    return value


def write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(value)
    body["payload_sha256"] = sha256(body)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new_text(path: Path, value: str) -> None:
    if path.exists():
        raise RuntimeError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_payload(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    payload_hash = value.get("payload_sha256")
    if not isinstance(payload_hash, str):
        raise RuntimeError(f"{label}_PAYLOAD_HASH_MISSING")
    unsigned = {key: child for key, child in value.items() if key != "payload_sha256"}
    if payload_hash != sha256(unsigned):
        raise RuntimeError(f"{label}_PAYLOAD_HASH_MISMATCH")
    return unsigned


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


def graph_fact_context(facts: Any) -> str:
    evidence: list[dict[str, Any]] = []
    for rank, fact in enumerate(facts, start=1):
        evidence.append(
            {
                "evidence_type": "graph_fact",
                "retrieval_rank": rank,
                "raw_evidence": str(getattr(fact, "fact", "")),
                "source_id": str(getattr(fact, "edge_uuid", "")),
                "valid_at": getattr(fact, "valid_at", None),
                "invalid_at": getattr(fact, "invalid_at", None),
                "expired_at": getattr(fact, "expired_at", None),
                "reference_time": getattr(fact, "reference_time", None),
                "relation": str(getattr(fact, "relation_name", "")),
            }
        )
    if not evidence:
        evidence.append(
            {
                "evidence_type": "graph_query_result",
                "retrieval_rank": 1,
                "raw_evidence": "NO_MATCHING_GRAPH_FACTS",
                "source_id": "none",
            }
        )
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True)


async def episode_mapping(*, graphiti: Any, namespace: str, episodes: Any) -> tuple[dict[str, str], int]:
    from paper_eval.s2_retrieval_probe import ProbeCounters, _read_only_query_guard

    expected = {episode.name: episode.session_id for episode in episodes}
    counters = ProbeCounters()
    query = """
    MATCH (e:Episodic)
    WHERE e.group_id = $group_id
    RETURN e.uuid AS uuid, e.name AS name, e.group_id AS group_id
    """
    with _read_only_query_guard(graphiti.driver, counters):
        result = await graphiti.driver.execute_query(
            query,
            params={"group_id": namespace},
            routing_="r",
        )
    records = getattr(result, "records", result[0] if isinstance(result, tuple) else result)
    if not isinstance(records, list):
        raise RuntimeError("EPISODE_MAPPING_RESULT_INVALID")
    mapping: dict[str, str] = {}
    for record in records:
        row = record if isinstance(record, Mapping) else dict(record)
        name = str(row.get("name") or "")
        uuid = str(row.get("uuid") or "")
        if name not in expected or not uuid:
            raise RuntimeError(f"EPISODE_MAPPING_FOREIGN:{namespace}:{name}")
        mapping[uuid] = expected[name]
    if len(mapping) != len(episodes):
        raise RuntimeError(f"EPISODE_MAPPING_INCOMPLETE:{namespace}:{len(mapping)}:{len(episodes)}")
    return mapping, counters.neo4j_read_requests


def build_runtime(env: dict[str, str]) -> Any:
    from paper_eval import graph_quality_live

    graph_quality_live.EMBEDDING_BASE_URL = ALT_EMBEDDING_BASE_URL
    alternate = dict(env)
    alternate["EMBEDDING_BASE_URL"] = ALT_EMBEDDING_BASE_URL
    alternate["EMBEDDING_MODEL"] = EMBEDDING_MODEL
    alternate["EMBEDDING_DIM"] = str(EMBEDDING_DIM)
    return graph_quality_live.build_graph_quality_runtime(env=alternate)


def load_operation(operation_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest_raw = read_json(operation_root / "operation_manifest.json")
    coverage_raw = read_json(operation_root / "completed_graph_coverage.json")
    if not isinstance(manifest_raw, Mapping) or not isinstance(coverage_raw, Mapping):
        raise RuntimeError("OPERATION_ARTIFACT_OBJECT_REQUIRED")
    manifest = verify_payload(manifest_raw, label="OPERATION_MANIFEST")
    manifest["payload_sha256"] = str(manifest_raw["payload_sha256"])
    coverage = dict(coverage_raw)
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("OPERATION_CASES_INVALID")
    selected = {
        str(row.get("question_id")): dict(row)
        for row in cases
        if isinstance(row, Mapping)
        and str(row.get("question_id")) in PAIRED_HISTORIES
    }
    if set(selected) != set(PAIRED_HISTORIES):
        raise RuntimeError("OPERATION_PAIRED_CASE_COVERAGE_INVALID")
    rows = coverage.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("OPERATION_COVERAGE_ROWS_INVALID")
    paired_rows = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("history_id")) in PAIRED_HISTORIES
        and str(row.get("method")) in METHODS
    ]
    if len(paired_rows) != 8:
        raise RuntimeError(f"OPERATION_PAIRED_GRAPH_COUNT_INVALID:{len(paired_rows)}")
    return manifest, selected, {"rows": paired_rows, "coverage": coverage}


async def main_async(args: argparse.Namespace) -> int:
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_3.longmemeval_fact_consolidation import (
        build_episode_inputs,
        load_longmemeval_records,
        select_longmemeval_cases,
    )
    from saturated_fixed_work_baseline_v1_3.longmemeval_state_qa import (
        inspect_longmemeval_current_state,
        paired_state_outcome,
        reader_diagnostic_verdict,
    )
    from paper_eval.graph_quality_transport import GraphQualityTransport
    from paper_eval.quality_evaluation_v1_reader import QualityEvaluationV1Reader
    from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1

    operation_root = args.operation_root.resolve()
    manifest, frozen_rows, coverage = load_operation(operation_root)
    raw_cases = {
        case.question_id: case
        for case in select_longmemeval_cases(load_longmemeval_records())
    }
    if not set(frozen_rows).issubset(raw_cases) or any(
        raw_cases[key].source_record_sha256 != frozen_rows[key].get("source_record_sha256")
        for key in frozen_rows
    ):
        raise RuntimeError("OPERATION_SOURCE_IDENTITY_DRIFT")

    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"OUTPUT_ROOT_MUST_BE_NEW:{output_root}")
    output_root.mkdir(parents=True, exist_ok=False)
    run_manifest = {
        "schema_version": "sfwb.v1.3.longmemeval-state-qa-manifest.v1",
        "status": "READ_ONLY_GRAPH_STATE_QA_AUTHORIZED",
        "operation_manifest": str(operation_root / "operation_manifest.json"),
        "operation_manifest_payload_sha256": manifest["payload_sha256"],
        "history_ids": list(PAIRED_HISTORIES),
        "methods": list(METHODS),
        "graph_payloads_read": True,
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
        "qa_policy": {
            "evidence_surface": "GRAPH_FACTS_ONLY",
            "episode_evidence": "EXCLUDED_FROM_READER_CONTEXT",
            "entity_summary": "EXCLUDED_FROM_READER_CONTEXT",
            "direct_graph_predicate_authoritative": True,
            "reader_answer_diagnostic_only": True,
            "old_new_value_status": "NOT_PROVABLE",
            "alternate_chat_base_url": ALT_CHAT_BASE_URL,
            "alternate_embedding_base_url": ALT_EMBEDDING_BASE_URL,
            "reference_time_source": "EXISTING_V1_3_RAW_QUESTION_DATE_FOR_EXISTING_GRAPHS",
        },
        "coverage_rows": coverage["rows"],
    }
    write_new_json(output_root / "state_qa_manifest.json", run_manifest)

    env = dict(_load_env(args.repository_root.resolve()))
    if not env.get("EMBEDDING_API_KEY"):
        raise RuntimeError("EMBEDDING_API_KEY_MISSING")
    if not env.get("NEO4J_URI") or not env.get("NEO4J_USER") or not env.get("NEO4J_PASSWORD"):
        raise RuntimeError("NEO4J_CONFIG_MISSING")
    runtime = await asyncio.to_thread(build_runtime, env)
    transport = GraphQualityTransport(
        model=MODEL,
        base_url=ALT_CHAT_BASE_URL,
        api_key=str(env.get("CONSTRUCTION_LLM_API_KEY") or env.get("OPENAI_API_KEY") or ""),
        timeout_seconds=180.0,
    )
    reader = QualityEvaluationV1Reader(model=MODEL, transport=transport)
    rows: list[dict[str, Any]] = []
    try:
        for coverage_row in sorted(coverage["rows"], key=lambda row: (PAIRED_HISTORIES.index(str(row["history_id"])), METHODS.index(str(row["method"])) )):
            history_id = str(coverage_row["history_id"])
            method = str(coverage_row["method"])
            graph_path = Path(str(coverage_row["canonical_graph_path"])).resolve()
            if not graph_path.is_file():
                raise RuntimeError(f"CANONICAL_GRAPH_MISSING:{graph_path}")
            graph = read_json(graph_path)
            if not isinstance(graph, Mapping):
                raise RuntimeError(f"CANONICAL_GRAPH_INVALID:{graph_path}")
            namespace = graph_namespace(graph)
            case = raw_cases[history_id]
            episodes = build_episode_inputs(case, namespace)
            mapping, mapping_reads = await episode_mapping(
                graphiti=runtime.graphiti,
                namespace=namespace,
                episodes=episodes,
            )
            state = inspect_longmemeval_current_state(
                graph,
                expected_answer=case.gold_current_answer,
                observation_time=case.question_date,
                question=case.question,
            )
            retrieval = await retrieve_quality_v1(
                graph=runtime.graphiti,
                query=case.question,
                namespace=namespace,
                episode_uuid_to_session_id=mapping,
            )
            response = await reader.answer(
                context_json=graph_fact_context(retrieval.facts),
                question_date=case.question_date,
                question=case.question,
            )
            rows.append(
                {
                    "question_id": history_id,
                    "history_id": history_id,
                    "method": method,
                    "namespace": namespace,
                    "canonical_graph_path": str(graph_path),
                    "question": case.question,
                    "official_current_answer": case.gold_current_answer,
                    "state_inspection": state,
                    "reader_answer": response.answer,
                    "reader_diagnostic": reader_diagnostic_verdict(case.gold_current_answer, response.answer),
                    "reader": {
                        "model": response.model,
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                        "finish_reason": response.finish_reason,
                        "config_sha256": response.config_sha256,
                    },
                    "retrieval": {
                        "fact_count": len(retrieval.facts),
                        "episode_count": len(retrieval.episodes),
                        "facts": [
                            {
                                "rank": fact.retrieval_rank,
                                "fact": fact.fact,
                                "relation": fact.relation_name,
                                "valid_at": fact.valid_at,
                                "invalid_at": fact.invalid_at,
                                "expired_at": fact.expired_at,
                            }
                            for fact in retrieval.facts
                        ],
                        "search_config_sha256": retrieval.search_config_sha256,
                        "graphiti_search_calls": retrieval.graphiti_search_calls,
                        "neo4j_read_requests": retrieval.neo4j_read_requests + mapping_reads,
                    },
                }
            )
        paired: list[dict[str, Any]] = []
        by_history_method = {(str(row["history_id"]), str(row["method"])): row for row in rows}
        for history_id in PAIRED_HISTORIES:
            b0 = by_history_method[(history_id, METHODS[0])]["state_inspection"]
            b1 = by_history_method[(history_id, METHODS[1])]["state_inspection"]
            paired.append({"history_id": history_id, **paired_state_outcome(b0, b1)})
        b0_eligible = all(row["b0_status"] == "PASS" for row in paired)
        concrete_divergence = [row["history_id"] for row in paired if row["b1_semantic_failure"]]
        if not b0_eligible:
            decision = "STOP_LONGMEMEVAL_B0_STATE_PREDICATE_INELIGIBLE"
        elif concrete_divergence:
            decision = "PAIRED_B1_STATE_DIVERGENCE_OBSERVED_REPLICATION_REQUIRED"
        else:
            decision = "NO_PAIRED_STATE_DIVERGENCE_ON_COMPLETED_GRAPHS"
        payload = {
            "schema_version": "sfwb.v1.3.longmemeval-state-qa-results.v1",
            "status": "READ_ONLY_GRAPH_STATE_QA_COMPLETE",
            "decision": decision,
            "operation_manifest_payload_sha256": manifest["payload_sha256"],
            "rows": rows,
            "paired": paired,
            "row_count": len(rows),
            "construction_calls": 0,
            "graph_writes": 0,
            "v5_started": False,
            "reader_semantic_authority": False,
        }
        write_new_json(output_root / "state_qa_results.json", payload)
        summary = {
            "status": payload["status"],
            "decision": decision,
            "row_count": len(rows),
            "paired_history_count": len(paired),
            "b0_pass_count": sum(row["b0_status"] == "PASS" for row in paired),
            "b1_pass_count": sum(row["b1_status"] == "PASS" for row in paired),
            "state_divergence_histories": [row["history_id"] for row in paired if row["state_divergence"]],
            "b0_pass_b1_fail_histories": concrete_divergence,
            "reader_diagnostic_match_count": sum(bool(row["reader_diagnostic"]["expected_match"]) for row in rows),
            "construction_calls": 0,
            "graph_writes": 0,
            "v5_started": False,
        }
        write_new_json(output_root / "state_qa_summary.json", summary)
        write_new_text(
            output_root / "state_qa_decision.txt",
            decision + "\n",
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    finally:
        await transport.aclose()
        await runtime.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation-root", type=Path, default=DEFAULT_OPERATION_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT.parent)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
