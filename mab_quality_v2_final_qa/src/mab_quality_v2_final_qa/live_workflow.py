"""Small-first live execution for the isolated MAB quality lane.

The module imports existing MemBind/Quality-v1 implementations only at the
live boundary.  It owns fresh namespaces and writes exclusively below the
caller-provided MAB artifact root.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from .artifacts import ArtifactStore, atomic_write_json
from .compatibility import build_context_pack, quality_v1_identity
from .contracts import MABContext, canonical_sha256
from .dataset_adapter import MABDatasetAdapter
from .live_adapters import render_public_episodes
from .qualification import qualify_declared_inventory
from .reducer import reduce_method_rows, reduce_paired_rows
from .report import render_final_report
from .runner import MABQualityRunner
from .runtime_gate import RuntimeTopology, check_model_endpoint


ROOT = Path(__file__).resolve().parents[3]
LEGACY = ROOT / "membind-validation"
PROJECT = ROOT / "paper-eval-v3"


def _import_project_surfaces() -> dict[str, Any]:
    for path in (PROJECT / "src", LEGACY / "src"):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
    from graphiti_native import load_env_file  # type: ignore
    from paper_eval.membind_v1.admission import RequestAdmission
    from paper_eval.membind_v1.aligned_live import production_aligned_live_hooks
    from paper_eval.membind_v1.graphiti_factories import build_source_log_from_episodes
    from paper_eval.membind_v1.live_runtime import build_membind_v1_runtime
    from paper_eval.membind_v31.coordinator import run_membind_v31_stream
    from paper_eval.membind_v31.admission import AdmissionPolicy
    from paper_eval.membind_v31.live_block import production_v31_live_hooks
    from paper_eval.membind_v31.live_runtime import build_membind_v31_runtime
    from paper_eval.membind_v31.freezer import (
        V31FreezePaths,
        load_v31_state_cut_certification,
    )
    from paper_eval.s2_adapters import build_qualified_qwen_judge
    from paper_eval.quality_evaluation_v1_reader import QualityEvaluationV1Reader

    return locals()


def _namespace_state(graph: Any, namespace: str) -> dict[str, Any]:
    async def query() -> dict[str, Any]:
        result = await graph.driver.execute_query(
            """
            CALL { MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count }
            CALL { MATCH ()-[r]->() WHERE r.group_id = $group_id RETURN count(r) AS relationship_count }
            CALL { MATCH (n:Episodic) WHERE n.group_id = $group_id RETURN collect(n.name) AS episode_names }
            RETURN node_count, relationship_count, episode_names
            """,
            params={"group_id": namespace},
        )
        rows = getattr(result, "records", None)
        if not isinstance(rows, Sequence) or len(rows) != 1:
            raise RuntimeError("NAMESPACE_PROBE_INVALID")
        row = rows[0]
        return {
            "node_count": int(row.get("node_count") or 0),
            "relationship_count": int(row.get("relationship_count") or 0),
            "episode_names": sorted(str(value) for value in row.get("episode_names") or []),
        }

    return asyncio.run(query())


async def _namespace_state_async(graph: Any, namespace: str) -> dict[str, Any]:
    result = await graph.driver.execute_query(
        """
        CALL { MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count }
        CALL { MATCH ()-[r]->() WHERE r.group_id = $group_id RETURN count(r) AS relationship_count }
        CALL { MATCH (n:Episodic) WHERE n.group_id = $group_id RETURN collect(n.name) AS episode_names }
        RETURN node_count, relationship_count, episode_names
        """,
        params={"group_id": namespace},
    )
    rows = getattr(result, "records", None)
    if not isinstance(rows, Sequence) or len(rows) != 1:
        raise RuntimeError("NAMESPACE_PROBE_INVALID")
    row = rows[0]
    return {
        "node_count": int(row.get("node_count") or 0),
        "relationship_count": int(row.get("relationship_count") or 0),
        "episode_names": sorted(str(value) for value in row.get("episode_names") or []),
    }


async def _episode_provenance(
    graph: Any, namespace: str, context_id: str
) -> dict[str, str]:
    result = await graph.driver.execute_query(
        """
        MATCH (episode:Episodic)
        WHERE episode.group_id = $group_id
        RETURN episode.uuid AS uuid, episode.name AS name
        """,
        params={"group_id": namespace},
    )
    rows = getattr(result, "records", None)
    if not isinstance(rows, Sequence):
        raise RuntimeError("EPISODE_PROVENANCE_INVALID")
    mapping: dict[str, str] = {}
    for row in rows:
        uuid = str(row.get("uuid") or "")
        name = str(row.get("name") or "")
        marker = "::episode::"
        prefix = f"{context_id}{marker}"
        if not uuid or not name.startswith(prefix):
            raise RuntimeError("EPISODE_PROVENANCE_INVALID")
        try:
            sequence = int(name[len(prefix) :])
        except ValueError:
            raise RuntimeError("EPISODE_PROVENANCE_INVALID") from None
        session_id = f"{context_id}:s{sequence:04d}"
        if uuid in mapping or session_id in mapping.values():
            raise RuntimeError("EPISODE_PROVENANCE_INVALID")
        mapping[uuid] = session_id
    return mapping


async def _close(runtime: Any) -> None:
    graph = getattr(runtime, "graphiti", None)
    close = getattr(graph, "close", None)
    if callable(close):
        value = close()
        if hasattr(value, "__await__"):
            await value


def _load_contexts(
    dataset_path: Path,
    *,
    revision: str,
    included_record_indices: Sequence[int],
) -> tuple[MABContext, ...]:
    parsed = json.loads(dataset_path.read_text(encoding="utf-8"))
    records = parsed.get("data", []) if isinstance(parsed, Mapping) else parsed
    if isinstance(records, Mapping):
        records = [records]
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("DATASET_RECORDS_INVALID")
    try:
        selected = [records[int(index)] for index in included_record_indices]
    except (IndexError, TypeError, ValueError):
        raise ValueError("DATASET_INVENTORY_INPUT_INVALID") from None
    return MABDatasetAdapter.from_records(
        selected, source="longmemeval_s*", dataset_revision=revision
    ).contexts


class _AbstentionAwareEvaluator:
    """Set the benchmark-native abstention bit without changing the legacy adapter."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    async def evaluate(self, item: Any) -> Any:
        question_id = str(getattr(item, "question_id", ""))
        if question_id.endswith("_abs") and getattr(item, "abstention", False) is not True:
            item = replace(item, abstention=True)
        return await self._delegate.evaluate(item)


def _enable_abstention_route(judge: Any) -> Any:
    evaluator = getattr(judge, "_evaluator", None)
    if evaluator is None or not callable(getattr(evaluator, "evaluate", None)):
        raise RuntimeError("JUDGE_EVALUATOR_UNAVAILABLE")
    # The legacy S2 wrapper intentionally defaults to non-abstention.  This
    # local proxy keeps that module untouched while honoring MAB *_abs routes.
    judge._evaluator = _AbstentionAwareEvaluator(evaluator)
    return judge


def _build_quality_callbacks(
    *,
    topology: RuntimeTopology,
    env: Mapping[str, str],
) -> tuple[Any, Any, Any, dict[str, str]]:
    surfaces = _import_project_surfaces()
    transport = __import__(
        "mab_quality_v2_final_qa.live_adapters", fromlist=["LiveReaderTransport"]
    ).LiveReaderTransport(
        model=topology.construction.model,
        base_url=topology.construction.base_url,
        api_key=str(env["CONSTRUCTION_LLM_API_KEY"]),
    )
    reader = surfaces["QualityEvaluationV1Reader"](
        model=topology.construction.model, transport=transport
    )
    judge = surfaces["build_qualified_qwen_judge"](
        base_url=topology.construction.base_url,
        api_key=str(env["CONSTRUCTION_LLM_API_KEY"]),
    )
    judge = _enable_abstention_route(judge)

    async def retrieve(**kwargs: Any) -> Mapping[str, Any]:
        from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1

        bundle = await retrieve_quality_v1(
            graph=kwargs["graph"],
            query=kwargs["query"],
            namespace=kwargs["namespace"],
            episode_uuid_to_session_id=kwargs["episode_uuid_to_session_id"],
        )
        return {"facts": bundle.facts, "episodes": bundle.episodes}

    async def reader_callback(**kwargs: Any) -> str:
        result = await reader.answer(
            context_json=kwargs["context_json"],
            question_date=kwargs["question_date"],
            question=kwargs["question"],
        )
        if getattr(result, "finish_reason", None) != "stop":
            raise ValueError("READER_INVALID_FINISH")
        return str(result.answer)

    async def judge_callback(**kwargs: Any) -> Mapping[str, Any]:
        labels = kwargs["labels"]
        result = await judge.evaluate(
            hypothesis=kwargs["answer"],
            inputs=SimpleNamespace(
                run_id="mab-quality-v2",
                # LongMemEval's official abstention route is keyed by the
                # benchmark question id suffix, not the private QA pair id.
                history_id=kwargs["public_qa"]["question_id"],
                question_type=labels.question_type,
                question=kwargs["public_qa"]["question"],
                reference_answer=labels.reference_answers[0],
            ),
        )
        status = str(result.get("status"))
        if status != "SUCCESS" or type(result.get("label")) is not bool:
            return {"valid": False, "failure_class": "JUDGE_INVALID"}
        return {"valid": True, "correct": bool(result["label"])}

    def pack(**kwargs: Any) -> Any:
        return build_context_pack(
            context=kwargs["context"],
            question=kwargs["question"],
            facts=kwargs["facts"],
            episodes=kwargs["episodes"],
        )

    identity = {
        "reader_config_sha256": reader.config_sha256,
        "judge_config_sha256": judge.config_sha256,
        "quality_v1_identity_sha256": canonical_sha256(quality_v1_identity()),
    }
    return retrieve, reader_callback, judge_callback, {**identity, "context_pack": pack}


async def _construct_u0(
    *, runtime: Any, context: MABContext, namespace: str, hooks: Any
) -> Mapping[str, Any]:
    public = context.public_context().as_dict()
    episodes = render_public_episodes(public, namespace=namespace)
    source_log, _ = hooks["build_source_log_from_episodes"](
        episodes, namespace=namespace, reference_time_to_ns=hooks["aligned"].reference_time_to_ns
    )
    for episode, source in zip(episodes, source_log.records, strict=True):
        await hooks["aligned"].native_add_episode(runtime, episode, source)
    provenance = await _episode_provenance(runtime.graphiti, namespace, context.context_id)
    return {
        "namespace_sealed": True,
        "episode_uuid_to_session_id": provenance,
        "construction_manifest_sha256": canonical_sha256(
            {"context_sha256": context.context_sha256, "method": "U0", "source_count": len(episodes)}
        ),
    }


async def _construct_membind(
    *, runtime: Any, context: MABContext, namespace: str, hooks: Any, root: Path
) -> Mapping[str, Any]:
    public = context.public_context().as_dict()
    episodes = render_public_episodes(public, namespace=namespace)
    source_log, _ = hooks["build_source_log_from_episodes"](
        episodes, namespace=namespace, reference_time_to_ns=hooks["aligned"].reference_time_to_ns
    )
    certification = hooks["load_v31_state_cut_certification"](
        hooks["V31FreezePaths"].from_repository(ROOT)
    )
    adapter = hooks["v31"].adapter_factory(runtime, certification)
    event_path = root / "construction.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)

    def observer(row: dict[str, object]) -> None:
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event_type": row.get("event_type"), "source_sequence": row.get("source_sequence")}) + "\n")

    async def persist_prepared(artifact: Any) -> None:
        target = root / "prepared" / f"{int(artifact.source_sequence):08d}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(target, artifact.to_document())

    async def publication_probe(sequence: int, _value: object) -> bool:
        return bool(
            await hooks["aligned"].source_visibility_probe(
                runtime, source_log.record(sequence)
            )
        )

    result = await hooks["run_membind_v31_stream"](
        stream_id=context.context_id,
        source_log=source_log,
        arrival_offsets_ns=(0,) * source_log.source_count,
        adapter=adapter,
        request_client=runtime.admitted_llm,
        compile_workers=2,
        lookahead=2,
        observer=observer,
        publication_probe=publication_probe,
        prepared_persistor=persist_prepared,
    )
    if result.get("publication_source_sequences") != list(range(source_log.source_count)):
        raise RuntimeError("CONSTRUCTION_FAILED")
    provenance = await _episode_provenance(runtime.graphiti, namespace, context.context_id)
    return {
        "namespace_sealed": True,
        "episode_uuid_to_session_id": provenance,
        "construction_manifest_sha256": canonical_sha256(
            {"context_sha256": context.context_sha256, "method": "MEMBIND_V31", "result": result}
        ),
    }


async def run_quality_workflow(
    *,
    dataset_path: Path,
    artifact_root: Path,
    run_id: str,
    revision: str,
    history_limit: int,
    mode: str,
) -> dict[str, Any]:
    if history_limit not in {1, 4}:
        raise ValueError("HISTORY_LIMIT_INVALID")
    if mode not in {"smoke", "full"}:
        raise ValueError("MODE_INVALID")
    surfaces = _import_project_surfaces()
    env = surfaces["load_env_file"](LEGACY / ".env")
    topology = RuntimeTopology.from_env(env)
    construction_probe = check_model_endpoint(
        topology.construction.base_url, expected_model=topology.construction.model
    )
    embedding_probe = check_model_endpoint(
        topology.embedding.base_url, expected_model=topology.embedding.model
    )
    if not construction_probe.available or not embedding_probe.available:
        raise RuntimeError("LIVE_FROZEN_ENDPOINT_GATE_FAILED")
    contexts = _load_contexts(
        dataset_path,
        revision=revision,
        included_record_indices=(0, 1, 2, 3),
    )[:history_limit]
    store = ArtifactStore(artifact_root)
    # Public inventory is frozen before any runtime or QA call.
    inventory = qualify_declared_inventory(
        json.loads(dataset_path.read_text(encoding="utf-8")),
        source="longmemeval_s*",
        dataset_revision=revision,
        included_record_indices=tuple(range(4)),
        excluded_failures={4: "question 38 gold session is absent from common context"},
    )
    store.write_json("dataset_inventory.json", inventory)
    retrieve, reader, judge, quality_ids = _build_quality_callbacks(topology=topology, env=env)
    hooks = {
        "aligned": surfaces["production_aligned_live_hooks"](),
        "v31": surfaces["production_v31_live_hooks"](),
        "build_source_log_from_episodes": surfaces["build_source_log_from_episodes"],
        "run_membind_v31_stream": surfaces["run_membind_v31_stream"],
        "load_v31_state_cut_certification": surfaces["load_v31_state_cut_certification"],
        "V31FreezePaths": surfaces["V31FreezePaths"],
    }
    all_rows: dict[str, list[dict[str, Any]]] = {"U0": [], "MEMBIND_V31": []}
    selected = contexts[:1] if mode == "smoke" else contexts
    for method in ("U0", "MEMBIND_V31"):
        for context in selected:
            if method == "U0":
                runtime = surfaces["build_membind_v1_runtime"](
                    # U0 is the unsalted construction baseline.
                    env=dict(env),
                    admission=surfaces["RequestAdmission"](limit=2),
                    request_id_prefix=f"mab-{run_id}-{method}",
                )
            else:
                runtime = surfaces["build_membind_v31_runtime"](
                    env={
                        **dict(env),
                        "CONSTRUCTION_CACHE_SALT": canonical_sha256(
                            [run_id, method, context.context_id]
                        ),
                    },
                    policy=surfaces["AdmissionPolicy"].CACHE_AFFINE,
                    request_id_prefix=f"mab-{run_id}-{method}",
                )
            await hooks["aligned"].runtime_ready(runtime)
            method_hash = hashlib.sha256(method.encode()).hexdigest()
            if method == "U0":
                construct = lambda **kwargs: _construct_u0(
                    runtime=runtime,
                    context=context,
                    namespace=kwargs["namespace"],
                    hooks=hooks,
                )
            else:
                construct = lambda **kwargs: _construct_membind(
                    runtime=runtime,
                    context=context,
                    namespace=kwargs["namespace"],
                    hooks=hooks,
                    root=store.root / "construction" / method / context.context_id,
                )

            runner: MABQualityRunner

            async def namespace_validator(receipt: Any) -> bool:
                expected = render_public_episodes(
                    context.public_context().as_dict(), namespace=receipt.namespace
                )
                state = await _namespace_state_async(runtime.graphiti, receipt.namespace)
                expected_names = sorted(episode.name for episode in expected)
                expected_sessions = {episode.session_id for episode in expected}
                return (
                    receipt.namespace == runner._namespace(context)
                    and receipt.context_id == context.context_id
                    and state["episode_names"] == expected_names
                    and set(receipt.episode_uuid_to_session_id.values())
                    == expected_sessions
                    and len(receipt.episode_uuid_to_session_id) == len(expected)
                )

            runner = MABQualityRunner(
                store=store,
                method=SimpleNamespace(method_id=method, implementation_sha256=method_hash),
                run_id=run_id,
                dataset_manifest_sha256=inventory["payload_sha256"],
                graph=runtime.graphiti,
                construct=construct,
                retrieve=retrieve,
                reader=reader,
                judge=judge,
                context_pack=quality_ids["context_pack"],
                reader_config_sha256=quality_ids["reader_config_sha256"],
                judge_config_sha256=quality_ids["judge_config_sha256"],
                namespace_validator=namespace_validator,
            )
            expected_namespace = runner._namespace(context)
            receipt_path = store.path(runner._receipt_relative(context))
            state = await _namespace_state_async(runtime.graphiti, expected_namespace)
            if not receipt_path.exists() and state["episode_names"]:
                raise RuntimeError("MAB_NAMESPACE_NOT_FRESH")
            try:
                rows = await runner.run_context(context)
                all_rows[method].extend(rows)
            finally:
                await _close(runtime)
    store.write_json("qa/U0/rows.json", all_rows["U0"])
    store.write_json("qa/MEMBIND_V31/rows.json", all_rows["MEMBIND_V31"])
    result: dict[str, Any] = {
        "schema_version": "mab-quality-v2-final-qa.live-workflow-result.v1",
        "run_id": run_id,
        "mode": mode,
        "history_limit": history_limit,
        "live_executed": True,
        "inventory_payload_sha256": inventory["payload_sha256"],
        "construction_endpoint": construction_probe.__dict__,
        "embedding_endpoint": embedding_probe.__dict__,
        "row_counts": {key: len(value) for key, value in all_rows.items()},
    }
    if mode == "full":
        paired = reduce_paired_rows(all_rows["U0"], all_rows["MEMBIND_V31"], bootstrap_samples=2000)
        result["paired"] = paired
        result["report"] = render_final_report(
            paired,
            run_id=run_id,
            dataset_manifest_sha256=inventory["payload_sha256"],
            freeze_sha256=canonical_sha256(quality_ids),
            live_executed=True,
        )
        (store.root / "FINAL_MAB_QUALITY_V2_REPORT.md").write_text(result["report"], encoding="utf-8")
    store.write_json("RESULT.json", result)
    return result


__all__ = ["run_quality_workflow"]
