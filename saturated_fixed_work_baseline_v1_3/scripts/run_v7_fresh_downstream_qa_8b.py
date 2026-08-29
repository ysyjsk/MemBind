#!/usr/bin/env python3
"""Run a read-only Quality-v1 downstream QA overlay for a sealed V7-FRESH run.

The construction namespace is consumed exactly as persisted.  This command
does not construct Graphiti data, write Neo4j, or alter the V7-FRESH run root.
It is an engineering qualification over the complete-gold questions whose
evidence is addressable by the requested source prefix; it is not a full
five-history benchmark result when the construction run is a prefix.
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
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "saturated_fixed_work_baseline_v1_3/src",
    ROOT / "paper-eval-v3/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.artifacts import (  # noqa: E402
    assert_snapshot_unchanged,
    canonical_sha256,
    snapshot_paths,
)
from mab_quality_v2_final_qa.mab_main_dataset import build_authority  # noqa: E402
from mab_quality_v2_final_qa.live_adapters import LiveReaderTransport  # noqa: E402
from mab_quality_v2_final_qa.compatibility import (  # noqa: E402
    build_context_pack,
    session_ranking_metrics,
)
from paper_eval import graph_quality_live  # noqa: E402
from paper_eval.quality_evaluation_v1_reader import QualityEvaluationV1Reader  # noqa: E402
from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1  # noqa: E402
from paper_eval.s2_adapters import S2LongMemEvalJudge  # noqa: E402
from saturated_fixed_work_baseline_v1_3.longmemeval_answer_qa import (  # noqa: E402
    evaluate_official_answer,
)


def _records(result: Any) -> list[dict[str, Any]]:
    values = getattr(result, "records", None)
    if values is None and isinstance(result, tuple) and result:
        values = result[0]
    if values is None and isinstance(result, list):
        values = result
    if not isinstance(values, Sequence):
        raise RuntimeError("READ_ONLY_QUERY_RESULT_INVALID")
    return [value if isinstance(value, dict) else dict(value) for value in values]


async def _namespace_state(graph: Any, namespace: str) -> dict[str, Any]:
    result = await graph.driver.execute_query(
        """
        CALL () { MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count }
        CALL () { MATCH ()-[r]->() WHERE r.group_id = $group_id RETURN count(r) AS relationship_count }
        CALL () { MATCH (n:Episodic) WHERE n.group_id = $group_id RETURN collect(n.name) AS episode_names }
        RETURN node_count, relationship_count, episode_names
        """,
        params={"group_id": namespace},
        routing_="r",
    )
    rows = _records(result)
    if len(rows) != 1:
        raise RuntimeError("NAMESPACE_STATE_INVALID")
    row = rows[0]
    return {
        "node_count": int(row.get("node_count") or 0),
        "relationship_count": int(row.get("relationship_count") or 0),
        "episode_names": sorted(str(value) for value in row.get("episode_names") or []),
    }


async def _episode_mapping(graph: Any, namespace: str, context_id: str, limit: int) -> dict[str, str]:
    result = await graph.driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE e.group_id = $group_id
        RETURN e.uuid AS uuid, e.name AS name
        ORDER BY e.name, e.uuid
        """,
        params={"group_id": namespace},
        routing_="r",
    )
    prefix = f"{context_id}::episode::"
    mapping: dict[str, str] = {}
    for row in _records(result):
        uuid = str(row.get("uuid") or "")
        name = str(row.get("name") or "")
        if not uuid or not name.startswith(prefix):
            raise RuntimeError("EPISODE_PROVENANCE_INVALID")
        try:
            sequence = int(name[len(prefix) :])
        except ValueError:
            raise RuntimeError("EPISODE_PROVENANCE_INVALID") from None
        if sequence < 0 or sequence >= limit:
            raise RuntimeError("EPISODE_PROVENANCE_OUTSIDE_PREFIX")
        session_id = f"{context_id}:s{sequence:04d}"
        if uuid in mapping or session_id in mapping.values():
            raise RuntimeError("EPISODE_PROVENANCE_DUPLICATE")
        mapping[uuid] = session_id
    if len(mapping) != limit:
        raise RuntimeError("EPISODE_PROVENANCE_COVERAGE_INVALID")
    return mapping


def _qa_candidates(context: Any, limit: int) -> tuple[Any, ...]:
    prefix_ids = {session.session_id for session in context.sessions[:limit]}
    selected = tuple(
        qa
        for qa in context.qa_items
        if qa.gold_mapping_status == "COMPLETE"
        and qa.gold_session_ids
        and set(qa.gold_session_ids).issubset(prefix_ids)
    )
    if not selected:
        raise RuntimeError("NO_PREFIX_COMPLETE_GOLD_QA")
    return selected


def _safe_judge_inputs(*, run_id: str, qa: Any, answer: str) -> Any:
    """Build the judge input without persisting references or prompts."""

    return SimpleNamespace(
        run_id=run_id,
        history_id=qa.question_id,
        question_type=qa.question_type,
        question=qa.question,
        reference_answer=qa.reference_answers[0],
        answer=answer,
    )


def _build_local_judge(*, model: str, base_url: str, api_key: str) -> Any:
    """Bind the official LongMemEval adapter to the served local 8B model."""

    from evaluation.backends.openai_compatible import OpenAICompatibleJudgeBackend
    from evaluation.benchmarks.longmemeval import LongMemEvalAdapter
    from evaluation.schemas import EvaluationItem

    backend = OpenAICompatibleJudgeBackend(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0,
        max_tokens=10,
        n=1,
        enable_thinking=False,
        thinking_control="client_request",
        max_attempts=1,
        timeout_seconds=float(os.environ.get("CONSTRUCTION_HTTP_TIMEOUT_SECONDS", "3600")),
        retry_delays=(0.0,),
    )
    return S2LongMemEvalJudge(
        backend=backend,
        evaluator=LongMemEvalAdapter(backend),
        evaluation_item_type=EvaluationItem,
    )


def _build_query_runtime() -> tuple[Any, str, str]:
    """Build the guarded Graphiti query runtime before entering asyncio."""

    llm_base_url = os.environ.get("CONSTRUCTION_LLM_BASE_URL", "http://127.0.0.1:18200/v1")
    api_key = os.environ.get("CONSTRUCTION_LLM_API_KEY") or os.environ.get("MEMBIND_LOCAL_API_KEY", "")
    embedding_base_url = os.environ.get("EMBEDDING_BASE_URL", "http://127.0.0.1:18202/v1")
    embedding_model = os.environ.get("EMBEDDING_MODEL", "qwen3-embedding-0.6b")
    embedding_dim = int(os.environ.get("EMBEDDING_DIM", "1024"))
    graph_quality_live.NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    graph_quality_live.EMBEDDING_BASE_URL = embedding_base_url.rstrip("/")
    graph_quality_live.EMBEDDING_MODEL = embedding_model
    graph_quality_live.EMBEDDING_DIMENSION = embedding_dim
    env = {
        "NEO4J_URI": graph_quality_live.NEO4J_URI,
        "NEO4J_USER": os.environ.get("NEO4J_USER", "neo4j"),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_PASSWORD", "password"),
        "EMBEDDING_BASE_URL": graph_quality_live.EMBEDDING_BASE_URL,
        "EMBEDDING_MODEL": embedding_model,
        "EMBEDDING_DIM": str(embedding_dim),
        "EMBEDDING_API_KEY": os.environ.get("EMBEDDING_API_KEY", api_key),
    }
    if not api_key:
        raise RuntimeError("QA_LLM_API_KEY_MISSING")
    return graph_quality_live.build_graph_quality_runtime(env=env), llm_base_url, api_key


async def _run(
    args: argparse.Namespace,
    runtime: Any,
    llm_base_url: str,
    api_key: str,
) -> dict[str, Any]:
    run_root = args.run_root.resolve()
    result_path = run_root / "RESULT.json"
    manifest_path = run_root / "RUN_MANIFEST_FINAL.json"
    if not result_path.is_file() or not manifest_path.is_file():
        raise RuntimeError("V7_FRESH_RESULT_OR_MANIFEST_MISSING")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if result.get("status") != "PASS" or result.get("method") != "V7_FRESH":
        raise RuntimeError("V7_FRESH_RESULT_NOT_SEALED")
    if manifest.get("namespace") != result.get("namespace"):
        raise RuntimeError("V7_FRESH_NAMESPACE_DRIFT")
    if manifest.get("profile_id") != "local-qwen3-8b-awq-dualreplica-v1":
        raise RuntimeError("V7_FRESH_PROFILE_DRIFT")
    namespace = str(result["namespace"])
    context_index = int(manifest.get("context_index", 0))
    source_count = int(result["source_count"])
    if source_count <= 0:
        raise RuntimeError("V7_FRESH_SOURCE_COUNT_INVALID")

    protected = snapshot_paths((run_root,))
    authority = build_authority(ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json")
    contexts = tuple(authority["contexts"])
    if context_index < 0 or context_index >= len(contexts):
        raise RuntimeError("CONTEXT_INDEX_INVALID")
    context = contexts[context_index]
    if source_count > len(context.sessions):
        raise RuntimeError("SOURCE_COUNT_EXCEEDS_CONTEXT")
    qas = _qa_candidates(context, source_count)

    reader_transport = LiveReaderTransport(
        model=os.environ.get("CONSTRUCTION_LLM_MODEL", "qwen3-8b-awq"),
        base_url=llm_base_url,
        api_key=api_key,
        timeout_seconds=float(os.environ.get("CONSTRUCTION_HTTP_TIMEOUT_SECONDS", "3600")),
    )
    reader = QualityEvaluationV1Reader(
        model=reader_transport.model,
        transport=reader_transport,
    )
    judge = _build_local_judge(
        model=reader_transport.model,
        base_url=llm_base_url,
        api_key=api_key,
    )
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    reader_calls = 0
    judge_calls = 0
    try:
        before_state = await _namespace_state(runtime.graphiti, namespace)
        mapping = await _episode_mapping(runtime.graphiti, namespace, context.context_id, source_count)
        expected_names = [f"{context.context_id}::episode::{index:04d}" for index in range(source_count)]
        if before_state["episode_names"] != expected_names:
            raise RuntimeError("NAMESPACE_EPISODE_INVENTORY_DRIFT")
        for qa in qas:
            base: dict[str, Any] = {
                "schema_version": "membind.v7b.downstream-qa-row.v1",
                "method": "V7_FRESH",
                "run_id": str(result["run_id"]),
                "context_id": context.context_id,
                "qa_pair_id": qa.qa_pair_id,
                "question_id": qa.question_id,
                "question_type": qa.question_type,
                "namespace": namespace,
                "construction_result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
                "qa_identity_sha256": canonical_sha256(qa.public_dict()),
                "retrieval_metrics": {},
                "reader_answer": None,
                "judge_valid": False,
                "correct": None,
                "status": "INVALID",
                "failure_class": None,
            }
            try:
                bundle = await retrieve_quality_v1(
                    graph=runtime.graphiti,
                    query=qa.question,
                    namespace=namespace,
                    episode_uuid_to_session_id=mapping,
                )
                ranked = [episode.session_id for episode in bundle.episodes]
                base["retrieval_metrics"] = session_ranking_metrics(ranked, qa.gold_session_ids)
                pack = build_context_pack(
                    context=context,
                    question=qa.question,
                    facts=bundle.facts,
                    episodes=bundle.episodes,
                )
                context_json = getattr(pack, "context_json", None)
                if not isinstance(context_json, str) or not context_json.strip():
                    raise ValueError("CONTEXT_PACK_INVALID")
                reader_calls += 1
                completion = await reader.answer(
                    context_json=context_json,
                    question_date=qa.question_date,
                    question=qa.question,
                )
                answer = str(completion.answer).strip()
                if not answer:
                    raise ValueError("READER_FAILED")
                base["reader_answer"] = answer
                judge_calls += 1
                judge_result = await judge.evaluate(
                    hypothesis=answer,
                    inputs=_safe_judge_inputs(run_id=str(result["run_id"]), qa=qa, answer=answer),
                )
                if str(judge_result.get("status")) != "SUCCESS" or type(judge_result.get("label")) is not bool:
                    raise ValueError("JUDGE_INVALID")
                base["judge_valid"] = True
                base["correct"] = bool(judge_result["label"])
                evaluation = evaluate_official_answer(
                    expected_answer=qa.reference_answers[0],
                    reader_answer=answer,
                    judge=judge_result,
                )
                # Persist only the terminal, authority-bearing projection;
                # reference answers and normalized Reader text remain private.
                base["answer_evaluation"] = {
                    "status": evaluation["status"],
                    "correct": evaluation["correct"],
                    "semantic_authority": evaluation["semantic_authority"],
                    "judge_status": evaluation["judge_status"],
                }
                base["status"] = "COMPLETE"
            except ValueError as error:
                base["failure_class"] = str(error) if str(error) in {
                    "CONTEXT_PACK_INVALID", "READER_FAILED", "JUDGE_INVALID"
                } else "UNKNOWN_INFRA_FAILURE"
            except Exception:
                base["failure_class"] = "UNKNOWN_INFRA_FAILURE"
            base["payload_sha256"] = canonical_sha256(
                {key: value for key, value in base.items() if key != "payload_sha256"}
            )
            rows.append(base)
        after_state = await _namespace_state(runtime.graphiti, namespace)
    finally:
        try:
            await judge.aclose()
        finally:
            await reader_transport.aclose()
            await runtime.aclose()
    assert before_state is not None and after_state is not None
    assert_snapshot_unchanged(protected)
    namespace_unchanged = before_state == after_state
    valid = [row for row in rows if row["judge_valid"] is True]
    summary = {
        "qa_count": len(rows),
        "valid_judge_count": len(valid),
        "invalid_judge_count": len(rows) - len(valid),
        "qa_accuracy": (sum(bool(row["correct"]) for row in valid) / len(valid)) if valid else None,
        "mean_recall_at_10": sum(row["retrieval_metrics"].get("recall_at_10", 0.0) for row in rows) / len(rows),
    }
    output = {
        "schema_version": "membind.v7b.downstream-qa-overlay.v1",
        "status": "PASS" if namespace_unchanged and rows and all(row["status"] == "COMPLETE" for row in rows) else "FAIL",
        "quality_scope": "V7_FRESH_PREFIX_DOWNSTREAM_QA_ENGINEERING_QUALIFICATION",
        "downstream_qa_authority": "FROZEN_QUALITY_V1_RETRIEVAL_READER_OFFICIAL_LONGMEMEVAL_JUDGE",
        "construction_latency_excluded": True,
        "construction_namespace": namespace,
        "construction_run_id": result["run_id"],
        "construction_result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "profile_id": manifest["profile_id"],
        "context_id": context.context_id,
        "context_index": context_index,
        "prefix_episode_count": source_count,
        "question_count": len(rows),
        "reader_calls": reader_calls,
        "judge_calls": judge_calls,
        "database_mutation_attempts": 0,
        "database_mutations": 0,
        "namespace_state_before": before_state,
        "namespace_state_after": after_state,
        "namespace_unchanged": namespace_unchanged,
        "construction_artifact_snapshot_sha256": protected["snapshot_sha256"],
        "summary": summary,
        "rows": rows,
        "full_five_history_qa": False,
        "headline_noninferiority_authorized": False,
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit(f"refusing to overwrite existing QA output root: {output_root}")
    runtime = None
    try:
        runtime, llm_base_url, api_key = _build_query_runtime()
        output = asyncio.run(_run(args, runtime, llm_base_url, api_key))
    except BaseException as exc:
        if runtime is not None:
            try:
                asyncio.run(runtime.aclose())
            except BaseException:
                pass
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)[:300]}, ensure_ascii=True))
        return 2
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / "RESULT.json"
    if destination.exists():
        raise SystemExit(f"refusing to overwrite existing QA artifact: {destination}")
    destination.write_text(json.dumps(output, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["status"], "question_count": output["question_count"], "summary": output["summary"], "output": str(destination)}, ensure_ascii=True))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
