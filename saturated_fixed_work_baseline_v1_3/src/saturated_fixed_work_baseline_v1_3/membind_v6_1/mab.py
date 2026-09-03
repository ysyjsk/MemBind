"""Live MAB composition for foreground-aware V6.1 exact replay."""

from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..artifact_seals import seal_construction_block
from ..evaluation_contract import (
    validate_block_trace,
    validate_order_contract,
    validate_v6_bindings,
)
from ..mab_live_runner import (
    MABLiveRunnerError,
    _episode_envelopes,
    _event_sink,
    _mab_graphiti_kwargs,
    _mab_publication_idempotency_key,
    episode_from_input,
)
from ..membind_v5.live_runner import _episode_node, _maybe_await
from ..membind_v5.p9_runner import (
    _native_previous_window,
    _transport_attempt_rows,
    _transport_evidence_summary,
)
from ..membind_v5.runtime.core.admission import AdmissionClass, CapacityAuthority
from ..membind_v5.runtime.core.binder import NativeBindingScope
from ..membind_v5.runtime.core.provider_admission import (
    current_provider_scope,
    provider_scope,
)
from ..membind_v5.runtime.core.transcript import TranscriptStore
from ..membind_v5.runtime.adapters.client_proxy import CERTIFIED_CALLSITES
from ..membind_v6.proof import (
    validate_replay_accounting,
    validate_request_comparisons,
)
from ..membind_v6.request_observation import compare_request_observations
from .admission import ForegroundAdmissionArbiter
from .evidence import extraction_work_inventory, provider_proof, span_work_inventory
from .edge_predicate import install_edge_invalidation_predicate_pushdown
from .executor import (
    DUAL_STREAMING_EXECUTION_STRATEGY,
    JIT_EXECUTION_STRATEGY,
    STAGED_EXECUTION_STRATEGY,
    run_jit_frontier_history_async,
    run_staged_frontier_history_async,
    run_resource_credit_frontier_history_async,
)
from .policy import V61Policy
from .resource_credit import ResourceCreditPolicy
from .provider import (
    V61ProviderClient,
    incremental_native_summary_context,
    install_auxiliary_transport_guard,
    install_routed_physical_admission,
    strip_certified_previous_context,
)
from .runtime import close_local_u0_runtime, local_prompt_token_count
from .structured_output_recovery import reliability_identity


class V61MABError(MABLiveRunnerError):
    pass


_DEFAULT_CERTIFIED_MESSAGE_TRANSFORM = object()


def _assert_core_context_integrity(
    artifact_method: str, inventory: Mapping[str, Any]
) -> None:
    """Fail a Core construction that reports any previous-context removal."""

    if artifact_method not in {"MEMBIND_CORE", "MEMBIND_V6_1_SHARED_BOUNDED_SO"}:
        return
    removed = int(inventory.get("certified_previous_context_chars_removed", 0) or 0)
    removed += int(
        inventory.get("incremental_summary_previous_context_chars_removed", 0) or 0
    )
    if removed > 0:
        raise V61MABError(
            "MemBind-Core context integrity violation: non-empty previous context removal"
        )


_SHADOW_SOURCE: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "membind_v6_1_shadow_source", default=None
)


class _Journal:
    SYNC_INTERVAL_NS = 30_000_000_000

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._committed: dict[int, dict[str, Any]] = {}
        if path.exists():
            self._load_existing()
            self._stream = path.open("a", encoding="utf-8")
        else:
            self._stream = path.open("x", encoding="utf-8")
        self._closed = False
        self._last_sync_ns = time.monotonic_ns()

    def _load_existing(self) -> None:
        """Recover only complete commit records from a prior interrupted run."""

        try:
            rows = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in rows:
            try:
                row = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                # A torn final line is deliberately ignored; no publication is
                # considered committed without a complete JSON record.
                continue
            if not isinstance(row, Mapping) or row.get("event") not in {
                "PUBLICATION_COMMITTED",
                "PUBLICATION_DURABLE",
            }:
                continue
            sequence = row.get("source_sequence")
            key = row.get("idempotency_key")
            if isinstance(sequence, int) and isinstance(key, str) and key:
                self._committed[int(sequence)] = dict(row)

    def committed_publications(self) -> dict[int, dict[str, Any]]:
        return {sequence: dict(row) for sequence, row in self._committed.items()}

    def _sync(self) -> None:
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._last_sync_ns = time.monotonic_ns()

    def append(self, row: Mapping[str, Any], *, durable: bool = False) -> None:
        # Graphiti's gather helper can finish cancellation callbacks after the
        # main coroutine has unwound. Keep shutdown fail-closed and avoid a
        # secondary I/O exception obscuring the original provider failure.
        if self._closed:
            return
        self._stream.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        # Keep the journal visible to the live observer without putting every
        # provider/admission event on the construction critical path. State
        # transitions and failures use a durable group-commit boundary; normal
        # trace rows are synced within a bounded 30-second window and again at
        # close. flush() still makes every row immediately visible to monitors.
        self._stream.flush()
        if durable or time.monotonic_ns() - self._last_sync_ns >= self.SYNC_INTERVAL_NS:
            self._sync()
        if row.get("event") in {"PUBLICATION_COMMITTED", "PUBLICATION_DURABLE"}:
            sequence = row.get("source_sequence")
            key = row.get("idempotency_key")
            if isinstance(sequence, int) and isinstance(key, str) and key:
                self._committed[int(sequence)] = dict(row)

    def close(self) -> None:
        if not self._stream.closed:
            self._sync()
            self._stream.close()
        self._closed = True


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _nanosecond_summary(values: Sequence[int]) -> dict[str, int]:
    ordered = sorted(max(0, int(value)) for value in values)
    if not ordered:
        return {"count": 0, "sum_ns": 0, "p50_ns": 0, "p95_ns": 0, "max_ns": 0}

    def percentile(fraction: float) -> int:
        return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]

    return {
        "count": len(ordered),
        "sum_ns": sum(ordered),
        "p50_ns": percentile(0.50),
        "p95_ns": percentile(0.95),
        "max_ns": ordered[-1],
    }


@contextmanager
def _shadow_source_scope(source_sequence: int):
    token = _SHADOW_SOURCE.set(int(source_sequence))
    try:
        yield
    finally:
        _SHADOW_SOURCE.reset(token)


def _install_shadow_db_guard(driver: Any, sink: list[dict[str, Any]]) -> Callable[[], None]:
    original = getattr(driver, "execute_query", None)
    if not callable(original):
        raise V61MABError("Graphiti driver execute_query seam is unavailable")

    async def guarded(*args: Any, **kwargs: Any) -> Any:
        source = _SHADOW_SOURCE.get()
        if source is not None:
            row = {
                "event": "SHADOW_DB_ACCESS_BLOCKED",
                "source_sequence": source,
                "monotonic_ns": time.monotonic_ns(),
            }
            sink.append(row)
            raise V61MABError("shadow preparation attempted database access")
        return await original(*args, **kwargs)

    setattr(driver, "execute_query", guarded)

    def restore() -> None:
        setattr(driver, "execute_query", original)

    return restore


def _resolve_routed_client(runtime: Any, original_llm: Any) -> Any | None:
    """Return the concrete router, not a provider-specific transport wrapper.

    ``QwenVLLMClient.client`` is an adapter whose ``_inner`` is the actual
    ``RoutedOpenAIClient``.  Mutating the adapter would create local hook
    attributes while the router continued dispatching without physical
    admission.  The 8B runtime exposes the concrete router explicitly; the
    bounded unwrap keeps this compatible with older runtime objects.
    """

    explicit = getattr(runtime, "_membind_route_client", None)
    candidates = [explicit, getattr(original_llm, "client", None)]
    seen: set[int] = set()
    for candidate in candidates:
        current = candidate
        for _ in range(4):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            # Check the instance dictionary so __getattr__ forwarding on a
            # wrapper cannot make a forwarded false flag look writable here.
            if isinstance(vars(current).get("_membind_physical_admission_enabled"), bool):
                return current
            current = getattr(current, "_inner", None)
    return None


def _materialize(
    root: Path,
    *,
    authority: Mapping[str, Any],
    workload_manifest: Any,
    frozen_config: Mapping[str, Any],
    environment: Mapping[str, Any],
    preflight: Mapping[str, Any],
    identity: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    method_boundary = result.get("method_boundary", "MEMBIND_CORE")
    artifact_method = str(result.get("artifact_method", "V6_1"))
    if root.exists() and any(root.iterdir()):
        raise V61MABError("V6.1 artifact root is not fresh")
    root.mkdir(parents=True, exist_ok=True)
    authority_body = {key: value for key, value in authority.items() if key != "contexts"}
    _write_json(root / "dataset_authority.json", authority_body)
    manifest_jsonl = str(workload_manifest.jsonl())
    manifest_hash = str(workload_manifest.manifest_sha256)
    (root / "workload_manifest.jsonl").write_text(
        manifest_jsonl if manifest_jsonl.endswith("\n") else manifest_jsonl + "\n",
        encoding="utf-8",
    )
    (root / "workload_manifest.sha256").write_text(manifest_hash + "\n", encoding="utf-8")
    _write_json(root / "frozen_config.json", frozen_config)
    _write_json(root / "environment.json", environment)
    _write_json(root / "preflight.json", preflight)
    _write_json(root / "policy.json", result["policy"])
    _write_jsonl(root / "raw_events.jsonl", result["events"])
    _write_jsonl(root / "frontier.jsonl", result["frontier_events"])
    _write_jsonl(root / "admission.jsonl", result["admission_events"])
    _write_jsonl(root / "provider_calls.jsonl", result["provider_calls"])
    _write_jsonl(root / "context_selection.jsonl", result["context_selection"])
    _write_jsonl(root / "native_trace.jsonl", result["native_trace"])
    _write_jsonl(root / "transport_trace.jsonl", result["transport_trace"])
    _write_jsonl(root / "request_identity.jsonl", result["request_identity"])
    _write_jsonl(root / "replay_binding.jsonl", result["bindings"])
    _write_json(root / "work_inventory.json", result["work_inventory"])
    _write_json(root / "lifecycle_validation.json", result["lifecycle_validation"])
    _write_json(root / "order_validation.json", result["order_validation"])
    _write_json(root / "refinement_validation.json", result["refinement_validation"])
    _write_json(root / "scheduler_evidence.json", result["scheduler_evidence"])
    _write_json(root / "shadow_db_proof.json", result["shadow_db_proof"])
    _write_json(root / "graph_diagnostics.json", result["graph_diagnostics"])
    _write_json(
        root / "metrics.json",
        {
            "method": artifact_method,
            "method_boundary": method_boundary,
            "t_build_ns": result["t_build_ns"],
            "durable_goodput": result["expected_episode_count"]
            / (result["t_build_ns"] / 1_000_000_000),
            "transport_evidence": result["transport_evidence"],
        },
    )
    members = (
        "dataset_authority.json",
        "workload_manifest.jsonl",
        "workload_manifest.sha256",
        "frozen_config.json",
        "environment.json",
        "preflight.json",
        "policy.json",
        "raw_events.jsonl",
        "frontier.jsonl",
        "admission.jsonl",
        "provider_calls.jsonl",
        "context_selection.jsonl",
        "native_trace.jsonl",
        "transport_trace.jsonl",
        "request_identity.jsonl",
        "replay_binding.jsonl",
        "work_inventory.json",
        "lifecycle_validation.json",
        "order_validation.json",
        "refinement_validation.json",
        "scheduler_evidence.json",
        "shadow_db_proof.json",
        "graph_diagnostics.json",
        "metrics.json",
    )
    return seal_construction_block(
        root,
        identity={
            **dict(identity),
            "method": artifact_method,
            "workload_hash": manifest_hash,
            "dataset_authority_sha256": authority_body.get("authority_sha256"),
        },
        required_members=members,
    )


async def run_mab_v61_construction_async(
    *,
    run_id: str,
    context_id: str,
    namespace: str,
    episodes: Sequence[Any],
    policy: V61Policy | ResourceCreditPolicy,
    runtime_builder: Callable[[], Any],
    instrumentation_installer: Callable[[Any, Any], Any],
    recorder_factory: Callable[[], Any],
    graph_exporter: Callable[[Any, list[Any], str], Any],
    output_root: str | Path,
    authority: Mapping[str, Any],
    workload_manifest: Any,
    frozen_config: Mapping[str, Any],
    environment: Mapping[str, Any],
    preflight: Mapping[str, Any],
    execution_strategy: str = STAGED_EXECUTION_STRATEGY,
    method_boundary: str = "MEMBIND_CORE",
    artifact_method: str = "V6_1",
    certified_callsites: frozenset[str] = CERTIFIED_CALLSITES,
    certified_message_transform: Callable[..., Any] | None | object = _DEFAULT_CERTIFIED_MESSAGE_TRANSFORM,
    binding_strict: bool = True,
    implementation_revision: str | None = None,
    publication_fault_injector: Callable[[str, int, Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    selected = tuple(episode_from_input(item) for item in episodes)
    if not selected or [item.source_sequence for item in selected] != list(range(len(selected))):
        raise V61MABError("MAB episode sequence is invalid")
    if execution_strategy not in {
        DUAL_STREAMING_EXECUTION_STRATEGY,
        JIT_EXECUTION_STRATEGY,
        STAGED_EXECUTION_STRATEGY,
    }:
        raise V61MABError(f"unknown V6.1 execution strategy: {execution_strategy}")
    if isinstance(policy, ResourceCreditPolicy) and not policy.is_resource_credit:
        raise V61MABError("fixed ablation must use the legacy V61Policy executor")
    if method_boundary not in {"MEMBIND_CORE", "WORK_REDUCTION_EXTENSION"}:
        raise V61MABError(f"unknown V6.1 method boundary: {method_boundary}")
    if artifact_method not in {
        "V6_1",
        "MEMBIND_CORE",
        "MEMBIND_V6_1_SHARED_BOUNDED_SO",
        "MEMBIND_RESOURCE_CREDIT_V1",
    }:
        raise V61MABError(f"unknown V6.1 artifact method: {artifact_method}")
    if not isinstance(certified_callsites, frozenset):
        certified_callsites = frozenset(certified_callsites)
    transform = (
        strip_certified_previous_context
        if certified_message_transform is _DEFAULT_CERTIFIED_MESSAGE_TRANSFORM
        else certified_message_transform
    )
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise V61MABError("V6.1 block root is not fresh")
    journal = _Journal(root.parent / f".{root.name}.v61_live_events.jsonl")
    common_events: list[dict[str, Any]] = []
    emit = _event_sink(common_events, context_id)
    admission_events: list[dict[str, Any]] = []
    frontier_events: list[dict[str, Any]] = []
    provider_calls: list[dict[str, Any]] = []
    shadow_db_attempts: list[dict[str, Any]] = []
    publication_state: dict[int, dict[str, Any]] = journal.committed_publications()
    runtime: Any = None
    graphiti: Any = None
    instrumentation: Any = None
    recorder: Any = None
    shadow_guard_restore: Callable[[], None] = lambda: None
    auxiliary_transport_restores: list[Callable[[], None]] = []
    closed = False

    def append_live(row: Mapping[str, Any], *, durable: bool = False) -> None:
        journal.append(dict(row), durable=durable)

    def admission_sink(row: dict[str, Any]) -> None:
        admission_events.append(dict(row))
        append_live({"channel": "admission", **row})

    def frontier_sink(row: dict[str, Any]) -> None:
        frontier_events.append(dict(row))
        if row.get("event") in {
            "PREPARE_READY",
            "PREPARATION_FRONTIER_DURABLE",
            "PREPARATION_STAGE_DURABLE",
            "NATIVE_START",
            "PUBLICATION_DURABLE",
            "PREPARE_FAILURE",
            "NATIVE_FAILURE",
        }:
            append_live(
                {"channel": "frontier", **row},
                durable=row.get("event")
                in {
                    "PREPARATION_STAGE_DURABLE",
                    "PUBLICATION_DURABLE",
                    "PREPARE_FAILURE",
                    "NATIVE_FAILURE",
                },
            )

    def provider_sink(row: dict[str, Any]) -> None:
        if row.get("event") == "V61_PROVIDER_CALL":
            provider_calls.append(dict(row))
        append_live(
            {"channel": "provider", **row},
            durable=row.get("status") == "failure"
            or row.get("event") == "V61_NATIVE_CONNECTION_RETRY",
        )

    async def close_runtime() -> None:
        nonlocal closed, instrumentation
        if closed:
            return
        shadow_guard_restore()
        for restore in reversed(auxiliary_transport_restores):
            restore()
        auxiliary_transport_restores.clear()
        if instrumentation is not None:
            instrumentation.restore()
            instrumentation = None
        if runtime is not None:
            await close_local_u0_runtime(runtime)
        closed = True

    try:
        from graphiti_core.utils.maintenance.edge_operations import extract_edges
        from graphiti_core.utils.maintenance.node_operations import extract_nodes

        runtime = await _maybe_await(runtime_builder())
        graphiti = runtime.graphiti
        recorder = recorder_factory()
        instrumentation = instrumentation_installer(graphiti, recorder)
        shadow_guard_restore = _install_shadow_db_guard(graphiti.driver, shadow_db_attempts)
        formal_start = time.monotonic_ns()
        emit("FORMAL_START", stamp=formal_start)
        for sequence in range(len(selected)):
            emit("SUBMIT", sequence)
        publication_frontier_ref = {"value": -1}
        preparation_admission_frontier_ref = {"value": -1}
        store = TranscriptStore()
        capacity = CapacityAuthority.from_protocol_runtime(runtime)
        arbiter = ForegroundAdmissionArbiter(
            capacity,
            policy=policy,
            event_sink=admission_sink,
            phase_isolated=execution_strategy == DUAL_STREAMING_EXECUTION_STRATEGY,
            # r31 showed that admitting a third bootstrap request dilates the
            # source-0 critical path more than it closes the future-work gap.
            # Keep borrowing available only as an explicit admission ablation.
            bootstrap_future_borrow=False,
            name=(
                "v6.1-dual-phase-isolated"
                if execution_strategy == DUAL_STREAMING_EXECUTION_STRATEGY
                else "v6.1-shared-provider"
            ),
        )
        original_llm = runtime.llm_client
        routed_client = _resolve_routed_client(runtime, original_llm)
        if routed_client is not None:
            auxiliary_transport_restores.append(
                install_routed_physical_admission(
                    routed_client,
                    arbiter=arbiter,
                    durable_frontier=lambda: preparation_admission_frontier_ref["value"],
                    token_counter=local_prompt_token_count,
                )
            )
        extraction_diagnostics = getattr(
            original_llm, "_membind_extraction_diagnostics", None
        )
        if not isinstance(extraction_diagnostics, list):
            raise V61MABError("V6.1 extraction diagnostics seam is unavailable")
        if method_boundary == "WORK_REDUCTION_EXTENSION":
            auxiliary_transport_restores.append(
                install_edge_invalidation_predicate_pushdown(extraction_diagnostics)
            )
        try:
            source_hash = hashlib.sha256(inspect.getsource(type(original_llm)).encode()).hexdigest()
        except (OSError, TypeError):
            source_hash = "unknown"
        client_identity = {
            "class": f"{type(original_llm).__module__}.{type(original_llm).__qualname__}",
            "source_hash": source_hash,
        }
        capture = V61ProviderClient(
            original_llm,
            store=store,
            arbiter=arbiter,
            mode="capture",
            durable_frontier=lambda: preparation_admission_frontier_ref["value"],
            client_identity=client_identity,
            token_counter=local_prompt_token_count,
            certified_callsites=certified_callsites,
            certified_message_transform=transform,
            native_message_transform=None,
            binding_strict=binding_strict,
            event_sink=provider_sink,
        )
        replay = V61ProviderClient(
            original_llm,
            store=store,
            arbiter=arbiter,
            mode="replay",
            durable_frontier=lambda: preparation_admission_frontier_ref["value"],
            client_identity=client_identity,
            token_counter=local_prompt_token_count,
            certified_callsites=certified_callsites,
            certified_message_transform=transform,
            # Core must preserve Native's non-certified provider context and
            # work.  Incremental summary context is a work-changing extension.
            native_message_transform=(
                incremental_native_summary_context
                if method_boundary == "WORK_REDUCTION_EXTENSION"
                else None
            ),
            binding_strict=binding_strict,
            event_sink=provider_sink,
        )

        class Multiplex:
            async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
                region, _source = current_provider_scope()
                if region is None:
                    raise V61MABError("provider call outside V6.1 region")
                return await (capture if region == "PREPARE" else replay).generate_response(
                    messages, **kwargs
                )

            def __getattr__(self, name: str) -> Any:
                return getattr(replay, name)

        multiplex = Multiplex()
        # Preserve method-level diagnostics installed on the concrete local
        # client when Graphiti is swapped to the capture/replay facade.  The
        # lists are shared, so entries remain visible after each physical call.
        multiplex._membind_extraction_diagnostics = getattr(
            original_llm, "_membind_extraction_diagnostics", []
        )
        multiplex._membind_semantic_shortcuts = getattr(
            original_llm, "_membind_semantic_shortcuts", []
        )
        for attribute in (
            "_membind_node_provenance_authority",
            "_membind_entity_partition_sources_by_scope",
            "_membind_entity_partition_hints_by_scope",
        ):
            value = getattr(original_llm, attribute, None)
            if value is not None:
                setattr(multiplex, attribute, value)
        graphiti.llm_client = multiplex
        graphiti.clients.llm_client = multiplex
        # Graphiti's OpenAI reranker calls the shared AsyncOpenAI transport
        # directly, bypassing ``generate_response``.  Install the guard on
        # every distinct local transport so these auxiliary provider calls are
        # admitted and accounted for without double-wrapping normal LLM calls.
        auxiliary_transports: list[Any] = []
        for candidate in (
            getattr(original_llm, "client", None),
            getattr(getattr(graphiti, "cross_encoder", None), "client", None),
        ):
            if candidate is not None and all(candidate is not item for item in auxiliary_transports):
                auxiliary_transports.append(candidate)
        for transport in auxiliary_transports:
            auxiliary_transport_restores.append(
                install_auxiliary_transport_guard(
                    transport,
                    arbiter=arbiter,
                    token_counter=local_prompt_token_count,
                    event_sink=provider_sink,
                )
            )

        async def prepare(sequence: int) -> dict[str, Any]:
            episode = selected[sequence]
            node_episode = _episode_node(episode, namespace=namespace)
            previous = [
                _episode_node(item, namespace=namespace, uuid_value=f"prep-{item.source_sequence}")
                for item in _native_previous_window(selected, sequence)
            ]
            def source_class() -> AdmissionClass:
                frontier = int(preparation_admission_frontier_ref["value"])
                return (
                    AdmissionClass.FRONTIER_PREPARE
                    if int(sequence) == frontier + 1
                    else AdmissionClass.FUTURE_PREPARE
                )

            lease = await arbiter.acquire_source_lease(
                source_class(),
                source_sequence=sequence,
                class_resolver=source_class,
            )
            try:
                with recorder.episode_scope(run_id, episode.name, sequence):
                    with _shadow_source_scope(sequence):
                        with provider_scope(region="PREPARE", source_sequence=sequence):
                            nodes, index_map = await extract_nodes(
                                graphiti.clients, node_episode, previous, None, None, None
                            )
                            edges = await extract_edges(
                                graphiti.clients,
                                node_episode,
                                nodes,
                                previous,
                                {("Entity", "Entity"): []},
                                namespace,
                                None,
                                None,
                            )
            finally:
                await arbiter.release_source_lease(lease)
            return {
                "source_sequence": sequence,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "node_index_count": len(index_map),
            }

        async def publish(sequence: int, _prepared: Any) -> Any:
            episode = selected[sequence]
            # Keep the stable local publication identity separate from
            # Graphiti's ``uuid`` parameter.  In graphiti-core 0.29.3 a
            # supplied UUID calls EpisodicNode.get_by_uuid() and raises
            # NodeNotFoundError for a fresh namespace; it is not a create key.
            idempotency_key = _mab_publication_idempotency_key(
                episode, namespace=namespace
            )
            publication_kwargs = _mab_graphiti_kwargs(
                episode, namespace=namespace, include_uuid=False
            )
            prior = publication_state.get(sequence)
            if prior is not None:
                if prior.get("idempotency_key") != idempotency_key:
                    raise V61MABError("source publication idempotency key changed on re-entry")
                emit("PUBLICATION_DURABLE", sequence, time.monotonic_ns())
                emit("NATIVE_ENTER", sequence)
                append_live(
                    {
                        "channel": "common",
                        "event": "PUBLICATION_REUSED",
                        "source_sequence": sequence,
                        "idempotency_key": idempotency_key,
                        "recovered_from": prior.get("event"),
                    },
                    durable=True,
                )
                # Reconstruct the certified Native extraction ledger without
                # invoking Graphiti publication.  A prior commit is trusted as
                # a local idempotency record only.  No claim is made that this
                # file and Neo4j form one atomic transaction; a torn commit
                # without a journal record is replayed at least once.
                node_episode = _episode_node(episode, namespace=namespace)
                previous = [
                    _episode_node(
                        item,
                        namespace=namespace,
                        uuid_value=f"prep-{item.source_sequence}",
                    )
                    for item in _native_previous_window(selected, sequence)
                ]
                with recorder.episode_scope(run_id, episode.name, sequence):
                    with provider_scope(region="NATIVE", source_sequence=sequence):
                        with NativeBindingScope(
                            store, source_sequence=sequence, strict=bool(binding_strict)
                        ):
                            await extract_nodes(
                                graphiti.clients,
                                node_episode,
                                previous,
                                None,
                                None,
                                None,
                            )
                            await extract_edges(
                                graphiti.clients,
                                node_episode,
                                [],
                                previous,
                                {("Entity", "Entity"): []},
                                namespace,
                                None,
                                None,
                            )
                # The journal is the local publication authority after a clean
                # committed record.  The executor only needs an opaque result;
                # no exactly-once or cross-system durable reconciliation claim
                # is made here.
                return {"recovered": True, "idempotency_key": idempotency_key}
            emit("NATIVE_ENTER", sequence)
            append_live(
                {
                    "channel": "common",
                    "event": "PUBLICATION_BEGIN",
                    "source_sequence": sequence,
                    "idempotency_key": idempotency_key,
                    "source_hash": episode.source_hash,
                },
                durable=True,
            )
            if publication_fault_injector is not None:
                injected = publication_fault_injector(
                    "before_db_write", sequence, publication_kwargs
                )
                await _maybe_await(injected)
            try:
                with recorder.episode_scope(run_id, episode.name, sequence):
                    with provider_scope(region="NATIVE", source_sequence=sequence):
                        with NativeBindingScope(
                            store, source_sequence=sequence, strict=bool(binding_strict)
                            ):
                                result = await graphiti.add_episode(**publication_kwargs)
            except BaseException as exc:
                emit("NATIVE_FAILURE", sequence, time.monotonic_ns())
                append_live(
                    {
                        "channel": "common",
                        "event": "NATIVE_FAILURE",
                        "source_sequence": sequence,
                        "idempotency_key": idempotency_key,
                        "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    },
                    durable=True,
                )
                raise
            if publication_fault_injector is not None:
                # This is the irreducible cross-system fault window: Graphiti's
                # Neo4j transaction has returned, but the local durable journal
                # has not recorded the publication. Re-entry reuses the stable
                # local idempotency key while omitting Graphiti's UUID lookup;
                # it can therefore replay at least once but cannot infer an
                # atomic commit across Neo4j and this file.
                injected = publication_fault_injector(
                    "after_db_commit_before_journal", sequence, publication_kwargs
                )
                await _maybe_await(injected)
            result_digest = hashlib.sha256(
                json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode(
                    "utf-8"
                )
            ).hexdigest()
            append_live(
                {
                    "channel": "common",
                    "event": "PUBLICATION_COMMITTED",
                    "source_sequence": sequence,
                    "idempotency_key": idempotency_key,
                    "source_hash": episode.source_hash,
                    "result_sha256": result_digest,
                },
                durable=True,
            )
            publication_state[sequence] = {
                "idempotency_key": idempotency_key,
                "result": result,
                "event": "PUBLICATION_COMMITTED",
                "result_sha256": result_digest,
            }
            if publication_fault_injector is not None:
                injected = publication_fault_injector(
                    "after_commit", sequence, publication_kwargs
                )
                await _maybe_await(injected)
            durable = time.monotonic_ns()
            publication_frontier_ref["value"] = sequence
            if execution_strategy in {
                DUAL_STREAMING_EXECUTION_STRATEGY,
                JIT_EXECUTION_STRATEGY,
            }:
                preparation_admission_frontier_ref["value"] = sequence
            emit("PUBLICATION_DURABLE", sequence, durable)
            append_live(
                {
                    "channel": "common",
                    "event": "PUBLICATION_DURABLE",
                    "source_sequence": sequence,
                    "idempotency_key": idempotency_key,
                    "monotonic_ns": durable,
                }
            )
            return result

        executor_common = {
            "authority": capacity,
            "policy": policy,
            "admission": arbiter,
            "event_sink": frontier_sink,
        }
        if isinstance(policy, ResourceCreditPolicy):
            execution = await run_resource_credit_frontier_history_async(
                len(selected),
                prepare,
                publish,
                execution_strategy=execution_strategy,
                **executor_common,
            )
        elif execution_strategy == STAGED_EXECUTION_STRATEGY:
            execution = await run_staged_frontier_history_async(
                len(selected),
                prepare,
                publish,
                preparation_frontier_sink=lambda sequence: (
                    preparation_admission_frontier_ref.__setitem__("value", sequence)
                ),
                **executor_common,
            )
        else:
            execution = await run_jit_frontier_history_async(
                len(selected),
                prepare,
                publish,
                execution_strategy=execution_strategy,
                **executor_common,
            )
        if execution.durable_frontier != len(selected) - 1:
            raise V61MABError("V6.1 durable frontier is incomplete")
        logical = store.summary()
        if logical["duplicates"] or logical["unconsumed"]:
            raise V61MABError("V6.1 replay accounting is incomplete")

        certified = capture.certified_callsites
        shadow = {
            (
                int(row["source_sequence"]),
                str(row["public_summary"]["callsite"]),
                int(row["public_summary"]["ordinal"]),
            ): row
            for row in capture.observations
            if row["public_summary"].get("callsite") in certified
        }
        native = {
            (
                int(row["source_sequence"]),
                str(row["public_summary"]["callsite"]),
                int(row["public_summary"]["ordinal"]),
            ): row
            for row in replay.observations
            if row["public_summary"].get("callsite") in certified
        }
        comparisons: list[dict[str, Any]] = []
        bindings: list[dict[str, Any]] = []
        provider_by_key: dict[tuple[int, str, int], dict[str, Any]] = {}
        for row in provider_calls:
            callsite = row.get("callsite")
            ordinal = row.get("ordinal")
            sequence = row.get("source_sequence")
            if not isinstance(callsite, str) or not isinstance(ordinal, int) or not isinstance(sequence, int):
                continue
            key = (sequence, callsite, ordinal)
            # Prefer the Native row.  A fallback is deliberately replay=False;
            # an exact certified consume is replay=True.
            if row.get("region") == "NATIVE" or key not in provider_by_key:
                provider_by_key[key] = row
        for key in sorted(set(shadow) | set(native)):
            if key not in shadow or key not in native:
                comparisons.append({"key": key, "match": False, "reason": "missing_side"})
                if key in native and key not in shadow:
                    right = native[key]
                    provider_row = provider_by_key.get(key, {})
                    bindings.append(
                        {
                            "source_sequence": key[0],
                            "callsite": key[1],
                            "ordinal_within_episode": key[2],
                            "request_identity_hash": None,
                            "prepared_response_hash": None,
                            "native_request_hash": right["public_summary"]["digest"],
                            "native_response_hash": right["response_sha256"],
                            "capture_count": 0,
                            "consume_count": 0,
                            "discard_count": 0,
                            "match_status": "MISSING_FRESH_FALLBACK",
                            "fallback_type": "missing",
                            "external_transport_attempted_during_replay": False,
                            "external_transport_attempted": True,
                            "transport_attempt_count": int(
                                provider_row.get("transport_attempt_count", 0)
                            ),
                        }
                    )
                continue
            left = shadow[key]
            right = native[key]
            comparison = compare_request_observations(left["observation"], right["observation"])
            response_match = left["response_sha256"] == right["response_sha256"]
            comparison = {**comparison, "response_match": response_match}
            comparisons.append({"key": key, **comparison})
            provider_row = provider_by_key.get(key, {})
            fallback_type = provider_row.get("fallback_type")
            is_fallback = fallback_type in {"mismatch", "missing"}
            match_status = (
                "MISMATCH_FRESH_FALLBACK"
                if fallback_type == "mismatch"
                else "MISSING_FRESH_FALLBACK"
                if fallback_type == "missing"
                else "EXACT_MATCH"
                if comparison["match"] and response_match
                else "MISMATCH"
            )
            bindings.append(
                {
                    "source_sequence": key[0],
                    "callsite": key[1],
                    "ordinal_within_episode": key[2],
                    "request_identity_hash": left["public_summary"]["digest"],
                    "prepared_response_hash": left["response_sha256"],
                    "native_request_hash": right["public_summary"]["digest"],
                    "native_response_hash": right["response_sha256"],
                    "capture_count": 1,
                    "consume_count": 0 if is_fallback else 1,
                    "discard_count": 1 if fallback_type == "mismatch" else 0,
                    "match_status": match_status,
                    "fallback_type": fallback_type,
                    "external_transport_attempted_during_replay": False,
                    "external_transport_attempted": bool(is_fallback),
                    "transport_attempt_count": int(
                        provider_row.get("transport_attempt_count", 0)
                    ),
                }
            )
        if not bindings:
            raise V61MABError("V6.1 produced no certified replay bindings")
        refinement = validate_v6_bindings(bindings)
        proof = {
            "request": validate_request_comparisons(comparisons),
            "replay": validate_replay_accounting(logical),
            "provider": provider_proof(
                admission_events,
                capacity=capacity.value,
                future_cap=(
                    policy.future_cap
                    if hasattr(policy, "future_cap")
                    else capacity.value
                ),
                arbiter_instance_id=arbiter.instance_id,
                token_budget=arbiter.token_budget,
                phase_isolated=arbiter.phase_isolated,
                bootstrap_future_borrow=arbiter.bootstrap_future_borrow,
            ),
            "shared_arbiter": {
                "status": "PASS"
                if capture.arbiter is replay.arbiter is arbiter
                and execution.arbiter_instance_id == arbiter.instance_id
                else "FAIL",
                "arbiter_instance_id": arbiter.instance_id,
            },
        }
        if proof["shared_arbiter"]["status"] != "PASS":
            raise V61MABError("V6.1 provider and scheduler arbiters differ")
        if shadow_db_attempts:
            raise V61MABError("shadow database proof failed")

        seal_time = time.monotonic_ns()
        emit("CONSTRUCTION_SEAL", stamp=seal_time)
        canonical = await _maybe_await(graph_exporter(graphiti, list(selected), namespace))
        if not isinstance(canonical, Mapping):
            raise V61MABError("canonical graph export is invalid")
        transport_rows = _transport_attempt_rows(recorder)
        external_provider_calls = [
            row for row in provider_calls if row.get("replay") is False
        ]
        replay_provider_calls = [
            row for row in provider_calls if row.get("replay") is True
        ]
        transport_retry_attempts = sum(
            int(row.get("transport_retry_count", 0)) for row in external_provider_calls
        )
        replay_transport_attempts = sum(
            int(row.get("transport_attempt_count", 0)) for row in replay_provider_calls
        )
        if replay_transport_attempts:
            raise V61MABError(
                "certified replay unexpectedly reached the external transport: "
                f"attempts={replay_transport_attempts}"
            )
        auxiliary_provider_calls = [
            row for row in external_provider_calls if row.get("auxiliary") is True
        ]
        managed_provider_calls = [
            row for row in external_provider_calls if row.get("auxiliary") is not True
        ]
        unverified_provider_attempts = sum(
            int(row.get("transport_attempt_count", 0))
            for row in managed_provider_calls
            if row.get("transport_attempts_observed") is False
        )
        expected_instrumented_transport_attempts = sum(
            int(row.get("transport_attempt_count", 0)) for row in managed_provider_calls
            if row.get("transport_attempts_observed") is not False
        )
        auxiliary_transport_attempts = sum(
            int(row.get("transport_attempt_count", 0)) for row in auxiliary_provider_calls
        )
        observed_instrumented_transport_attempts = len(transport_rows)
        if observed_instrumented_transport_attempts != expected_instrumented_transport_attempts:
            raise V61MABError(
                "managed provider/transport attempt accounting is inconsistent: "
                f"observed={observed_instrumented_transport_attempts} "
                f"expected={expected_instrumented_transport_attempts}"
            )
        expected_transport_attempts = sum(
            int(row.get("transport_attempt_count", 0)) for row in external_provider_calls
        )
        observed_transport_attempts = (
            observed_instrumented_transport_attempts
            + auxiliary_transport_attempts
            + unverified_provider_attempts
        )
        transport_expansion_attempts = sum(
            max(
                0,
                int(row.get("transport_attempt_count", 0))
                - 1
                - int(row.get("transport_retry_count", 0)),
            )
            for row in external_provider_calls
        )
        context_selection = [
            *capture.context_selection_events,
            *replay.context_selection_events,
        ]
        capture_context_selection = [
            row
            for row in context_selection
            if row.get("mode") == "capture"
            and row.get("event") == "CERTIFIED_CONTEXT_SELECTION"
        ]
        certified_context_selection = [
            row
            for row in context_selection
            if row.get("event") == "CERTIFIED_CONTEXT_SELECTION"
        ]
        summary_context_selection = [
            row
            for row in context_selection
            if row.get("event") == "NATIVE_SUMMARY_CONTEXT_SELECTION"
        ]
        inventory = {
            "expected_episode_count": len(selected),
            "submitted_count": len(selected),
            "completed_count": len(selected),
            **span_work_inventory(list(getattr(recorder, "records", ()) or ())),
            **extraction_work_inventory(
                list(
                    getattr(runtime.llm_client, "_membind_extraction_diagnostics", ()) or ()
                )
            ),
            "provider_wrapper_calls": len(provider_calls),
            "provider_external_logical_calls": len(external_provider_calls),
            "provider_replay_logical_calls": len(replay_provider_calls),
            "certified_context_selection_events": len(certified_context_selection),
            "certified_context_selection_capture_events": len(capture_context_selection),
            "certified_previous_context_chars_removed": sum(
                int(row.get("previous_context_chars_removed", 0) or 0)
                for row in capture_context_selection
            ),
            "incremental_summary_context_selection_events": len(summary_context_selection),
            "incremental_summary_previous_context_chars_removed": sum(
                int(row.get("previous_context_chars_removed", 0) or 0)
                for row in summary_context_selection
            ),
            "incremental_summary_existing_summary_chars_retained": sum(
                int(row.get("existing_summary_chars_retained", 0) or 0)
                for row in summary_context_selection
            ),
            "instrumented_transport_attempts": observed_instrumented_transport_attempts,
            "auxiliary_transport_attempts": auxiliary_transport_attempts,
            "unverified_provider_attempts": unverified_provider_attempts,
            "transport_retry_attempts": transport_retry_attempts,
            "transport_true_retry_attempts": transport_retry_attempts,
            "transport_expansion_attempts": transport_expansion_attempts,
            "compatibility_expansion_attempts": transport_expansion_attempts,
            "expected_transport_attempts_from_provider": expected_transport_attempts,
        }
        _assert_core_context_integrity(artifact_method, inventory)
        inventory["transport_attempts"] = observed_transport_attempts
        if observed_transport_attempts != expected_transport_attempts:
            raise V61MABError(
                "provider/transport attempt accounting is inconsistent: "
                f"observed={observed_transport_attempts} expected={expected_transport_attempts}"
            )
        lifecycle_validation = validate_block_trace(
            common_events,
            expected_source_count=len(selected),
            method="V6",
            context_id=context_id,
        )
        order_validation = validate_order_contract(
            common_events, expected_source_count=len(selected), method="V6"
        )
        result = {
            "schema_version": "membind.v6.1.mab-live-block.v1",
            "status": "PASS",
            "method": "V6_1",
            "artifact_method": artifact_method,
            "method_boundary": method_boundary,
            "context_id": context_id,
            "namespace": namespace,
            "policy": policy.to_dict(),
            "execution_strategy": execution.execution_strategy,
            "expected_episode_count": len(selected),
            "events": common_events,
            "frontier_events": frontier_events,
            "admission_events": admission_events,
            "provider_calls": provider_calls,
            "context_selection": context_selection,
            "native_trace": _episode_envelopes(recorder, run_id, selected),
            "transport_trace": transport_rows,
            "transport_evidence": _transport_evidence_summary(transport_rows),
            "request_identity": [
                {
                    **dict(row["public_summary"]),
                    "mode": row["mode"],
                    "region": row["region"],
                    "response_sha256": row["response_sha256"],
                    "arbiter_instance_id": row["arbiter_instance_id"],
                }
                for row in (*capture.observations, *replay.observations)
            ],
            "bindings": bindings,
            "work_inventory": inventory,
            "lifecycle_validation": lifecycle_validation,
            "order_validation": order_validation,
            "refinement_validation": {**refinement, "proof": proof},
            "scheduler_evidence": {
                "method_boundary": method_boundary,
                "execution_strategy": execution.execution_strategy,
                "policy": policy.to_dict(),
                "max_started_ahead": execution.max_started_ahead,
                "preparation_durable_frontier": execution.preparation_durable_frontier,
                "preparation_stage_barrier": execution.stage_barrier,
                "preparation_intervals": execution.preparation_intervals,
                "native_intervals": execution.native_intervals,
                "frontier_wait_intervals": execution.frontier_wait_intervals,
                "provider_timing": {
                    "queue_wait": _nanosecond_summary(
                        [
                            int(row.get("queue_wait_ns", 0))
                            for row in provider_calls
                            if row.get("replay") is False
                        ]
                    ),
                    "service": _nanosecond_summary(
                        [
                            int(row.get("service_ns", 0))
                            for row in provider_calls
                            if row.get("replay") is False
                        ]
                    ),
                    "frontier_wait": _nanosecond_summary(
                        [int(row["duration_ns"]) for row in execution.frontier_wait_intervals]
                    ),
                },
                "live_journal": {
                    "policy": "bounded_group_commit_v1",
                    "sync_interval_ns": _Journal.SYNC_INTERVAL_NS,
                    "durable_events": [
                        "PUBLICATION_DURABLE",
                        "PREPARE_FAILURE",
                        "NATIVE_FAILURE",
                        "V61_NATIVE_CONNECTION_RETRY",
                        "provider status=failure",
                        "journal close",
                    ],
                },
                "arbiter": arbiter.evidence(),
            },
            "shadow_db_proof": {
                "status": "PASS",
                "blocked_attempt_count": 0,
                "contract": "no driver.execute_query call is allowed in PREPARE scope",
            },
            "graph_diagnostics": dict(canonical),
            "t_build_ns": lifecycle_validation["t_build_ns"],
            **reliability_identity(),
        }
        seal = _materialize(
            root,
            authority=authority,
            workload_manifest=workload_manifest,
            frozen_config=frozen_config,
            environment=environment,
            preflight=preflight,
            identity={
                "method": artifact_method,
                "context_id": context_id,
                "namespace": namespace,
                "run_id": run_id,
                "policy": policy.to_dict(),
                "execution_strategy": execution.execution_strategy,
                "method_boundary": method_boundary,
                **(
                    {"implementation_revision": implementation_revision}
                    if implementation_revision is not None
                    else {}
                ),
            },
            result=result,
        )
        await close_runtime()
        journal.close()
        return {**result, "construction_seal": seal}
    except BaseException:
        try:
            await close_runtime()
        finally:
            journal.close()
        raise


__all__ = ["V61MABError", "run_mab_v61_construction_async"]
