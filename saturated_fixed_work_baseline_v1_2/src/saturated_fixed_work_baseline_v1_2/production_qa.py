"""Read-only production QA over the eight sealed formal namespaces."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .qa_lane import (
    NamespaceSeal,
    build_gold_blind_projection,
    validate_l4_namespace_inventory,
)
from .dataset import load_episode_inputs, load_frozen_qa_source_record
from .reuse import import_paper_eval_module, import_validation_module


class ProductionQAError(ValueError):
    """The live QA inventory, read-only boundary, or durable row is invalid."""


@dataclass(frozen=True, slots=True)
class ProductionQADependencies:
    runtime_factory: Callable[[NamespaceSeal], Any]
    snapshot_graph: Callable[[Any, NamespaceSeal], Any]
    question_runner: Callable[..., Any]
    close: Callable[[], Any] | None = None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _append(path: Path, row: Mapping[str, Any]) -> None:
    payload = _canonical_bytes(dict(row)) + b"\n"
    descriptor = os.open(path, os.O_APPEND | os.O_WRONLY)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normalize_question_result(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    metrics = (
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "mrr",
        "ndcg_at_10",
    )
    if any(
        isinstance(result.get(field), bool)
        or not isinstance(result.get(field), (int, float))
        or not math.isfinite(float(result[field]))
        or not 0.0 <= float(result[field]) <= 1.0
        for field in metrics
    ):
        raise ProductionQAError("QA_RESULT_METRICS_INVALID")
    if type(result.get("correct")) is not bool or type(result.get("invalid")) is not bool:
        raise ProductionQAError("QA_RESULT_VERDICT_INVALID")
    for field in ("construction_calls", "graph_write_attempts"):
        if (
            isinstance(result.get(field), bool)
            or not isinstance(result.get(field), int)
            or result[field] < 0
        ):
            raise ProductionQAError("QA_RESULT_COUNTER_INVALID")
    return result


async def execute_production_qa(
    *,
    seals: Sequence[NamespaceSeal],
    questions: Sequence[Mapping[str, Any]],
    expected_histories: Sequence[str],
    construction_calls: int,
    output_path: Path,
    dependencies: ProductionQADependencies,
) -> list[dict[str, Any]]:
    selected_seals = validate_l4_namespace_inventory(
        seals,
        expected_histories=expected_histories,
        construction_calls=construction_calls,
    )
    selected_questions = tuple(questions)
    question_ids = [str(row.get("question_id") or "") for row in selected_questions]
    if (
        len(selected_questions) != 16
        or len(set(question_ids)) != 16
        or any(
            sum(row.get("history_id") == history for row in selected_questions) != 4
            for history in expected_histories
        )
    ):
        raise ProductionQAError("QA_INVENTORY_INVALID")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")
    if output_path.exists():
        raise ProductionQAError("QA_ROWS_ALREADY_EXIST")
    try:
        descriptor = os.open(
            partial_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError:
        raise ProductionQAError("QA_ROWS_ALREADY_EXIST") from None
    os.fsync(descriptor)
    os.close(descriptor)
    rows: list[dict[str, Any]] = []
    try:
        for seal in selected_seals:
            runtime = await _await(dependencies.runtime_factory(seal))
            graphiti = getattr(runtime, "graphiti", None)
            if graphiti is None:
                raise ProductionQAError("QA_RUNTIME_GRAPHITI_MISSING")
            try:
                before_graph = await _await(dependencies.snapshot_graph(runtime, seal))
                before_hash = _hash(before_graph)
                writes = 0
                constructions = 0
                history_questions = [
                    row
                    for row in selected_questions
                    if row.get("history_id") == seal.history_id
                ]
                for question in history_questions:
                    public = build_gold_blind_projection(question)
                    try:
                        private = {
                            field: question[field]
                            for field in (
                                "reference_answer",
                                "gold_session_ids",
                                "gold_evidence_quotes",
                            )
                        }
                    except KeyError:
                        raise ProductionQAError("QA_PRIVATE_EVALUATION_INVALID") from None
                    try:
                        result = _normalize_question_result(
                            await _await(
                                dependencies.question_runner(
                                    runtime=runtime,
                                    seal=seal,
                                    public_question=public,
                                    private_evaluation=private,
                                )
                            )
                        )
                    except ProductionQAError:
                        raise
                    except Exception as error:
                        result = {
                            "recall_at_1": 0.0,
                            "recall_at_3": 0.0,
                            "recall_at_5": 0.0,
                            "recall_at_10": 0.0,
                            "mrr": 0.0,
                            "ndcg_at_10": 0.0,
                            "correct": False,
                            "invalid": True,
                            "invalid_reason": f"{type(error).__module__}.{type(error).__qualname__}",
                            "failure_layer": "contract",
                            "construction_calls": 0,
                            "graph_write_attempts": 0,
                        }
                    writes += int(result["graph_write_attempts"])
                    constructions += int(result["construction_calls"])
                    row = {
                        "schema_version": "membind.saturated-fixed-work.qa-row.v1",
                        "method": seal.method,
                        "history_id": seal.history_id,
                        "namespace": seal.namespace,
                        "question_id": public["question_id"],
                        "qa_pair_id": public["qa_pair_id"],
                        **result,
                        "public_projection_sha256": _hash(public),
                        "graph_hash_before": before_hash,
                    }
                    row["payload_sha256"] = _hash(row)
                    rows.append(row)
                    _append(partial_path, row)
                after_graph = await _await(dependencies.snapshot_graph(runtime, seal))
                after_hash = _hash(after_graph)
                if before_hash != after_hash or writes != 0:
                    raise ProductionQAError("QA_GRAPH_WRITE_OR_MUTATION")
                if constructions != 0:
                    raise ProductionQAError("QA_EXTRA_CONSTRUCTION_CALLS")
                for row in rows[-4:]:
                    row["graph_hash_after"] = after_hash
                    row.pop("payload_sha256", None)
                    row["payload_sha256"] = _hash(row)
            finally:
                close = getattr(graphiti, "close", None)
                if callable(close):
                    await _await(close())
    finally:
        if dependencies.close is not None:
            await _await(dependencies.close())
    if len(rows) != 32:
        raise ProductionQAError("QA_ROW_COVERAGE_INVALID")
    # Materialize final rows only after all namespace guards pass. The raw
    # partial journal is retained as append-only execution evidence.
    final_payload = b"".join(_canonical_bytes(row) + b"\n" for row in rows)
    descriptor = os.open(
        output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
    )
    try:
        os.write(descriptor, final_payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return rows


def build_local_qa_components(
    *, repository_root: Path, api_key: str
) -> tuple[Any, Any, Any]:
    """Bind the frozen local Reader/Judge stack without running old entrypoints."""

    import_validation_module(
        repository_root, "evaluation.backends.openai_compatible"
    )
    transport_module = import_paper_eval_module(
        repository_root, "paper_eval.graph_quality_transport"
    )
    reader_module = import_paper_eval_module(
        repository_root, "paper_eval.quality_evaluation_v1_reader"
    )
    adapter_module = import_paper_eval_module(
        repository_root, "paper_eval.s2_adapters"
    )
    transport = transport_module.GraphQualityTransport(
        model="qwen3-32b-fp8",
        base_url="http://10.87.5.247:8000/v1",
        api_key=api_key,
        timeout_seconds=180.0,
    )
    reader = reader_module.QualityEvaluationV1Reader(
        model="qwen3-32b-fp8", transport=transport
    )
    judge = adapter_module.build_qualified_qwen_judge(
        base_url="http://10.87.5.247:8000/v1", api_key=api_key
    )
    return reader, judge, transport


def build_production_question_runner(
    *,
    repository_root: Path,
    reader: Any,
    judge: Any,
    episode_loader: Callable[[str, str], Sequence[Any]],
    source_record_loader: Callable[[str], Mapping[str, Any]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    retrieval_module = import_paper_eval_module(
        repository_root, "paper_eval.quality_evaluation_v1_retrieval"
    )
    quality_module = import_paper_eval_module(
        repository_root, "paper_eval.quality_evaluation_v1"
    )
    adapters = import_paper_eval_module(repository_root, "paper_eval.s2_adapters")

    async def run_question(
        *,
        runtime: Any,
        seal: NamespaceSeal,
        public_question: Mapping[str, Any],
        private_evaluation: Mapping[str, Any],
    ) -> dict[str, Any]:
        metrics = {
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "mrr": 0.0,
            "ndcg_at_10": 0.0,
        }
        graph = runtime.graphiti
        episodes = tuple(episode_loader(seal.history_id, seal.namespace))
        expected_by_name = {episode.name: episode.session_id for episode in episodes}
        try:
            query = await graph.driver.execute_query(
                "MATCH (e:Episodic) WHERE e.group_id = $group_id RETURN e.uuid AS uuid, e.name AS name",
                params={"group_id": seal.namespace},
                routing_="r",
            )
            records = getattr(query, "records", query[0] if isinstance(query, tuple) else query)
            episode_map = {
                str((record if isinstance(record, Mapping) else dict(record))["uuid"]): expected_by_name[
                    str((record if isinstance(record, Mapping) else dict(record))["name"])
                ]
                for record in records
            }
            if len(episode_map) != len(episodes):
                raise ValueError("qa_corpus_mapping_incomplete")
            retrieval = await retrieval_module.retrieve_quality_v1(
                graph=graph,
                query=str(public_question["question"]),
                namespace=seal.namespace,
                episode_uuid_to_session_id=episode_map,
            )
            metrics = dict(
                quality_module.session_ranking_metrics(
                    tuple(value.session_id for value in retrieval.episodes),
                    tuple(str(value) for value in private_evaluation["gold_session_ids"]),
                )
            )
        except Exception as error:
            return {
                **metrics,
                "correct": False,
                "invalid": True,
                "invalid_reason": f"{type(error).__module__}.{type(error).__qualname__}",
                "failure_layer": "retrieval",
                "construction_calls": 0,
                "graph_write_attempts": 0,
            }
        record = {
            key: value
            for key, value in source_record_loader(seal.history_id).items()
            if key in {"haystack_session_ids", "haystack_dates", "haystack_sessions"}
        }
        record.update(public_question)
        context = quality_module.build_context_pack(
            record=record,
            question=str(public_question["question"]),
            facts=retrieval.facts,
            episodes=retrieval.episodes,
        )
        try:
            answer = await reader.answer(
                context_json=context.context_json,
                question_date=str(public_question["question_date"]),
                question=str(public_question["question"]),
            )
        except Exception as error:
            return {
                **metrics,
                "correct": False,
                "invalid": True,
                "invalid_reason": f"{type(error).__module__}.{type(error).__qualname__}",
                "failure_layer": "reader",
                "construction_calls": 0,
                "graph_write_attempts": 0,
            }
        inputs = adapters.S2LiveInputs(
            run_id="SATURATED_FIXED_WORK_BASELINE_V1_2_QA",
            history_id=str(public_question["question_id"]),
            question_type=str(public_question["question_type"]),
            question=str(public_question["question"]),
            reference_answer=str(private_evaluation["reference_answer"]),
        )
        try:
            verdict = await judge.evaluate(hypothesis=answer.answer, inputs=inputs)
            if verdict.get("status") not in {"SUCCESS", "INVALID_OUTPUT"} or type(
                verdict.get("label")
            ) is not bool:
                raise ValueError("judge_verdict_invalid")
        except Exception as error:
            return {
                **metrics,
                "correct": False,
                "invalid": True,
                "invalid_reason": f"{type(error).__module__}.{type(error).__qualname__}",
                "failure_layer": "judge",
                "construction_calls": 0,
                "graph_write_attempts": 0,
            }
        return {
            **metrics,
            "correct": bool(verdict["label"]),
            "invalid": verdict.get("status") != "SUCCESS",
            "invalid_reason": (
                None if verdict.get("status") == "SUCCESS" else "judge_invalid_output"
            ),
            "failure_layer": (
                None if verdict.get("status") == "SUCCESS" else "judge"
            ),
            "construction_calls": 0,
            "graph_write_attempts": 0,
        }

    return run_question


def build_production_qa_dependencies(
    *,
    repository_root: Path,
    env_loader: Callable[[], Mapping[str, str]] | None = None,
    read_only_runtime_builder: Callable[..., Any] | None = None,
    graph_exporter: Callable[..., Any] | None = None,
    component_builder: Callable[..., tuple[Any, Any, Any]] = build_local_qa_components,
    question_runner_builder: Callable[..., Callable[..., Any]] = build_production_question_runner,
) -> ProductionQADependencies:
    """Compose the pinned read-only runtime and frozen QA data path."""

    if env_loader is None:
        native = import_validation_module(repository_root, "graphiti_native")
        env_path = repository_root / "membind-validation/.env"
        env_loader = lambda: native.load_env_file(env_path)
    env = dict(env_loader())
    required = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "CONSTRUCTION_LLM_API_KEY")
    if any(not isinstance(env.get(name), str) or not env[name] for name in required):
        raise ProductionQAError("QA_PRODUCTION_ENV_INVALID")
    if read_only_runtime_builder is None:
        runtime_module = import_paper_eval_module(repository_root, "paper_eval.s2_r0_live")
        read_only_runtime_builder = runtime_module.build_read_only_graphiti
    if graph_exporter is None:
        outputs = import_validation_module(repository_root, "live_outputs")
        graph_exporter = outputs.export_canonical_graph
    if not all(
        callable(value)
        for value in (
            read_only_runtime_builder,
            graph_exporter,
            component_builder,
            question_runner_builder,
        )
    ):
        raise ProductionQAError("QA_PRODUCTION_COMPONENT_INVALID")
    reader, judge, transport = component_builder(
        repository_root=repository_root,
        api_key=env["CONSTRUCTION_LLM_API_KEY"],
    )
    question_runner = question_runner_builder(
        repository_root=repository_root,
        reader=reader,
        judge=judge,
        episode_loader=lambda history, namespace: load_episode_inputs(
            repository_root, history, namespace
        ),
        source_record_loader=lambda history: load_frozen_qa_source_record(
            repository_root, history
        ),
    )

    async def runtime_factory(seal: NamespaceSeal) -> Any:
        del seal
        return await asyncio.to_thread(read_only_runtime_builder, env=env)

    async def snapshot_graph(runtime: Any, seal: NamespaceSeal) -> Any:
        episodes = list(
            load_episode_inputs(repository_root, seal.history_id, seal.namespace)
        )
        return await _await(
            graph_exporter(runtime.graphiti, episodes, seal.namespace)
        )

    async def close_transport() -> None:
        for name in ("aclose", "close"):
            close = getattr(transport, name, None)
            if callable(close):
                await _await(close())
                return

    return ProductionQADependencies(
        runtime_factory=runtime_factory,
        snapshot_graph=snapshot_graph,
        question_runner=question_runner,
        close=close_transport,
    )


__all__ = [
    "ProductionQADependencies",
    "ProductionQAError",
    "build_local_qa_components",
    "build_production_qa_dependencies",
    "build_production_question_runner",
    "execute_production_qa",
]
