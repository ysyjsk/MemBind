#!/usr/bin/env python3
"""Read-only graph-state challenge QA over already sealed MemOps attempts.

This runner never calls Graphiti construction and never mutates a namespace.
It reuses the frozen MemOps gold cohort, queries the completed namespaces with
Graphiti's read-only search path, and sends graph-fact-only context to a Qwen
reader on the alternate 8002 endpoint.  Graphiti embedding search is pinned to
the alternate 8003 endpoint for this exploratory challenge lane.

The output is exploratory evidence, not a replacement for the frozen official
MemOps qualification.  Selection is the intersection of two completed result
manifests and is recorded explicitly; no B1 answer is used to select samples.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ALT_CHAT_BASE_URL = "http://10.87.5.247:8002/v1"
ALT_EMBEDDING_BASE_URL = "http://10.87.5.247:8003/v1"
MODEL = "qwen3-32b-fp8"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
EMBEDDING_DIM = 1024
QUESTION_DATE = "2000-01-01T00:10:00Z"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def load_result(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    result = read_json(path)
    outputs = result.get("outputs")
    if not isinstance(outputs, list):
        raise RuntimeError(f"RESULT_OUTPUTS_INVALID:{path}")
    by_sample: dict[str, dict[str, Any]] = {}
    for row in outputs:
        if not isinstance(row, dict) or not isinstance(row.get("sample_id"), str):
            continue
        by_sample[str(row["sample_id"])] = row
    return result, by_sample


def challenge_questions(sample: Any) -> tuple[dict[str, Any], ...]:
    """Create fixed gold-derived questions; no runtime result is consulted."""

    expected = str(sample.latest_confirmed_value)
    stale = tuple(str(value) for value in sample.stale_confirmed_values)
    target = str(sample.target_name)
    rows: list[dict[str, Any]] = [
        {
            "question_id": f"{sample.sample_id}:current-edge",
            "kind": "CURRENT_EDGE_VALUE",
            "question": (
                "Graph-only state question. Ignore episode text and entity summaries. "
                f"At {QUESTION_DATE}, what is the current confirmed value of {target}? "
                "Use only active temporal graph facts and return only the value."
            ),
            "expected_answer": expected,
            "stale_values": list(stale),
            "target_id": sample.target_id,
            "target_name": target,
        }
    ]
    if stale:
        options = [expected, *stale]
        rows.append(
            {
                "question_id": f"{sample.sample_id}:current-vs-stale",
                "kind": "CURRENT_VS_STALE",
                "question": (
                    "Graph-only temporal-state question. Ignore episode text and entity summaries. "
                    f"At {QUESTION_DATE}, which one of these values is the active current value for {target}? "
                    "Return exactly one option and do not return a historical value.\n"
                    + "Options:\n"
                    + "\n".join(f"- {value}" for value in options)
                ),
                "expected_answer": expected,
                "stale_values": list(stale),
                "target_id": sample.target_id,
                "target_name": target,
            }
        )
    return tuple(rows)


def graph_fact_context(facts: Any) -> str:
    evidence: list[dict[str, Any]] = []
    for rank, fact in enumerate(facts, start=1):
        evidence.append(
            {
                "evidence_type": "graph_fact",
                "retrieval_rank": rank,
                "raw_evidence": str(getattr(fact, "fact", "")),
                "timestamp": getattr(fact, "reference_time", None) or getattr(fact, "valid_at", None),
                "source_id": str(getattr(fact, "edge_uuid", "")),
                "valid_at": getattr(fact, "valid_at", None),
                "invalid_at": getattr(fact, "invalid_at", None),
                "expired_at": getattr(fact, "expired_at", None),
                "relation": str(getattr(fact, "relation_name", "")),
            }
        )
    if not evidence:
        evidence.append(
            {
                "evidence_type": "graph_query_result",
                "raw_evidence": "NO_MATCHING_GRAPH_FACTS",
                "retrieval_rank": 1,
                "source_id": "none",
            }
        )
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True)


def answer_matches(question: Mapping[str, Any], answer: str) -> dict[str, Any]:
    text = norm(answer)
    expected = norm(question["expected_answer"])
    stale = [norm(value) for value in question.get("stale_values", []) if norm(value)]
    expected_match = bool(expected) and expected in text
    stale_matches = [value for value in stale if value in text]
    return {
        "answer_normalized": text,
        "expected_match": expected_match,
        "stale_matches": stale_matches,
        "challenge_answer_pass": bool(expected_match and not stale_matches),
    }


async def episode_mapping(*, graph: Any, namespace: str, episodes: Any) -> dict[str, str]:
    expected = {episode.name: episode.session_id for episode in episodes}
    query = await graph.driver.execute_query(
        "MATCH (e:Episodic) WHERE e.group_id = $group_id RETURN e.uuid AS uuid, e.name AS name",
        params={"group_id": namespace},
        routing_="r",
    )
    records = getattr(query, "records", query[0] if isinstance(query, tuple) else query)
    mapping: dict[str, str] = {}
    for record in records:
        value = record if isinstance(record, Mapping) else dict(record)
        name = str(value["name"])
        if name not in expected:
            raise RuntimeError(f"EPISODE_NAME_NOT_IN_FROZEN_WORKLOAD:{namespace}:{name}")
        mapping[str(value["uuid"])] = expected[name]
    if len(mapping) != len(episodes):
        raise RuntimeError(f"EPISODE_MAPPING_INCOMPLETE:{namespace}:{len(mapping)}:{len(episodes)}")
    return mapping


def build_runtime(env: dict[str, str]) -> Any:
    from paper_eval import graph_quality_live

    # The normal runtime remains pinned to 8001.  This child process owns the
    # alternate endpoint override and never changes the production module on
    # disk or the construction process environment.
    graph_quality_live.EMBEDDING_BASE_URL = ALT_EMBEDDING_BASE_URL
    alternate = dict(env)
    alternate["EMBEDDING_BASE_URL"] = ALT_EMBEDDING_BASE_URL
    alternate["EMBEDDING_MODEL"] = EMBEDDING_MODEL
    alternate["EMBEDDING_DIM"] = str(EMBEDDING_DIM)
    return graph_quality_live.build_graph_quality_runtime(env=alternate)


async def main_async(args: argparse.Namespace) -> int:
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_3.memops_adapter import build_episode_inputs, inspect_current_state
    from saturated_fixed_work_baseline_v1_3.memops_qualification import _load_frozen_samples
    from paper_eval.graph_quality_transport import GraphQualityTransport
    from paper_eval.quality_evaluation_v1_reader import QualityEvaluationV1Reader
    from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1

    b0_result, b0 = load_result(args.b0_result.resolve())
    b1_result, b1 = load_result(args.b1_result.resolve())
    cohort_root = args.audit_root.resolve() / "replication_cohort"
    samples = {sample.sample_id: sample for sample in _load_frozen_samples(cohort_root)}
    common_ids = sorted(set(samples) & set(b0) & set(b1))
    if not common_ids:
        raise RuntimeError("NO_COMMON_COMPLETED_SAMPLES")

    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"OUTPUT_ROOT_MUST_BE_NEW:{output_root}")
    output_root.mkdir(parents=True, exist_ok=False)

    manifest = {
        "schema_version": "sfwb.v1.3.memops-state-attack-qa-manifest.v1",
        "status": "EXPLORATORY_READ_ONLY_CHALLENGE",
        "selection_basis": "intersection_of_completed_B0_B1_result_manifests_and_frozen_gold_cohort",
        "sample_ids": common_ids,
        "sample_count": len(common_ids),
        "methods": [b0_result.get("method"), b1_result.get("method")],
        "b0_result": str(args.b0_result.resolve()),
        "b1_result": str(args.b1_result.resolve()),
        "qa_policy": {
            "construction": "FORBIDDEN",
            "episode_evidence": "EXCLUDED_FROM_READER_CONTEXT",
            "entity_summary": "EXCLUDED_FROM_READER_CONTEXT",
            "graph_facts_only": True,
            "read_only": True,
            "alternate_chat_base_url": ALT_CHAT_BASE_URL,
            "alternate_embedding_base_url": ALT_EMBEDDING_BASE_URL,
            "not_official_memops_qualification": True,
        },
        "question_kinds": ["CURRENT_EDGE_VALUE", "CURRENT_VS_STALE"],
    }
    (output_root / "challenge_manifest.json").write_text(
        json.dumps({**manifest, "payload_sha256": sha256(manifest)}, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    env = dict(_load_env(args.repository_root.resolve()))
    if not env.get("CONSTRUCTION_LLM_API_KEY"):
        raise RuntimeError("CONSTRUCTION_LLM_API_KEY_MISSING")
    if not env.get("EMBEDDING_API_KEY"):
        raise RuntimeError("EMBEDDING_API_KEY_MISSING")

    runtime = await asyncio.to_thread(build_runtime, env)
    transport = GraphQualityTransport(
        model=MODEL,
        base_url=ALT_CHAT_BASE_URL,
        api_key=str(env["CONSTRUCTION_LLM_API_KEY"]),
        timeout_seconds=180.0,
    )
    reader = QualityEvaluationV1Reader(model=MODEL, transport=transport)
    rows: list[dict[str, Any]] = []
    try:
        for method, outputs in (("B0_NATIVE_SERIAL", b0), ("B1_NAIVE_WHOLE_UPDATE_ASYNC", b1)):
            for sample_id in common_ids:
                sample = samples[sample_id]
                output = outputs[sample_id]
                attempt_root = Path(str(output["attempt_root"]))
                graph = read_json(attempt_root / "canonical_graph.json")
                namespace = str(output["namespace"])
                episodes = build_episode_inputs(sample, namespace)
                mapping = await episode_mapping(graph=runtime.graphiti, namespace=namespace, episodes=episodes)
                state = inspect_current_state(sample, graph)
                for question in challenge_questions(sample):
                    retrieval = await retrieve_quality_v1(
                        graph=runtime.graphiti,
                        query=str(question["question"]),
                        namespace=namespace,
                        episode_uuid_to_session_id=mapping,
                    )
                    response = await reader.answer(
                        context_json=graph_fact_context(retrieval.facts),
                        question_date=QUESTION_DATE,
                        question=str(question["question"]),
                    )
                    verdict = answer_matches(question, response.answer)
                    rows.append(
                        {
                            "sample_id": sample_id,
                            "method": method,
                            "namespace": namespace,
                            "attempt_root": str(attempt_root),
                            "question": question,
                            "answer": response.answer,
                            "answer_verdict": verdict,
                            "state_inspection": state,
                            "retrieval": {
                                "fact_count": len(retrieval.facts),
                                "episode_count_discarded": len(retrieval.episodes),
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
                                "neo4j_read_requests": retrieval.neo4j_read_requests,
                            },
                            "reader": {
                                "model": response.model,
                                "prompt_tokens": response.prompt_tokens,
                                "completion_tokens": response.completion_tokens,
                                "finish_reason": response.finish_reason,
                                "config_sha256": response.config_sha256,
                            },
                        }
                    )
        payload = {
            "schema_version": "sfwb.v1.3.memops-state-attack-qa-results.v1",
            "status": "LIVE_READ_ONLY_COMPLETE",
            "manifest_sha256": sha256(manifest),
            "rows": rows,
            "row_count": len(rows),
            "v5_started": False,
            "construction_calls": 0,
            "graph_writes": 0,
        }
        (output_root / "challenge_results.json").write_text(
            json.dumps({**payload, "payload_sha256": sha256(payload)}, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        summary = {
            "status": payload["status"],
            "row_count": len(rows),
            "answer_pass_count": sum(bool(row["answer_verdict"]["challenge_answer_pass"]) for row in rows),
            "state_pass_count": sum((row["state_inspection"].get("status") == "PASS") for row in rows),
            "by_method": {
                method: {
                    "rows": sum(row["method"] == method for row in rows),
                    "answer_pass": sum(row["method"] == method and row["answer_verdict"]["challenge_answer_pass"] for row in rows),
                    "state_pass": sum(row["method"] == method and row["state_inspection"].get("status") == "PASS" for row in rows),
                }
                for method in ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")
            },
        }
        (output_root / "challenge_summary.json").write_text(
            json.dumps({**summary, "payload_sha256": sha256(summary)}, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    finally:
        await transport.aclose()
        await runtime.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--b0-result", type=Path, required=True)
    parser.add_argument("--b1-result", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
