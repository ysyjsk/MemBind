#!/usr/bin/env python3
"""Run the final read-only QA sidecar for sealed V6 repetitions.

The command never constructs Graphiti data and never writes Neo4j.  It uses
the existing Quality-v1 retrieval, pinned LongMemEval Reader/Judge adapters,
and only persisted ``EpisodicNode.content`` as model-visible source text.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from saturated_fixed_work_baseline_v1_3.membind_v6.final_qa import (  # noqa: E402
    EXPECTED_HISTORY_ID,
    EXPECTED_SOURCE_COUNT,
    V6FinalQAError,
    create_fresh_output_root,
    final_qa_verdict,
    graph_namespace,
    payload_sha256,
    read_json,
    retrieval_identity_sha256,
    tree_sha256,
    validate_candidates,
    validate_persisted_episode_rows,
    write_new_json,
)


DEFAULT_BASELINE_GRAPH = (
    REPO_ROOT
    / "artifacts/sfwb-v1-3-formal-baseline-20260822-002"
    / "blocks/formal-005-6071bd76-B0_NATIVE_SERIAL/attempt-001/canonical_graph.json"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/qa/v6-final-qa-20260823-001"
CHAT_BASE_URL = "http://10.87.5.247:8000/v1"
EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1"
MODEL = "qwen3-32b-fp8"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
TOP_K = 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-root",
        type=Path,
        action="append",
        required=True,
        help="A sealed V6 candidate root. Repeat for independent repetitions.",
    )
    parser.add_argument("--baseline-graph", type=Path, default=DEFAULT_BASELINE_GRAPH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT.parent)
    parser.add_argument("--chat-base-url", default=CHAT_BASE_URL)
    parser.add_argument("--embedding-base-url", default=EMBEDDING_BASE_URL)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    return parser


def _records(result: Any) -> list[dict[str, Any]]:
    values = getattr(result, "records", None)
    if values is None and isinstance(result, tuple) and result:
        values = result[0]
    if values is None and isinstance(result, list):
        values = result
    if not isinstance(values, list):
        raise V6FinalQAError("Neo4j read result has invalid shape")
    return [value if isinstance(value, dict) else dict(value) for value in values]


async def _read_persisted_episodes(
    *, graphiti: Any, namespace: str, expected_name_to_session_id: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, dict[str, str]], int]:
    from paper_eval.s2_retrieval_probe import ProbeCounters, _read_only_query_guard

    query = """
    MATCH (e:Episodic)
    WHERE e.group_id = $group_id
    RETURN e.uuid AS uuid, e.name AS name, e.group_id AS group_id, e.content AS content
    ORDER BY e.name, e.uuid
    """
    counters = ProbeCounters()
    with _read_only_query_guard(graphiti.driver, counters):
        result = await graphiti.driver.execute_query(
            query, params={"group_id": namespace}, routing_="r"
        )
    records = _records(result)
    mapping, rows = validate_persisted_episode_rows(
        records=records,
        namespace=namespace,
        expected_name_to_session_id=expected_name_to_session_id,
    )
    from saturated_fixed_work_baseline_v1_3.longmemeval_session_qa import parse_episodic_content

    for row in rows.values():
        parse_episodic_content(row["content"])
    return mapping, rows, counters.neo4j_read_requests


async def _neo4j_health(graphiti: Any) -> dict[str, Any]:
    from paper_eval.s2_retrieval_probe import ProbeCounters, _read_only_query_guard

    counters = ProbeCounters()
    with _read_only_query_guard(graphiti.driver, counters):
        result = await graphiti.driver.execute_query("RETURN 1 AS ok", routing_="r")
    records = _records(result)
    if len(records) != 1 or records[0].get("ok") != 1:
        raise V6FinalQAError("Neo4j health query failed")
    return {"status": "PASS", "neo4j_read_requests": counters.neo4j_read_requests}


async def _endpoint_health(
    *, base_url: str, expected_model: str, api_key: str, role: str
) -> dict[str, Any]:
    import httpx

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        raise V6FinalQAError(f"{role} endpoint health failed: {type(exc).__name__}") from None
    data = body.get("data") if isinstance(body, dict) else None
    model_ids = sorted(
        str(item.get("id"))
        for item in data
        if isinstance(item, Mapping) and item.get("id")
    ) if isinstance(data, list) else []
    if expected_model not in model_ids:
        raise V6FinalQAError(
            f"{role} endpoint model identity mismatch: expected {expected_model}, got {model_ids}"
        )
    return {"status": "PASS", "base_url": base_url, "model": expected_model, "model_ids": model_ids}


def _session_metadata(case: Any) -> dict[int, dict[str, str]]:
    return {
        int(segment.source_sequence): {
            "session_id": str(segment.session_id),
            "session_date": str(segment.original_date),
        }
        for segment in case.segments
    }


def _qa_runtime_env(
    env: Mapping[str, str], *, embedding_base_url: str, embedding_model: str
) -> dict[str, str]:
    """Pin the read-only Graphiti retrieval runtime to the V6 embedding service."""

    alternate = dict(env)
    alternate["EMBEDDING_BASE_URL"] = embedding_base_url
    alternate["EMBEDDING_MODEL"] = embedding_model
    alternate["EMBEDDING_DIM"] = "1024"
    return alternate


def _build_read_only_runtime(env: Mapping[str, str]) -> Any:
    """Build Graphiti's existing guarded read-only runtime outside the loop."""

    from paper_eval import graph_quality_live

    graph_quality_live.EMBEDDING_BASE_URL = EMBEDDING_BASE_URL
    graph_quality_live.EMBEDDING_MODEL = EMBEDDING_MODEL
    alternate = _qa_runtime_env(
        env,
        embedding_base_url=EMBEDDING_BASE_URL,
        embedding_model=EMBEDDING_MODEL,
    )
    return graph_quality_live.build_graph_quality_runtime(env=alternate)


def _posthoc_recall(ranked: list[str], gold: tuple[str, ...]) -> dict[str, Any]:
    gold_set = set(gold)
    ranks = [index for index, value in enumerate(ranked, start=1) if value in gold_set]
    return {
        "gold_session_count": len(gold_set),
        "gold_hits_at_10": len(ranks),
        "gold_ranks": ranks,
        "recall_at_10": len(ranks) / len(gold_set) if gold_set else 0.0,
        "authority": "POST_HOC_PROVENANCE_METRIC_ONLY",
    }


class _CountingTransport:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.config_sha256 = delegate.config_sha256
        self.calls = 0

    async def complete(self, request: dict[str, object]) -> object:
        self.calls += 1
        return await self._delegate.complete(request)


async def _run_reader_lane(
    *,
    reader: Any,
    sessions: Any,
    case: Any,
    output_root: Path,
    judge: Any,
    lane: str,
) -> dict[str, Any]:
    from saturated_fixed_work_baseline_v1_3.longmemeval_answer_qa import evaluate_official_answer

    try:
        reader_result = await reader.answer(
            sessions, question_date=case.question_date, question=case.question
        )
    except Exception as exc:
        return {
            "reader": {"status": "ERROR", "error_type": type(exc).__name__},
            "reader_answer": "",
            "official_judge": {"status": "SERVICE_ERROR", "error_class": "READER_ERROR"},
            "answer_evaluation": {
                "status": "UNSCORED_READER_ERROR",
                "correct": None,
                "semantic_authority": "NONE",
            },
        }
    inputs = SimpleNamespace(
        run_id=output_root.name,
        history_id=EXPECTED_HISTORY_ID,
        question_type=case.question_type,
        question=case.question,
        reference_answer=str(case.gold_current_answer),
    )
    try:
        judge_result = await judge.evaluate(hypothesis=reader_result.answer, inputs=inputs)
    except Exception as exc:
        judge_result = {"status": "SERVICE_ERROR", "error_class": type(exc).__name__}
    else:
        raw_output = str(judge_result.pop("raw_output", ""))
        judge_result["output_sha256"] = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
        judge_result["raw_output_persisted"] = False
    try:
        answer = evaluate_official_answer(
            expected_answer=case.gold_current_answer,
            reader_answer=reader_result.answer,
            judge=judge_result,
        )
    except Exception as exc:
        answer = {
            "status": "UNSCORED_QA_CONTRACT_ERROR",
            "correct": None,
            "semantic_authority": "NONE",
            "error_type": type(exc).__name__,
        }
    return {
        "reader": reader_result.to_artifact(),
        "reader_answer": reader_result.answer,
        "official_judge": judge_result,
        "answer_evaluation": answer,
        "lane": lane,
    }


async def run(args: argparse.Namespace) -> int:
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_3.longmemeval_con_reader import ChainOfNoteReader
    from saturated_fixed_work_baseline_v1_3.longmemeval_fact_consolidation import (
        load_longmemeval_records,
        select_longmemeval_cases,
    )
    from saturated_fixed_work_baseline_v1_3.longmemeval_session_qa import (
        materialize_retrieved_sessions,
        persisted_episode_identity,
    )
    from paper_eval.graph_quality_judge import build_graph_quality_qwen_judge
    from paper_eval.graph_quality_transport import GraphQualityTransport
    from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1
    from paper_eval.s2_session_reader import OfficialSessionReader

    if args.top_k != TOP_K:
        raise V6FinalQAError(f"top-k is frozen at {TOP_K}")
    candidates = validate_candidates(
        candidate_roots=args.candidate_root,
        baseline_graph_path=args.baseline_graph,
    )
    output_root = create_fresh_output_root(args.output_root)
    env = dict(_load_env(args.repository_root.resolve()))
    chat_key = str(env.get("CONSTRUCTION_LLM_API_KEY") or env.get("OPENAI_API_KEY") or "")
    embedding_key = str(env.get("EMBEDDING_API_KEY") or chat_key)
    if not env.get("NEO4J_URI") or not chat_key:
        raise V6FinalQAError("read-only QA runtime configuration is missing")
    chat_health = await _endpoint_health(
        base_url=args.chat_base_url,
        expected_model=args.model,
        api_key=chat_key,
        role="QA chat",
    )
    embedding_health = await _endpoint_health(
        base_url=args.embedding_base_url,
        expected_model=args.embedding_model,
        api_key=embedding_key,
        role="QA embedding",
    )
    runtime_env = _qa_runtime_env(
        env,
        embedding_base_url=args.embedding_base_url,
        embedding_model=args.embedding_model,
    )
    runtime = await asyncio.to_thread(_build_read_only_runtime, runtime_env)
    neo4j_health = await _neo4j_health(runtime.graphiti)
    cases = {
        case.question_id: case
        for case in select_longmemeval_cases(load_longmemeval_records())
    }
    case = cases.get(EXPECTED_HISTORY_ID)
    if case is None:
        raise V6FinalQAError(f"LongMemEval case is missing: {EXPECTED_HISTORY_ID}")

    transport_delegate = GraphQualityTransport(
        model=args.model,
        base_url=args.chat_base_url,
        api_key=chat_key,
        timeout_seconds=180.0,
    )
    transport = _CountingTransport(transport_delegate)
    con_reader = ChainOfNoteReader(model=args.model, transport=transport)
    session_reader = OfficialSessionReader(model=args.model, transport=transport)
    judge = build_graph_quality_qwen_judge(base_url=args.chat_base_url, api_key=chat_key)
    rows: list[dict[str, Any]] = []
    read_requests = int(neo4j_health["neo4j_read_requests"])
    try:
        for candidate in candidates:
            graph = read_json(Path(candidate["canonical_graph_path"]))
            namespace = str(candidate["namespace"])
            episode_mapping = {
                f"{EXPECTED_HISTORY_ID}::episode::{int(item['source_sequence']):04d}": str(item["session_id"])
                for item in graph["episodes"]
            }
            uuid_to_session, episodic_rows, corpus_reads = await _read_persisted_episodes(
                graphiti=runtime.graphiti,
                namespace=namespace,
                expected_name_to_session_id=episode_mapping,
            )
            read_requests += corpus_reads
            retrieval = await retrieve_quality_v1(
                graph=runtime.graphiti,
                query=case.question,
                namespace=namespace,
                episode_uuid_to_session_id=uuid_to_session,
            )
            read_requests += int(retrieval.neo4j_read_requests)
            if len(retrieval.episodes) < TOP_K:
                raise V6FinalQAError(
                    f"retrieval returned fewer than {TOP_K} episodes: {candidate['candidate_id']}"
                )
            sessions = materialize_retrieved_sessions(
                history_id=EXPECTED_HISTORY_ID,
                retrieved_episodes=retrieval.episodes,
                episodic_rows=episodic_rows,
                public_session_metadata=_session_metadata(case),
                top_k=TOP_K,
            )
            ranked_session_ids = [item.session_id for item in sessions]
            retrieval_artifact = {
                "graphiti_search_calls": retrieval.graphiti_search_calls,
                "neo4j_read_requests": retrieval.neo4j_read_requests + corpus_reads,
                "fact_count": len(retrieval.facts),
                "episode_count": len(retrieval.episodes),
                "top_k_context": TOP_K,
                "ranked_session_ids": ranked_session_ids,
                "retrieval_identity_sha256": retrieval_identity_sha256(
                    ranked_session_ids=ranked_session_ids,
                    query=case.question,
                    search_config_sha256=retrieval.search_config_sha256,
                ),
                "persisted_evidence_identity_sha256": persisted_episode_identity(
                    history_id=EXPECTED_HISTORY_ID,
                    episodic_rows={
                        episode.episode_uuid: episodic_rows[episode.episode_uuid]
                        for episode in retrieval.episodes
                    },
                ),
                "search_config_sha256": retrieval.search_config_sha256,
                "gold_inputs_during_selection": False,
            }
            headline = await _run_reader_lane(
                reader=con_reader,
                sessions=sessions,
                case=case,
                output_root=output_root,
                judge=judge,
                lane="JSON_CHAIN_OF_NOTE",
            )
            ablation = await _run_reader_lane(
                reader=session_reader,
                sessions=sessions,
                case=case,
                output_root=output_root,
                judge=judge,
                lane="OFFICIAL_SESSION_VALUE_NO_CON",
            )
            rows.append(
                {
                    "candidate_root": candidate["root"],
                    "candidate_id": candidate["candidate_id"],
                    "namespace": namespace,
                    "history_id": EXPECTED_HISTORY_ID,
                    "question": case.question,
                    "evidence_surface": "READ_ONLY_NEO4J_EPISODIC_CONTENT_ONLY",
                    "retrieval": retrieval_artifact,
                    "session_recall_posthoc": _posthoc_recall(
                        ranked_session_ids, tuple(case.answer_session_ids)
                    ),
                    "headline": headline,
                    "ablation": ablation,
                }
            )
    finally:
        await judge.aclose()
        await transport_delegate.aclose()
        await runtime.aclose()

    for candidate in candidates:
        current = tree_sha256(Path(candidate["root"]))
        candidate["tree_sha256_after"] = current
        candidate["root_unchanged"] = current == candidate["tree_sha256_before"]
    roots_unchanged = all(item.get("root_unchanged", True) for item in candidates)
    runtime_evidence = {
        "construction_calls": 0,
        "graph_writes": 0,
        "candidate_roots_unchanged": roots_unchanged,
        "neo4j_read_requests": read_requests,
        "qa_reader_judge_calls": transport.calls,
        "endpoint_health": {
            "chat": chat_health,
            "embedding": embedding_health,
            "neo4j": neo4j_health,
        },
    }
    verdict = final_qa_verdict(rows=rows, runtime_evidence=runtime_evidence)
    write_new_json(
        output_root / "v6_final_qa_manifest.json",
        {
            "schema_version": "membind.v6.final-qa-manifest.v1",
            "status": "READ_ONLY_V6_FINAL_QA_COMPLETE",
            "history_id": EXPECTED_HISTORY_ID,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "baseline_graph": str(args.baseline_graph.resolve()),
            "baseline_formal_seal_sha256": "695cb71c9b6e305ad9c3e26b90c1b9d9487c54e32200fed65e40ff9a1205e8c2",
            "retrieval": "EXISTING_QUALITY_V1_TOP20_RRF_GOLD_BLIND",
            "headline_reader": con_reader.public_config,
            "ablation_reader": session_reader.public_config,
            "judge": {
                "implementation": "PINNED_OFFICIAL_LONGMEMEVAL_JUDGE",
            "base_url": args.chat_base_url,
                "raw_output_persisted": False,
            },
            "runtime_evidence": runtime_evidence,
            "construction_calls": 0,
            "graph_writes": 0,
        },
    )
    write_new_json(
        output_root / "v6_final_qa_results.json",
        {
            "schema_version": "membind.v6.final-qa-results.v1",
            "status": "READ_ONLY_V6_FINAL_QA_COMPLETE",
            "rows": rows,
            "runtime_evidence": runtime_evidence,
        },
    )
    write_new_json(output_root / "v6_final_qa_verdict.json", verdict)
    write_new_json(
        output_root / "v6_final_qa_summary.json",
        {
            "status": "READ_ONLY_V6_FINAL_QA_COMPLETE",
            "verdict": verdict["verdict"],
            "quality_claim": verdict["quality_claim"],
            "headline_statuses": verdict["headline_statuses"],
            "ablation_statuses": verdict["ablation_statuses"],
            "repetition_stable": verdict["repetition_stable"],
            "session_recall_at_10": [
                row["session_recall_posthoc"]["recall_at_10"] for row in rows
            ],
            "construction_calls": 0,
            "graph_writes": 0,
        },
    )
    print(
        json.dumps(
            {"status": "READ_ONLY_V6_FINAL_QA_COMPLETE", "output": str(output_root), **verdict},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(run(build_parser().parse_args()))
    except V6FinalQAError as exc:
        print(json.dumps({"status": "QA_INDETERMINATE", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
