"""Formal MemBind composition over the unmodified upstream Graphiti core."""

from __future__ import annotations

import hashlib
import inspect
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..artifact_materializer import materialize_construction_block
from ..evaluation_contract import (
    validate_block_trace,
    validate_order_contract,
    validate_v6_bindings,
)
from ..mab_live_runner import (
    MAB8192_ADAPTER_VERSION,
    MABLiveRunnerError,
    _adapter_coverage,
    _episode_envelopes,
    _event_sink,
    _logical_identity,
    _mab_graphiti_kwargs,
    _span_metrics,
    episode_from_input,
)
from ..membind_v5.live_runner import _maybe_await, _parse_reference_time
from ..membind_v5.p9_runner import _native_previous_window
from ..membind_v5.runtime.core.admission import CapacityAuthority
from ..membind_v5.runtime.core.binder import NativeBindingScope
from ..membind_v5.runtime.core.provider_admission import (
    current_provider_scope,
    provider_scope,
)
from ..membind_v5.runtime.core.transcript import TranscriptStore
from .admission import ForegroundAdmissionArbiter
from .executor import run_resource_credit_frontier_history_async
from .resource_credit import ResourceCreditPolicy
from .upstream_replay import UpstreamReplayClient
from .upstream_runtime import (
    FORMAL_ARM_C,
    formal_runtime_identity,
    logical_request_context,
    resolve_deployment_policy,
)


class UpstreamCampaignError(MABLiveRunnerError):
    """The upstream-only C construction cannot satisfy its frozen contract."""


class _MultiplexClient:
    def __init__(self, capture: UpstreamReplayClient, replay: UpstreamReplayClient):
        self.capture = capture
        self.replay = replay

    async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
        region, _ = current_provider_scope()
        if region == "PREPARE":
            return await self.capture.generate_response(messages, **kwargs)
        if region == "NATIVE":
            return await self.replay.generate_response(messages, **kwargs)
        raise UpstreamCampaignError("provider call escaped PREPARE/NATIVE scope")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.replay, name)


def _episode_node(episode: Any, *, namespace: str) -> Any:
    from graphiti_core.nodes import EpisodicNode, EpisodeType

    return EpisodicNode(
        uuid=str(uuid.uuid4()),
        name=episode.name,
        group_id=namespace,
        labels=[],
        source=EpisodeType.message,
        content=episode.body,
        source_description="MemoryAgentBench LongMemEval session",
        created_at=datetime.now(timezone.utc),
        valid_at=_parse_reference_time(episode.reference_time),
    )


def _client_identity(client: Any) -> dict[str, str]:
    try:
        source = inspect.getsource(type(client)).encode("utf-8")
    except (OSError, TypeError):
        source = f"{type(client).__module__}.{type(client).__qualname__}".encode(
            "utf-8"
        )
    return {
        "class": f"{type(client).__module__}.{type(client).__qualname__}",
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }


def _build_bindings(
    capture: UpstreamReplayClient, replay: UpstreamReplayClient
) -> list[dict[str, Any]]:
    def selected(client: UpstreamReplayClient) -> dict[tuple[int, str, int], dict[str, Any]]:
        return {
            (
                int(row["source_sequence"]),
                str(row["callsite"]),
                int(row["ordinal"]),
            ): row
            for row in client.provider_calls
            if row.get("callsite") in client.certified_callsites
        }

    prepared = selected(capture)
    native = selected(replay)
    if set(prepared) != set(native):
        raise UpstreamCampaignError("certified capture/replay inventory mismatch")
    bindings: list[dict[str, Any]] = []
    for key in sorted(prepared):
        left = prepared[key]
        right = native[key]
        if (
            left.get("request_identity_sha256")
            != right.get("request_identity_sha256")
            or left.get("response_sha256") != right.get("response_sha256")
            or right.get("physical_attempt_count") != 0
        ):
            raise UpstreamCampaignError("certified capture/replay identity mismatch")
        bindings.append(
            {
                "source_sequence": key[0],
                "callsite": key[1],
                "ordinal_within_episode": key[2],
                "request_identity_hash": left["request_identity_sha256"],
                "prepared_response_hash": left["response_sha256"],
                "native_request_hash": right["request_identity_sha256"],
                "native_response_hash": right["response_sha256"],
                "capture_count": 1,
                "consume_count": 1,
                "discard_count": 0,
                "fallback_type": None,
                "match_status": "EXACT_MATCH",
                "transport_attempt_count": 0,
                "external_transport_attempted": False,
                "external_transport_attempted_during_replay": False,
            }
        )
    return bindings


async def run_upstream_membind_construction_async(
    *,
    run_id: str,
    context_id: str,
    namespace: str,
    episodes: Sequence[Any],
    policy: ResourceCreditPolicy,
    runtime_builder: Callable[[], Any],
    instrumentation_installer: Callable[[Any, Any], Any],
    recorder_factory: Callable[[], Any],
    graph_exporter: Callable[[Any, list[Any], str], Any],
    output_root: str | Path,
    authority: Mapping[str, Any],
    workload_manifest: Any,
    frozen_config: Mapping[str, Any],
    environment: Mapping[str, Any] | None = None,
    preflight: Mapping[str, Any] | None = None,
    extract_nodes_fn: Callable[..., Any] | None = None,
    extract_edges_fn: Callable[..., Any] | None = None,
    runtime_closer: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Run C with exact upstream extraction replay and ordered publication."""

    if not isinstance(policy, ResourceCreditPolicy) or not policy.is_resource_credit:
        raise UpstreamCampaignError("formal C requires MEMBIND_RESOURCE_CREDIT_V1")
    selected = tuple(episode_from_input(item) for item in episodes)
    if not selected or [item.source_sequence for item in selected] != list(
        range(len(selected))
    ):
        raise UpstreamCampaignError("MAB episode sequence is invalid")
    coverage = _adapter_coverage(selected, require_mab8192=True)
    if coverage["status"] != "PASS" or coverage["adapter_version"] != MAB8192_ADAPTER_VERSION:
        raise UpstreamCampaignError("MAB8192 adapter coverage is invalid")
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise UpstreamCampaignError("formal C block root is not fresh")

    if extract_nodes_fn is None:
        from graphiti_core.utils.maintenance.node_operations import extract_nodes

        extract_nodes_fn = extract_nodes
    if extract_edges_fn is None:
        from graphiti_core.utils.maintenance.edge_operations import extract_edges

        extract_edges_fn = extract_edges

    events: list[dict[str, Any]] = []
    emit = _event_sink(events, context_id)
    frontier_events: list[dict[str, Any]] = []
    admission_events: list[dict[str, Any]] = []
    runtime: Any = None
    graphiti: Any = None
    recorder: Any = None
    instrumentation: Any = None
    original_graph_llm: Any = None
    original_clients_llm: Any = None
    closed = False

    async def close_runtime() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        if graphiti is not None and original_graph_llm is not None:
            graphiti.llm_client = original_graph_llm
        if graphiti is not None and original_clients_llm is not None:
            graphiti.clients.llm_client = original_clients_llm
        if instrumentation is not None:
            instrumentation.restore()
        if runtime is None:
            return
        if runtime_closer is not None:
            await _maybe_await(runtime_closer(runtime))
            return
        close = getattr(graphiti, "close", None)
        if callable(close):
            await _maybe_await(close())

    try:
        runtime = await _maybe_await(runtime_builder())
        graphiti = runtime.graphiti
        runtime_identity = formal_runtime_identity(
            runtime,
            mab8192_manifest_sha256=str(workload_manifest.manifest_sha256),
        )
        capacity = CapacityAuthority.from_protocol_runtime(runtime)
        admission = ForegroundAdmissionArbiter(
            capacity,
            policy=policy,
            name="formal-upstream-c",
            event_sink=lambda row: admission_events.append(dict(row)),
        )
        store = TranscriptStore()
        durable_frontier = {"value": -1}
        identity = _client_identity(runtime.llm_client)
        deployment = getattr(
            runtime, "_membind_deployment_policy", resolve_deployment_policy()
        )
        transport_identity = {
            "deployment_policy_id": deployment.policy_id,
            "model": deployment.served_model,
            "sampling": dict(deployment.sampling),
            "sdk_retries": 0,
        }
        capture = UpstreamReplayClient(
            runtime.llm_client,
            store=store,
            admission=admission,
            mode="capture",
            durable_frontier=lambda: durable_frontier["value"],
            client_identity=identity,
            transport_identity=transport_identity,
        )
        replay = UpstreamReplayClient(
            runtime.llm_client,
            store=store,
            admission=admission,
            mode="replay",
            durable_frontier=lambda: durable_frontier["value"],
            client_identity=identity,
            transport_identity=transport_identity,
        )
        multiplex = _MultiplexClient(capture, replay)
        original_graph_llm = graphiti.llm_client
        original_clients_llm = graphiti.clients.llm_client
        graphiti.llm_client = multiplex
        graphiti.clients.llm_client = multiplex
        recorder = recorder_factory()
        instrumentation = instrumentation_installer(graphiti, recorder)

        formal_start = time.monotonic_ns()
        emit("FORMAL_START", stamp=formal_start)
        for sequence in range(len(selected)):
            emit("SUBMIT", sequence)

        async def prepare(sequence: int) -> dict[str, int]:
            episode = selected[sequence]
            current = _episode_node(episode, namespace=namespace)
            previous = [
                _episode_node(item, namespace=namespace)
                for item in _native_previous_window(selected, sequence)
            ]
            with logical_request_context(_logical_identity(episode)):
                with recorder.episode_scope(run_id, episode.name, sequence):
                    with provider_scope(region="PREPARE", source_sequence=sequence):
                        nodes, index_map = await _maybe_await(
                            extract_nodes_fn(
                                graphiti.clients,
                                current,
                                previous,
                                None,
                                None,
                                None,
                            )
                        )
                        edges = await _maybe_await(
                            extract_edges_fn(
                                graphiti.clients,
                                current,
                                nodes,
                                previous,
                                {("Entity", "Entity"): []},
                                namespace,
                                None,
                                None,
                            )
                        )
            return {
                "source_sequence": sequence,
                "node_count": len(nodes),
                "node_index_count": len(index_map),
                "edge_count": len(edges),
            }

        async def publish(sequence: int, _prepared: Any) -> Any:
            episode = selected[sequence]
            emit("NATIVE_ENTER", sequence)
            with logical_request_context(_logical_identity(episode)):
                with recorder.episode_scope(run_id, episode.name, sequence):
                    with provider_scope(region="NATIVE", source_sequence=sequence):
                        with NativeBindingScope(
                            store, source_sequence=sequence, strict=True
                        ):
                            result = await graphiti.add_episode(
                                **_mab_graphiti_kwargs(
                                    episode,
                                    namespace=namespace,
                                    include_uuid=False,
                                )
                            )
            durable_frontier["value"] = sequence
            emit("PUBLICATION_DURABLE", sequence, time.monotonic_ns())
            return result

        def dependency_ready(sequence: int, frontier: int) -> bool:
            episode = selected[sequence]
            if episode.chunk_ordinal == 0:
                return True
            predecessor = selected[sequence - 1] if sequence > 0 else None
            return bool(
                predecessor is not None
                and predecessor.chunk_id == episode.previous_chunk_id
                and predecessor.session_id == episode.session_id
                and frontier >= sequence - 1
            )

        execution = await run_resource_credit_frontier_history_async(
            len(selected),
            prepare,
            publish,
            authority=capacity,
            policy=policy,
            admission=admission,
            event_sink=lambda row: frontier_events.append(dict(row)),
            dependency_ready=dependency_ready,
        )
        if execution.durable_frontier != len(selected) - 1:
            raise UpstreamCampaignError("durable frontier is incomplete")
        transcript_summary = store.summary()
        if any(
            transcript_summary[name]
            for name in (
                "logical_discarded",
                "unconsumed",
                "duplicates",
                "fresh_fallback",
                "mismatch_fallback",
                "missing_fallback",
            )
        ):
            raise UpstreamCampaignError("exact transcript accounting is incomplete")
        bindings = _build_bindings(capture, replay)
        refinement = validate_v6_bindings(bindings)
        seal_time = time.monotonic_ns()
        emit("CONSTRUCTION_SEAL", stamp=seal_time)
        canonical = await _maybe_await(
            graph_exporter(graphiti, list(selected), namespace)
        )
        if not isinstance(canonical, Mapping):
            raise UpstreamCampaignError("canonical graph export is invalid")
        provider_calls = [*capture.provider_calls, *replay.provider_calls]
        result = {
            "schema_version": "membind.upstream-c-block.v1",
            "status": "PASS",
            "method": FORMAL_ARM_C,
            "semantic_class": "MEMBIND_V6_1",
            "context_id": context_id,
            "namespace": namespace,
            "workload_hash": str(workload_manifest.manifest_sha256),
            "expected_episode_count": len(selected),
            "submitted_count": len(selected),
            "completed_count": len(selected),
            "events": events,
            "frontier_events": frontier_events,
            "admission_events": admission_events,
            "provider_calls": provider_calls,
            "native_trace": _episode_envelopes(recorder, run_id, selected),
            "transport_trace": list(
                getattr(runtime, "_membind_transport_telemetry", ()) or ()
            ),
            "request_identity": [
                *capture.request_identities,
                *replay.request_identities,
            ],
            "runtime_identity": runtime_identity,
            "bindings": bindings,
            "transcript_summary": transcript_summary,
            "adapter_coverage": coverage,
            "lifecycle_validation": validate_block_trace(
                events,
                expected_source_count=len(selected),
                method=FORMAL_ARM_C,
                context_id=context_id,
            ),
            "order_validation": validate_order_contract(
                events,
                expected_source_count=len(selected),
                method=FORMAL_ARM_C,
            ),
            "refinement_validation": refinement,
            "graph_diagnostics": dict(canonical),
            "scheduler_evidence": {
                "method_identity": policy.method_identity,
                "execution_strategy": execution.execution_strategy,
                "durable_frontier": execution.durable_frontier,
                "max_started_ahead": execution.max_started_ahead,
                "arbiter_instance_id": execution.arbiter_instance_id,
                "dependency_contract": "session_chunk_predecessor_durable",
            },
            "policy": policy.to_dict(),
            "t_build_ns": seal_time - formal_start,
            "publication_guarantee": "ORDERED_DURABLE_FRONTIER_NO_ATTEMPT_RESUME",
            "structured_output_policy": "UPSTREAM_GRAPHITI_PYDANTIC_JSON_SCHEMA",
            "response_repair_enabled": runtime_identity["response_repair_enabled"],
            "finite_pair_tasks_enabled": runtime_identity["finite_pair_tasks_enabled"],
            "llm_logical_requests": len(provider_calls),
            "transport_attempts": sum(
                int(row["physical_attempt_count"]) for row in provider_calls
            ),
            "transport_retry_attempts": 0,
            **_span_metrics(recorder),
        }
        seal = materialize_construction_block(
            root,
            authority=authority,
            workload_manifest=workload_manifest,
            frozen_config=frozen_config,
            result=result,
            identity={
                "method": FORMAL_ARM_C,
                "context_id": context_id,
                "namespace": namespace,
                "run_id": run_id,
            },
            environment=environment,
            preflight=preflight,
        )
        await close_runtime()
        return {**result, "construction_seal": seal}
    except BaseException:
        await close_runtime()
        raise


__all__ = ["UpstreamCampaignError", "run_upstream_membind_construction_async"]
