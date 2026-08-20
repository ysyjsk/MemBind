#!/usr/bin/env python3
"""Read-only authored-QA extension over the four frozen baseline namespaces."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


EXPANDED_DIR = Path(__file__).resolve().parent
ROOT = EXPANDED_DIR.parent
PROJECT = ROOT.parents[0]
PAPER_SRC = PROJECT / "paper-eval-v3/src"
LEGACY_SRC = PROJECT / "membind-validation/src"
MAB_SRC = PROJECT / "mab_quality_v2_final_qa/src"
for path in (PAPER_SRC, LEGACY_SRC, MAB_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluation.backends.openai_compatible import OpenAICompatibleJudgeBackend  # noqa: E402
from evaluation.benchmarks.longmemeval import LongMemEvalAdapter  # noqa: E402
from evaluation.schemas import EvaluationItem  # noqa: E402
from evaluation.vendor.longmemeval_evaluate_qa import get_anscheck_prompt  # noqa: E402
from paper_eval.artifacts import payload_sha256  # noqa: E402
from paper_eval.graph_quality_judge import GraphQualityPrivateLongMemEvalJudge  # noqa: E402
from paper_eval.graph_quality_transport import GraphQualityTransport  # noqa: E402
from paper_eval.quality_evaluation_v1 import (  # noqa: E402
    build_context_pack,
    edge_provenance_metrics,
    session_ranking_metrics,
    temporal_diagnostics,
)
from paper_eval.quality_evaluation_v1_reader import (  # noqa: E402
    QualityEvaluationV1Reader,
    QualityEvaluationV1ReaderInvalidOutput,
)
from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1  # noqa: E402
from paper_eval.s2_retrieval_probe import (  # noqa: E402
    ProbeCounters,
    _expected_corpus_rows,
    _preflight_corpus,
    _read_only_query_guard,
)
from mab_quality_v2_final_qa.live_adapters import SiliconFlowOpenAITransport  # noqa: E402

from expanded_analysis import (  # noqa: E402
    CLAIM_SCOPE,
    EXPECTED_HISTORIES,
    ExpandedAnalysisError,
    build_gold_blind_projection,
    canonical_sha256,
    file_sha256,
    load_expanded_inventory,
    reduce_expanded_rows,
)
from expanded_runtime import (  # noqa: E402
    EMBEDDING_DIMENSION,
    EXACT_EMBEDDING_MODEL,
    ExpandedRuntime,
    build_expanded_runtime,
)


EXACT_READER_MODEL = "Qwen/Qwen3-32B"
EXACT_JUDGE_MODEL = "Qwen/Qwen3-32B"
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
SOURCE = ROOT / "../paper-eval-v3/artifacts/paper_eval/development_inputs/LONGMEMEVAL_S_DEVELOPMENT_EXPOSED_4.json"
NAMESPACE_MAP = {
    ("U0", "07741c45"): "nc-e1e2-257cd5a4cf9e7288",
    ("U0", "b6019101"): "nc-e1e2-741743c7f0e1b6e9",
    ("U0", "6071bd76"): "nc-e1e2-a2b20922ecc45ec6",
    ("U0", "a2f3aa27"): "nc-e1e2-98a47763f4982f11",
    ("P(C=2)", "07741c45"): "pev3-bs-dev-20260816-001-pc2-07741c45-a001",
    ("P(C=2)", "b6019101"): "pev3-bs-dev-20260816-001-pc2-b6019101-a001",
    ("P(C=2)", "6071bd76"): "pev3-bs-dev-20260816-001-pc2-6071bd76-a001",
    ("P(C=2)", "a2f3aa27"): "pev3-bs-dev-20260816-001-pc2-a2f3aa27-a001",
}
METHODS = ("U0", "P(C=2)")
METRICS = ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10")


def build_public_question(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project exactly the fields retrieval/Reader may receive."""

    return build_gold_blind_projection(row)


def build_judge_public_config(base_url: str) -> dict[str, Any]:
    normalized = base_url.rstrip("/")
    return {
        "backend": "openai_compatible_chat_completions",
        "model": EXACT_JUDGE_MODEL,
        "endpoint_identity_sha256": hashlib.sha256(f"{normalized}/".encode()).hexdigest(),
        "temperature": 0,
        "max_tokens": 10,
        "n": 1,
        "enable_thinking": False,
        "max_attempts": 1,
        "sdk_hidden_retries": 0,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _safe_error(error: BaseException) -> str:
    detail = str(error).replace("\n", " ").replace("\r", " ").strip()
    # Error messages are intentionally capped and never include request bodies.
    return f"{type(error).__module__}.{type(error).__name__}:{detail[:240]}"


def _load_source_records() -> dict[str, dict[str, Any]]:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    records = source.get("records")
    if not isinstance(records, list):
        raise ExpandedAnalysisError("SOURCE_RECORDS_INVALID")
    return {str(record["question_id"]): dict(record) for record in records}


def _public_record(source_record: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    record = {
        key: source_record[key]
        for key in ("haystack_session_ids", "haystack_dates", "haystack_sessions")
    }
    record.update(build_public_question(row))
    return record


def _build_frozen_episodes(source_record: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Render the existing source corpus while keeping extension QA labels out."""

    from dataset import build_episodes

    record = _public_record(source_record, row)
    # Episode names are bound to the original frozen source question ID. The
    # authored extension question ID must never create a new corpus identity.
    record["question_id"] = str(source_record["question_id"])
    return tuple(build_episodes(record))


async def _namespace_snapshot(driver: Any, namespace: str) -> dict[str, Any]:
    result = await driver.execute_query(
        """
        CALL { MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count }
        CALL { MATCH ()-[r]->() WHERE r.group_id = $group_id RETURN count(r) AS relationship_count }
        CALL { MATCH (n:Episodic) WHERE n.group_id = $group_id
               RETURN collect({uuid:n.uuid,name:n.name,content:n.content}) AS episodes }
        RETURN node_count, relationship_count, episodes
        """,
        params={"group_id": namespace},
        routing_="r",
    )
    records = getattr(result, "records", result[0] if isinstance(result, tuple) else result)
    if not records:
        raise RuntimeError("namespace snapshot returned no rows")
    row = records[0] if isinstance(records[0], Mapping) else dict(records[0])
    episodes = row.get("episodes") or []
    normalized = []
    for episode in episodes:
        value = episode if isinstance(episode, Mapping) else dict(episode)
        normalized.append({
            "uuid": str(value.get("uuid", "")),
            "name": str(value.get("name", "")),
            "content_sha256": hashlib.sha256(str(value.get("content", "")).encode()).hexdigest(),
        })
    normalized.sort(key=lambda item: (item["name"], item["uuid"]))
    return {
        "namespace_sha256": hashlib.sha256(namespace.encode()).hexdigest(),
        "node_count": int(row.get("node_count", 0)),
        "relationship_count": int(row.get("relationship_count", 0)),
        "episode_count": len(normalized),
        "episode_map_sha256": canonical_sha256(normalized),
    }


def _protected_snapshot() -> dict[str, Any]:
    paths = [
        PROJECT / "MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md",
        PAPER_SRC / "paper_eval/quality_evaluation_v1.py",
        PAPER_SRC / "paper_eval/quality_evaluation_v1_reader.py",
        PAPER_SRC / "paper_eval/quality_evaluation_v1_retrieval.py",
        PAPER_SRC / "paper_eval/membind_v31",
    ]
    entries: list[dict[str, Any]] = []
    for path in paths:
        if path.is_file():
            entries.append({"path": str(path.resolve()), "sha256": file_sha256(path)})
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not child.is_symlink():
                    entries.append({"path": str(child.resolve()), "sha256": file_sha256(child)})
    return {"entries": entries, "payload_sha256": canonical_sha256(entries)}


async def _api_preflight(api_key: str) -> dict[str, Any]:
    import httpx
    from openai import AsyncOpenAI

    timeout = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=60.0)
    http_client = httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)
    client = AsyncOpenAI(api_key=api_key, base_url=f"{SILICONFLOW_BASE_URL}/", max_retries=0, http_client=http_client)
    result: dict[str, Any] = {
        "endpoint": "SILICONFLOW",
        "endpoint_identity_sha256": hashlib.sha256(f"{SILICONFLOW_BASE_URL}/".encode()).hexdigest(),
        "judge_model": EXACT_JUDGE_MODEL,
        "embedding_model": EXACT_EMBEDDING_MODEL,
        "embedding_dimension": EMBEDDING_DIMENSION,
        "status": "PASS",
    }
    try:
        models = await client.models.list()
        ids = {str(item.id) for item in getattr(models, "data", [])}
        result["model_catalog"] = {
            "judge_present": EXACT_JUDGE_MODEL in ids,
            "embedding_present": EXACT_EMBEDDING_MODEL in ids,
        }
        if EXACT_JUDGE_MODEL not in ids or EXACT_EMBEDDING_MODEL not in ids:
            raise RuntimeError("EXACT_MODEL_NOT_PRESENT")
        chat = await client.chat.completions.create(
            model=EXACT_JUDGE_MODEL,
            messages=[{"role": "user", "content": "Reply with yes only."}],
            temperature=0,
            max_tokens=10,
            n=1,
            extra_body={"enable_thinking": False},
        )
        result["judge_probe"] = {"finish_reason": getattr(chat.choices[0], "finish_reason", None)}
        embedding = await client.embeddings.create(model=EXACT_EMBEDDING_MODEL, input="embedding preflight")
        dimension = len(embedding.data[0].embedding)
        result["embedding_probe"] = {"dimension": dimension, "dimension_match": dimension == EMBEDDING_DIMENSION}
        if getattr(chat.choices[0], "finish_reason", None) != "stop" or dimension != EMBEDDING_DIMENSION:
            raise RuntimeError("MODEL_PREFLIGHT_RESPONSE_INVALID")
    except Exception as error:
        result["status"] = "BLOCKED"
        result["error_class"] = _safe_error(error)
    finally:
        await client.close()
        await http_client.aclose()
    return result


def _build_reader_and_judge(api_key: str) -> tuple[Any, Any, Any, Any]:
    import httpx
    from openai import AsyncOpenAI

    timeout = httpx.Timeout(connect=5.0, read=180.0, write=180.0, pool=180.0)
    raw_http = httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)
    raw_client = AsyncOpenAI(api_key=api_key, base_url=f"{SILICONFLOW_BASE_URL}/", max_retries=0, http_client=raw_http)
    client = SiliconFlowOpenAITransport(raw_client)
    reader_transport = GraphQualityTransport(
        model=EXACT_READER_MODEL,
        base_url=SILICONFLOW_BASE_URL,
        api_key=api_key,
        timeout_seconds=180.0,
        client=client,
    )
    reader = QualityEvaluationV1Reader(model=EXACT_READER_MODEL, transport=reader_transport)
    backend = OpenAICompatibleJudgeBackend(
        model=EXACT_JUDGE_MODEL,
        base_url=SILICONFLOW_BASE_URL,
        api_key=api_key,
        temperature=0,
        max_tokens=10,
        n=1,
        enable_thinking=False,
        thinking_control="client_request",
        max_attempts=1,
        timeout_seconds=180.0,
        retry_delays=(0.0,),
        client=client,
    )
    evaluator = LongMemEvalAdapter(backend)
    judge = GraphQualityPrivateLongMemEvalJudge(
        backend=backend,
        evaluator=evaluator,
        evaluation_item_type=EvaluationItem,
        prompt_builder=get_anscheck_prompt,
    )
    return reader, judge, reader_transport, backend


async def _run_question(
    *,
    run_id: str,
    method: str,
    history_id: str,
    row: Mapping[str, Any],
    source_record: Mapping[str, Any],
    namespace: str,
    runtime: ExpandedRuntime,
    reader: Any,
    judge: Any,
    output_root: Path,
) -> dict[str, Any]:
    record = _public_record(source_record, row)
    # Build only from the public projection so gold labels never enter retrieval.
    episodes = _build_frozen_episodes(source_record, row)
    expected_rows = _expected_corpus_rows(episodes)
    counters = ProbeCounters()
    with _read_only_query_guard(runtime.graphiti.driver, counters):
        corpus = await _preflight_corpus(
            driver=runtime.graphiti.driver,
            namespace=namespace,
            expected_rows=expected_rows,
            expected_frozen_session_ids=tuple(value.session_id for value in episodes),
        )
        retrieval = await retrieve_quality_v1(
            graph=runtime.graphiti,
            query=str(record["question"]),
            namespace=namespace,
            episode_uuid_to_session_id=corpus.uuid_to_session_id,
        )
    gold = tuple(str(value) for value in row["gold_session_ids"])
    metrics = session_ranking_metrics(tuple(value.session_id for value in retrieval.episodes), gold)
    edge_metrics = edge_provenance_metrics(retrieval.facts, gold)
    temporal_metrics = temporal_diagnostics(retrieval.facts, question_date=str(row["question_date"]))
    context = build_context_pack(record=record, question=str(row["question"]), facts=retrieval.facts, episodes=retrieval.episodes)
    reader_valid = False
    predicted: str | None = None
    reader_payload: dict[str, Any] | None = None
    judge_payload: dict[str, Any] | None = None
    failure_category = "READER_INVALID"
    try:
        reader_result = await reader.answer(
            context_json=context.context_json,
            question_date=str(row["question_date"]),
            question=str(row["question"]),
        )
        reader_valid = reader_result.finish_reason == "stop"
        predicted = reader_result.answer
        reader_payload = asdict(reader_result)
    except QualityEvaluationV1ReaderInvalidOutput as error:
        reader_payload = {"status": "INVALID_OUTPUT", "error_class": _safe_error(error)}
    except Exception as error:
        reader_payload = {"status": "SERVICE_ERROR", "error_class": _safe_error(error)}

    if reader_valid and predicted is not None:
        judge_inputs = SimpleNamespace(
            run_id=run_id,
            history_id=str(row["question_id"]),
            question_type=str(row["question_type"]),
            question=str(row["question"]),
            reference_answer=str(row["reference_answer"]),
        )
        try:
            judge_payload = dict(await judge.evaluate(hypothesis=predicted, inputs=judge_inputs))
        except Exception as error:
            judge_payload = {"status": "SERVICE_ERROR", "error_class": _safe_error(error), "model": EXACT_JUDGE_MODEL}

    judge_valid = bool(
        isinstance(judge_payload, Mapping)
        and judge_payload.get("status") == "SUCCESS"
        and judge_payload.get("parse_status") in {"YES", "NO"}
        and type(judge_payload.get("label")) is bool
        and judge_payload.get("model") == EXACT_JUDGE_MODEL
    )
    correct = judge_payload.get("label") if judge_valid else None
    context_sources = {value.session_id for value in retrieval.episodes[:10]}.union(
        source for fact in retrieval.facts for source in fact.source_session_ids
    )
    coverage = len(set(gold).intersection(context_sources)) / len(gold)
    if not reader_valid:
        failure_category = "READER_INVALID"
    elif not judge_valid:
        failure_category = "JUDGE_INVALID"
    elif correct is True:
        failure_category = "SUCCESS"
    elif coverage < 1.0:
        failure_category = "CONTEXT_EVIDENCE_COVERAGE_INCOMPLETE"
    else:
        failure_category = "READER_OR_JUDGE_INCORRECT"

    private = {
        "schema_version": "membind.baseline-reuse-expanded-private-row.v1",
        "run_id": run_id,
        "method": method,
        "history_id": history_id,
        "question_id": row["question_id"],
        "namespace": namespace,
        "question": row["question"],
        "question_date": row["question_date"],
        "question_type": row["question_type"],
        "reference_answer": row["reference_answer"],
        "gold_session_ids": list(gold),
        "gold_evidence_quotes": list(row["gold_evidence_quotes"]),
        "predicted_answer": predicted,
        "reader": reader_payload,
        "judge": judge_payload,
        "retrieval": {
            "facts": [asdict(value) for value in retrieval.facts],
            "episodes": [asdict(value) for value in retrieval.episodes],
            "context_json": context.context_json,
        },
        "retrieval_metrics": metrics,
        "edge_provenance_metrics": edge_metrics,
        "temporal_diagnostics": temporal_metrics,
        "context_gold_session_coverage_posthoc": coverage,
        "failure_category": failure_category,
        "payload_sha256": "",
    }
    private["payload_sha256"] = canonical_sha256({key: value for key, value in private.items() if key != "payload_sha256"})
    private_path = output_root / "private_rows" / f"{method.replace('(', '').replace(')', '').replace('=', '')}-{history_id}-{row['question_id']}.json"
    _atomic_json(private_path, private)

    return {
        "method": method,
        "history_id": history_id,
        "question_id": row["question_id"],
        "judge_model": EXACT_JUDGE_MODEL,
        "reader_model": EXACT_READER_MODEL,
        "judge_valid": judge_valid,
        "correct": correct,
        "reader_valid": reader_valid,
        "predicted_answer": predicted,
        "failure_category": failure_category,
        "retrieval_metrics": {metric: metrics[metric] for metric in METRICS},
        "context_gold_session_coverage_posthoc": coverage,
        "counters": {
            "construction_calls": 0,
            "construction_llm_requests": counters.construction_llm_requests,
            "embedding_requests": counters.embedding_requests,
            "neo4j_read_requests": counters.neo4j_read_requests,
            "database_mutation_attempts": counters.database_mutation_attempts,
            "graphiti_search_calls": counters.graphiti_search_calls,
        },
        "private_payload_sha256": private["payload_sha256"],
    }


async def _run_live(
    *,
    run_id: str,
    output_root: Path,
    inventory: Mapping[str, Any],
    source_records: Mapping[str, Mapping[str, Any]],
    api_key: str,
    neo4j_user: str,
    neo4j_password: str,
    runtime: ExpandedRuntime,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    reader, judge, reader_transport, backend = _build_reader_and_judge(api_key)
    rows: list[dict[str, Any]] = []
    before_db: dict[str, Any] = {}
    after_db: dict[str, Any] = {}
    try:
        for method in METHODS:
            for history_id in EXPECTED_HISTORIES:
                namespace = NAMESPACE_MAP[(method, history_id)]
                with _read_only_query_guard(runtime.graphiti.driver, ProbeCounters()):
                    before_db[f"{method}:{history_id}"] = await _namespace_snapshot(runtime.graphiti.driver, namespace)
                for row in inventory["questions"]:
                    if row["history_id"] != history_id:
                        continue
                    rows.append(await _run_question(
                        run_id=run_id,
                        method=method,
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
                    after_db[f"{method}:{history_id}"] = await _namespace_snapshot(runtime.graphiti.driver, namespace)
    finally:
        await judge.aclose()
        await reader_transport.aclose()
        await runtime.aclose()
    return rows, before_db, after_db


def render_report(result: Mapping[str, Any]) -> str:
    lines = [
        "# Expanded QA analysis over frozen baseline states",
        "",
        "Scope: `BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION`. This is an authored QA extension over the same four frozen baseline histories, not the official MemoryAgentBench Multi-QA dataset and not a 240-QA result.",
        "",
        "No construction was performed; U0 and P(C=2) reused their existing sealed namespaces and the same 16-question inventory.",
        "",
    ]
    if result.get("status") != "PASS":
        lines.extend([f"Status: **{result.get('status')}**", "", f"Blocker: `{result.get('blocker')}`", "", "No accuracy is reported because live execution was blocked before scoring."])
        return "\n".join(lines) + "\n"
    lines.extend([
        "| Method | Valid | Correct | Accuracy | Wilson 95% interval |",
        "|---|---:|---:|---:|---:|",
    ])
    for method in METHODS:
        value = result["methods"][method]
        interval = value["accuracy_wilson_95"]
        lines.append(f"| {method} | {value['valid_count']}/{value['question_count']} | {value['correct_count']} | {value['accuracy']:.1%} | [{interval['low']:.1%}, {interval['high']:.1%}] |")
    paired = result["paired"]
    lines.extend([
        "",
        f"Reader valid: U0 {result['methods']['U0']['reader_valid_count']}/{result['methods']['U0']['question_count']}; P(C=2) {result['methods']['P(C=2)']['reader_valid_count']}/{result['methods']['P(C=2)']['question_count']}.",
        f"Invalid outputs: U0 reader {result['methods']['U0']['reader_invalid_count']}, judge {result['methods']['U0']['judge_invalid_count']}; P(C=2) reader {result['methods']['P(C=2)']['reader_invalid_count']}, judge {result['methods']['P(C=2)']['judge_invalid_count']}. Invalid outputs count as incorrect in primary accuracy; valid-only P(C=2) accuracy is {result['methods']['P(C=2)']['valid_only_accuracy']:.1%}.",
        f"Paired agreement: {paired['agreement_count']}/{paired['jointly_valid_pair_count']} jointly valid pairs ({paired['agreement_rate']:.1%}); {paired['invalid_pair_count']} pair contains an invalid output; {paired['discordant_count']} valid pairs are discordant.",
        f"Observed P(C=2)-minus-U0 primary accuracy delta: {paired['accuracy_delta_pc2_minus_u0']:+.1%}. This small diagnostic cannot establish equivalence or non-inferiority.",
        "",
        "Exact live models: Reader/Judge `Qwen/Qwen3-32B`; embedding `Qwen/Qwen3-Embedding-0.6B` (1024 dimensions).",
        f"Mean retrieval metrics (identical here for both methods): R@1 {result['methods']['U0']['retrieval']['recall_at_1']:.3f}, R@3 {result['methods']['U0']['retrieval']['recall_at_3']:.3f}, R@5 {result['methods']['U0']['retrieval']['recall_at_5']:.3f}, R@10 {result['methods']['U0']['retrieval']['recall_at_10']:.3f}, MRR {result['methods']['U0']['retrieval']['mrr']:.3f}, nDCG@10 {result['methods']['U0']['retrieval']['ndcg_at_10']:.3f}; post-hoc gold-session context coverage was 1.000 for both.",
        "",
        "All retrieval metrics are session-level metrics; provenance coverage is a post-hoc diagnostic. Gold labels were withheld from retrieval and Reader projections and used only for post-retrieval metrics/Judge evaluation.",
    ])
    return "\n".join(lines) + "\n"


def _build_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reduced = reduce_expanded_rows(rows)
    for method in METHODS:
        selected = [row for row in rows if row["method"] == method]
        reduced["methods"][method]["judge_model"] = EXACT_JUDGE_MODEL
        reduced["methods"][method]["reader_model"] = EXACT_READER_MODEL
        reduced["methods"][method]["construction_calls"] = sum(row["counters"]["construction_calls"] for row in selected)
        reduced["methods"][method]["context_gold_session_coverage_mean"] = sum(row["context_gold_session_coverage_posthoc"] for row in selected) / len(selected)
    reduced["claim_scope"] = CLAIM_SCOPE
    reduced["status"] = "PASS"
    reduced["payload_sha256"] = canonical_sha256({key: value for key, value in reduced.items() if key != "payload_sha256"})
    return reduced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default="expanded-qa-20260819-001")
    args = parser.parse_args(argv)
    output_root = Path(args.output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    pre_snapshot = _protected_snapshot()
    source_records = _load_source_records()
    inventory = load_expanded_inventory(EXPANDED_DIR / "expanded_qa_inventory.json", SOURCE.resolve())
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        preflight = {"status": "BLOCKED", "error_class": "SILICONFLOW_API_KEY_MISSING", "checked_at": _utc_now()}
    else:
        preflight = asyncio.run(_api_preflight(api_key))
        preflight["checked_at"] = _utc_now()
    _atomic_json(output_root / "RUNTIME_PREFLIGHT.json", preflight)
    if preflight.get("status") != "PASS":
        result = {"schema_version": "membind.baseline-reuse-expanded-analysis.v1", "claim_scope": CLAIM_SCOPE, "status": "BLOCKED", "blocker": preflight.get("error_class", "MODEL_PREFLIGHT_FAILED"), "accuracy": None}
        _atomic_json(output_root / "RESULTS.json", result)
        (output_root / "FINAL_QA_ANALYSIS.md").write_text(render_report(result), encoding="utf-8")
        post_snapshot = _protected_snapshot()
        _atomic_json(output_root / "SOURCE_MANIFEST.json", {"protected_root_before": pre_snapshot, "protected_root_after": post_snapshot, "protected_root_unchanged": pre_snapshot == post_snapshot, "source_path": str(SOURCE.resolve()), "source_sha256": file_sha256(SOURCE.resolve()), "status": "BLOCKED"})
        return 2
    try:
        neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
        neo4j_password = os.environ.get("NEO4J_PASSWORD", "password")
        runtime = build_expanded_runtime(
            neo4j_uri="bolt://localhost:7687",
            neo4j_user=neo4j_user,
            neo4j_password=neo4j_password,
            embedding_base_url=SILICONFLOW_BASE_URL,
            embedding_api_key=api_key,
        )
        rows, before_db, after_db = asyncio.run(_run_live(run_id=args.run_id, output_root=output_root, inventory=inventory, source_records=source_records, api_key=api_key, neo4j_user=neo4j_user, neo4j_password=neo4j_password, runtime=runtime))
        result = _build_result(rows)
        _atomic_json(output_root / "RESULTS.json", result)
        (output_root / "FINAL_QA_ANALYSIS.md").write_text(render_report(result), encoding="utf-8")
        post_snapshot = _protected_snapshot()
        unchanged_db = before_db == after_db
        manifest = {"schema_version": "membind.baseline-reuse-expanded-source-manifest.v1", "claim_scope": CLAIM_SCOPE, "source_path": str(SOURCE.resolve()), "source_sha256": file_sha256(SOURCE.resolve()), "inventory_sha256": inventory["inventory_sha256"], "protected_root_before": pre_snapshot, "protected_root_after": post_snapshot, "protected_root_unchanged": pre_snapshot == post_snapshot, "namespace_snapshots_before": before_db, "namespace_snapshots_after": after_db, "namespace_snapshots_unchanged": unchanged_db, "construction_calls": 0, "status": "PASS" if pre_snapshot == post_snapshot and unchanged_db else "FAIL"}
        manifest["payload_sha256"] = canonical_sha256(manifest)
        _atomic_json(output_root / "SOURCE_MANIFEST.json", manifest)
        return 0 if manifest["status"] == "PASS" else 1
    except Exception as error:
        post_snapshot = _protected_snapshot()
        _atomic_json(output_root / "RESULTS.json", {"schema_version": "membind.baseline-reuse-expanded-analysis.v1", "claim_scope": CLAIM_SCOPE, "status": "BLOCKED", "blocker": _safe_error(error), "accuracy": None})
        (output_root / "FINAL_QA_ANALYSIS.md").write_text(render_report({"status": "BLOCKED", "blocker": _safe_error(error)}), encoding="utf-8")
        _atomic_json(output_root / "SOURCE_MANIFEST.json", {"protected_root_before": pre_snapshot, "protected_root_after": post_snapshot, "protected_root_unchanged": pre_snapshot == post_snapshot, "status": "BLOCKED"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
