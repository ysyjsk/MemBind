"""Append-only v1.3 B0/B1 four-history formal baseline orchestration.

This module is deliberately an evaluation composition layer.  Construction
semantics, instrumentation, Graphiti adaptation, and the frozen QA runner are
imported from the already-qualified v1.2/v1.3 components; no runtime policy is
implemented here. Formal execution is history-scoped: B0 and B1 complete
before the read-only QA checkpoint for that history runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .block_lifecycle import BlockLifecycle

FORMAL_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
FORMAL_METHODS = ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")
EXPECTED_EPISODES = {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}
EXPECTED_SOURCE_TOKENS = {"07741c45": 104014, "b6019101": 106914, "6071bd76": 105786, "a2f3aa27": 105977}


@dataclass(frozen=True, slots=True)
class FormalMatrixRow:
    ordinal: int
    block_id: str
    run_id: str
    history_id: str
    method: str
    attempt_ordinal: int
    namespace: str
    cache_salt: str


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _write_new_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    if path.exists():
        raise ValueError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    body = dict(value)
    body["payload_sha256"] = _hash(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return body


def _write_new_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Materialize the compatibility aggregate after all checkpoints pass."""

    if path.exists():
        raise ValueError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    payload = b"".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        for row in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_formal_matrix(run_id: str) -> tuple[FormalMatrixRow, ...]:
    if not isinstance(run_id, str) or not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in run_id):
        raise ValueError("RUN_ID_INVALID")
    rows: list[FormalMatrixRow] = []
    ordinal = 1
    for history_id in FORMAL_HISTORIES:
        for method in FORMAL_METHODS:
            block_id = f"formal-{ordinal:03d}-{history_id}-{method}"
            namespace = f"sfwb-v1-3-{method}-{history_id}-{run_id}-attempt-001"
            cache_salt = "sfwb13-" + hashlib.sha256(
                f"SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_3\0{run_id}\0{block_id}\01".encode("ascii")
            ).hexdigest()[:56]
            rows.append(FormalMatrixRow(ordinal, block_id, run_id, history_id, method, 1, namespace, cache_salt))
            ordinal += 1
    validate_formal_matrix(rows)
    return tuple(rows)


def group_formal_matrix_by_history(
    rows: Sequence[FormalMatrixRow],
) -> tuple[tuple[str, tuple[FormalMatrixRow, ...]], ...]:
    """Return the fixed B0/B1 order for each history before the next history."""

    selected = tuple(rows)
    validate_formal_matrix(selected)
    groups: list[tuple[str, tuple[FormalMatrixRow, ...]]] = []
    for history_id in FORMAL_HISTORIES:
        group = tuple(row for row in selected if row.history_id == history_id)
        if tuple(row.method for row in group) != FORMAL_METHODS:
            raise ValueError("FORMAL_HISTORY_METHOD_ORDER_INVALID")
        groups.append((history_id, group))
    return tuple(groups)


def validate_formal_matrix(rows: Sequence[FormalMatrixRow]) -> None:
    selected = tuple(rows)
    expected = {(history, method) for history in FORMAL_HISTORIES for method in FORMAL_METHODS}
    observed = {(row.history_id, row.method) for row in selected}
    if len(selected) != 8:
        raise ValueError("FORMAL_MATRIX_COVERAGE_INVALID")
    if len({row.namespace for row in selected}) != 8:
        raise ValueError("FORMAL_MATRIX_NAMESPACE_INVALID")
    if observed != expected:
        raise ValueError("FORMAL_MATRIX_COVERAGE_INVALID")
    if len({row.cache_salt for row in selected}) != 8:
        raise ValueError("FORMAL_MATRIX_CACHE_SALT_INVALID")
    if {row.ordinal for row in selected} != set(range(1, 9)):
        raise ValueError("FORMAL_MATRIX_ORDINAL_INVALID")
    if any(row.method not in FORMAL_METHODS or row.history_id not in FORMAL_HISTORIES for row in selected):
        raise ValueError("FORMAL_MATRIX_IDENTITY_INVALID")


def build_lifecycle_evidence(*, formal_start_ns: int, durable_complete_ns: int, validation_complete_ns: int, namespace: str) -> dict[str, Any]:
    values = (formal_start_ns, durable_complete_ns, validation_complete_ns)
    if not namespace or any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("LIFECYCLE_TIMESTAMP_INVALID")
    if not formal_start_ns < durable_complete_ns:
        raise ValueError("DURABLE_COMPLETION_REQUIRED")
    if validation_complete_ns < durable_complete_ns:
        raise ValueError("DURABLE_COMPLETION_REQUIRED")
    # Use the shared contract's state order as the source of truth.  The live
    # runner supplies the measured monotonic boundary; preparation is recorded
    # separately because it is outside T_build.
    lifecycle = BlockLifecycle(monotonic_ns=lambda: formal_start_ns)
    lifecycle.fresh_namespace()
    lifecycle.backend_prepared()
    lifecycle.service_ready()
    lifecycle.warmup_complete()
    lifecycle.backend_idle()
    lifecycle.formal_start()
    lifecycle.events[-1] = ("FORMAL_START", formal_start_ns)
    lifecycle.events.append(("CONSTRUCTION_COMPLETE", durable_complete_ns))
    lifecycle.events.append(("DURABLE_COMPLETE", durable_complete_ns))
    lifecycle.state = "DURABLE_COMPLETE"
    lifecycle.timer_stop_ns = durable_complete_ns
    lifecycle.events.append(("VALIDATION_COMPLETE", validation_complete_ns))
    lifecycle.state = "VALIDATION_COMPLETE"
    return {
        "schema_version": "sfwb.v1.3.formal-block-lifecycle.v1",
        "namespace": namespace,
        "events": [{"event": event, "monotonic_ns": stamp} for event, stamp in lifecycle.events],
        "timer_start_ns": formal_start_ns,
        "timer_stop_ns": durable_complete_ns,
        "build_makespan_ns": durable_complete_ns - formal_start_ns,
        "validation_outside_build_timer": True,
    }


def _quality_index(quality_rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, float | int | None]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in quality_rows:
        grouped.setdefault((str(row.get("method")), str(row.get("history_id"))), []).append(row)
    result: dict[tuple[str, str], dict[str, float | int | None]] = {}
    metrics = ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10")
    for key, rows in grouped.items():
        if len(rows) != 4:
            raise ValueError("QA_HISTORY_ROW_COVERAGE_INVALID")
        result[key] = {
            metric: sum(float(row.get(metric, 0.0)) for row in rows) / 4.0 for metric in metrics
        }
        result[key]["qa_score"] = sum(
            bool(row.get("correct")) and not bool(row.get("invalid")) for row in rows
        ) / 4.0
        result[key]["qa_n"] = 4
    return result


def reduce_baseline_outputs(rows: Sequence[Mapping[str, Any]], quality_rows: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    selected = [dict(row) for row in rows]
    keys = {(str(row.get("history_id")), str(row.get("method"))) for row in selected}
    expected = {(history, method) for history in FORMAL_HISTORIES for method in FORMAL_METHODS}
    if keys != expected or len(selected) != 8:
        raise ValueError("FORMAL_RESULT_COVERAGE_INVALID")
    quality = _quality_index(quality_rows) if quality_rows else {}
    performance: list[dict[str, Any]] = []
    work: list[dict[str, Any]] = []
    correctness: list[dict[str, Any]] = []
    main: list[dict[str, Any]] = []
    for row in sorted(selected, key=lambda item: (FORMAL_HISTORIES.index(str(item["history_id"])), FORMAL_METHODS.index(str(item["method"])))):
        history = str(row["history_id"])
        method = str(row["method"])
        makespan = float(row["build_makespan_s"])
        episodes = int(row["episode_count"])
        q = quality.get((method, history), {})
        performance.append({
            "policy": method, "history": history, "episodes": episodes,
            "makespan": makespan, "throughput": episodes / makespan,
            "source_tokens_per_s": float(row["source_tokens_per_s"]),
            "max_inflight": row.get("whole_update_active_max"),
            "publication_inversion": row.get("inversion_count"),
            "inversion_density": row.get("inversion_density"),
            "kendall_tau": row.get("kendall_tau"),
            "max_displacement": row.get("max_displacement"),
        })
        work.append({
            "policy": method, "history": history,
            "logical_llm_calls": row.get("llm_logical_calls"),
            "transport_attempts": row.get("llm_transport_attempts"),
            "retry_count": row.get("retry_count", max(0, int(row.get("llm_transport_attempts", 0)) - int(row.get("llm_logical_calls", 0)))),
            "input_tokens": row.get("llm_input_tokens"),
            "embedding_calls": row.get("embedding_calls"),
            "embedding_items": row.get("embedding_items"),
            "db_writes": row.get("db_writes"),
        })
        correctness.append({
            "policy": method, "history": history,
            "published_episodes": row.get("published_episodes"),
            "expected_episodes": episodes,
            "publication_coverage": row.get("published_episodes") == episodes,
            "semantic_violations": row.get("direct_semantic_violations"),
            "instrumentation_errors": row.get("instrumentation_error_spans"),
            "ordering": {key: row.get(key) for key in ("inversion_count", "inversion_density", "kendall_tau", "max_displacement")},
        })
        main.append({
            "policy": method,
            "history": history,
            "makespan": makespan,
            "throughput": episodes / makespan,
            "llm_calls": row.get("llm_logical_calls"),
            "tokens": row.get("llm_input_tokens"),
            "quality": q.get("qa_score"),
        })
    return {
        "main_table": main,
        "performance_table": performance,
        "work_attribution": work,
        "correctness_table": correctness,
        "quality_table": [
            {"policy": method, "history": history, **quality.get((method, history), {})}
            for history in FORMAL_HISTORIES for method in FORMAL_METHODS
        ],
    }


def _attempt_metrics(attempt_root: Path, result: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(result)
    trace_path = attempt_root / "native_trace.jsonl"
    spans: list[Mapping[str, Any]] = []
    if trace_path.is_file():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            envelope = json.loads(line)
            spans.extend(span for span in envelope.get("spans", []) if isinstance(span, Mapping))
    raw_events = [json.loads(line) for line in (attempt_root / "raw_events.jsonl").read_text(encoding="utf-8").splitlines()]
    published = [event for event in raw_events if event.get("event") == "PUBLICATION_DURABLE"]
    metrics.update({
        "episodes_per_s": int(metrics["episode_count"]) / float(metrics["build_makespan_s"]),
        "embedding_calls": sum(span.get("phase") == "embedding" for span in spans),
        "retry_count": max(0, int(metrics.get("llm_transport_attempts", 0)) - int(metrics.get("llm_logical_calls", 0))),
        "published_episodes": len(published),
        "formal_lifecycle_path": str(attempt_root / "lifecycle.json"),
    })
    return metrics


def summarize_history_qa(
    history_id: str, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Materialize the checkpoint decision without changing QA semantics."""

    selected = tuple(rows)
    expected_rows = 2 * 4
    invalid_rows = sum(bool(row.get("invalid")) for row in selected)
    correct_rows = sum(
        bool(row.get("correct")) and not bool(row.get("invalid"))
        for row in selected
    )
    construction_calls = sum(int(row.get("construction_calls", 0)) for row in selected)
    graph_write_attempts = sum(int(row.get("graph_write_attempts", 0)) for row in selected)
    contract_pass = (
        len(selected) == expected_rows
        and invalid_rows == 0
        and construction_calls == 0
        and graph_write_attempts == 0
    )
    return {
        "schema_version": "sfwb.v1.3.history-qa-decision.v1",
        "history_id": history_id,
        "expected_row_count": expected_rows,
        "qa_row_count": len(selected),
        "invalid_row_count": invalid_rows,
        "correct_row_count": correct_rows,
        "accuracy": correct_rows / len(selected) if selected else 0.0,
        "construction_calls": construction_calls,
        "graph_write_attempts": graph_write_attempts,
        "contract_status": "PASS" if contract_pass else "FAIL",
    }


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def run_formal_baseline_async(run_root: Path) -> dict[str, Any]:
    """Run all eight blocks with a read-only QA checkpoint per history."""
    root = Path(run_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("FORMAL_ROOT_MUST_BE_NEW")
    root.mkdir(parents=True, exist_ok=False)
    repository_root = root.parents[2]
    from saturated_fixed_work_baseline_v1_2.dataset import EXPECTED_EPISODE_COUNTS, load_and_validate_qa_inventory, load_episode_inputs
    from saturated_fixed_work_baseline_v1_2.live import FormalBlock
    from saturated_fixed_work_baseline_v1_2.live_block import execute_live_block
    from saturated_fixed_work_baseline_v1_3.live_dependencies import build_v13_live_dependencies, build_v13_neo4j_idle_probe
    from saturated_fixed_work_baseline_v1_3.simple_campaign import _SimpleAttemptStore, build_execution_identity, _hash_json, _tokenizer_source_counter
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env, _repository_root
    from saturated_fixed_work_baseline_v1_2.reuse import import_validation_module, import_paper_eval_module
    from saturated_fixed_work_baseline_v1_2.services import direct_get_text, probe_model_catalog
    from saturated_fixed_work_baseline_v1_2.telemetry import parse_vllm_026_metrics
    from saturated_fixed_work_baseline_v1_2.production_qa import build_production_qa_dependencies, execute_production_qa
    from saturated_fixed_work_baseline_v1_2.qa_lane import NamespaceSeal

    repository_root = _repository_root(root)
    env = _load_env(repository_root)
    driver_module = import_validation_module(repository_root, "graphiti_core.driver.neo4j_driver")
    driver = driver_module.Neo4jDriver(env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"])
    neo4j_probe = build_v13_neo4j_idle_probe(driver)

    async def service_idle() -> bool:
        for port in (8000, 8001):
            response = await asyncio.to_thread(direct_get_text, f"http://10.87.5.247:{port}/metrics", timeout_s=10.0)
            parsed = parse_vllm_026_metrics(str(response["text"]), timestamp_ns=time.monotonic_ns(), repository_root=repository_root)
            if parsed.value is None:
                return False
            values = parsed.value.values
            if float(values["running_requests"]) != 0.0 or float(values["waiting_requests"]) != 0.0:
                return False
        return (await neo4j_probe()).get("idle") is True

    construction = await asyncio.to_thread(probe_model_catalog, "http://10.87.5.247:8000/v1/models", expected_model="qwen3-32b-fp8", expected_max_model_len=65536)
    embedding = await asyncio.to_thread(probe_model_catalog, "http://10.87.5.247:8001/v1/models", expected_model="qwen3-embedding-0.6b", expected_max_model_len=32768)
    if construction.get("status") != "PASS" or embedding.get("status") != "PASS":
        raise ValueError("MODEL_ENDPOINT_UNAVAILABLE")
    canary = await driver.execute_query("RETURN 1 AS ok", routing_="r")
    records = getattr(canary, "records", canary[0] if isinstance(canary, tuple) else canary)
    if not records or dict(records[0]).get("ok") != 1:
        raise ValueError("NEO4J_CANARY_FAILED")
    inventory = load_and_validate_qa_inventory(repository_root)
    for history, expected in EXPECTED_EPISODES.items():
        if len(load_episode_inputs(repository_root, history, f"{root.name}-preflight")) != expected:
            raise ValueError("WORKLOAD_UNAVAILABLE")
    if not await service_idle():
        raise ValueError("BACKEND_NOT_IDLE")
    _write_new_json(root / "preflight.json", {"status": "PASS", "construction": construction, "embedding": embedding, "neo4j_canary": True, "workload": inventory.get("inventory_sha256")})
    dependencies = build_v13_live_dependencies(repository_root=repository_root, service_idle=service_idle)
    telemetry = import_paper_eval_module(repository_root, "paper_eval.apc_vllm_telemetry")
    run_id = root.name
    matrix = build_formal_matrix(run_id)
    block_rows: list[dict[str, Any]] = []
    qa_rows: list[dict[str, Any]] = []
    qa_history_decisions: list[dict[str, Any]] = []
    try:
        for history_id, history_plans in group_formal_matrix_by_history(matrix):
            history_block_rows: list[dict[str, Any]] = []
            for plan in history_plans:
                method_enum = __import__("saturated_fixed_work_baseline_v1_2.schedules", fromlist=["Method"]).Method(plan.method)
                block = FormalBlock(plan.ordinal, plan.block_id, run_id, plan.history_id, method_enum, 1, plan.namespace, plan.cache_salt)
                attempt_root = root / "blocks" / plan.block_id / "attempt-001"
                attempt_root.parent.mkdir(parents=True, exist_ok=True)
                prep = await asyncio.gather(
                    asyncio.to_thread(telemetry.probe_vllm_cache_salt, "http://10.87.5.247:8000/v1", env.get("CONSTRUCTION_LLM_API_KEY"), plan.cache_salt),
                    asyncio.to_thread(telemetry.probe_vllm_embedding_cache_salt, "http://10.87.5.247:8001/v1", env.get("EMBEDDING_API_KEY"), plan.cache_salt),
                )
                if prep[0].get("status") != "CACHE_SALT_ACCEPTED" or prep[1].get("status") != "EMBEDDING_CACHE_SALT_ACCEPTED" or not await service_idle():
                    raise ValueError("BLOCK_PREPARATION_FAILED")
                episodes = tuple(load_episode_inputs(repository_root, plan.history_id, plan.namespace))
                workload_hash = _hash_json([{"source_sequence": row.source_sequence, "source_hash": row.source_hash} for row in episodes])
                identity = build_execution_identity(run_id=run_id, repository_root=repository_root, workload_sha256=workload_hash, namespace=plan.namespace)
                result = await execute_live_block(
                    repository_root=repository_root,
                    run_root=root,
                    block=block,
                    identity=identity,
                    episodes=episodes,
                    dependencies=dependencies,
                    source_tokens=EXPECTED_SOURCE_TOKENS[plan.history_id],
                    attempt_store_factory=_SimpleAttemptStore.create,
                )
                lifecycle = build_lifecycle_evidence(formal_start_ns=int(result["t0_ns"]), durable_complete_ns=int(result["t_durable_complete_ns"]), validation_complete_ns=int(result["t_validated_seal_ns"]), namespace=plan.namespace)
                _write_new_json(attempt_root / "lifecycle.json", lifecycle)
                metrics = _attempt_metrics(attempt_root, result)
                metrics["lifecycle"] = lifecycle
                _write_new_json(attempt_root / "formal_block_result.json", metrics)
                block_rows.append(metrics)
                history_block_rows.append(metrics)

            history_seals = tuple(
                NamespaceSeal(
                    method=row["method"],
                    history_id=row["history_id"],
                    namespace=row["namespace"],
                    canonical_hash=row["canonical_graph_hash"],
                    construction_call_ordinal=index,
                )
                for index, row in enumerate(history_block_rows, start=1)
            )
            history_questions = tuple(
                question
                for question in inventory["questions"]
                if question.get("history_id") == history_id
            )
            history_qa_dependencies = build_production_qa_dependencies(
                repository_root=repository_root
            )
            history_qa_rows = await execute_production_qa(
                seals=history_seals,
                questions=history_questions,
                expected_histories=(history_id,),
                construction_calls=2,
                output_path=root / "qa" / history_id / "qa_rows.jsonl",
                dependencies=history_qa_dependencies,
            )
            qa_rows.extend(history_qa_rows)
            decision = summarize_history_qa(history_id, history_qa_rows)
            _write_new_json(root / "qa" / history_id / "qa_decision.json", decision)
            qa_history_decisions.append(decision)
    finally:
        await _await(driver.close())

    _write_new_jsonl(root / "qa" / "qa_rows.jsonl", qa_rows)
    reduced = reduce_baseline_outputs(block_rows, qa_rows)
    for name, value in (("performance_table.json", reduced["performance_table"]), ("work_attribution.json", reduced["work_attribution"]), ("correctness_table.json", reduced["correctness_table"]), ("quality_table.json", reduced["quality_table"])):
        _write_new_json(root / "qualification" / name, {"rows": value})
    qa_contract_pass = (
        len(qa_history_decisions) == len(FORMAL_HISTORIES)
        and all(
            decision.get("contract_status") == "PASS"
            for decision in qa_history_decisions
        )
    )
    qualification_status = "PASS" if qa_contract_pass else "FAIL_QA_CONTRACT"
    _write_new_json(root / "qualification/baseline_results.json", {"schema_version": "sfwb.v1.3.formal-baseline.v1", "status": qualification_status, "run_id": run_id, "formal_blocks": 8, "histories": list(FORMAL_HISTORIES), "methods": list(FORMAL_METHODS), "blocks": block_rows, "main_table": reduced["main_table"], "qa_rows": len(qa_rows), "qa_history_decisions": qa_history_decisions})
    md_lines = ["# MemBind v1.3 Formal Baseline", "", "|Policy|History|Makespan|Throughput|LLM Calls|Tokens|Quality|", "|---|---|---:|---:|---:|---:|---:|"]
    for row in reduced["main_table"]:
        quality_text = "n/a" if row["quality"] is None else f"{row['quality']:.6f}"
        md_lines.append(f"|{row['policy']}|{row['history']}|{row['makespan']:.3f}|{row['throughput']:.6f}|{row['llm_calls']}|{row['tokens']}|{quality_text}|")
    (root / "qualification/baseline_results.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "qualification/baseline_results.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    _write_new_json(root / "formal_run_seal.json", {"schema_version": "sfwb.v1.3.formal-run-seal.v1", "status": "FORMAL_RUN_SEALED", "formal_blocks": 8, "selected_blocks": [{"ordinal": row["attempt_ordinal"], "block_id": row["block_id"], "method": row["method"], "history_id": row["history_id"], "namespace": row["namespace"], "attempt_root": row["attempt_root"]} for row in block_rows]})
    return {"status": qualification_status, "run_root": str(root), "formal_blocks": 8, "qa_rows": len(qa_rows), "qa_history_decisions": qa_history_decisions, "main_table": reduced["main_table"]}


def run_formal_baseline(run_root: Path) -> dict[str, Any]:
    return asyncio.run(run_formal_baseline_async(run_root))


__all__ = [
    "FORMAL_HISTORIES",
    "FORMAL_METHODS",
    "FormalMatrixRow",
    "build_formal_matrix",
    "group_formal_matrix_by_history",
    "validate_formal_matrix",
    "build_lifecycle_evidence",
    "summarize_history_qa",
    "reduce_baseline_outputs",
    "run_formal_baseline",
    "run_formal_baseline_async",
]
