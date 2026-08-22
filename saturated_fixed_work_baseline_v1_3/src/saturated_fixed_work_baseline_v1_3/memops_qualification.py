"""Offline freeze and minimal live qualification for selected MemOps samples.

The live path composes the existing v1.3 dependencies and v1.2
``execute_live_block``.  It never implements a new scheduler or Graphiti
adapter.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .memops_adapter import (
    DEFAULT_MEMOPS_ROOT,
    MemOpsSample,
    build_episode_inputs,
    build_memops_qa_projection,
    build_memops_source_record,
    build_workload_identity,
    inspect_current_state,
    sample_manifest_row,
    select_memops_samples,
)


class MemOpsQualificationError(ValueError):
    """Qualification contract or live dependency failed closed."""


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


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise MemOpsQualificationError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(value)
    body["payload_sha256"] = _hash(body)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(
            descriptor,
            json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            + b"\n",
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_text(path: Path, value: str) -> None:
    if path.exists():
        raise MemOpsQualificationError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MemOpsQualificationError(f"ARTIFACT_UNREADABLE:{path}") from None
    if not isinstance(value, dict):
        raise MemOpsQualificationError(f"ARTIFACT_OBJECT_REQUIRED:{path}")
    payload_sha256 = value.get("payload_sha256")
    if payload_sha256 is not None:
        unsigned = {key: child for key, child in value.items() if key != "payload_sha256"}
        if not isinstance(payload_sha256, str) or payload_sha256 != _hash(unsigned):
            raise MemOpsQualificationError(f"ARTIFACT_HASH_MISMATCH:{path}")
    return value


def freeze_memops_selection(
    output_root: Path,
    *,
    memops_root: Path = DEFAULT_MEMOPS_ROOT,
    limit: int = 5,
) -> dict[str, Any]:
    """Freeze official MemOps source/gold before any B0/B1 execution."""

    root = Path(output_root)
    if root.exists() and any(root.iterdir()):
        raise MemOpsQualificationError("MEMOPS_QUALIFICATION_ROOT_MUST_BE_NEW")
    root.mkdir(parents=True, exist_ok=False)
    selected = select_memops_samples(memops_root, limit=limit)
    rows: list[dict[str, Any]] = []
    for sample in selected:
        rows.append(sample_manifest_row(sample))
        envelope = {
            "schema_version": "sfwb.v1.3.memops-frozen-sample.v1",
            "sample_id": sample.sample_id,
            "source_file": sample.source_file,
            "source_sha256": sample.source_sha256,
            "sample": sample.raw,
        }
        _write_new_json(root / "frozen_samples" / f"{sample.sample_id}.json", envelope)
    manifest = {
        "schema_version": "sfwb.v1.3.memops-qualification-selection.v1",
        "status": "OFFLINE_SELECTION_FROZEN",
        "benchmark": "MemOps",
        "memops_root": str(Path(memops_root).resolve()),
        "selection_rule": {
            "official_operation_types": ["Update", "TrajectoryOps"],
            "preferred_operation_type": "Update",
            "max_samples": limit,
            "same_target_id": True,
            "confirmed_old_new": True,
            "old_value_not_equal_new_value": True,
            "old_new_segment_indices_distinct": True,
            "qa_types": sorted(
                {"StateTransition", "CandidateDisambiguation", "StateTrajectory"}
            ),
            "gold_blind_before_execution": True,
            "special_semantics_policy": "tentative/retracted values are retained only as official negative distractors; qualifying current state is derived from confirmed transitions",
        },
        "official_qa_evaluator": {
            "path": str(Path(memops_root).resolve() / "5.5-evaluate_operation_metrics.py"),
            "sha256": hashlib.sha256(
                (Path(memops_root) / "5.5-evaluate_operation_metrics.py").read_bytes()
            ).hexdigest(),
            "prompt_builder": "build_evaluation_prompt",
            "result_parser": "parse_judge_metrics",
        },
        "samples": rows,
    }
    _write_new_json(root / "selection_manifest.json", manifest)
    _write_new_text(
        root / "offline_gate.txt",
        "OFFLINE_MEMOPS_SELECTION_PASS\n",
    )
    return {
        "status": "OFFLINE_MEMOPS_SELECTION_PASS",
        "output_root": str(root),
        "sample_ids": [sample.sample_id for sample in selected],
        "selection_manifest": str(root / "selection_manifest.json"),
    }


def _load_frozen_samples(root: Path) -> tuple[MemOpsSample, ...]:
    manifest = _read_json(root / "selection_manifest.json")
    evaluator = manifest.get("official_qa_evaluator")
    if evaluator is not None:
        if not isinstance(evaluator, Mapping):
            raise MemOpsQualificationError("MEMOPS_OFFICIAL_EVALUATOR_FREEZE_INVALID")
        evaluator_path = Path(str(evaluator.get("path") or ""))
        expected_hash = evaluator.get("sha256")
        if (
            not evaluator_path.is_file()
            or not isinstance(expected_hash, str)
            or hashlib.sha256(evaluator_path.read_bytes()).hexdigest() != expected_hash
        ):
            raise MemOpsQualificationError("MEMOPS_OFFICIAL_EVALUATOR_HASH_MISMATCH")
    rows = manifest.get("samples")
    if not isinstance(rows, list) or not rows:
        raise MemOpsQualificationError("MEMOPS_SELECTION_MANIFEST_INVALID")
    samples: list[MemOpsSample] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise MemOpsQualificationError("MEMOPS_SELECTION_ROW_INVALID")
        sample_id = str(row.get("sample_id") or "")
        envelope = _read_json(root / "frozen_samples" / f"{sample_id}.json")
        sample_raw = envelope.get("sample")
        if not isinstance(sample_raw, Mapping):
            raise MemOpsQualificationError("MEMOPS_FROZEN_SAMPLE_INVALID")
        # Reconstruct through the public parser contract using a temporary
        # source-free path is unnecessary; the frozen manifest is authoritative
        # for selection, while the source JSON remains in the envelope.
        from .memops_adapter import _confirmed_update_pairs, _qa_rows  # type: ignore[attr-defined]

        operations = sample_raw.get("operations")
        if not isinstance(operations, list):
            raise MemOpsQualificationError("MEMOPS_FROZEN_OPERATIONS_INVALID")
        transitions = _confirmed_update_pairs(tuple(row for row in operations if isinstance(row, Mapping)))
        if not transitions:
            raise MemOpsQualificationError("MEMOPS_FROZEN_TRANSITIONS_INVALID")
        target_id = str(row.get("target_id") or transitions[0].target_id)
        target_name = str(row.get("target_name") or transitions[0].target_name)
        target_ops = [
            operation
            for operation in operations
            if isinstance(operation, Mapping)
            and operation.get("target", {}).get("target_id") == target_id
            and operation.get("validity") == "confirmed"
            and operation.get("new_value") is not None
        ]
        target_ops.sort(key=lambda operation: int(operation["trigger_span"]["segment_index"]))
        latest = str(target_ops[-1]["new_value"])
        stale = tuple(
            dict.fromkeys(
                str(operation["new_value"])
                for operation in target_ops[:-1]
                if str(operation["new_value"]) != latest
            )
        )
        questions = _qa_rows(sample_raw, sample_id=sample_id, transitions=transitions)
        samples.append(
            MemOpsSample(
                sample_id=sample_id,
                operation_type=str(sample_raw["operation_type"]),
                source_file=str(envelope.get("source_file") or "frozen"),
                source_sha256=str(envelope.get("source_sha256") or ""),
                history_id=str(row.get("history_id") or f"memops-{sample_id.lower()}"),
                target_id=target_id,
                target_name=target_name,
                transitions=transitions,
                latest_confirmed_value=latest,
                stale_confirmed_values=stale,
                questions=questions,
                raw=sample_raw,
            )
        )
    return tuple(samples)


def _block_identity(*, run_id: str, workload_sha256: str, namespace: str) -> Any:
    from .simple_campaign import build_execution_identity

    return build_execution_identity(
        run_id=run_id,
        repository_root=Path("."),
        workload_sha256=workload_sha256,
        namespace=namespace,
    )


def _source_tokens(episodes: Sequence[Any]) -> int:
    from .simple_campaign import _tokenizer_source_counter

    return _tokenizer_source_counter(episodes)


def _namespace(run_id: str, method: str, sample: MemOpsSample) -> str:
    return f"{run_id}-MEMOPS-{sample.sample_id}-{method}-attempt-001"


def _cache_salt(run_id: str, block_id: str) -> str:
    from saturated_fixed_work_baseline_v1_2.live import derive_cache_salt

    return derive_cache_salt(run_id, block_id, attempt_ordinal=1)


async def _service_idle(repository_root: Path, driver: Any) -> bool:
    from saturated_fixed_work_baseline_v1_2.services import direct_get_text
    from saturated_fixed_work_baseline_v1_2.telemetry import parse_vllm_026_metrics

    for port in (8000, 8001):
        response = await asyncio.to_thread(
            direct_get_text, f"http://10.87.5.247:{port}/metrics", timeout_s=10.0
        )
        parsed = parse_vllm_026_metrics(
            str(response["text"]),
            timestamp_ns=time.monotonic_ns(),
            repository_root=repository_root,
        )
        if parsed.value is None:
            return False
        values = parsed.value.values
        if float(values["running_requests"]) != 0.0 or float(values["waiting_requests"]) != 0.0:
            return False
    result = driver.execute_query(
        "SHOW TRANSACTIONS YIELD currentQuery "
        "WHERE currentQuery IS NULL OR NOT currentQuery STARTS WITH 'SHOW TRANSACTIONS' "
        "RETURN count(*) AS active_transactions",
        routing_="r",
    )
    if hasattr(result, "__await__"):
        result = await result
    records = getattr(result, "records", result[0] if isinstance(result, tuple) else result)
    if not records or int(dict(records[0])["active_transactions"]) != 0:
        return False
    return True


def _load_official_memops_evaluator(memops_root: Path = DEFAULT_MEMOPS_ROOT) -> Any:
    path = Path(memops_root) / "5.5-evaluate_operation_metrics.py"
    if not path.is_file():
        raise MemOpsQualificationError("MEMOPS_OFFICIAL_EVALUATOR_MISSING")
    spec = importlib.util.spec_from_file_location("_sfwb_memops_official_evaluator", path)
    if spec is None or spec.loader is None:
        raise MemOpsQualificationError("MEMOPS_OFFICIAL_EVALUATOR_UNLOADABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "build_evaluation_prompt", None)) or not callable(
        getattr(module, "parse_judge_metrics", None)
    ) or not callable(getattr(module, "format_evidence_conversation", None)):
        raise MemOpsQualificationError("MEMOPS_OFFICIAL_EVALUATOR_CONTRACT_INVALID")
    return module


def _official_memops_judge_entry(
    sample: MemOpsSample, qa: Any, hypothesis: str
) -> dict[str, Any]:
    answers = sample.raw.get("answer")
    if not isinstance(answers, list):
        raise MemOpsQualificationError("MEMOPS_OFFICIAL_ANSWER_INVENTORY_INVALID")
    selected = next(
        (
            row
            for row in answers
            if isinstance(row, Mapping)
            and row.get("question_pair_id") == qa.question_pair_id
            and row.get("evaluation_setting") == qa.evaluation_setting
            and row.get("evaluation_type") == qa.evaluation_type
        ),
        None,
    )
    if selected is None:
        raise MemOpsQualificationError("MEMOPS_OFFICIAL_ANSWER_ROW_MISSING")
    operations = sample.raw.get("operations")
    if not isinstance(operations, list):
        raise MemOpsQualificationError("MEMOPS_OFFICIAL_OPERATIONS_INVALID")
    return {
        **dict(selected),
        "sample_id": sample.sample_id,
        "source_file": sample.source_file,
        "operation_type": sample.operation_type,
        "gold_operations": operations,
        "hypothesis": hypothesis,
    }


class _MemOpsOfficialJudge:
    def __init__(self, *, sample: MemOpsSample, qa: Any, transport: Any, module: Any) -> None:
        self._sample = sample
        self._qa = qa
        self._transport = transport
        self._module = module
        self.last_metrics: dict[str, Any] | None = None

    async def evaluate(self, *, hypothesis: str, inputs: Any) -> dict[str, Any]:
        del inputs
        entry = _official_memops_judge_entry(self._sample, self._qa, hypothesis)
        evidence = self._module.format_evidence_conversation(self._sample.raw)
        prompt = self._module.build_evaluation_prompt(
            entry, evidence_conversation=evidence
        )
        response = await self._transport.complete(
            {
                "model": "qwen3-32b-fp8",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 512,
                "n": 1,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            }
        )
        if response.finish_reason != "stop":
            raise MemOpsQualificationError("MEMOPS_OFFICIAL_JUDGE_TRUNCATED")
        metrics = dict(self._module.parse_judge_metrics(response.content, entry=entry))
        self.last_metrics = {
            **metrics,
            "official_prompt_builder": "build_evaluation_prompt",
            "official_result_parser": "parse_judge_metrics",
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "response_sha256": hashlib.sha256(response.content.encode("utf-8")).hexdigest(),
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "finish_reason": response.finish_reason,
            "raw_prompt_persisted": False,
            "raw_response_persisted": False,
        }
        return {
            "status": "SUCCESS",
            "label": metrics.get("answer_score") == 1,
        }


async def _qa_sample(
    *,
    repository_root: Path,
    env: Mapping[str, str],
    sample: MemOpsSample,
    method: str,
    namespace: str,
    canonical_hash: str,
    construction_ordinal: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from saturated_fixed_work_baseline_v1_2.production_qa import (
        build_local_qa_components,
        build_production_question_runner,
    )
    from saturated_fixed_work_baseline_v1_2.qa_lane import NamespaceSeal
    from saturated_fixed_work_baseline_v1_2.reuse import import_paper_eval_module

    quality_runtime_module = import_paper_eval_module(
        repository_root, "paper_eval.graph_quality_live"
    )
    outputs_module = import_paper_eval_module(repository_root, "live_outputs")
    episodes = build_episode_inputs(sample, namespace)
    source_record = build_memops_source_record(sample, episodes)
    reader, legacy_judge, transport = build_local_qa_components(
        repository_root=repository_root,
        api_key=str(env["CONSTRUCTION_LLM_API_KEY"]),
    )
    official_evaluator = _load_official_memops_evaluator()
    runtime = await asyncio.to_thread(
        quality_runtime_module.build_graph_quality_runtime,
        env=dict(env),
    )
    seal = NamespaceSeal(
        method=method,
        history_id=sample.history_id,
        namespace=namespace,
        canonical_hash=canonical_hash,
        construction_call_ordinal=construction_ordinal,
    )
    rows: list[dict[str, Any]] = []
    try:
        # The QA runtime is read-only, but capture a before/after snapshot so
        # the qualification result proves that property instead of assuming it.
        graph_before = await outputs_module.export_canonical_graph(
            runtime.graphiti, list(episodes), namespace
        )
        graph_before_hash = _hash(graph_before)
        for qa in sample.questions:
            official_judge = _MemOpsOfficialJudge(
                sample=sample,
                qa=qa,
                transport=transport,
                module=official_evaluator,
            )
            runner = build_production_question_runner(
                repository_root=repository_root,
                reader=reader,
                judge=official_judge,
                episode_loader=lambda _history, _namespace: episodes,
                source_record_loader=lambda _history: source_record,
            )
            public, private = build_memops_qa_projection(sample, qa)
            result = await runner(
                runtime=runtime,
                seal=seal,
                public_question=public,
                private_evaluation=private,
            )
            rows.append(
                {
                    **result,
                    "sample_id": sample.sample_id,
                    "question_id": qa.question_id,
                    "question_pair_id": qa.question_pair_id,
                    "evaluation_type": qa.evaluation_type,
                    "evaluation_setting": qa.evaluation_setting,
                    "expected_answer": qa.expected_answer,
                    "gold_memory_state": qa.gold_memory_state,
                    "judge_rubric": dict(qa.judge_rubric),
                    "gold_provenance": list(qa.gold_provenance),
                    "memops_official_judge": official_judge.last_metrics,
                }
            )
        graph = await outputs_module.export_canonical_graph(
            runtime.graphiti, list(episodes), namespace
        )
        writes = sum(int(row.get("graph_write_attempts") or 0) for row in rows)
        construction_calls = sum(int(row.get("construction_calls") or 0) for row in rows)
        state = inspect_current_state(sample, graph)
        graph_after = _hash(graph)
    finally:
        await runtime.aclose()
        close_judge = getattr(legacy_judge, "aclose", None)
        if callable(close_judge):
            value = close_judge()
            if hasattr(value, "__await__"):
                await value
        for name in ("aclose", "close"):
            close = getattr(transport, name, None)
            if callable(close):
                value = close()
                if hasattr(value, "__await__"):
                    await value
                break
    qa_summary = {
        "sample_id": sample.sample_id,
        "method": method,
        "namespace": namespace,
        "rows": len(rows),
        "correct_rows": sum(bool(row.get("correct")) and not bool(row.get("invalid")) for row in rows),
        "all_correct": bool(rows) and all(bool(row.get("correct")) and not bool(row.get("invalid")) for row in rows),
        "stale_value_errors": sum(
            int((row.get("memops_official_judge") or {}).get("stale_value") == 1)
            for row in rows
        ),
        "graph_write_attempts": writes,
        "construction_calls": construction_calls,
        "graph_hash_before_qa": graph_before_hash,
        "graph_hash_after_qa": graph_after,
        "graph_mutated": graph_before_hash != graph_after,
        "state_inspection": state,
    }
    return rows, qa_summary


async def run_memops_live(
    *,
    qualification_root: Path,
    method_names: Sequence[str],
    repository_root: Path,
) -> dict[str, Any]:
    """Run exactly the requested policy set over the frozen sample set."""

    root = Path(qualification_root)
    samples = _load_frozen_samples(root)
    allowed = {"B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC"}
    methods = tuple(method_names)
    if not methods or any(method not in allowed for method in methods):
        raise MemOpsQualificationError("MEMOPS_METHOD_SET_INVALID")
    from saturated_fixed_work_baseline_v1_2.live import FormalBlock
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_2.reuse import import_validation_module, import_paper_eval_module
    from saturated_fixed_work_baseline_v1_2.schedules import Method
    from saturated_fixed_work_baseline_v1_2.live_block import execute_live_block
    from saturated_fixed_work_baseline_v1_2.services import probe_model_catalog
    from .live_dependencies import build_v13_live_dependencies, build_v13_neo4j_idle_probe
    from .formal_baseline import build_lifecycle_evidence
    from .simple_campaign import _SimpleAttemptStore

    env = _load_env(repository_root)
    driver_module = import_validation_module(repository_root, "graphiti_core.driver.neo4j_driver")
    driver = driver_module.Neo4jDriver(env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"])
    telemetry = import_paper_eval_module(repository_root, "paper_eval.apc_vllm_telemetry")
    async def service_idle() -> bool:
        return await _service_idle(repository_root, driver)

    construction = await asyncio.to_thread(
        probe_model_catalog,
        "http://10.87.5.247:8000/v1/models",
        expected_model="qwen3-32b-fp8",
        expected_max_model_len=65536,
    )
    embedding = await asyncio.to_thread(
        probe_model_catalog,
        "http://10.87.5.247:8001/v1/models",
        expected_model="qwen3-embedding-0.6b",
        expected_max_model_len=32768,
    )
    canary = await driver.execute_query("RETURN 1 AS ok", routing_="r")
    records = getattr(canary, "records", canary[0] if isinstance(canary, tuple) else canary)
    if construction.get("status") != "PASS" or embedding.get("status") != "PASS" or not records:
        raise MemOpsQualificationError("MEMOPS_LIVE_PREFLIGHT_FAILED")
    dependencies = build_v13_live_dependencies(
        repository_root=repository_root,
        service_idle=service_idle,
    )
    run_id = root.name
    outputs: list[dict[str, Any]] = []
    ordinal = 1
    b0_ineligible = False
    try:
        for sample in samples:
            for method_name in methods:
                method = Method(method_name)
                block_id = f"memops-{ordinal:03d}-{sample.sample_id}-{method_name}"
                namespace = _namespace(run_id, method_name, sample)
                cache_salt = _cache_salt(run_id, block_id)
                prep = await asyncio.gather(
                    asyncio.to_thread(
                        telemetry.probe_vllm_cache_salt,
                        "http://10.87.5.247:8000/v1",
                        env.get("CONSTRUCTION_LLM_API_KEY"),
                        cache_salt,
                    ),
                    asyncio.to_thread(
                        telemetry.probe_vllm_embedding_cache_salt,
                        "http://10.87.5.247:8001/v1",
                        env.get("EMBEDDING_API_KEY"),
                        cache_salt,
                    ),
                )
                if prep[0].get("status") != "CACHE_SALT_ACCEPTED" or prep[1].get("status") != "EMBEDDING_CACHE_SALT_ACCEPTED" or not await service_idle():
                    raise MemOpsQualificationError("MEMOPS_BLOCK_PREPARATION_FAILED")
                episodes = build_episode_inputs(sample, namespace)
                workload_hash = build_workload_identity(sample, episodes)
                block = FormalBlock(
                    ordinal=ordinal,
                    block_id=block_id,
                    run_id=run_id,
                    history_id=sample.history_id,
                    method=method,
                    attempt_ordinal=1,
                    namespace=namespace,
                    cache_salt=cache_salt,
                )
                identity = _block_identity(
                    run_id=run_id,
                    workload_sha256=workload_hash,
                    namespace=namespace,
                )
                result = await execute_live_block(
                    repository_root=repository_root,
                    run_root=root,
                    block=block,
                    identity=identity,
                    episodes=episodes,
                    dependencies=dependencies,
                    source_tokens=_source_tokens(episodes),
                    attempt_store_factory=_SimpleAttemptStore.create,
                )
                attempt_root = root / "blocks" / block_id / "attempt-001"
                lifecycle = build_lifecycle_evidence(
                    formal_start_ns=int(result["t0_ns"]),
                    durable_complete_ns=int(result["t_durable_complete_ns"]),
                    validation_complete_ns=int(result["t_validated_seal_ns"]),
                    namespace=namespace,
                )
                _write_new_json(attempt_root / "lifecycle.json", lifecycle)
                _write_new_json(
                    attempt_root / "memops_block_result.json",
                    {
                        "schema_version": "sfwb.v1.3.memops-block-result.v1",
                        "sample_id": sample.sample_id,
                        "method": method_name,
                        "namespace": namespace,
                        "workload_sha256": workload_hash,
                        "metrics": result,
                    },
                )
                graph = _read_json(attempt_root / "canonical_graph.json")
                output = {
                    "sample_id": sample.sample_id,
                    "method": method_name,
                    "block_id": block_id,
                    "namespace": namespace,
                    "attempt_root": str(attempt_root),
                    "workload_sha256": workload_hash,
                    "metrics": result,
                    "state_inspection": inspect_current_state(sample, graph),
                }
                outputs.append(output)
                qa_rows, qa_summary = await _qa_sample(
                    repository_root=repository_root,
                    env=env,
                    sample=sample,
                    method=output["method"],
                    namespace=output["namespace"],
                    canonical_hash=str(output["metrics"].get("canonical_graph_hash")),
                    construction_ordinal=1,
                )
                output["qa_rows"] = qa_rows
                output["qa_summary"] = qa_summary
                _write_new_json(
                    Path(output["attempt_root"]) / "memops_qa_summary.json",
                    qa_summary,
                )
                _write_new_json(
                    Path(output["attempt_root"]) / "memops_qa_rows.json",
                    {"rows": qa_rows},
                )
                b0_ineligible = bool(
                    method_name == "B0_NATIVE_SERIAL"
                    and (
                        output["state_inspection"].get("status") != "PASS"
                        or not qa_summary.get("all_correct")
                        or qa_summary.get("graph_write_attempts") != 0
                        or qa_summary.get("construction_calls") != 0
                        or qa_summary.get("graph_mutated") is not False
                    )
                )
                ordinal += 1
                if b0_ineligible:
                    break
            if b0_ineligible:
                break
    finally:
        await driver.close()
    return {
        "schema_version": "sfwb.v1.3.memops-live-qualification.v1",
        "status": "LIVE_COMPLETE",
        "qualification_root": str(root),
        "methods": list(methods),
        "sample_ids": [sample.sample_id for sample in samples],
        "outputs": outputs,
    }


def b0_eligibility(result: Mapping[str, Any]) -> dict[str, Any]:
    outputs = result.get("outputs")
    if (
        result.get("status") != "LIVE_COMPLETE"
        or result.get("methods") != ["B0_NATIVE_SERIAL"]
        or not isinstance(outputs, list)
        or not outputs
    ):
        return {"status": "STOP_MEMOPS_GRAPHITI_B0_INELIGIBLE", "reason": "B0_OUTPUT_EMPTY"}
    failures: list[dict[str, Any]] = []
    sample_ids = result.get("sample_ids")
    observed_sample_ids = [str(output.get("sample_id")) for output in outputs]
    if (
        not isinstance(sample_ids, list)
        or not sample_ids
        or observed_sample_ids != [str(value) for value in sample_ids]
        or len(set(observed_sample_ids)) != len(observed_sample_ids)
    ):
        failures.append({"reason": "B0_SAMPLE_COVERAGE_INCOMPLETE"})
    for output in outputs:
        metrics = output.get("metrics", {})
        state = output.get("state_inspection", {})
        qa = output.get("qa_summary", {})
        created = metrics.get("created_sequences")
        episode_count = metrics.get("episode_count")
        expected_created: list[int] | None = None
        if isinstance(episode_count, int) and not isinstance(episode_count, bool) and episode_count >= 1:
            expected_created = list(range(episode_count))
        if (
            metrics.get("valid") is not True
            or not isinstance(created, list)
            or expected_created is None
            or created != expected_created
        ):
            failures.append({"sample_id": output.get("sample_id"), "reason": "PUBLICATION_INCOMPLETE"})
        if state.get("status") != "PASS":
            failures.append({"sample_id": output.get("sample_id"), "reason": "CURRENT_STATE_INSPECTION", "detail": state})
        if (
            not qa.get("all_correct")
            or qa.get("graph_write_attempts") != 0
            or qa.get("construction_calls") != 0
            or qa.get("graph_mutated") is not False
        ):
            failures.append({"sample_id": output.get("sample_id"), "reason": "QA_NOT_ELIGIBLE", "detail": qa})
    return {
        "status": "B0_QUALIFIED" if not failures else "STOP_MEMOPS_GRAPHITI_B0_INELIGIBLE",
        "failures": failures,
        "sample_count": len(outputs),
    }


def compare_b0_b1(result: Mapping[str, Any]) -> dict[str, Any]:
    outputs = result.get("outputs")
    if not isinstance(outputs, list):
        return {"status": "STOP_MEMOPS_NO_B1_DIVERGENCE", "reason": "B1_OUTPUT_INVALID"}
    by_sample: dict[str, dict[str, Mapping[str, Any]]] = {}
    for output in outputs:
        by_sample.setdefault(str(output.get("sample_id")), {})[str(output.get("method"))] = output
    divergences: list[dict[str, Any]] = []
    coverage_failures: list[dict[str, Any]] = []
    sample_ids = result.get("sample_ids")
    expected_sample_ids = (
        [str(value) for value in sample_ids]
        if isinstance(sample_ids, list) and sample_ids
        else []
    )
    if not expected_sample_ids or set(by_sample) != set(expected_sample_ids):
        coverage_failures.append({"reason": "PAIRED_SAMPLE_COVERAGE_INCOMPLETE"})
    for sample_id, pair in by_sample.items():
        b0 = pair.get("B0_NATIVE_SERIAL")
        b1 = pair.get("B1_NAIVE_WHOLE_UPDATE_ASYNC")
        if not b0 or not b1:
            coverage_failures.append({"sample_id": sample_id, "reason": "PAIRED_METHOD_MISSING"})
            continue
        b0_state = b0.get("state_inspection", {})
        b1_state = b1.get("state_inspection", {})
        b0_qa = b0.get("qa_summary", {})
        b1_qa = b1.get("qa_summary", {})
        same_workload = b0.get("workload_sha256") == b1.get("workload_sha256")
        if not same_workload:
            coverage_failures.append({"sample_id": sample_id, "reason": "WORKLOAD_IDENTITY_MISMATCH"})
            continue
        concrete = (
            b0_state.get("status") == "PASS"
            and b0_qa.get("all_correct") is True
            and b1_state.get("status") != "PASS"
        )
        if concrete:
            divergences.append(
                {
                    "sample_id": sample_id,
                    "b0_state": b0_state,
                    "b1_state": b1_state,
                    "b0_qa": b0_qa,
                    "b1_qa": b1_qa,
                }
            )
    return {
        "status": (
            "GO_MEMOPS_B1_ATTACK_QUALIFIED"
            if divergences and not coverage_failures
            else "STOP_MEMOPS_NO_B1_DIVERGENCE"
        ),
        "concrete_b1_state_divergences": divergences,
        "pair_coverage_failures": coverage_failures,
    }


def load_qualified_b0_result(root: Path) -> dict[str, Any]:
    """Load the append-only B0 result and re-evaluate its eligibility gate."""

    selected = Path(root)
    gate = _read_json(selected / "b0_gate.json")
    result = _read_json(selected / "b0_result.json")
    recomputed = b0_eligibility(result)
    if gate.get("status") != "B0_QUALIFIED" or recomputed.get("status") != "B0_QUALIFIED":
        raise MemOpsQualificationError("MEMOPS_B1_REQUIRES_QUALIFIED_B0")
    return result


__all__ = [
    "MemOpsQualificationError",
    "b0_eligibility",
    "compare_b0_b1",
    "freeze_memops_selection",
    "load_qualified_b0_result",
    "run_memops_live",
]
