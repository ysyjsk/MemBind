"""One fresh, checkpointed MemBind v3.1 development block."""

from __future__ import annotations

import inspect
import math
import time
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v1.graphiti_factories import build_source_log_from_episodes
from paper_eval.membind_v31.admission import AdmissionPolicy
from paper_eval.membind_v31.artifacts import V31BlockStore, inspect_v31_block
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.coordinator import run_membind_v31_stream
from paper_eval.membind_v31.graphiti_adapter import MemBindV31GraphitiAdapter
from paper_eval.membind_v31.live_runtime import build_membind_v31_runtime
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan


class MemBindV31LiveBlockError(ValueError):
    """A plan, runtime, namespace, or durable block invariant failed."""


def _fail(code: str) -> MemBindV31LiveBlockError:
    return MemBindV31LiveBlockError(code)


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    return await value


@dataclass(frozen=True, slots=True)
class V31LiveHooks:
    runtime_builder: Callable[..., object]
    runtime_ready: Callable[[object], object]
    namespace_probe: Callable[[object, str], object]
    namespace_episode: Callable[[object, str], object]
    source_visibility_probe: Callable[[object, object], object]
    reference_time_to_ns: Callable[[str], int]
    adapter_factory: Callable[[object, StateCutCertification], object]
    close_runtime: Callable[[object], object]


def _policy(value: object) -> AdmissionPolicy:
    mapping = {
        "FRONTIER_BARRIER": AdmissionPolicy.BARRIER,
        "FRONTIER_FIRST_FIFO": AdmissionPolicy.FIFO,
        "FRONTIER_FIRST_CACHE_AFFINITY": AdmissionPolicy.CACHE_AFFINE,
    }
    try:
        return mapping[value]
    except (KeyError, TypeError):
        raise _fail("method_policy_invalid") from None


def _snapshot(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    try:
        nodes = int(value.get("node_count", 0))
        relationships = int(value.get("relationship_count", 0))
    except (TypeError, ValueError):
        raise _fail(code) from None
    names = value.get("episode_names")
    if nodes < 0 or relationships < 0 or isinstance(names, (str, bytes)) or not isinstance(
        names, Sequence
    ):
        raise _fail(code)
    selected_names = [str(item) for item in names]
    return {
        "node_count": nodes,
        "relationship_count": relationships,
        "episode_names": sorted(selected_names),
    }


def _percentile(values: Sequence[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _performance(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    arrivals: dict[int, int] = {}
    publications: dict[int, int] = {}
    for event in events:
        sequence = int(event["source_sequence"])
        timestamp = int(event["timestamp_ns"])
        if event["event_type"] == "ARRIVAL":
            arrivals[sequence] = timestamp
        elif event["event_type"] == "PUBLICATION_DURABLE":
            publications[sequence] = timestamp
    freshness = [publications[index] - arrivals[index] for index in sorted(publications)]
    makespan = (
        max(publications.values()) - min(arrivals.values())
        if arrivals and publications
        else None
    )
    return {
        "published_episode_count": len(publications),
        "p50_freshness_ns": _percentile(freshness, 0.50),
        "p95_freshness_ns": _percentile(freshness, 0.95),
        "p99_freshness_ns": _percentile(freshness, 0.99),
        "max_freshness_ns": max(freshness) if freshness else None,
        "makespan_ns": makespan,
        "goodput_episodes_per_second": (
            None
            if makespan is None or makespan <= 0
            else len(publications) * 1_000_000_000 / makespan
        ),
    }


async def execute_v31_live_block(
    *,
    verified_plan: Mapping[str, object],
    block_index: int,
    episodes: Sequence[object],
    env: Mapping[str, str],
    block_root: Path,
    state_cut_certification: StateCutCertification,
    compile_workers: int,
    lookahead: int,
    hooks: V31LiveHooks | None = None,
) -> dict[str, object]:
    """Execute a single plan block without touching baseline artifacts."""

    try:
        plan = verify_membind_v31_method_plan(verified_plan)
    except ValueError:
        raise _fail("verified_plan_invalid") from None
    if isinstance(block_index, bool) or not isinstance(block_index, int) or not 0 <= block_index < len(
        plan["blocks"]
    ):
        raise _fail("block_index_invalid")
    block = plan["blocks"][block_index]
    if (
        compile_workers != plan["compile_workers"]
        or lookahead != plan["lookahead"]
        or compile_workers != block["compile_workers"]
        or lookahead != block["lookahead"]
    ):
        raise _fail("runtime_knob_plan_mismatch")
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise _fail("episodes_invalid")
    if len(episodes) != block["source_count"]:
        raise _fail("episode_count_mismatch")
    if not isinstance(env, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in env.items()
    ):
        raise _fail("environment_invalid")
    if not isinstance(state_cut_certification, StateCutCertification):
        raise _fail("state_cut_certification_invalid")
    try:
        certification = state_cut_certification.verify()
    except ValueError:
        raise _fail("state_cut_certification_invalid") from None
    selected_hooks = production_v31_live_hooks() if hooks is None else hooks
    if not isinstance(selected_hooks, V31LiveHooks):
        raise _fail("live_hooks_invalid")

    namespace = str(block["namespace"])
    try:
        scoped = tuple(selected_hooks.namespace_episode(item, namespace) for item in episodes)
        source_log, raw_hashes = build_source_log_from_episodes(
            scoped,
            namespace=namespace,
            reference_time_to_ns=selected_hooks.reference_time_to_ns,
        )
    except Exception:
        raise _fail("source_log_materialization_failed") from None
    expected_raw = tuple(plan["history_source_sha256s"][block["history_id"]])
    if raw_hashes != expected_raw:
        raise _fail("source_identity_mismatch")

    policy = _policy(block["policy"])
    request_rows: list[dict[str, object]] = []
    store: V31BlockStore | None = None

    def request_observer(row: dict[str, object]) -> None:
        if store is None:
            request_rows.append(dict(row))
        else:
            store.append_telemetry("llm", row)

    block_env = {**dict(env), "CONSTRUCTION_CACHE_SALT": str(block["cache_salt_sha256"])}
    runtime: object | None = None
    try:
        runtime = selected_hooks.runtime_builder(
            env=block_env,
            policy=policy,
            request_id_prefix=f"v31-{block_index:02d}-{block['history_id']}",
            observer=request_observer,
        )
        if inspect.isawaitable(runtime):
            raise _fail("runtime_builder_must_be_synchronous")
        if getattr(runtime, "shared_execution_envelope_sha256", None) != block[
            "shared_execution_envelope_sha256"
        ]:
            raise _fail("shared_execution_envelope_mismatch")
        await _await(selected_hooks.runtime_ready(runtime), "runtime_ready_must_be_async")
        initial = _snapshot(
            await _await(
                selected_hooks.namespace_probe(runtime, namespace),
                "namespace_probe_must_be_async",
            ),
            "initial_namespace_invalid",
        )
        if initial != {"node_count": 0, "relationship_count": 0, "episode_names": []}:
            raise _fail("namespace_not_fresh")
        execution_identity = payload_sha256(
            {
                "method_execution_identity_sha256": getattr(
                    runtime, "method_execution_identity_sha256", None
                ),
                "methodology_sha256": plan["methodology_sha256"],
                "workplan_sha256": plan["workplan_sha256"],
                "state_cut_certification_sha256": certification.certification_sha256,
                "compile_workers": compile_workers,
                "lookahead": lookahead,
            }
        )
        store = V31BlockStore.create(
            Path(block_root),
            verified_plan=plan,
            block_index=block_index,
            execution_identity_sha256=execution_identity,
            state_cut_certification_sha256=certification.certification_sha256,
            compile_workers=compile_workers,
            lookahead=lookahead,
            compile_source_sha256s=[record.source_sha256 for record in source_log.records],
        )
        for row in request_rows:
            store.append_telemetry("llm", row)
        request_rows.clear()
        adapter = selected_hooks.adapter_factory(runtime, certification)

        lifecycle_map = {
            "arrival": "ARRIVAL",
            "arrival_failure": "TERMINAL_FAILURE",
            "compile_start": "COMPILE_STARTED",
            "prepared_durable": "PREPARED_DURABLE",
            "bind_start": "BIND_STARTED",
            "compile_failure": "TERMINAL_FAILURE",
            "bind_failure": "TERMINAL_FAILURE",
        }

        def lifecycle(row: dict[str, object]) -> None:
            event_type = lifecycle_map.get(str(row.get("event_type")))
            if event_type is None:
                return
            telemetry = {
                key: value
                for key, value in row.items()
                if key
                not in {"event_type", "stream_id", "source_sequence", "timestamp_ns"}
            }
            store.append_lifecycle(
                int(row["source_sequence"]),
                event_type,
                int(row["timestamp_ns"]),
                telemetry,
            )

        async def visibility(sequence: int, _result: object) -> bool:
            value = await _await(
                selected_hooks.source_visibility_probe(runtime, source_log.record(sequence)),
                "visibility_probe_must_be_async",
            )
            if not isinstance(value, bool):
                raise _fail("visibility_probe_invalid")
            return value

        def commit(sequence: int, _result: object) -> None:
            store.append_lifecycle(sequence, "COMMIT_RETURNED", time.monotonic_ns())

        def publication(sequence: int, _result: object) -> None:
            store.append_lifecycle(
                sequence,
                "PUBLICATION_DURABLE",
                time.monotonic_ns(),
                {"visibility_confirmed": True},
            )
            print(
                f"CHECKPOINT method={block['method']} history={block['history_id']} "
                f"source_sequence={sequence} visibility=True",
                flush=True,
            )

        coordinator = await run_membind_v31_stream(
            stream_id=str(block["history_id"]),
            source_log=source_log,
            arrival_offsets_ns=tuple(
                plan["arrival_traces"][block["history_id"]]["arrival_offsets_ns"]
            ),
            adapter=adapter,
            request_client=getattr(runtime, "admitted_llm"),
            compile_workers=compile_workers,
            lookahead=lookahead,
            observer=lifecycle,
            publication_probe=visibility,
            prepared_persistor=store.persist_prepared,
            commit_observer=commit,
            publication_persistor=publication,
        )
        inspected = inspect_v31_block(Path(block_root))
        if inspected["checkpoint"]["complete_coverage"] is not True:
            raise _fail("block_coverage_incomplete")
        final = _snapshot(
            await _await(
                selected_hooks.namespace_probe(runtime, namespace),
                "namespace_probe_must_be_async",
            ),
            "final_namespace_invalid",
        )
        expected_names = sorted(str(getattr(item, "name")) for item in scoped)
        if final["episode_names"] != expected_names:
            raise _fail("final_namespace_episode_mismatch")
        body: dict[str, object] = {
            "schema_version": "membind.paper-eval-v3.membind-v31-live-block-result.v1",
            "status": "PASS",
            "run_id": plan["run_id"],
            "block_index": block_index,
            "method": block["method"],
            "policy": block["policy"],
            "history_id": block["history_id"],
            "namespace": namespace,
            "source_count": source_log.source_count,
            "plan_payload_sha256": plan["payload_sha256"],
            "manifest_sha256": inspected["manifest"]["manifest_sha256"],
            "state_cut_certification_sha256": certification.certification_sha256,
            "execution_identity_sha256": execution_identity,
            "compile_workers": compile_workers,
            "lookahead": lookahead,
            "global_llm_admission_k": 2,
            "direct_violation_count": coordinator["direct_violation_count"],
            "performance": _performance(inspected["events"]),
            "request_admission": deepcopy(getattr(runtime, "admitted_llm").observation()),
            "initial_namespace": initial,
            "final_namespace": final,
            "checkpoint": inspected["checkpoint"],
        }
        body["payload_sha256"] = payload_sha256(body)
        atomic_write_json(Path(block_root) / "result.json", body)
        return body
    finally:
        if runtime is not None:
            await _await(selected_hooks.close_runtime(runtime), "runtime_close_must_be_async")


def production_v31_live_hooks() -> V31LiveHooks:
    """Compose the already-qualified runtime and pinned Graphiti symbols lazily."""

    from paper_eval.membind_v1.aligned_live import production_aligned_live_hooks
    from paper_eval.membind_v1.graphiti_factories import make_graphiti_node_factories
    from paper_eval.s5_graphiti_semantic_binding import load_graphiti_semantic_binding

    legacy = production_aligned_live_hooks()

    def runtime_builder(**kwargs: object) -> object:
        return build_membind_v31_runtime(**kwargs)

    def adapter_factory(runtime: object, certification: StateCutCertification) -> object:
        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode

        graphiti = getattr(runtime, "graphiti")
        factories = make_graphiti_node_factories(
            episodic_node_type=EpisodicNode,
            entity_node_type=EntityNode,
            message_source=EpisodeType.message,
        )
        return MemBindV31GraphitiAdapter(
            graphiti=graphiti,
            llm_client=getattr(graphiti, "llm_client"),
            semantic_binding=load_graphiti_semantic_binding(),
            episode_factory=factories.episode_factory,
            extracted_node_factory=factories.extracted_node_factory,
            extracted_edge_factory=lambda value: EntityEdge(**dict(value)),
            state_cut_certification=certification,
        )

    return V31LiveHooks(
        runtime_builder=runtime_builder,
        runtime_ready=legacy.runtime_ready,
        namespace_probe=legacy.namespace_probe,
        namespace_episode=legacy.namespace_episode,
        source_visibility_probe=legacy.source_visibility_probe,
        reference_time_to_ns=legacy.reference_time_to_ns,
        adapter_factory=adapter_factory,
        close_runtime=legacy.close_runtime,
    )


__all__ = [
    "MemBindV31LiveBlockError",
    "V31LiveHooks",
    "execute_v31_live_block",
    "production_v31_live_hooks",
]
