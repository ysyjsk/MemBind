#!/usr/bin/env python3
"""Execute the frozen MAB v1.3 first-pass campaign in context order.

The command is intentionally resumable at the campaign ledger level.  Each
attempt keeps its own namespace and artifact directory; a failed attempt is
never cleared or reused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
VALIDATION = ROOT / "membind-validation"
PAPER = ROOT / "paper-eval-v3"
MAB = ROOT / "mab_quality_v2_final_qa"
for source in (SFWB / "src", VALIDATION / "src", PAPER / "src", MAB / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.live_workflow import _build_quality_callbacks  # noqa: E402
from mab_quality_v2_final_qa.mab_main_dataset import (  # noqa: E402
    build_authority,
    build_qa_manifest,
    build_workload_manifest,
)
from mab_quality_v2_final_qa.runtime_gate import RuntimeTopology  # noqa: E402
from mab_quality_v2_final_qa.workload_contract import WorkloadManifest  # noqa: E402
from saturated_fixed_work_baseline_v1_3.mab_live_runner import (  # noqa: E402
    resolve_runtime_builder,
    run_mab_construction_async,
)
from saturated_fixed_work_baseline_v1_3.qa_lane import (  # noqa: E402
    run_mab_qa_on_sealed_namespace_async,
)
from saturated_fixed_work_baseline_v1_3.artifact_seals import verify_seal  # noqa: E402


METHODS = ("B0", "B1", "V6")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


async def _namespace_state(graph: Any, namespace: str) -> dict[str, Any]:
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
    if not rows:
        raise RuntimeError("NAMESPACE_STATE_INVALID")
    row = rows[0]
    return {
        "node_count": int(row.get("node_count") or 0),
        "relationship_count": int(row.get("relationship_count") or 0),
        "episode_names": sorted(str(value) for value in row.get("episode_names") or []),
    }


async def _episode_mapping(graph: Any, namespace: str, episodes: tuple[Any, ...]) -> dict[str, str]:
    result = await graph.driver.execute_query(
        "MATCH (episode:Episodic) WHERE episode.group_id = $group_id RETURN episode.uuid AS uuid, episode.name AS name",
        params={"group_id": namespace},
    )
    by_sequence = {episode.source_sequence: episode.session_id for episode in episodes}
    mapping: dict[str, str] = {}
    for row in getattr(result, "records", ()):
        name = str(row.get("name") or "")
        try:
            sequence = int(name.rsplit("::", 1)[-1])
        except ValueError:
            raise RuntimeError("EPISODE_PROVENANCE_INVALID") from None
        if sequence not in by_sequence:
            raise RuntimeError("EPISODE_PROVENANCE_INVALID")
        mapping[str(row.get("uuid"))] = by_sequence[sequence]
    if len(mapping) != len(episodes):
        raise RuntimeError("EPISODE_PROVENANCE_INCOMPLETE")
    return mapping


async def _run_qa(
    *,
    block_root: Path,
    construction_seal: Mapping[str, Any],
    context: Any,
    qa_scope: str,
    runtime_builder: Any,
    qa_runtime: Any | None = None,
    qa_output_root: Path | None = None,
) -> dict[str, Any]:
    """Run smoke/full QA against one sealed namespace without construction writes."""

    from mab_quality_v2_final_qa.runner import ReadOnlyNamespace

    env: dict[str, str] = {}
    from graphiti_native import load_env_file

    env.update(load_env_file(VALIDATION / ".env"))
    env.update({key: value for key, value in os.environ.items() if key in {
        "CONSTRUCTION_LLM_API_KEY", "VLLM_API_KEY", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
        "MAB_RUNTIME_PROVIDER", "CONSTRUCTION_LLM_BASE_URL", "CONSTRUCTION_LLM_MODEL",
        "EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_DIM",
        "QUALITY_LLM_BASE_URL", "QUALITY_LLM_MODEL", "QUALITY_LLM_API_KEY",
    }})
    key = env.get("CONSTRUCTION_LLM_API_KEY") or env.get("VLLM_API_KEY")
    if not key:
        raise RuntimeError("QA_API_KEY_MISSING")
    env["QUALITY_LLM_API_KEY"] = key
    env.setdefault("MAB_RUNTIME_PROVIDER", "FROZEN_V31")
    topology = RuntimeTopology.from_env(env)
    retrieve, reader, judge, quality_ids = _build_quality_callbacks(topology=topology, env=env)
    runtime = qa_runtime if qa_runtime is not None else await resolve_runtime_builder(runtime_builder)
    graph = runtime.graphiti
    try:
        episodes = tuple(SimpleNamespace(source_sequence=session.source_sequence, session_id=session.session_id) for session in context.sessions)
        mapping = await _episode_mapping(graph, str(construction_seal["namespace"]), episodes)
        readonly = ReadOnlyNamespace(graph, str(construction_seal["namespace"]))
        qa_by_pair = {qa.qa_pair_id: qa for qa in context.qa_items}
        qa_manifest = build_qa_manifest(context, scope=qa_scope)

        async def answer(row: Mapping[str, Any]) -> Mapping[str, Any]:
            qa = qa_by_pair[str(row["qa_pair_id"])]
            public_qa = {"question_id": qa.question_id, "question": qa.question, "question_date": qa.question_date}
            bundle = await retrieve(
                graph=readonly,
                query=qa.question,
                public_qa=public_qa,
                namespace=str(construction_seal["namespace"]),
                episode_uuid_to_session_id=mapping,
            )
            facts = tuple(bundle.get("facts", ()))
            retrieved = tuple(bundle.get("episodes", ()))
            metrics = dict(quality_ids["context_pack"] and __import__("mab_quality_v2_final_qa.compatibility", fromlist=["session_ranking_metrics"]).session_ranking_metrics([item.session_id for item in retrieved], qa.gold_session_ids))
            if qa.gold_mapping_status == "PARTIAL_GOLD_MAPPING":
                for metric in ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10"):
                    metrics[metric] = None
            pack = quality_ids["context_pack"](context=context, question=qa.question, facts=facts, episodes=retrieved)
            answer_text = await reader(context_json=pack.context_json, question=qa.question, question_date=qa.question_date, public_qa=public_qa)
            judged = await judge(labels=qa.private_labels(), answer=answer_text, public_qa=public_qa)
            return {"retrieval_metrics": metrics, "answer": answer_text, "judge_valid": judged.get("valid") is True, "correct": judged.get("correct"), "failure_class": judged.get("failure_class")}

        before = await _namespace_state(graph, str(construction_seal["namespace"]))
        summary = await run_mab_qa_on_sealed_namespace_async(
            construction_seal=construction_seal,
            qa_manifest=qa_manifest,
            output_root=qa_output_root or block_root / "qa",
            state_reader=lambda: _namespace_state(graph, str(construction_seal["namespace"])),
            answer_fn=answer,
        )
        after = await _namespace_state(graph, str(construction_seal["namespace"]))
        if before != after:
            raise RuntimeError("QA_PHASE_WRITE_VIOLATION")
        return summary
    finally:
        if qa_runtime is None:
            close = getattr(graph, "close", None)
            if callable(close):
                await close()


async def _main(args: argparse.Namespace, *, qa_runtime: Any | None = None) -> int:
    frozen_root = args.frozen_root.resolve()
    authority_file = frozen_root / "dataset_authority.json"
    frozen_authority = _json(authority_file)
    authority = build_authority(ROOT / "mab_quality_v2_final_qa" / "data" / "official_5_contexts.json")
    authority_public = {key: value for key, value in authority.items() if key != "contexts"}
    if frozen_authority.get("authority_sha256") != authority_public.get("authority_sha256"):
        raise RuntimeError("AUTHORITY_HASH_MISMATCH")
    contexts = tuple(authority["contexts"])
    selected_contexts = tuple(args.contexts) if args.contexts else tuple(range(5))
    if any(index not in range(5) for index in selected_contexts):
        raise ValueError("context index must be in 0..4")
    if args.formal and selected_contexts != tuple(range(5)):
        raise ValueError("formal mode requires contexts 0..4")
    if args.formal and args.session_limit is not None:
        raise ValueError("formal mode cannot use a session prefix")
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    ledger = output_root / "campaign_ledger.jsonl"
    run_id = args.run_id
    from graphiti_native import load_env_file
    from native_characterization_runtime import build_u0_graphiti_from_env
    from native_characterization_instrumentation import install_native_characterization_instrumentation
    from native_characterization_tracing import TraceRecorder
    from live_outputs import export_canonical_graph

    load_env_file(VALIDATION / ".env")

    def runtime_builder():
        return build_u0_graphiti_from_env(
            authorization_checker=lambda *_args, **_kwargs: {"allowed": True},
            env_loader=lambda: load_env_file(VALIDATION / ".env"),
        )

    for context_index in selected_contexts:
        context = contexts[context_index]
        full_manifest = build_workload_manifest(context, authority_public, scope="FORMAL")
        if args.session_limit is not None:
            manifest = WorkloadManifest.from_episodes(
                context_id=context.context_id,
                episodes=full_manifest.episodes[: args.session_limit],
                dataset_revision=full_manifest.dataset_revision,
                dataset_file_sha256=full_manifest.dataset_file_sha256,
                scope="ENGINEERING_DIAGNOSTIC",
                expected_episode_count=None,
            )
            context_for_build = context
        else:
            manifest = full_manifest
            context_for_build = context
        # Attach the private session identity only as an in-memory runner field;
        # the frozen workload manifest remains the public method-independent form.
        source_inputs = tuple(
            SimpleNamespace(**episode.to_dict(), session_id=session.session_id)
            for episode, session in zip(
                manifest.episodes,
                context_for_build.sessions[: len(manifest.episodes)],
                strict=True,
            )
        )
        selected_methods = tuple(args.methods) if args.methods else METHODS
        for method in selected_methods:
            attempt = uuid.uuid4().hex[:12]
            namespace = f"mab-v13-{run_id}-c{context_index}-{method.lower()}-{attempt}"
            block_root = output_root / f"context-{context_index}" / method / attempt
            start_row = {"event": "ATTEMPT_START", "run_id": run_id, "context_index": context_index, "method": method, "namespace": namespace, "attempt_id": attempt, "started_at_ns": time.monotonic_ns()}
            _append(ledger, start_row)
            try:
                result = await run_mab_construction_async(
                    method=method,
                    run_id=run_id,
                    context_id=context.context_id,
                    namespace=namespace,
                    episodes=source_inputs,
                    runtime_builder=runtime_builder,
                    instrumentation_installer=install_native_characterization_instrumentation,
                    recorder_factory=TraceRecorder,
                    graph_exporter=export_canonical_graph,
                    output_root=block_root,
                    authority=authority_public,
                    workload_manifest=manifest,
                    frozen_config=frozen_authority.get("frozen_config", frozen_authority),
                    environment={"status": "CAPTURED", "run_id": run_id},
                    preflight={"status": "READY_FOR_A3"},
                )
                flat_seal = {"status": "CONSTRUCTION_SEALED", "context_id": context.context_id, "method": method, "namespace": namespace, "workload_hash": manifest.manifest_sha256}
                verify_seal(block_root)
                if not args.skip_qa:
                    smoke = await _run_qa(block_root=block_root, construction_seal=flat_seal, context=context, qa_scope="SMOKE", runtime_builder=runtime_builder, qa_runtime=qa_runtime)
                    full = await _run_qa(block_root=block_root, construction_seal=flat_seal, context=context, qa_scope="FULL", runtime_builder=runtime_builder, qa_runtime=qa_runtime)
                    result["qa_summary"] = full
                    (block_root / "qa_smoke_summary.json").write_text(json.dumps(smoke, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                _append(ledger, {**start_row, "event": "ATTEMPT_COMPLETE", "status": "PASS", "ended_at_ns": time.monotonic_ns(), "build_makespan_ns": result.get("t_build_ns"), "qa_status": result.get("qa_summary", {}).get("quality_status") if isinstance(result.get("qa_summary"), Mapping) else "SKIPPED"})
            except Exception as exc:
                block_root.mkdir(parents=True, exist_ok=True)
                (block_root / "failure.json").write_text(json.dumps({"status": "FAILED", "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}", "error": str(exc)[:500], "method": method, "context_index": context_index, "namespace": namespace}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                _append(ledger, {**start_row, "event": "ATTEMPT_FAILURE", "status": "FAILED", "ended_at_ns": time.monotonic_ns(), "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}", "error": str(exc)[:500]})
                if not args.continue_on_error:
                    raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--contexts", type=int, nargs="*")
    parser.add_argument("--session-limit", type=int)
    parser.add_argument(
        "--methods",
        choices=METHODS,
        nargs="*",
        help="methods to execute in the requested context order",
    )
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--skip-qa", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    qa_runtime = None
    try:
        if not args.skip_qa:
            from graphiti_native import load_env_file
            from paper_eval.graph_quality_live import build_graph_quality_runtime

            env: dict[str, str] = {}
            env.update(load_env_file(VALIDATION / ".env"))
            env.update(os.environ)
            qa_runtime = build_graph_quality_runtime(env=env)
        return asyncio.run(_main(args, qa_runtime=qa_runtime))
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}", "error": str(exc)[:500]}, ensure_ascii=False, sort_keys=True))
        return 2
    finally:
        if qa_runtime is not None:
            try:
                asyncio.run(qa_runtime.aclose())
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
