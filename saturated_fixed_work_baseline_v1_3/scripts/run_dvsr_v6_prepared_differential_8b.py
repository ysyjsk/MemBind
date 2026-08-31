#!/usr/bin/env python3
"""Run the smallest real Frozen-V6/DVSR Prepared no-reuse differential.

This is a G1 capture, not a Phase-3 opportunity experiment.  It uses one
development source in a fresh isolated namespace, persists digest-only
evidence, and never authorizes scaling or live reuse.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import copy
import hashlib
import inspect
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "saturated_fixed_work_baseline_v1_3/src",
    ROOT / "paper-eval-v3/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from graphiti_core.utils.maintenance.graph_data_operations import clear_data  # noqa: E402

from saturated_fixed_work_baseline_v1_3.mab_live_runner import _mab_graphiti_kwargs  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v5.live_runner import _episode_node  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import (  # noqa: E402
    CapacityAuthority,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import (  # noqa: E402
    NativeBindingScope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (  # noqa: E402
    provider_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import (  # noqa: E402
    TranscriptStore,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.admission import (  # noqa: E402
    ForegroundAdmissionArbiter,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.core import (  # noqa: E402
    build_membind_core_runtime_8b,
    core_identity,
    core_policy,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.mab import (  # noqa: E402
    _resolve_routed_client,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.provider import (  # noqa: E402
    V61ProviderClient,
    install_auxiliary_transport_guard,
    install_routed_physical_admission,
    strip_certified_previous_context,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (  # noqa: E402
    local_prompt_token_count,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime_8b import (  # noqa: E402
    close_8b_u0_runtime,
    load_8b_routing_contract,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_cross_snapshot import (  # noqa: E402
    _sanitize_continuation,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_v6_differential import (  # noqa: E402
    build_prepared_path_evidence,
    compare_prepared_paths,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_v6_prepared_adapter import (  # noqa: E402
    FrozenV6PreparedArtifact,
    PreparedExtractionBindings,
    install_prepared_randomness_binding,
    prepare_frozen_v6_artifact_async,
    resolve_prepared_no_reuse_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_workload import (  # noqa: E402
    DEV_HISTORIES,
    load_development_history_episodes,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import (  # noqa: E402
    GraphitiCaptureInstallation,
    _ensure_single_partition_provenance,
    canonical_digest,
    load_backend_projection_async,
)


PROFILE_ID = "local-qwen3-8b-awq-dualreplica-v1"
BACKEND_EPOCH = "neo4j-local-v1"
MODEL_EPOCH = "qwen3-8b-awq@4da05a8edb55c6046cce958586c33b61da07bb79"
_MUTATION_RE = re.compile(r"\b(?:CREATE|MERGE|DELETE|SET|REMOVE|DROP|DETACH)\b", re.I)
_PATH: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dvsr_v6_differential_path", default=None
)
_PUBLICATION: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "dvsr_v6_differential_publication", default=False
)


def _digest(value: Any) -> str:
    return canonical_digest(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False, default=str)
        + "\n"
    ).encode("ascii")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _projection_digest(projection: Any) -> str:
    return _digest(
        {
            "nodes": projection.nodes,
            "edges": projection.edges,
            "episodes": projection.episodes,
        }
    )


def _safe_projection_diagnostics(projection: Any) -> dict[str, Any]:
    def rows(values: Mapping[str, Any]) -> list[dict[str, str]]:
        return sorted(
            (
                {
                    "identity_digest": _digest(identity),
                    "payload_digest": _digest(payload),
                }
                for identity, payload in values.items()
            ),
            key=lambda row: row["identity_digest"],
        )

    return {
        "nodes": rows(projection.nodes),
        "edges": rows(projection.edges),
        "episodes": rows(projection.episodes),
    }


def _request_rows(capture: Mapping[str, Any]) -> list[dict[str, Any]]:
    certified = {
        "extract_nodes.extract_message",
        "extract_nodes.extract_text",
        "extract_nodes.extract_json",
        "extract_edges.edge",
    }
    return [
        {
            "prompt_name": row.get("prompt_name"),
            "prompt_ordinal": row.get("prompt_ordinal"),
            "request_identity": row.get("request_identity"),
            "field_digests": row.get("field_digests"),
            "response_digest": row.get("response_digest"),
            "response_binding": row.get("response_binding"),
            "status": row.get("status"),
        }
        for row in capture.get("requests", ())
        if isinstance(row, Mapping) and row.get("prompt_name") not in certified
    ]


def _canonical_request_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project logical request identity without response/oracle diagnostics."""

    return [
        {
            "prompt_name": row.get("prompt_name"),
            "prompt_ordinal": row.get("prompt_ordinal"),
            "request_identity": row.get("request_identity"),
            "field_digests": row.get("field_digests"),
            "status": row.get("status"),
        }
        for row in rows
    ]


def _read_inventory(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": index,
            "query_digest": row["query_digest"],
            "params_digest": row["params_digest"],
        }
        for index, row in enumerate(rows)
        if row.get("is_write") is False
    ]


def _install_database_observer(graphiti: Any, sink: list[dict[str, Any]]):
    driver_class = type(graphiti.driver)
    original = driver_class.execute_query

    async def observed(self: Any, *args: Any, **kwargs: Any) -> Any:
        query = kwargs.get("cypher_query_") or kwargs.get("query_") or (args[0] if args else "")
        path = _PATH.get()
        is_write = bool(_MUTATION_RE.search(str(query)))
        if path is not None:
            sink.append(
                {
                    "path": path,
                    "query_digest": _digest(str(query)),
                    "params_digest": _digest(kwargs.get("params", {})),
                    "is_write": is_write,
                    "publication": _PUBLICATION.get(),
                    "monotonic_ns": time.monotonic_ns(),
                }
            )
            if is_write and not _PUBLICATION.get():
                raise RuntimeError("pre-publication database write in G1 differential")
        return await original(self, *args, **kwargs)

    setattr(driver_class, "execute_query", observed)

    def restore() -> None:
        setattr(driver_class, "execute_query", original)

    return restore


def _install_publication_boundary(graphiti: Any):
    original = graphiti._process_episode_data

    async def process(*args: Any, **kwargs: Any) -> Any:
        token = _PUBLICATION.set(True)
        try:
            return await original(*args, **kwargs)
        finally:
            _PUBLICATION.reset(token)

    graphiti._process_episode_data = process

    def restore() -> None:
        graphiti._process_episode_data = original

    return restore


def _provider_sequence(client: V61ProviderClient, *, source_sequence: int) -> list[dict[str, Any]]:
    return [
        {
            "source_sequence": row["source_sequence"],
            "callsite": row["public_summary"].get("callsite"),
            "ordinal": row["public_summary"].get("ordinal"),
            "request_digest": row["public_summary"].get("digest"),
            "field_digests": row["public_summary"].get("field_digests"),
            "response_digest": row["response_sha256"],
        }
        for row in client.observations
        if int(row.get("source_sequence", -1)) == source_sequence
    ]


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("MEMBIND_PROFILE_ID") != PROFILE_ID:
        raise RuntimeError("source scripts/local_runtime_8b_dual/activate.sh first")
    episodes = load_development_history_episodes(
        repository_root=ROOT,
        history_id=args.history,
        # The shared Phase-3 workload loader validates that a cross-snapshot
        # prefix contains a pair.  G1 consumes only source 0, but loading the
        # smallest legal prefix keeps the frozen development split authority.
        source_count=2,
    )
    episode = episodes[0]
    run_id = args.run_id or (
        f"g1-differential-{args.history}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    output = (
        args.output
        or Path(os.environ["MEMBIND_EXPERIMENT_ROOT"]) / "v7_dvsr_g1" / run_id
    ).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("G1 differential output must use a fresh namespace")
    namespace = f"{PROFILE_ID}-dvsr-g1-{run_id}-{uuid.uuid4().hex[:8]}"

    route_events: list[dict[str, Any]] = []
    routes = load_8b_routing_contract(os.environ["MEMBIND_V61_ROUTING_CONFIG"])
    runtime = build_membind_core_runtime_8b(
        routing_contract=routes,
        route_event_sink=route_events.append,
    )
    graphiti = runtime.graphiti
    init_task = getattr(graphiti.driver, "_init_task", None)
    if init_task is not None:
        await init_task
    original_llm = runtime.llm_client
    capacity = CapacityAuthority.from_protocol_runtime(runtime)
    arbiter = ForegroundAdmissionArbiter(
        capacity,
        policy=core_policy(),
        phase_isolated=True,
        name="dvsr-g1-frozen-v6",
    )
    client_identity = {
        "class": f"{type(original_llm).__module__}.{type(original_llm).__qualname__}",
        "source_hash": hashlib.sha256(inspect.getsource(type(original_llm)).encode()).hexdigest(),
    }
    capture_store = TranscriptStore()
    capture_client = V61ProviderClient(
        original_llm,
        store=capture_store,
        arbiter=arbiter,
        mode="capture",
        durable_frontier=lambda: -1,
        client_identity=client_identity,
        token_counter=local_prompt_token_count,
        certified_message_transform=strip_certified_previous_context,
    )
    active: dict[str, V61ProviderClient] = {"client": capture_client}

    class Multiplex:
        async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
            return await active["client"].generate_response(messages, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(active["client"], name)

    multiplex = Multiplex()
    # Preserve the conventional wrapper chain used by the already-audited
    # single-partition provenance bridge.
    multiplex.inner = original_llm
    multiplex._membind_extraction_diagnostics = getattr(
        original_llm, "_membind_extraction_diagnostics", []
    )
    multiplex._membind_semantic_shortcuts = getattr(
        original_llm, "_membind_semantic_shortcuts", []
    )
    graphiti.llm_client = multiplex
    graphiti.clients.llm_client = multiplex

    restores: list[Any] = []
    routed = _resolve_routed_client(runtime, original_llm)
    if routed is not None:
        restores.append(
            install_routed_physical_admission(
                routed,
                arbiter=arbiter,
                durable_frontier=lambda: -1,
                token_counter=local_prompt_token_count,
            )
        )
    auxiliary: list[Any] = []
    for candidate in (
        getattr(original_llm, "client", None),
        getattr(getattr(graphiti, "cross_encoder", None), "client", None),
    ):
        if candidate is not None and all(candidate is not item for item in auxiliary):
            auxiliary.append(candidate)
    for transport in auxiliary:
        restores.append(
            install_auxiliary_transport_guard(
                transport,
                arbiter=arbiter,
                token_counter=local_prompt_token_count,
            )
        )

    db_rows: list[dict[str, Any]] = []
    restore_db = _install_database_observer(graphiti, db_rows)
    observer = GraphitiCaptureInstallation(
        graphiti,
        model_epoch=MODEL_EPOCH,
        query_epoch="neo4j-node-query-v1",
        index_epoch="neo4j-index-v1",
        config_epoch=PROFILE_ID,
        backend_epoch=BACKEND_EPOCH,
        single_call_branch_oracle=True,
    )
    frozen_capture: dict[str, Any] | None = None
    dvsr_capture: dict[str, Any] | None = None
    frozen_projection = dvsr_projection = None
    prepared: FrozenV6PreparedArtifact | None = None
    cleanup_verified = False
    try:
        kwargs = _mab_graphiti_kwargs(episode, namespace=namespace)
        kwargs["source_sequence"] = int(episode.source_sequence)
        prepared_episode = _episode_node(episode, namespace=namespace)
        from graphiti_core.utils.maintenance.edge_operations import extract_edges
        from graphiti_core.utils.maintenance.node_operations import extract_nodes

        async def prepared_nodes(clients: Any, node_episode: Any, previous: Sequence[Any], values: Mapping[str, Any]):
            return await extract_nodes(
                clients,
                node_episode,
                list(previous),
                values.get("entity_types"),
                values.get("excluded_entity_types"),
                values.get("custom_extraction_instructions"),
            )

        async def prepared_edges(clients: Any, node_episode: Any, nodes: Sequence[Any], previous: Sequence[Any], values: Mapping[str, Any]):
            _ensure_single_partition_provenance(graphiti, node_episode, nodes)
            return await extract_edges(
                clients,
                node_episode,
                list(nodes),
                list(previous),
                {("Entity", "Entity"): []},
                namespace,
                None,
                values.get("custom_extraction_instructions"),
            )

        # The adapter needs the post-call transcript identity.  Materialize
        # once with placeholders, then rebuild the immutable record from its
        # captured semantic objects and measured provider evidence.
        with provider_scope(region="PREPARE", source_sequence=0):
            provisional = await prepare_frozen_v6_artifact_async(
                clients=graphiti.clients,
                source_sequence=0,
                source_workload_digest=_digest(
                    {"history": args.history, "episode": kwargs, "profile": PROFILE_ID}
                ),
                episode=prepared_episode,
                previous_episodes=(),
                episode_kwargs=kwargs,
                provider_transcript_digest="PENDING",
                request_sequence_digest="PENDING",
                previous_context_policy="current_evidence_only_certified_extraction_v1",
                previous_context_digest="PENDING",
                bindings=PreparedExtractionBindings(prepared_nodes, prepared_edges),
            )
        extraction_sequence = _provider_sequence(capture_client, source_sequence=0)
        physical_calls = sum(
            int(row.get("transport_attempt_count", 0))
            for row in capture_client.provider_calls
            if int(row.get("source_sequence", -1)) == 0 and row.get("replay") is False
        )
        prepared = FrozenV6PreparedArtifact(
            source_sequence=0,
            source_workload_digest=provisional.source_workload_digest,
            episode=provisional.episode,
            previous_episodes=provisional.previous_episodes,
            extracted_nodes=provisional.extracted_nodes,
            extracted_edges=provisional.extracted_edges,
            node_episode_index_map=provisional.node_episode_index_map,
            provider_transcript_digest=_digest(capture_store.export_public_summary()),
            request_sequence_digest=_digest(extraction_sequence),
            previous_context_policy="current_evidence_only_certified_extraction_v1",
            previous_context_digest=_digest(capture_client.context_selection_events),
            physical_extraction_call_count=max(2, physical_calls),
        )

        frozen_store = copy.deepcopy(capture_store)
        dvsr_store = copy.deepcopy(capture_store)
        replay_frozen = V61ProviderClient(
            original_llm,
            store=frozen_store,
            arbiter=arbiter,
            mode="replay",
            durable_frontier=lambda: -1,
            client_identity=client_identity,
            token_counter=local_prompt_token_count,
            certified_message_transform=strip_certified_previous_context,
        )
        replay_dvsr = V61ProviderClient(
            original_llm,
            store=dvsr_store,
            arbiter=arbiter,
            mode="replay",
            durable_frontier=lambda: -1,
            client_identity=client_identity,
            token_counter=local_prompt_token_count,
            certified_message_transform=strip_certified_previous_context,
        )

        observer.install()
        restore_publication = _install_publication_boundary(graphiti)
        try:
            active["client"] = replay_frozen
            import graphiti_core.graphiti as graphiti_module

            restore_randomness = install_prepared_randomness_binding(
                graphiti_module,
                prepared,
            )
            path_token = _PATH.set("FROZEN_V6")
            try:
                with observer.scope(
                    phase="OLD",
                    source_sequence=0,
                    state_version=0,
                    episode_kwargs=kwargs,
                ) as record:
                    with provider_scope(region="NATIVE", source_sequence=0):
                        with NativeBindingScope(frozen_store, source_sequence=0):
                            await graphiti.add_episode(
                                **{key: value for key, value in kwargs.items() if key != "source_sequence"}
                            )
                frozen_capture = record.to_record()
            finally:
                _PATH.reset(path_token)
                restore_randomness()
            frozen_projection = await load_backend_projection_async(
                graphiti.driver,
                namespace=namespace,
                version=1,
                backend_epoch=BACKEND_EPOCH,
            )

            await clear_data(graphiti.driver, [namespace])
            empty = await load_backend_projection_async(
                graphiti.driver,
                namespace=namespace,
                version=0,
                backend_epoch=BACKEND_EPOCH,
            )
            if empty.nodes or empty.edges or empty.episodes:
                raise RuntimeError("isolated G1 namespace cleanup between branches failed")

            active["client"] = replay_dvsr
            from graphiti_core.nodes import EpisodicNode

            authoritative_episode = EpisodicNode(
                uuid=str(getattr(prepared.episode, "uuid")),
                name=str(kwargs["name"]),
                group_id=namespace,
                labels=[],
                source=kwargs.get("source"),
                content=str(kwargs["episode_body"]),
                source_description=str(kwargs["source_description"]),
                created_at=getattr(prepared.episode, "created_at"),
                valid_at=kwargs["reference_time"],
            )
            execution_artifact = FrozenV6PreparedArtifact(
                source_sequence=prepared.source_sequence,
                source_workload_digest=prepared.source_workload_digest,
                episode=authoritative_episode,
                previous_episodes=prepared.previous_episodes,
                extracted_nodes=prepared.extracted_nodes,
                extracted_edges=prepared.extracted_edges,
                node_episode_index_map=prepared.node_episode_index_map,
                provider_transcript_digest=prepared.provider_transcript_digest,
                request_sequence_digest=prepared.request_sequence_digest,
                previous_context_policy=prepared.previous_context_policy,
                previous_context_digest=prepared.previous_context_digest,
                logical_extraction_call_count=prepared.logical_extraction_call_count,
                physical_extraction_call_count=prepared.physical_extraction_call_count,
            )
            path_token = _PATH.set("DVSR_PREPARED_NOREUSE")
            try:
                with observer.scope(
                    phase="FRESH_NATIVE",
                    source_sequence=0,
                    state_version=0,
                    episode_kwargs=kwargs,
                ) as record:
                    with provider_scope(region="NATIVE", source_sequence=0):
                        from graphiti_core.search.search_utils import RELEVANT_SCHEMA_LIMIT

                        authoritative_previous = await graphiti.retrieve_episodes(
                            kwargs["reference_time"],
                            last_n=RELEVANT_SCHEMA_LIMIT,
                            group_ids=[namespace],
                            source=kwargs.get("source"),
                        )
                        resolution = await resolve_prepared_no_reuse_async(
                            clients=graphiti.clients,
                            artifact=execution_artifact,
                            episode_kwargs=kwargs,
                            publication_frontier=0,
                            backend_epoch=BACKEND_EPOCH,
                            read_epoch="state-0",
                            authoritative_previous_episodes=authoritative_previous,
                        )
                        await graphiti._process_episode_data(
                            execution_artifact.episode,
                            list(resolution.nodes),
                            list(resolution.entity_edges),
                            getattr(execution_artifact.episode, "created_at"),
                            namespace,
                            None,
                            None,
                            dict(prepared.node_episode_index_map),
                        )
                dvsr_capture = record.to_record()
            finally:
                _PATH.reset(path_token)
            dvsr_projection = await load_backend_projection_async(
                graphiti.driver,
                namespace=namespace,
                version=1,
                backend_epoch=BACKEND_EPOCH,
            )
        finally:
            restore_publication()
            observer.restore()

        assert frozen_capture is not None and dvsr_capture is not None
        assert frozen_projection is not None and dvsr_projection is not None
        shared_source = {
            "history_id_digest": _digest(args.history),
            "source_sequence": 0,
            "source_digest": _digest(
                {"name": episode.name, "body": episode.body, "reference_time": episode.reference_time}
            ),
            "workload_config_digest": _digest(
                {"profile": PROFILE_ID, "core": core_identity(), "route": routes}
            ),
        }
        shared_previous = {
            "policy": prepared.previous_context_policy,
            "projection_digest": _digest(prepared.previous_episodes),
            "selection_events_digest": prepared.previous_context_digest,
        }
        shared_extraction = {
            "canonical_request_sequence_digest": prepared.request_sequence_digest,
            "transcript_identity_digest": prepared.provider_transcript_digest,
            "semantic_output_digest": prepared.semantic_output_digest,
            "logical_call_sequence_digest": _digest(extraction_sequence),
            "physical_call_count": prepared.physical_extraction_call_count,
        }
        shared_routing = {
            "route_contract_digest": _digest(routes),
            "region_sequence_digest": _digest(["PREPARE:certified-extraction", "NATIVE:stateful"]),
        }

        def evidence(
            *,
            path: str,
            capture_record: Mapping[str, Any],
            projection: Any,
        ) -> dict[str, Any]:
            requests = _request_rows(capture_record)
            canonical_requests = _canonical_request_rows(requests)
            db_path = [row for row in db_rows if row.get("path") == path]
            continuation = _sanitize_continuation(capture_record["continuation_k"])
            prepublication_writes = [
                row for row in db_path if row.get("is_write") and not row.get("publication")
            ]
            return build_prepared_path_evidence(
                path=path,
                source_workload=shared_source,
                previous_context=shared_previous,
                extraction=shared_extraction,
                routing=shared_routing,
                execution_binding={
                    "uuid_time_randomness_digest": _digest(
                        {
                            "continuation": continuation,
                            "prepared_binding": prepared.uuid_time_randomness_digest,
                        }
                    )
                },
                stateful={
                    "canonical_request_sequence_digest": _digest(canonical_requests),
                    "logical_call_sequence_digest": _digest(
                        [
                            [row.get("prompt_name"), row.get("prompt_ordinal")]
                            for row in requests
                        ]
                    ),
                    "db_read_inventory_digest": _digest(_read_inventory(db_path)),
                },
                continuation_k_digest=_digest(continuation),
                canonical_graph_projection_digest=_projection_digest(projection),
                publication_order_digest=_digest([0]),
                no_prepublication_write=not prepublication_writes,
                runtime_metadata={
                    "observer_enabled": path == "DVSR_PREPARED_NOREUSE",
                    "runtime_instance_id": path,
                    "observer_version": "v7-run-831-g1-corrective-v1",
                    "capture_id": run_id,
                },
            )

        frozen_evidence = evidence(
            path="FROZEN_V6",
            capture_record=frozen_capture,
            projection=frozen_projection,
        )
        dvsr_evidence = evidence(
            path="DVSR_PREPARED_NOREUSE",
            capture_record=dvsr_capture,
            projection=dvsr_projection,
        )
        result = compare_prepared_paths(frozen_evidence, dvsr_evidence)
        artifact = {
            **result,
            "run_id": run_id,
            "history_role": "DEVELOPMENT_SHAKEOUT_ONLY",
            "history_id_digest": _digest(args.history),
            "source_count": 1,
            "profile_id": PROFILE_ID,
            "core_identity": core_identity(),
            "prepared_artifact_digest": prepared.artifact_digest,
            "prepared_logical_extraction_call_count": prepared.logical_extraction_call_count,
            "prepared_physical_extraction_call_count": prepared.physical_extraction_call_count,
            "frozen_v6_evidence": frozen_evidence,
            "dvsr_prepared_noreuse_evidence": dvsr_evidence,
            "diagnostics": {
                "frozen_v6": {
                    "stateful_requests": _canonical_request_rows(
                        _request_rows(frozen_capture)
                    ),
                    "db_reads": _read_inventory(
                        [row for row in db_rows if row.get("path") == "FROZEN_V6"]
                    ),
                    "continuation_k": _sanitize_continuation(
                        frozen_capture["continuation_k"]
                    ),
                    "graph_projection": _safe_projection_diagnostics(frozen_projection),
                },
                "dvsr_prepared_noreuse": {
                    "stateful_requests": _canonical_request_rows(
                        _request_rows(dvsr_capture)
                    ),
                    "db_reads": _read_inventory(
                        [
                            row
                            for row in db_rows
                            if row.get("path") == "DVSR_PREPARED_NOREUSE"
                        ]
                    ),
                    "continuation_k": _sanitize_continuation(
                        dvsr_capture["continuation_k"]
                    ),
                    "graph_projection": _safe_projection_diagnostics(dvsr_projection),
                },
            },
            "phase3_scaling_authorized": False,
        }
        differential_path = output / "DVSR_V6_PREPARED_DIFFERENTIAL.json"
        manifest_path = output / "manifest.json"
        _write_json(differential_path, artifact)
        _write_json(
            manifest_path,
            {
                "schema_version": "membind.dvsr.g1-differential-manifest.v1",
                "status": result["status"],
                "g1_eligible": result["g1_eligible"],
                "run_id": run_id,
                "namespace_digest": _digest(namespace),
                "output": str(output),
                "held_out_accessed": False,
                "phase3_scaling_authorized": False,
            },
        )
        _write_json(
            output / "seal.json",
            {
                "schema_version": "membind.dvsr.g1-differential-seal.v1",
                "status": "SEALED",
                "members": {
                    differential_path.name: _file_sha256(differential_path),
                    manifest_path.name: _file_sha256(manifest_path),
                },
            },
        )
        return {
            "status": result["status"],
            "g1_eligible": result["g1_eligible"],
            "output": str(output),
            "semantic_mismatch_count": len(result["semantic_mismatches"]),
        }
    finally:
        try:
            await clear_data(graphiti.driver, [namespace])
            remaining = await load_backend_projection_async(
                graphiti.driver,
                namespace=namespace,
                version=0,
                backend_epoch=BACKEND_EPOCH,
            )
            cleanup_verified = not remaining.nodes and not remaining.edges and not remaining.episodes
        finally:
            restore_db()
            for restore in reversed(restores):
                restore()
            await close_8b_u0_runtime(runtime)
        if not cleanup_verified:
            raise RuntimeError("isolated G1 namespace final cleanup failed")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", choices=DEV_HISTORIES, default="b6019101")
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(_run(_parse_args())), ensure_ascii=True, sort_keys=True))
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "INVALID_ATTEMPT",
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "error_digest": hashlib.sha256(
                        str(exc).encode("utf-8", errors="backslashreplace")
                    ).hexdigest(),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        raise
