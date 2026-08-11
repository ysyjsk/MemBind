"""Offline C1 requalification for the supplemental C2 measurement path.

This module exercises the base Native-characterization instrumentation and the
C2-only measurement adapter together.  It never constructs a live Graphiti
client and fails closed if the qualification attempts network access.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import gc
import hashlib
import importlib.metadata
import json
import math
import os
import socket
import sys
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping, Sequence

from native_characterization_c2_measurement import install_c2_measurement_adapter
from native_characterization_instrumentation import (
    install_native_characterization_instrumentation,
)
from native_characterization_tracing import TraceRecorder


PAIR_ORDERS = tuple(
    ("trace_off", "trace_on") if index % 2 == 0 else ("trace_on", "trace_off")
    for index in range(5)
)
TIMED_EPISODES_PER_ARM = 10
UNMEASURED_WARMUP_ORDER = ("trace_off", "trace_on")
_PINNED_GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
_SCHEMA_VERSION = "membind.native-characterization-c2-measurement-qualification.v1"
_FORBIDDEN_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "messages",
    "parameters",
    "prompt",
    "query",
    "raw_prompt",
    "raw_response",
    "response",
    "secret",
    "token",
}
_NETWORK_AUDIT_EVENTS = {
    "socket.bind",
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyaddr",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "socket.getnameinfo",
    "socket.sendmsg",
    "socket.sendto",
}
_NETWORK_AUDIT_SINK: contextvars.ContextVar[list[str] | None] = (
    contextvars.ContextVar("c2_measurement_qualification_network_guard", default=None)
)
_AUDIT_HOOK_INSTALLED = False


class MeasurementQualificationError(RuntimeError):
    """Sanitized failure in the offline measurement-path qualification."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_FIELDS or "private_" in normalized:
                raise ValueError(f"forbidden qualification field: {key}")
            _assert_sanitized(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_sanitized(child)
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise ValueError("qualification payload contains a non-JSON scalar")


def classify_overhead(overhead_ratio: float) -> str:
    """Apply the C1 guardrail without weakening a blocking observation."""

    ratio = float(overhead_ratio)
    if not math.isfinite(ratio):
        raise ValueError("overhead ratio must be finite")
    if ratio <= 0.02:
        return "clean_pass"
    if ratio <= 0.05:
        return "warning_continue"
    return "block_repair"


def _network_audit_hook(event: str, _arguments: tuple[Any, ...]) -> None:
    sink = _NETWORK_AUDIT_SINK.get()
    if sink is not None and event in _NETWORK_AUDIT_EVENTS:
        sink.append(event)
        raise MeasurementQualificationError("network_attempt_denied")


def _ensure_audit_hook() -> None:
    global _AUDIT_HOOK_INSTALLED
    if not _AUDIT_HOOK_INSTALLED:
        sys.addaudithook(_network_audit_hook)
        _AUDIT_HOOK_INSTALLED = True


@contextmanager
def _deny_network() -> Iterator[list[str]]:
    """Combine interpreter auditing with guards on common socket entry points."""

    _ensure_audit_hook()
    attempts: list[str] = []
    token = _NETWORK_AUDIT_SINK.set(attempts)
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def denied_connect(*_args: Any, **_kwargs: Any) -> Any:
        attempts.append("socket.socket.connect")
        raise MeasurementQualificationError("network_attempt_denied")

    def denied_connect_ex(*_args: Any, **_kwargs: Any) -> Any:
        attempts.append("socket.socket.connect_ex")
        raise MeasurementQualificationError("network_attempt_denied")

    def denied_create_connection(*_args: Any, **_kwargs: Any) -> Any:
        attempts.append("socket.create_connection")
        raise MeasurementQualificationError("network_attempt_denied")

    socket.socket.connect = denied_connect
    socket.socket.connect_ex = denied_connect_ex
    socket.create_connection = denied_create_connection
    try:
        yield attempts
    finally:
        socket.create_connection = original_create_connection
        socket.socket.connect_ex = original_connect_ex
        socket.socket.connect = original_connect
        _NETWORK_AUDIT_SINK.reset(token)


def _cpu_work(label: str, work_units: int) -> str:
    digest = hashlib.sha256(label.encode("ascii")).digest()
    for _ in range(work_units):
        digest = hashlib.sha256(digest).digest()
    return digest.hex()


class _Embedder:
    def __init__(self, events: list[str], work_units: int) -> None:
        self.events = events
        self.work_units = work_units

    async def create(self, input_data: Any) -> list[float]:
        self.events.append("embedding-create")
        digest = _cpu_work("embedding-create", self.work_units)
        return [float(int(digest[:2], 16)), float(int(digest[2:4], 16))]

    async def create_batch(self, input_data_list: Sequence[Any]) -> list[list[float]]:
        self.events.append("embedding-create-batch")
        digest = _cpu_work("embedding-create-batch", self.work_units)
        vector = [float(int(digest[:2], 16)), float(int(digest[2:4], 16))]
        return [list(vector) for _ in input_data_list]


class _OfflineFixture:
    """Deterministic Graphiti-shaped call graph covering both patch layers."""

    def __init__(self, work_units: int) -> None:
        self.work_units = work_units
        self.events: list[str] = []
        self.graph_state: list[str] = []
        self.phase_module = SimpleNamespace()
        self.node_module = SimpleNamespace()
        self.edge_module = SimpleNamespace()
        embedder = _Embedder(self.events, work_units)

        async def transport_create(*_args: Any, **_kwargs: Any) -> Any:
            self.events.append("llm-transport")
            _cpu_work("llm-transport", work_units)
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3)
            )

        async def generate_response(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("llm-logical")
            await llm.client.chat.completions.create(request_kind="fixture")
            return _cpu_work("llm-logical", work_units)

        llm = SimpleNamespace(
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=transport_create)
                )
            ),
            generate_response=generate_response,
        )
        clients = SimpleNamespace(embedder=embedder, llm_client=llm, driver=None)

        async def node_candidate_search(
            bound_clients: Any, extracted_nodes: Sequence[Any]
        ) -> list[list[Any]]:
            self.events.append("node-candidate-search")
            await bound_clients.embedder.create_batch(
                [node.name for node in extracted_nodes]
            )
            _cpu_work("node-candidate-search", work_units)
            return [
                [SimpleNamespace(uuid=f"node-candidate-{index}")]
                for index, _node in enumerate(extracted_nodes)
            ]

        async def edge_search(bound_clients: Any, fact: str, **kwargs: Any) -> Any:
            self.events.append("edge-candidate-search")
            await bound_clients.embedder.create(fact)
            _cpu_work("edge-candidate-search", work_units)
            return SimpleNamespace(
                edges=[SimpleNamespace(uuid="edge-candidate", fact="candidate")]
            )

        async def create_edge_embeddings(
            bound_embedder: Any, edges: Sequence[Any]
        ) -> None:
            self.events.append("edge-embedding-function")
            await bound_embedder.create_batch([edge.fact for edge in edges])

        def resolve_contradictions(
            _resolved_edge: Any, candidates: Sequence[Any]
        ) -> list[Any]:
            self.events.append("edge-invalidation")
            _cpu_work("edge-invalidation", work_units)
            return list(candidates[:1])

        async def resolve_one_edge(
            _llm_client: Any,
            extracted_edge: Any,
            _related_edges: Sequence[Any],
            existing_edges: Sequence[Any],
            _episode: Any,
            _edge_types: Any,
        ) -> tuple[Any, list[Any], list[Any]]:
            self.events.append("resolve-one-edge")
            invalidated = self.edge_module.resolve_edge_contradictions(
                extracted_edge, existing_edges
            )
            return extracted_edge, invalidated, []

        async def extract_nodes(*_args: Any, **_kwargs: Any) -> list[Any]:
            self.events.append("extract-nodes")
            _cpu_work("extract-nodes", work_units)
            return [SimpleNamespace(name="node-a", group_id="group-a")]

        async def resolve_nodes(
            bound_clients: Any, extracted_nodes: Sequence[Any], *_args: Any, **_kwargs: Any
        ) -> list[Any]:
            self.events.append("resolve-nodes")
            await self.node_module._semantic_candidate_search(
                bound_clients, extracted_nodes
            )
            _cpu_work("resolve-nodes", work_units)
            return list(extracted_nodes)

        async def extract_edges(*_args: Any, **_kwargs: Any) -> list[Any]:
            self.events.append("extract-edges")
            _cpu_work("extract-edges", work_units)
            return [
                SimpleNamespace(
                    fact="node-a relates node-b",
                    uuid="edge-a",
                    expired_at=None,
                )
            ]

        async def resolve_edges(
            bound_clients: Any,
            extracted_edges: Sequence[Any],
            episode: Any,
            _entities: Sequence[Any],
            _edge_types: Any,
            _edge_type_map: Any,
        ) -> tuple[list[Any], list[Any], list[Any]]:
            self.events.append("resolve-edges")
            await self.edge_module.create_entity_edge_embeddings(
                bound_clients.embedder, extracted_edges
            )
            related = [
                await self.edge_module.search(
                    bound_clients, edge.fact, search_filter="dedupe"
                )
                for edge in extracted_edges
            ]
            existing = [
                await self.edge_module.search(
                    bound_clients, edge.fact, search_filter="invalidation"
                )
                for edge in extracted_edges
            ]
            resolved = [
                await self.edge_module.resolve_extracted_edge(
                    bound_clients.llm_client,
                    edge,
                    related_result.edges,
                    existing_result.edges,
                    episode,
                    {},
                )
                for edge, related_result, existing_result in zip(
                    extracted_edges, related, existing, strict=True
                )
            ]
            resolved_edges = [item[0] for item in resolved]
            invalidated = [edge for item in resolved for edge in item[1]]
            await self.edge_module.create_entity_edge_embeddings(
                bound_clients.embedder, resolved_edges
            )
            return resolved_edges, invalidated, resolved_edges

        async def extract_attributes(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("extract-attributes")
            return _cpu_work("extract-attributes", work_units)

        async def execute_query(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("database")
            return _cpu_work("database", work_units)

        async def retrieve_episodes(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("retrieve-episodes")
            return _cpu_work("retrieve-episodes", work_units)

        async def publish(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("publication")
            return _cpu_work("publication", work_units)

        self.node_module._semantic_candidate_search = node_candidate_search
        self.edge_module.search = edge_search
        self.edge_module.create_entity_edge_embeddings = create_edge_embeddings
        self.edge_module.resolve_extracted_edge = resolve_one_edge
        self.edge_module.resolve_edge_contradictions = resolve_contradictions
        self.phase_module.extract_nodes = extract_nodes
        self.phase_module.resolve_extracted_nodes = resolve_nodes
        self.phase_module.extract_edges = extract_edges
        self.phase_module.resolve_extracted_edges = resolve_edges
        self.phase_module.extract_attributes_from_nodes = extract_attributes

        async def add_episode(payload: str, *, option: int) -> str:
            self.events.append("add-episode")
            previous = await graphiti.retrieve_episodes(payload)
            nodes = await self.phase_module.extract_nodes(clients, payload, previous)
            nodes = await self.phase_module.resolve_extracted_nodes(
                clients, nodes, payload, previous
            )
            edges = await self.phase_module.extract_edges(
                clients, payload, nodes, previous, {}, "group-a"
            )
            resolved = await self.phase_module.resolve_extracted_edges(
                clients, edges, payload, nodes, {}, {}
            )
            attributes = await self.phase_module.extract_attributes_from_nodes(
                clients, nodes, payload, previous, edges=resolved[0]
            )
            llm_result = await graphiti.llm_client.generate_response(
                payload, prompt_name="fixture.operation"
            )
            embedding = await graphiti.embedder.create(input_data=payload)
            database = await graphiti.driver.execute_query(payload, routing_="r")
            publication = await graphiti._process_episode_data(database)
            state = _sha256_bytes(
                canonical_bytes(
                    [option, attributes, llm_result, embedding, database, publication]
                )
            )
            self.graph_state.append(state)
            return state

        driver = SimpleNamespace(execute_query=execute_query)
        clients.driver = driver
        graphiti = SimpleNamespace(
            llm_client=llm,
            embedder=embedder,
            driver=driver,
            retrieve_episodes=retrieve_episodes,
            _process_episode_data=publish,
            add_episode=add_episode,
        )
        self.graphiti = graphiti


async def _timed_fixture_run(
    *, trace_on: bool, work_units: int, episode_count: int = 1
) -> dict[str, Any]:
    if episode_count <= 0:
        raise ValueError("episode_count must be positive")
    fixture = _OfflineFixture(work_units)
    recorder = TraceRecorder()
    base_handle = None
    adapter_handle = None
    if trace_on:
        base_handle = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
        try:
            adapter_handle = install_c2_measurement_adapter(
                fixture.graphiti,
                recorder,
                phase_module=fixture.phase_module,
                node_module=fixture.node_module,
                edge_module=fixture.edge_module,
            )
        except BaseException:
            base_handle.restore()
            raise
    try:
        start_ns = time.perf_counter_ns()
        for episode_index in range(episode_count):
            if trace_on:
                with recorder.episode_scope(
                    "c2-measurement-aa", f"fixture-{episode_index}", episode_index
                ):
                    returned = await fixture.graphiti.add_episode(
                        "fixture", option=7
                    )
            else:
                returned = await fixture.graphiti.add_episode("fixture", option=7)
        end_ns = time.perf_counter_ns()
    finally:
        if adapter_handle is not None:
            adapter_handle.restore()
        if base_handle is not None:
            base_handle.restore()
    return {
        "duration_ns": end_ns - start_ns,
        "return_sha256": _sha256_bytes(canonical_bytes(returned)),
        "event_sequence_sha256": _sha256_bytes(canonical_bytes(fixture.events)),
        "state_sha256": _sha256_bytes(canonical_bytes(fixture.graph_state)),
        "span_count": len(recorder.records),
    }


async def _gc_stable_timed_fixture_run(
    *, trace_on: bool, work_units: int
) -> dict[str, Any]:
    """Run one predeclared arm without an asymmetric cyclic-GC collection."""

    gc.collect()
    was_enabled = gc.isenabled()
    if was_enabled:
        gc.disable()
    try:
        return await _timed_fixture_run(
            trace_on=trace_on,
            work_units=work_units,
            episode_count=TIMED_EPISODES_PER_ARM,
        )
    finally:
        if was_enabled:
            gc.enable()


def _callable_identity(value: Any) -> tuple[Any, ...]:
    bound_self = getattr(value, "__self__", None)
    bound_function = getattr(value, "__func__", None)
    if bound_self is not None and bound_function is not None:
        return ("bound", id(bound_self), bound_function)
    return ("direct", value)


class _SmokeEmbedder:
    async def create(self, _input_data: Any) -> list[float]:
        return [0.0]

    async def create_batch(self, values: Sequence[Any]) -> list[list[float]]:
        return [[0.0] for _ in values]


class _SmokeGraphiti:
    def __init__(self) -> None:
        async def transport_create(*_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(usage=None)

        async def generate_response(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def execute_query(*_args: Any, **_kwargs: Any) -> None:
            return None

        self.llm_client = SimpleNamespace(
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=transport_create)
                )
            ),
            generate_response=generate_response,
        )
        self.embedder = _SmokeEmbedder()
        self.driver = SimpleNamespace(execute_query=execute_query)

    async def add_episode(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def retrieve_episodes(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def _process_episode_data(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _snapshot_targets(
    graphiti: Any, phase_module: Any, node_module: Any, edge_module: Any
) -> dict[str, tuple[Any, ...]]:
    targets = {
        **{
            f"phase.{name}": getattr(phase_module, name)
            for name in (
                "extract_nodes",
                "resolve_extracted_nodes",
                "extract_edges",
                "resolve_extracted_edges",
                "extract_attributes_from_nodes",
            )
        },
        "node.semantic-candidate-search": node_module._semantic_candidate_search,
        "edge.search": edge_module.search,
        "edge.create-embeddings": edge_module.create_entity_edge_embeddings,
        "edge.resolve-one": edge_module.resolve_extracted_edge,
        "edge.resolve-contradictions": edge_module.resolve_edge_contradictions,
        "graphiti.add-episode": graphiti.add_episode,
        "graphiti.retrieve-episodes": graphiti.retrieve_episodes,
        "graphiti.process-episode": graphiti._process_episode_data,
        "llm.generate": graphiti.llm_client.generate_response,
        "llm.transport": graphiti.llm_client.client.chat.completions.create,
        "embedder.create": graphiti.embedder.create,
        "embedder.create-batch": graphiti.embedder.create_batch,
        "driver.execute": graphiti.driver.execute_query,
    }
    return {name: _callable_identity(value) for name, value in targets.items()}


def _pinned_graphiti_alias_smoke() -> dict[str, Any]:
    import graphiti_core.graphiti as phase_module
    import graphiti_core.utils.maintenance.edge_operations as edge_module
    import graphiti_core.utils.maintenance.node_operations as node_module

    graphiti_identity = _graphiti_distribution_identity()
    graphiti_version = graphiti_identity["graphiti_version"]
    graphiti = _SmokeGraphiti()
    recorder = TraceRecorder()
    before = _snapshot_targets(graphiti, phase_module, node_module, edge_module)
    base_handle = None
    adapter_handle = None
    second_base = None
    second_adapter = None
    all_targets_patched = False
    identities_restored = False
    double_restore_idempotent = False
    reinstall_succeeds = False
    try:
        base_handle = install_native_characterization_instrumentation(
            graphiti, recorder, phase_module=phase_module
        )
        adapter_handle = install_c2_measurement_adapter(
            graphiti,
            recorder,
            phase_module=phase_module,
            node_module=node_module,
            edge_module=edge_module,
        )
        installed = _snapshot_targets(
            graphiti, phase_module, node_module, edge_module
        )
        all_targets_patched = all(installed[name] != value for name, value in before.items())
        adapter_handle.restore()
        base_handle.restore()
        restored = _snapshot_targets(graphiti, phase_module, node_module, edge_module)
        identities_restored = restored == before
        adapter_handle.restore()
        base_handle.restore()
        double_restored = _snapshot_targets(
            graphiti, phase_module, node_module, edge_module
        )
        double_restore_idempotent = double_restored == before

        second_base = install_native_characterization_instrumentation(
            graphiti, recorder, phase_module=phase_module
        )
        second_adapter = install_c2_measurement_adapter(
            graphiti,
            recorder,
            phase_module=phase_module,
            node_module=node_module,
            edge_module=edge_module,
        )
        reinstalled = _snapshot_targets(
            graphiti, phase_module, node_module, edge_module
        )
        reinstall_succeeds = all(
            reinstalled[name] != value for name, value in before.items()
        )
    finally:
        if second_adapter is not None:
            second_adapter.restore()
        if second_base is not None:
            second_base.restore()
        if adapter_handle is not None:
            adapter_handle.restore()
        if base_handle is not None:
            base_handle.restore()
    final = _snapshot_targets(graphiti, phase_module, node_module, edge_module)
    embedder_clean = not ({"create", "create_batch"} & set(graphiti.embedder.__dict__))
    all_targets_callable = all(callable(identity[-1]) for identity in final.values())
    status = (
        "pass"
        if graphiti_version == "0.29.3"
        and graphiti_identity["graphiti_commit"] == _PINNED_GRAPHITI_COMMIT
        and all_targets_callable
        and all_targets_patched
        and identities_restored
        and double_restore_idempotent
        and reinstall_succeeds
        and final == before
        and embedder_clean
        and not recorder.records
        else "fail"
    )
    return {
        "graphiti_version": graphiti_version,
        "graphiti_commit": graphiti_identity["graphiti_commit"],
        "direct_url_sha256": graphiti_identity["direct_url_sha256"],
        "status": status,
        "base_and_adapter_installed_together": all_targets_patched,
        "all_targets_callable": all_targets_callable,
        "all_targets_patched": all_targets_patched,
        "all_identities_restored": identities_restored and final == before,
        "double_restore_idempotent": double_restore_idempotent,
        "reinstall_after_restore_succeeds": reinstall_succeeds,
        "embedder_instance_attributes_removed": embedder_clean,
        "trace_record_count": len(recorder.records),
    }


def _graphiti_distribution_identity() -> dict[str, str]:
    distribution = importlib.metadata.distribution("graphiti-core")
    raw_direct_url = distribution.read_text("direct_url.json")
    if not isinstance(raw_direct_url, str):
        raise MeasurementQualificationError("graphiti_direct_url_missing")
    try:
        direct_url = json.loads(raw_direct_url)
        commit = direct_url["vcs_info"]["commit_id"]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise MeasurementQualificationError("graphiti_direct_url_invalid") from None
    if not isinstance(commit, str):
        raise MeasurementQualificationError("graphiti_revision_invalid")
    return {
        "graphiti_version": distribution.version,
        "graphiti_commit": commit,
        "direct_url_sha256": _sha256_bytes(raw_direct_url.encode("utf-8")),
    }


def _runtime_identity() -> dict[str, Any]:
    affinity = (
        sorted(int(cpu) for cpu in os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else [0]
    )
    return {
        "python_implementation": sys.implementation.name,
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "cpu_affinity": affinity,
    }


def _clock_identity() -> dict[str, Any]:
    clock = time.get_clock_info("perf_counter")
    return {
        "implementation": "time.perf_counter_ns",
        "monotonic": bool(clock.monotonic),
        "resolution_ns": float(clock.resolution * 1_000_000_000),
    }


def _source_hashes() -> dict[str, str]:
    source_root = Path(__file__).resolve().parent
    repository_root = source_root.parent
    return {
        "adapter_source_sha256": sha256_file(
            source_root / "native_characterization_c2_measurement.py"
        ),
        "base_instrumentation_source_sha256": sha256_file(
            source_root / "native_characterization_instrumentation.py"
        ),
        "c2_runner_source_sha256": sha256_file(
            source_root / "native_characterization_c2.py"
        ),
        "qualification_source_sha256": sha256_file(Path(__file__).resolve()),
        "qualification_test_sha256": sha256_file(
            repository_root
            / "tests/test_native_characterization_c2_measurement_qualification.py"
        ),
        "tracing_source_sha256": sha256_file(
            source_root / "native_characterization_tracing.py"
        ),
    }


async def run_qualification(*, work_units: int = 20_000) -> dict[str, Any]:
    """Run five alternating offline pairs plus the pinned-alias restore smoke."""

    if not isinstance(work_units, int) or isinstance(work_units, bool) or work_units <= 0:
        raise ValueError("work_units must be a positive integer")
    pairs: list[dict[str, Any]] = []
    return_hashes: set[str] = set()
    event_hashes: set[str] = set()
    state_hashes: set[str] = set()
    network_attempts: list[str]
    with _deny_network() as network_attempts:
        for mode in UNMEASURED_WARMUP_ORDER:
            await _timed_fixture_run(
                trace_on=mode == "trace_on",
                work_units=work_units,
                episode_count=1,
            )
        for pair_index, order in enumerate(PAIR_ORDERS):
            executions: dict[str, dict[str, Any]] = {}
            for mode in order:
                observed = await _gc_stable_timed_fixture_run(
                    trace_on=mode == "trace_on", work_units=work_units
                )
                executions[mode] = observed
                return_hashes.add(observed["return_sha256"])
                event_hashes.add(observed["event_sequence_sha256"])
                state_hashes.add(observed["state_sha256"])
            off_ns = executions["trace_off"]["duration_ns"]
            on_ns = executions["trace_on"]["duration_ns"]
            ratio = (on_ns - off_ns) / off_ns
            pairs.append(
                {
                    "pair_index": pair_index,
                    "execution_order": list(order),
                    "trace_off_ns": off_ns,
                    "trace_on_ns": on_ns,
                    "paired_overhead_ratio": ratio,
                    "paired_overhead_percent": ratio * 100.0,
                    "trace_off_span_count": executions["trace_off"]["span_count"],
                    "trace_on_span_count": executions["trace_on"]["span_count"],
                    "return_sha256": executions["trace_on"]["return_sha256"],
                    "event_sequence_sha256": executions["trace_on"][
                        "event_sequence_sha256"
                    ],
                    "state_sha256": executions["trace_on"]["state_sha256"],
                }
            )
        smoke = _pinned_graphiti_alias_smoke()
    smoke["network_attempt_count"] = len(network_attempts)

    ratios = [pair["paired_overhead_ratio"] for pair in pairs]
    median_ratio = sorted(ratios)[2]
    classification = classify_overhead(median_ratio)
    semantic_parity = (
        len(return_hashes) == 1
        and len(event_hashes) == 1
        and len(state_hashes) == 1
        and all(pair["trace_off_span_count"] == 0 for pair in pairs)
        and all(pair["trace_on_span_count"] > 0 for pair in pairs)
    )
    result: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_id": "native-characterization-c2-measurement-offline-qualification",
        "fixture_scope": "deterministic_offline_combined_base_and_adapter_not_c2_workload",
        "measurement_scope": "in_memory_span_wrapper_overhead_only",
        "benchmark_contract": {
            "work_units_per_operation": work_units,
            "timed_episode_count_per_arm": TIMED_EPISODES_PER_ARM,
            "unmeasured_warmup_order": list(UNMEASURED_WARMUP_ORDER),
            "unmeasured_warmup_episode_count_per_mode": 1,
            "gc_policy": (
                "collect_before_arm_disable_during_arm_restore_after_arm"
            ),
            "cache_policy": "fresh_fixture_per_arm_no_cross_arm_state",
        },
        "clock": _clock_identity(),
        "runtime_identity": _runtime_identity(),
        "pair_count": len(pairs),
        "pair_order_policy": "alternate_off_on_then_on_off",
        "classification_statistic": "median_paired_overhead_ratio",
        "guardrail": {
            "clean_pass_max_ratio": 0.02,
            "warning_continue_max_ratio": 0.05,
            "above_warning_action": "block_repair",
        },
        "pairs": pairs,
        "paired_distribution": {
            "overhead_ratio": ratios,
            "minimum_ratio": min(ratios),
            "median_ratio": median_ratio,
            "maximum_ratio": max(ratios),
        },
        "overhead_classification": classification,
        "qualification_status": (
            "blocked_overhead" if classification == "block_repair" else "pass"
        ),
        "semantic_parity": semantic_parity,
        "return_hash_count": len(return_hashes),
        "event_sequence_hash_count": len(event_hashes),
        "state_hash_count": len(state_hashes),
        "pinned_graphiti_alias_smoke": smoke,
        "network_guard": {
            "audit_hook_enabled": True,
            "socket_connect_guarded": True,
            "network_attempt_count": len(network_attempts),
        },
        "source_hashes": _source_hashes(),
    }
    if not semantic_parity:
        raise MeasurementQualificationError("semantic_parity_failed")
    if smoke["status"] != "pass" or network_attempts:
        raise MeasurementQualificationError("pinned_alias_smoke_failed")
    _assert_sanitized(result)
    result["payload_sha256"] = _sha256_bytes(canonical_bytes(result))
    validate_result(result)
    return result


def validate_result(result: Mapping[str, Any]) -> None:
    if not isinstance(result, Mapping):
        raise ValueError("qualification result must be an object")
    candidate = deepcopy(dict(result))
    observed_hash = candidate.pop("payload_sha256", None)
    _assert_sanitized(candidate)
    if observed_hash != _sha256_bytes(canonical_bytes(candidate)):
        raise ValueError("payload_sha256 mismatch")
    if candidate.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("qualification schema mismatch")
    pairs = candidate.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 5:
        raise ValueError("qualification requires five pairs")
    for index, pair in enumerate(pairs):
        if not isinstance(pair, Mapping) or pair.get("execution_order") != list(
            PAIR_ORDERS[index]
        ):
            raise ValueError("qualification pair order mismatch")
        off_ns = int(pair.get("trace_off_ns", 0))
        on_ns = int(pair.get("trace_on_ns", 0))
        if off_ns <= 0 or on_ns <= 0:
            raise ValueError("qualification durations must be positive")
        expected_ratio = (on_ns - off_ns) / off_ns
        if not math.isclose(
            float(pair.get("paired_overhead_ratio")),
            expected_ratio,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("qualification paired ratio mismatch")
    median = sorted(float(pair["paired_overhead_ratio"]) for pair in pairs)[2]
    ratios = [float(pair["paired_overhead_ratio"]) for pair in pairs]
    expected_distribution = {
        "overhead_ratio": ratios,
        "minimum_ratio": min(ratios),
        "median_ratio": median,
        "maximum_ratio": max(ratios),
    }
    if candidate.get("paired_distribution") != expected_distribution:
        raise ValueError("qualification paired distribution mismatch")
    classification = classify_overhead(median)
    if candidate.get("overhead_classification") != classification:
        raise ValueError("qualification overhead classification mismatch")
    expected_status = "blocked_overhead" if classification == "block_repair" else "pass"
    if candidate.get("qualification_status") != expected_status:
        raise ValueError("qualification status mismatch")
    if candidate.get("semantic_parity") is not True:
        raise ValueError("qualification semantic parity mismatch")
    for key in (
        "return_hash_count",
        "event_sequence_hash_count",
        "state_hash_count",
    ):
        if candidate.get(key) != 1:
            raise ValueError(f"qualification {key} mismatch")
    if candidate.get("measurement_scope") != "in_memory_span_wrapper_overhead_only":
        raise ValueError("qualification measurement scope mismatch")
    benchmark = candidate.get("benchmark_contract")
    if not isinstance(benchmark, Mapping) or not (
        isinstance(benchmark.get("work_units_per_operation"), int)
        and not isinstance(benchmark.get("work_units_per_operation"), bool)
        and int(benchmark["work_units_per_operation"]) > 0
        and benchmark.get("timed_episode_count_per_arm")
        == TIMED_EPISODES_PER_ARM
        and benchmark.get("unmeasured_warmup_order")
        == list(UNMEASURED_WARMUP_ORDER)
        and benchmark.get("unmeasured_warmup_episode_count_per_mode") == 1
        and benchmark.get("gc_policy")
        == "collect_before_arm_disable_during_arm_restore_after_arm"
        and benchmark.get("cache_policy")
        == "fresh_fixture_per_arm_no_cross_arm_state"
    ):
        raise ValueError("qualification benchmark contract mismatch")
    if candidate.get("clock") != _clock_identity():
        raise ValueError("qualification clock identity mismatch")
    if candidate.get("runtime_identity") != _runtime_identity():
        raise ValueError("qualification runtime identity mismatch")
    source_hashes = candidate.get("source_hashes")
    expected_source_hashes = _source_hashes()
    if not isinstance(source_hashes, Mapping) or set(source_hashes) != set(
        expected_source_hashes
    ):
        raise ValueError("qualification source hash inventory mismatch")
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in source_hashes.values()
    ):
        raise ValueError("qualification source hash invalid")
    if dict(source_hashes) != expected_source_hashes:
        raise ValueError("qualification source hash mismatch")
    smoke = candidate.get("pinned_graphiti_alias_smoke")
    graphiti_identity = _graphiti_distribution_identity()
    if not isinstance(smoke, Mapping) or not (
        smoke.get("status") == "pass"
        and smoke.get("graphiti_version") == graphiti_identity["graphiti_version"]
        and smoke.get("graphiti_commit") == graphiti_identity["graphiti_commit"]
        and smoke.get("direct_url_sha256") == graphiti_identity["direct_url_sha256"]
        and smoke.get("graphiti_commit") == _PINNED_GRAPHITI_COMMIT
    ):
        raise ValueError("qualification pinned alias smoke mismatch")
    if smoke.get("network_attempt_count") != 0:
        raise ValueError("qualification network guard mismatch")


def _atomic_write(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_result(result: Mapping[str, Any], path: str | Path) -> str:
    validate_result(result)
    encoded = canonical_bytes(result) + b"\n"
    _atomic_write(Path(path), encoded)
    return _sha256_bytes(encoded)


def _main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-units", type=int, default=20_000)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            root
            / "artifacts/tdd/native_characterization_c2_measurement_qualification_20260811.json"
        ),
    )
    args = parser.parse_args()
    result = asyncio.run(run_qualification(work_units=args.work_units))
    file_sha256 = write_result(result, args.output)
    print(
        json.dumps(
            {
                "file_sha256": file_sha256,
                "median_overhead_percent": (
                    result["paired_distribution"]["median_ratio"] * 100.0
                ),
                "output": str(args.output),
                "overhead_classification": result["overhead_classification"],
                "pair_count": result["pair_count"],
                "qualification_status": result["qualification_status"],
                "semantic_parity": result["semantic_parity"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 2 if result["qualification_status"] == "blocked_overhead" else 0


if __name__ == "__main__":
    raise SystemExit(_main())
