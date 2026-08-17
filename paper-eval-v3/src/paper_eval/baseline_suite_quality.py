"""Frozen read-only retrieval, Reader-v2, and Judge composition.

The construction and retrieval path remains the live-qualified Native chain.
All compared methods reuse the two-sided session values frozen for Native U0.
LongMemEval section 5.1 applies user-only projection to retrieval keys, while
its public generation recipe defaults session values to ``useronly=false``.
The quality graph is independently constructed and never owns mutation.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from .native_baseline_runner import verify_native_quality_bindings
from .native_reader_v2 import OfficialConSessionReader
from .s2_adapters import OpenAIChatCompletionsTransport, build_qualified_qwen_judge
from .s2_formal_retrieval import run_formal_session_retrieval
from .s2_retrieval_probe import (
    ProbeCounters,
    build_episode_bm25_search_config,
    corpus_identity_sha256,
)
from .s2_session_policy import evaluate_session_retrieval
from .s2_session_reader import materialize_ranked_sessions


def build_baseline_quality_adapters(
    *,
    env: Mapping[str, str],
    frozen_baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact Reader/Judge identity already frozen for Native U0."""

    base_url = str(
        env.get("CONSTRUCTION_LLM_BASE_URL", "http://10.87.5.247:8000/v1/")
    )
    model = str(env.get("CONSTRUCTION_LLM_MODEL", "qwen3-32b-fp8"))
    api_key = str(env.get("CONSTRUCTION_LLM_API_KEY", "not-required")) or "not-required"
    transport = OpenAIChatCompletionsTransport(
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=180.0,
    )
    reader = OfficialConSessionReader(
        model=model,
        transport=transport,
        useronly=False,
    )
    judge = build_qualified_qwen_judge(base_url=base_url, api_key=api_key)
    identity = verify_native_quality_bindings(
        frozen_baseline=frozen_baseline,
        reader_config_sha256=reader.config_sha256,
        judge_config_sha256=judge.config_sha256,
    )
    return {
        "transport": transport,
        "reader": reader,
        "judge": judge,
        "quality_identity": identity,
    }


async def run_baseline_quality_chain(
    *,
    graph: Any,
    record: Mapping[str, Any],
    episodes: Sequence[Any],
    history_id: str,
    namespace: str,
    run_id: str,
    reader: Any,
    judge: Any,
) -> dict[str, Any]:
    """Execute one history-level quality query and retain only safe evidence."""

    session_ids = tuple(str(value) for value in record["haystack_session_ids"])
    gold_ids = tuple(str(value) for value in record["answer_session_ids"])
    counters = ProbeCounters()
    started = time.monotonic_ns()
    outcome = await run_formal_session_retrieval(
        graph=graph,
        query=str(record["question"]),
        namespace=namespace,
        episodes=episodes,
        expected_frozen_session_ids=session_ids,
        expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
        search_config=build_episode_bm25_search_config(),
        counters=counters,
    )
    retrieval_done = time.monotonic_ns()
    metrics = evaluate_session_retrieval(
        retrieved_session_ids=outcome.retrieved_session_ids,
        gold_session_ids=gold_ids,
        top_k=10,
        allowed_session_ids=session_ids,
    )
    sessions = materialize_ranked_sessions(
        record=record,
        ranked_session_ids=outcome.retrieved_session_ids,
        top_k=10,
    )
    reader_result = await reader.answer(
        sessions,
        question_date=str(record["question_date"]),
        question=str(record["question"]),
    )
    reader_done = time.monotonic_ns()
    judge_result = await judge.evaluate(
        hypothesis=reader_result.answer,
        inputs=SimpleNamespace(
            run_id=run_id,
            history_id=history_id,
            question_type=str(record["question_type"]),
            question=str(record["question"]),
            reference_answer=str(record["answer"]),
        ),
    )
    judge_done = time.monotonic_ns()
    return {
        "status": "SUCCESS",
        "history_id": history_id,
        "retrieval": {
            "evidence_recall_at_10": metrics.evidence_recall_at_10,
            "gold_ranks": list(metrics.gold_ranks),
            "retrieved_session_ids_sha256": hashlib.sha256(
                json.dumps(
                    list(outcome.retrieved_session_ids), sort_keys=True
                ).encode("utf-8")
            ).hexdigest(),
            "retrieved_count": len(outcome.retrieved_session_ids),
        },
        "qa_accuracy": 1.0 if judge_result.get("label") is True else 0.0,
        "reader": reader_result.to_artifact(),
        "judge": dict(judge_result),
        "latency_ns": {
            "retrieval": retrieval_done - started,
            "reader": reader_done - retrieval_done,
            "judge": judge_done - reader_done,
            "quality_total": judge_done - started,
        },
        "counters": {
            "graphiti_search_calls": outcome.graphiti_search_calls,
            "neo4j_read_requests": outcome.neo4j_read_requests,
            "reader_requests": 1,
            "judge_requests": 1,
        },
    }


__all__ = ["build_baseline_quality_adapters", "run_baseline_quality_chain"]
