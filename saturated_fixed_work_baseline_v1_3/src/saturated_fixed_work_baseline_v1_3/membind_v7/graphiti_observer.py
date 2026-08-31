"""Observer-only Graphiti build-stage and exact node-cosine reference data.

This module has no import-time Graphiti, Neo4j, or provider side effects.  The
production bindings are loaded only by :func:`build_to_seam_async`; tests may
inject bindings to prove that the build stops before native publication.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
import contextvars
import functools
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .state_delta import DeltaChange, StateDelta
from .continuation import (
    CONTINUATION_K_SCHEMA,
    CONTINUATION_SEAM,
    validate_continuation_k,
)


class GraphitiObserverError(RuntimeError):
    """The observer cannot establish a complete, fail-closed record."""


@dataclass(slots=True)
class _CaptureScope:
    phase: str
    source_sequence: int
    state_version: int
    request_ordinal: int = 0
    read_ordinal: int = 0
    request_prompt_ordinals: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.request_prompt_ordinals is None:
            self.request_prompt_ordinals = {}


_CAPTURE_SCOPE: contextvars.ContextVar[_CaptureScope | None] = contextvars.ContextVar(
    "membind_v7_observer_capture_scope", default=None
)
_REQUEST_SCOPE: contextvars.ContextVar[Mapping[str, Any] | None] = contextvars.ContextVar(
    "membind_v7_observer_request_scope", default=None
)


def current_provider_observation_scope() -> dict[str, Any] | None:
    value = _REQUEST_SCOPE.get()
    return None if value is None else dict(value)


@dataclass(slots=True)
class CaptureRecord:
    phase: str
    source_sequence: int
    state_version: int
    episode_kwargs: Mapping[str, Any]
    start_ns: int
    end_ns: int | None = None
    previous_episode: Mapping[str, Any] | None = None
    reads: list[dict[str, Any]] | None = None
    requests: list[dict[str, Any]] | None = None
    continuation_k: Mapping[str, Any] | None = None
    continuation_status: str = "NOT_REACHED"
    process_calls: int = 0

    def __post_init__(self) -> None:
        self.reads = [] if self.reads is None else self.reads
        self.requests = [] if self.requests is None else self.requests

    def to_record(self) -> dict[str, Any]:
        if self.end_ns is None:
            raise GraphitiObserverError("capture record is still active")
        dependency_edges = [
            {"source": "episode", "target": "previous_episode", "kind": "control"},
            {"source": "previous_episode", "target": "node_extraction", "kind": "data"},
            {"source": "previous_episode", "target": "node_extraction", "kind": "ordered-collection"},
            {"source": "previous_episode", "target": "node_extraction", "kind": "existence"},
            {"source": "previous_episode", "target": "node_resolution", "kind": "data"},
            {"source": "previous_episode", "target": "edge_extraction", "kind": "data"},
            {"source": "previous_episode", "target": "edge_resolution", "kind": "data"},
            {"source": "previous_episode", "target": "attributes_summary", "kind": "data"},
            {"source": "environment_epochs", "target": "node_cosine", "kind": "environment/oracle"},
            {"source": "node_extraction", "target": "node_cosine", "kind": "data"},
            {"source": "node_cosine", "target": "node_resolution", "kind": "ordered-collection"},
            {"source": "node_resolution", "target": "edge_extraction", "kind": "control"},
            {"source": "edge_extraction", "target": "edge_resolution", "kind": "data"},
            {"source": "edge_resolution", "target": "attributes_summary", "kind": "data"},
            {"source": "attributes_summary", "target": "continuation_k", "kind": "data"},
            {"source": "continuation_k", "target": "native_publication", "kind": "effect/publication"},
        ]
        return {
            "schema_version": "membind.v7.graphiti-build-capture.v1",
            "status": "OBSERVER_ONLY",
            "phase": self.phase,
            "source_sequence": self.source_sequence,
            "state_version": self.state_version,
            "previous_episode": dict(self.previous_episode or {}),
            "reads": list(self.reads or ()),
            "requests": list(self.requests or ()),
            "dependency_edges": dependency_edges,
            "continuation_k": dict(self.continuation_k or {}),
            "continuation": {"status": self.continuation_status},
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "duration_ns": self.end_ns - self.start_ns,
            "publication_calls": self.process_calls,
            "treatment_calls": 0,
        }


_ACTIVE_CAPTURE: contextvars.ContextVar[CaptureRecord | None] = contextvars.ContextVar(
    "membind_v7_graphiti_capture", default=None
)


@contextmanager
def observer_capture_scope(*, phase: str, source_sequence: int, state_version: int):
    if phase not in {"OLD", "FRESH_NATIVE", "R1_PROBE"}:
        raise GraphitiObserverError("observer phase is invalid")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (source_sequence, state_version)
    ):
        raise GraphitiObserverError("observer source/state identity is invalid")
    token = _CAPTURE_SCOPE.set(_CaptureScope(phase, source_sequence, state_version))
    try:
        yield
    finally:
        _CAPTURE_SCOPE.reset(token)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(child) for key, child in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(child) for child in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(child) for child in value), key=repr)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _canonical(dump(mode="python"))
    namespace = getattr(value, "__dict__", None)
    if isinstance(namespace, Mapping):
        return _canonical({key: child for key, child in namespace.items() if not str(key).startswith("_")})
    return str(value)


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def build_semantic_cost_dag(build: Mapping[str, Any]) -> dict[str, Any]:
    """Build the pinned sequential phase DAG with a node-read fork/join."""

    phase = build.get("phase")
    required = [
        "previous-context",
        "node-extraction",
        "node-resolution",
        "edge-extraction",
        "edge-resolution",
        "attributes-summary",
    ]
    root_phase = "build-to-seam" if phase == "OLD" else "add-episode"
    if phase == "FRESH_NATIVE":
        required.append("publication")
    elif phase != "OLD":
        return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "build phase is unsupported"}
    trace = build.get("trace")
    if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes, bytearray)):
        return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "trace is missing"}
    rows = [row for row in trace if isinstance(row, Mapping)]
    roots = [row for row in rows if row.get("phase") == root_phase and row.get("parent_span_id") is None]
    if len(roots) != 1 or roots[0].get("status") != "ok":
        return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "semantic trace root is missing or ambiguous"}
    selected: dict[str, Mapping[str, Any]] = {}
    for name in required:
        matches = [row for row in rows if row.get("phase") == name and row.get("status") == "ok"]
        if len(matches) != 1:
            return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "required semantic phase is missing or ambiguous"}
        selected[name] = matches[0]

    def interval(row: Mapping[str, Any]) -> tuple[int, int] | None:
        start = row.get("start_ns")
        end = row.get("end_ns")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)) or int(end) < int(start):
            return None
        return int(start), int(end)

    root_interval = interval(roots[0])
    phase_intervals = [interval(selected[name]) for name in required]
    if root_interval is None or any(value is None for value in phase_intervals):
        return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "semantic phase interval is invalid"}
    concrete = [value for value in phase_intervals if value is not None]
    if any(
        start < root_interval[0] or end > root_interval[1]
        for start, end in concrete
    ) or any(concrete[index][1] > concrete[index + 1][0] for index in range(len(concrete) - 1)):
        return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "semantic phase order is not the pinned partial order"}

    reads = build.get("reads")
    if not isinstance(reads, Sequence) or isinstance(reads, (str, bytes, bytearray)):
        return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "semantic read evidence is missing"}
    read_rows: list[tuple[Mapping[str, Any], int, int]] = []
    read_keys: set[tuple[str, int]] = set()
    resolution_start, resolution_end = concrete[required.index("node-resolution")]
    observer_overhead = 0
    for raw in reads:
        if not isinstance(raw, Mapping):
            return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "semantic read evidence is invalid"}
        operator = raw.get("operator")
        occurrence = raw.get("occurrence")
        native_start = raw.get("native_start_ns")
        native_end = raw.get("native_end_ns")
        observer_start = raw.get("observer_start_ns")
        observer_end = raw.get("observer_end_ns")
        values = (occurrence, native_start, native_end, observer_start, observer_end)
        if (
            not isinstance(operator, str)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or int(native_start) < resolution_start
            or int(native_end) > resolution_end
            or not int(observer_start) <= int(native_start) <= int(native_end) <= int(observer_end)
        ):
            return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "semantic read interval is invalid"}
        key = (operator, int(occurrence))
        if key in read_keys:
            return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "semantic read stable name is ambiguous"}
        read_keys.add(key)
        duration = int(native_end) - int(native_start)
        overhead = (int(observer_end) - int(observer_start)) - duration
        if duration < 0 or overhead < 0:
            return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "semantic read cost is invalid"}
        observer_overhead += overhead
        read_rows.append((raw, duration, overhead))

    build_duration = build.get("duration_ns")
    if isinstance(build_duration, bool) or not isinstance(build_duration, int) or build_duration < 0:
        return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "build cost is invalid"}
    durations = {name: concrete[index][1] - concrete[index][0] for index, name in enumerate(required)}
    # Read observers can run concurrently (one fork per candidate).  Their
    # wrapper/domain-loader overhead is therefore not additive wall time and
    # must not be subtracted once per read from the serial resolution phase.
    # Keep it as explicit certificate cost nodes below; the phase interval and
    # the longest native read determine the critical-path shell.
    longest_read = max((duration for _raw, duration, _overhead in read_rows), default=0)
    resolution_shell = durations["node-resolution"] - longest_read
    # The semantic root is the authoritative build clock.  Capture setup and
    # teardown can surround it, so prefer its interval when available.
    native_duration = root_interval[1] - root_interval[0]
    unattributed = native_duration - sum(durations.values())
    if resolution_shell < 0 or unattributed < 0:
        return {"schema_version": "membind.v7.semantic-cost-dag.v1", "status": "UNKNOWN", "reason": "phase costs do not decompose the build"}

    nodes: list[dict[str, Any]] = []

    def add(node_id: str, predecessors: Sequence[str], cost: int, **fields: Any) -> None:
        nodes.append(
            {
                "node_id": node_id,
                "predecessors": list(predecessors),
                "cost_ns": cost,
                "state_dependent": True,
                **fields,
            }
        )

    add("episode-input", (), 0)
    add("unattributed-native", ("episode-input",), unattributed)
    add("previous-context", ("unattributed-native",), durations["previous-context"])
    add("node-extraction", ("previous-context",), durations["node-extraction"])
    add("node-resolution-shell", ("node-extraction",), resolution_shell)
    read_node_ids: list[str] = []
    for raw, duration, _overhead in read_rows:
        node_id = f"node-cosine-{int(raw['occurrence']):04d}"
        read_node_ids.append(node_id)
        add(
            node_id,
            ("node-resolution-shell",),
            duration,
            read_key=[str(raw["operator"]), int(raw["occurrence"])],
        )
    add(
        "node-resolution-join",
        tuple(read_node_ids or ("node-resolution-shell",)),
        0,
    )
    predecessor = "node-resolution-join"
    for name in ("edge-extraction", "edge-resolution", "attributes-summary"):
        add(name, (predecessor,), durations[name])
        predecessor = name
    add("continuation-k", (predecessor,), 0)
    if "publication" in durations:
        add("publication", ("continuation-k",), durations["publication"])

    cost_nodes: list[dict[str, Any]] = []
    previous_cost = "node-resolution-shell"
    for raw, _duration, overhead in read_rows:
        node_id = f"certificate-{int(raw['occurrence']):04d}"
        cost_nodes.append(
            {
                "node_id": node_id,
                "predecessors": [previous_cost],
                "cost_ns": overhead,
                "kind": "certificate",
                "gates": ["node-resolution-join"],
            }
        )
        previous_cost = node_id
    return {
        "schema_version": "membind.v7.semantic-cost-dag.v1",
        "status": "COMPLETE",
        "source": "pinned_graphiti_v0.29.3_phase_chain_with_node_read_fork_join",
        "nodes": nodes,
        "cost_nodes": cost_nodes,
    }


def _response_model_identity(value: Any) -> Any:
    schema = getattr(value, "model_json_schema", None)
    if callable(schema):
        try:
            return {
                "class": f"{value.__module__}.{value.__qualname__}",
                "schema": schema(),
            }
        except (TypeError, ValueError):
            pass
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    return _canonical(value)


def _public_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = ("key", "token", "secret", "password", "authorization", "credential")
    return {
        str(key): _canonical(value)
        for key, value in kwargs.items()
        if key != "response_model" and not any(fragment in str(key).casefold() for fragment in forbidden)
    }


class RequestObservationClient:
    """Transparent LLM client wrapper retaining only digest identity and cost."""

    def __init__(
        self,
        delegate: Any,
        *,
        sink: Callable[[dict[str, Any]], Any],
        model_epoch: str,
        single_call_branch_oracle: bool = False,
    ) -> None:
        if not callable(getattr(delegate, "generate_response", None)):
            raise GraphitiObserverError("request observer delegate is invalid")
        if not callable(sink) or not model_epoch:
            raise GraphitiObserverError("request observer identity is incomplete")
        self.inner = delegate
        self._sink = sink
        self._model_epoch = str(model_epoch)
        self._single_call_branch_oracle = bool(single_call_branch_oracle)
        self._old_responses: dict[tuple[int, str, int, str], Any] = {}

    async def generate_response(self, messages: Any, **kwargs: Any) -> Any:
        scope = _CAPTURE_SCOPE.get()
        if scope is None:
            raise GraphitiObserverError("provider call occurred outside observer capture scope")
        ordinal = scope.request_ordinal
        scope.request_ordinal += 1
        prompt_name = str(kwargs.get("prompt_name") or "unknown")
        prompt_ordinals = scope.request_prompt_ordinals
        if prompt_ordinals is None:
            raise GraphitiObserverError("request prompt ordinal state is missing")
        prompt_ordinal = prompt_ordinals.get(prompt_name, 0)
        prompt_ordinals[prompt_name] = prompt_ordinal + 1
        fields = {
            "messages": _canonical(messages),
            "response_model": _response_model_identity(kwargs.get("response_model")),
            "kwargs": _public_kwargs(kwargs),
            "model_epoch": self._model_epoch,
        }
        field_digests = {name: canonical_digest(value) for name, value in fields.items()}
        request_identity = canonical_digest(field_digests)
        start_ns = time.monotonic_ns()
        status = "PASS"
        result: Any = None
        transport_result: Any = None
        response_binding = "PROVIDER_SINGLE_CALL"
        request_token = _REQUEST_SCOPE.set(
            {
                "phase": scope.phase,
                "source_sequence": scope.source_sequence,
                "state_version": scope.state_version,
                "request_ordinal": ordinal,
                "prompt_name": prompt_name,
            }
        )
        try:
            transport_result = await _maybe_await(self.inner.generate_response(messages, **kwargs))
            result = transport_result
            oracle_key = (scope.source_sequence, prompt_name, prompt_ordinal, request_identity)
            if self._single_call_branch_oracle and scope.phase == "OLD":
                self._old_responses[oracle_key] = copy.deepcopy(transport_result)
            elif self._single_call_branch_oracle and scope.phase == "FRESH_NATIVE":
                old_result = self._old_responses.get(oracle_key)
                if old_result is not None:
                    result = copy.deepcopy(old_result)
                    response_binding = "OLD_SINGLE_CALL_REPLAY"
        except BaseException:
            status = "FAILED"
            raise
        finally:
            end_ns = time.monotonic_ns()
            row = {
                "schema_version": "membind.v7.request-observation.v1",
                "phase": scope.phase,
                "source_sequence": scope.source_sequence,
                "state_version": scope.state_version,
                "ordinal": ordinal,
                "prompt_name": prompt_name,
                "prompt_ordinal": prompt_ordinal,
                "request_identity": request_identity,
                "response_digest": canonical_digest(result) if status == "PASS" else None,
                "transport_response_digest": canonical_digest(transport_result) if status == "PASS" else None,
                "response_binding": response_binding if status == "PASS" else None,
                "field_digests": field_digests,
                "model_epoch": self._model_epoch,
                "start_ns": start_ns,
                "end_ns": end_ns,
                "duration_ns": end_ns - start_ns,
                "status": status,
            }
            try:
                self._sink(row)
            finally:
                _REQUEST_SCOPE.reset(request_token)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _replace_attribute(owner: Any, name: str, replacement: Any) -> Callable[[], None]:
    namespace = getattr(owner, "__dict__", {})
    had_own = name in namespace
    previous = namespace.get(name)
    setattr(owner, name, replacement)

    def restore() -> None:
        if had_own:
            setattr(owner, name, previous)
        else:
            try:
                delattr(owner, name)
            except AttributeError:
                pass

    return restore


def _episode_observation(value: Any) -> dict[str, Any]:
    projection = _canonical(value)
    if not isinstance(projection, Mapping):
        raise GraphitiObserverError("previous episode projection is invalid")
    content = projection.get("content")
    result = {
        key: child
        for key, child in projection.items()
        if key not in {"content", "name_embedding", "fact_embedding"}
    }
    result["content_digest"] = canonical_digest(content)
    return dict(result)


def _continuation_record(
    graphiti: Any,
    capture: CaptureRecord,
    *,
    episode: Any,
    nodes: Sequence[Any],
    entity_edges: Sequence[Any],
    now: Any,
    group_id: str,
    saga: Any,
    saga_previous_episode_uuid: Any,
    node_episode_index_map: Mapping[str, Any] | None,
) -> dict[str, Any]:
    episodes = list(episode) if isinstance(episode, list | tuple) else [episode]
    driver = graphiti.driver
    provider = getattr(getattr(driver, "provider", None), "value", getattr(driver, "provider", None))
    value = {
        "schema_version": CONTINUATION_K_SCHEMA,
        "seam": CONTINUATION_SEAM,
        "episodes": [_canonical(item) for item in episodes],
        "nodes": [_canonical(item) for item in nodes],
        "entity_edges": [_canonical(item) for item in entity_edges],
        "node_episode_index_map": _canonical(dict(node_episode_index_map or {})),
        "now": _canonical(now),
        "group_id": str(group_id),
        "store_raw_episode_content": bool(getattr(graphiti, "store_raw_episode_content", True)),
        "driver_provider": str(provider).lower(),
        "driver_database": str(getattr(driver, "_database", group_id)),
        "backend_epoch": str(capture.episode_kwargs["backend_epoch"]),
        "publication_frontier": capture.state_version,
        "saga": _canonical(saga),
        "saga_previous_episode_uuid": _canonical(saga_previous_episode_uuid),
        "update_communities": bool(capture.episode_kwargs.get("update_communities", False)),
    }
    return value


class GraphitiCaptureInstallation:
    """Reversible passive instrumentation for the pinned native build path."""

    def __init__(
        self,
        graphiti: Any,
        *,
        model_epoch: str,
        query_epoch: str,
        index_epoch: str,
        config_epoch: str,
        backend_epoch: str,
        node_module: Any | None = None,
        domain_loader: Callable[[Any, Sequence[str] | None], Any] | None = None,
        single_call_branch_oracle: bool = False,
    ) -> None:
        self.graphiti = graphiti
        self.model_epoch = str(model_epoch)
        self.query_epoch = str(query_epoch)
        self.index_epoch = str(index_epoch)
        self.config_epoch = str(config_epoch)
        self.backend_epoch = str(backend_epoch)
        if not all((self.model_epoch, self.query_epoch, self.index_epoch, self.config_epoch, self.backend_epoch)):
            raise GraphitiObserverError("capture epochs are incomplete")
        if node_module is None:
            from graphiti_core.utils.maintenance import node_operations

            node_module = node_operations
        self.node_module = node_module
        self.domain_loader = domain_loader
        self.single_call_branch_oracle = bool(single_call_branch_oracle)
        self.original_llm_client = graphiti.llm_client
        self._original_clients_llm = graphiti.clients.llm_client
        self._restorers: list[Callable[[], None]] = []
        self._installed = False

    def _active(self) -> CaptureRecord:
        capture = _ACTIVE_CAPTURE.get()
        if capture is None:
            raise GraphitiObserverError("Graphiti observation occurred outside capture scope")
        return capture

    def _request_sink(self, row: dict[str, Any]) -> None:
        self._active().requests.append(row)  # type: ignore[union-attr]

    def _read_sink(self, row: dict[str, Any]) -> None:
        self._active().reads.append(row)  # type: ignore[union-attr]

    def install(self) -> None:
        if self._installed:
            raise GraphitiObserverError("Graphiti capture is already installed")
        self._installed = True
        try:
            observed_llm = RequestObservationClient(
                self.original_llm_client,
                sink=self._request_sink,
                model_epoch=self.model_epoch,
                single_call_branch_oracle=self.single_call_branch_oracle,
            )
            self.graphiti.llm_client = observed_llm
            self.graphiti.clients.llm_client = observed_llm

            def restore_llm() -> None:
                self.graphiti.llm_client = self.original_llm_client
                self.graphiti.clients.llm_client = self._original_clients_llm

            self._restorers.append(restore_llm)

            original_retrieve = self.graphiti.retrieve_episodes

            @functools.wraps(original_retrieve)
            async def retrieve(*args: Any, **kwargs: Any) -> Any:
                capture = self._active()
                start_ns = time.monotonic_ns()
                result = await _maybe_await(original_retrieve(*args, **kwargs))
                end_ns = time.monotonic_ns()
                try:
                    bound = inspect.signature(original_retrieve).bind_partial(*args, **kwargs)
                    selector = {
                        name: _canonical(bound.arguments.get(name))
                        for name in ("reference_time", "last_n", "group_ids", "source", "saga")
                    }
                except (TypeError, ValueError):
                    selector = {"args_digest": canonical_digest(args), "kwargs_digest": canonical_digest(kwargs)}
                projected = [_episode_observation(item) for item in result]
                order = [str(item.get("uuid")) for item in projected]
                capture.previous_episode = {
                    "selector": selector,
                    "window": projected,
                    "order": order,
                    "projection_digest": canonical_digest(projected),
                    "start_ns": start_ns,
                    "end_ns": end_ns,
                    "duration_ns": end_ns - start_ns,
                }
                return result

            self._restorers.append(_replace_attribute(self.graphiti, "retrieve_episodes", retrieve))

            original_search = self.node_module.node_similarity_search

            @functools.wraps(original_search)
            async def node_similarity_search(
                driver: Any,
                search_vector: Sequence[float],
                search_filter: Any,
                group_ids: Sequence[str] | None = None,
                limit: int = 10,
                min_score: float = 0.6,
            ) -> Any:
                return await observe_node_similarity_async(
                    original_search,
                    driver,
                    search_vector,
                    search_filter,
                    group_ids,
                    limit,
                    min_score,
                    sink=self._read_sink,
                    domain_loader=self.domain_loader or load_node_embedding_domain_async,
                    query_epoch=self.query_epoch,
                    index_epoch=self.index_epoch,
                    config_epoch=self.config_epoch,
                )

            self._restorers.append(
                _replace_attribute(self.node_module, "node_similarity_search", node_similarity_search)
            )

            original_process = self.graphiti._process_episode_data

            @functools.wraps(original_process)
            async def process(*args: Any, **kwargs: Any) -> Any:
                capture = self._active()
                bound = inspect.signature(original_process).bind_partial(*args, **kwargs)
                values = bound.arguments
                k = _continuation_record(
                    self.graphiti,
                    capture,
                    episode=values["episode"],
                    nodes=values["nodes"],
                    entity_edges=values["entity_edges"],
                    now=values["now"],
                    group_id=values["group_id"],
                    saga=values.get("saga"),
                    saga_previous_episode_uuid=values.get("saga_previous_episode_uuid"),
                    node_episode_index_map=values.get("node_episode_index_map"),
                )
                check = validate_continuation_k(k)
                capture.continuation_k = k
                capture.continuation_status = check.status.value
                capture.process_calls += 1
                return await _maybe_await(original_process(*args, **kwargs))

            self._restorers.append(
                _replace_attribute(self.graphiti, "_process_episode_data", process)
            )
        except BaseException:
            self.restore()
            raise

    @contextmanager
    def scope(
        self,
        *,
        phase: str,
        source_sequence: int,
        state_version: int,
        episode_kwargs: Mapping[str, Any],
    ):
        if not self._installed:
            raise GraphitiObserverError("Graphiti capture is not installed")
        if _ACTIVE_CAPTURE.get() is not None:
            raise GraphitiObserverError("Graphiti capture scopes cannot be nested")
        enriched = dict(episode_kwargs)
        enriched["backend_epoch"] = self.backend_epoch
        record = CaptureRecord(
            phase,
            source_sequence,
            state_version,
            enriched,
            time.monotonic_ns(),
        )
        capture_token = _ACTIVE_CAPTURE.set(record)
        try:
            with observer_capture_scope(
                phase=phase,
                source_sequence=source_sequence,
                state_version=state_version,
            ):
                yield record
        finally:
            record.end_ns = time.monotonic_ns()
            _ACTIVE_CAPTURE.reset(capture_token)

    def restore(self) -> None:
        if not self._installed and not self._restorers:
            return
        for restore in reversed(self._restorers):
            restore()
        self._restorers.clear()
        self._installed = False

    def attach_shadow_result(self, record: CaptureRecord, result: "BuildStageResult") -> None:
        if record.phase != "OLD" or record.process_calls != 0:
            raise GraphitiObserverError("shadow seam result is bound to an invalid capture")
        value = _canonical(result.continuation_k)
        if not isinstance(value, Mapping):
            raise GraphitiObserverError("shadow continuation K is invalid")
        check = validate_continuation_k(value)
        record.continuation_k = dict(value)
        record.continuation_status = check.status.value


@dataclass(frozen=True, slots=True)
class BackendProjection:
    version: int
    backend_epoch: str
    namespace: str
    nodes: Mapping[str, Mapping[str, Any]]
    edges: Mapping[str, Mapping[str, Any]]
    episodes: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise GraphitiObserverError("projection version is invalid")
        if not self.backend_epoch:
            raise GraphitiObserverError("projection backend epoch is missing")
        if not self.namespace:
            raise GraphitiObserverError("projection namespace is missing")
        for field in ("nodes", "edges", "episodes"):
            value = getattr(self, field)
            if not isinstance(value, Mapping):
                raise GraphitiObserverError(f"projection {field} is invalid")

    @property
    def digest(self) -> str:
        return canonical_digest(
            {
                "version": self.version,
                "backend_epoch": self.backend_epoch,
                "namespace": self.namespace,
                "nodes": self.nodes,
                "edges": self.edges,
                "episodes": self.episodes,
            }
        )


def build_projection_delta(before: BackendProjection, after: BackendProjection) -> StateDelta:
    """Build exact insert/update/delete images for every projected state kind."""

    if before.namespace != after.namespace:
        raise GraphitiObserverError("projection namespace changed")
    if before.version >= after.version:
        raise GraphitiObserverError("projection version did not advance")
    changes: list[DeltaChange] = []
    for kind, old_projection, new_projection in (
        ("node", before.nodes, after.nodes),
        ("edge", before.edges, after.edges),
        ("episode", before.episodes, after.episodes),
    ):
        old_values = {str(key): dict(value) for key, value in old_projection.items()}
        new_values = {str(key): dict(value) for key, value in new_projection.items()}
        for key in sorted(set(old_values) | set(new_values)):
            old = old_values.get(key)
            new = new_values.get(key)
            if old is None and new is not None:
                changes.append(
                    DeltaChange(
                        kind,
                        key,
                        frozenset(new),
                        before={},
                        after=new,
                        operation="insert",
                    )
                )
                continue
            if new is None and old is not None:
                changes.append(
                    DeltaChange(
                        kind,
                        key,
                        frozenset(old),
                        before=old,
                        after={},
                        operation="delete",
                    )
                )
                continue
            assert old is not None and new is not None
            changed_fields = frozenset(
                field
                for field in sorted(set(old) | set(new))
                if _canonical(old.get(field)) != _canonical(new.get(field))
            )
            if changed_fields:
                changes.append(
                    DeltaChange(
                        kind,
                        key,
                        changed_fields,
                        before=old,
                        after=new,
                        operation="update",
                    )
                )
    environment_changes = (
        frozenset()
        if before.backend_epoch == after.backend_epoch
        else frozenset({"backend_epoch", "index_epoch"})
    )
    return StateDelta(before.version, after.version, tuple(changes), environment_changes)


def _vector(value: Sequence[float] | None, *, expected: int | None = None) -> tuple[float, ...]:
    if value is None or isinstance(value, (str, bytes, bytearray)):
        raise GraphitiObserverError("node embedding is missing")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        raise GraphitiObserverError("node embedding is invalid") from None
    if not result:
        raise GraphitiObserverError("node embedding is missing")
    if expected is not None and len(result) != expected:
        raise GraphitiObserverError("node embedding dimension changed")
    if not all(math.isfinite(item) for item in result):
        raise GraphitiObserverError("node embedding is non-finite")
    return result


def exact_cosine_domain(
    *,
    query: Sequence[float],
    domain: Mapping[str, Sequence[float] | None],
    limit: int,
    min_score: float,
) -> dict[str, Any]:
    """Evaluate the complete exact domain with deterministic audit ordering.

    Native Graphiti does not define a secondary tie order.  The deterministic
    UUID order here is only an observer representation; every cutoff tie is
    called out and therefore remains ``UNKNOWN`` to the certificate.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise GraphitiObserverError("cosine limit is invalid")
    q = _vector(query)
    q_norm = math.sqrt(sum(item * item for item in q))
    if q_norm == 0:
        raise GraphitiObserverError("cosine query norm is zero")
    scored: list[dict[str, Any]] = []
    for uuid, raw_embedding in sorted(domain.items(), key=lambda item: str(item[0])):
        embedding = _vector(raw_embedding, expected=len(q))
        norm = math.sqrt(sum(item * item for item in embedding))
        if norm == 0:
            raise GraphitiObserverError("node embedding norm is zero")
        score = sum(left * right for left, right in zip(q, embedding, strict=True)) / (q_norm * norm)
        scored.append({"uuid": str(uuid), "embedding": list(embedding), "score": score})
    scored.sort(key=lambda row: (-float(row["score"]), str(row["uuid"])))
    eligible = [row for row in scored if float(row["score"]) > float(min_score)]
    selected = eligible[:limit]
    cutoff = float(selected[-1]["score"]) if len(selected) == limit else None
    selected_ids = [str(row["uuid"]) for row in selected]
    boundary_ties = (
        [str(row["uuid"]) for row in eligible[limit:] if float(row["score"]) == cutoff]
        if cutoff is not None
        else []
    )
    return {
        "query": list(q),
        "domain": scored,
        "limit": limit,
        "min_score": float(min_score),
        "result": selected_ids,
        "cutoff": cutoff,
        "boundary_ties": boundary_ties,
        "tie_contract": "UNKNOWN" if boundary_ties else "NO_BOUNDARY_TIE_OBSERVED",
    }


NODE_PROJECTION_QUERY = """
MATCH (n:Entity)
WHERE n.group_id = $group_id
RETURN 'node' AS kind,
       n.uuid AS uuid,
       n.name AS name,
       n.group_id AS group_id,
       n.name_embedding AS name_embedding,
       n.summary AS summary,
       labels(n) AS labels,
       properties(n) AS attributes
ORDER BY n.uuid ASC
"""

EDGE_PROJECTION_QUERY = """
MATCH (source:Entity)-[edge:RELATES_TO]->(target:Entity)
WHERE edge.group_id = $group_id
RETURN 'edge' AS kind,
       edge.uuid AS uuid,
       source.uuid AS source_node_uuid,
       target.uuid AS target_node_uuid,
       edge.group_id AS group_id,
       edge.name AS name,
       edge.fact AS fact,
       edge.fact_embedding AS fact_embedding,
       edge.episodes AS episodes,
       edge.valid_at AS valid_at,
       edge.invalid_at AS invalid_at,
       edge.expired_at AS expired_at,
       properties(edge) AS attributes
ORDER BY edge.uuid ASC
"""

EPISODE_PROJECTION_QUERY = """
MATCH (episode:Episodic)
WHERE episode.group_id = $group_id
RETURN 'episode' AS kind,
       episode.uuid AS uuid,
       episode.name AS name,
       episode.group_id AS group_id,
       episode.source AS source,
       episode.source_description AS source_description,
       episode.content AS content,
       episode.created_at AS created_at,
       episode.valid_at AS valid_at,
       properties(episode) AS attributes
ORDER BY episode.uuid ASC
"""

NODE_EMBEDDING_DOMAIN_QUERY = """
MATCH (n:Entity)
WHERE n.group_id IN $group_ids
RETURN n.uuid AS uuid, n.name_embedding AS name_embedding
ORDER BY n.uuid ASC
"""


def _records(result: Any) -> list[Any]:
    records = getattr(result, "records", None)
    if records is not None:
        return list(records)
    if isinstance(result, tuple) and result:
        return list(result[0])
    if isinstance(result, list):
        return result
    raise GraphitiObserverError("Neo4j projection result shape is unsupported")


def _record(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        return dict(value)
    except (TypeError, ValueError):
        raise GraphitiObserverError("Neo4j projection record is invalid") from None


def _projection_payload(record: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {field: _canonical(record.get(field)) for field in fields}


async def load_backend_projection_async(
    driver: Any,
    *,
    namespace: str,
    version: int,
    backend_epoch: str,
) -> BackendProjection:
    """Load the exact namespace projection around one native publication."""

    if not callable(getattr(driver, "execute_query", None)):
        raise GraphitiObserverError("Neo4j projection driver is invalid")
    params = {"group_id": namespace}
    node_rows = _records(await _maybe_await(driver.execute_query(NODE_PROJECTION_QUERY, params=params, routing_="r")))
    edge_rows = _records(await _maybe_await(driver.execute_query(EDGE_PROJECTION_QUERY, params=params, routing_="r")))
    episode_rows = _records(await _maybe_await(driver.execute_query(EPISODE_PROJECTION_QUERY, params=params, routing_="r")))
    nodes: dict[str, Mapping[str, Any]] = {}
    for raw in node_rows:
        row = _record(raw)
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise GraphitiObserverError("node projection UUID is invalid")
        if uuid in nodes:
            raise GraphitiObserverError("node projection UUID is duplicated")
        nodes[uuid] = _projection_payload(
            row,
            ("name", "group_id", "name_embedding", "summary", "labels", "attributes"),
        )
    edges: dict[str, Mapping[str, Any]] = {}
    for raw in edge_rows:
        row = _record(raw)
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise GraphitiObserverError("edge projection UUID is invalid")
        if uuid in edges:
            raise GraphitiObserverError("edge projection UUID is duplicated")
        edges[uuid] = _projection_payload(
            row,
            (
                "source_node_uuid",
                "target_node_uuid",
                "group_id",
                "name",
                "fact",
                "fact_embedding",
                "episodes",
                "valid_at",
                "invalid_at",
                "expired_at",
                "attributes",
            ),
        )
    episodes: dict[str, Mapping[str, Any]] = {}
    for raw in episode_rows:
        row = _record(raw)
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise GraphitiObserverError("episode projection UUID is invalid")
        if uuid in episodes:
            raise GraphitiObserverError("episode projection UUID is duplicated")
        episodes[uuid] = _projection_payload(
            row,
            (
                "name",
                "group_id",
                "source",
                "source_description",
                "content",
                "created_at",
                "valid_at",
                "attributes",
            ),
        )
    return BackendProjection(version, backend_epoch, namespace, nodes, edges, episodes)


async def load_node_embedding_domain_async(
    driver: Any, group_ids: Sequence[str] | None
) -> dict[str, Sequence[float] | None]:
    if not group_ids:
        raise GraphitiObserverError("node cosine observer requires explicit group_ids")
    result = await _maybe_await(
        driver.execute_query(
            NODE_EMBEDDING_DOMAIN_QUERY,
            group_ids=list(group_ids),
            routing_="r",
        )
    )
    domain: dict[str, Sequence[float] | None] = {}
    for raw in _records(result):
        row = _record(raw)
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid or uuid in domain:
            raise GraphitiObserverError("node cosine domain UUID is invalid")
        domain[uuid] = row.get("name_embedding")
    return domain


async def observe_node_similarity_async(
    native: Callable[..., Any],
    driver: Any,
    search_vector: Sequence[float],
    search_filter: Any,
    group_ids: Sequence[str] | None,
    limit: int,
    min_score: float,
    *,
    sink: Callable[[dict[str, Any]], Any],
    domain_loader: Callable[[Any, Sequence[str] | None], Awaitable[Mapping[str, Sequence[float] | None]] | Mapping[str, Sequence[float] | None]] = load_node_embedding_domain_async,
    query_epoch: str,
    index_epoch: str,
    config_epoch: str,
) -> Any:
    """Observe one pinned exact-cosine call without modifying its return."""

    scope = _CAPTURE_SCOPE.get()
    if scope is None:
        raise GraphitiObserverError("node cosine read occurred outside observer capture scope")
    ordinal = scope.read_ordinal
    scope.read_ordinal += 1
    observer_start = time.monotonic_ns()
    domain = await _maybe_await(domain_loader(driver, group_ids))
    reference = exact_cosine_domain(
        query=search_vector,
        domain=domain,
        limit=limit,
        min_score=min_score,
    )
    native_start = time.monotonic_ns()
    result = await _maybe_await(
        native(driver, search_vector, search_filter, group_ids, limit, min_score)
    )
    native_end = time.monotonic_ns()
    try:
        actual = [str(_field(node, "uuid")) for node in result]
    except TypeError:
        raise GraphitiObserverError("node cosine native result is not iterable") from None
    if any(value in {"", "None"} for value in actual):
        raise GraphitiObserverError("node cosine native result UUID is invalid")
    complete = actual == reference["result"] and not reference["boundary_ties"]
    if reference["boundary_ties"]:
        completeness_reason = "BOUNDARY_TIE"
    elif actual != reference["result"]:
        actual_set = set(actual)
        reference_set = set(reference["result"])
        completeness_reason = "RESULT_MISMATCH"
    else:
        completeness_reason = "EXACT"
    row = {
        "schema_version": "membind.v7.node-cosine-observation.v1",
        "phase": scope.phase,
        "source_sequence": scope.source_sequence,
        "state_version": scope.state_version,
        "operator": "node_cosine",
        "occurrence": ordinal,
        "query": list(_vector(search_vector)),
        "query_digest": canonical_digest(list(search_vector)),
        "filter": _canonical(search_filter),
        "filter_fingerprint": canonical_digest(search_filter),
        "group_ids": list(group_ids or ()),
        "limit": int(limit),
        "min_score": float(min_score),
        "ranking": "neo4j.vector.similarity.cosine.score_desc",
        "actual_result": actual,
        "reference_result": list(reference["result"]),
        "complete_domain": list(reference["domain"]),
        "cutoff": reference["cutoff"],
        "boundary_ties": list(reference["boundary_ties"]),
        "tie_contract": reference["tie_contract"],
        "query_epoch": query_epoch,
        "index_epoch": index_epoch,
        "config_epoch": config_epoch,
        "completeness_status": "COMPLETE" if complete else "INCOMPLETE",
        # Digest-only/count-only diagnostics make provider/index drift
        # auditable without persisting query vectors or node payloads.
        "completeness_reason": completeness_reason,
        "domain_count": len(reference["domain"]),
        "actual_count": len(actual),
        "reference_count": len(reference["result"]),
        "actual_result_digest": canonical_digest(actual),
        "reference_result_digest": canonical_digest(reference["result"]),
        "actual_not_in_reference_count": len(set(actual) - set(reference["result"])),
        "reference_not_in_actual_count": len(set(reference["result"]) - set(actual)),
        "order_mismatch_count": sum(
            left != right for left, right in zip(actual, reference["result"])
        ) + abs(len(actual) - len(reference["result"])),
        "observer_start_ns": observer_start,
        "native_start_ns": native_start,
        "native_end_ns": native_end,
        "observer_end_ns": time.monotonic_ns(),
        "witness": {
            "operator": "node_cosine",
            "result": actual,
            "domain": [row["uuid"] for row in reference["domain"]],
            "k": int(limit),
            "cutoff": reference["cutoff"],
            "ties": list(reference["boundary_ties"]),
            "query_epoch": query_epoch,
            "index_epoch": index_epoch,
        },
    }
    sink(row)
    return result


@dataclass(frozen=True, slots=True)
class BuildStageBindings:
    now: Callable[[], Any]
    retrieve_previous: Callable[[Any, Mapping[str, Any]], Awaitable[Sequence[Any]]]
    make_episode: Callable[[Any, Mapping[str, Any], Any], Any]
    extract_nodes: Callable[[Any, Any, Sequence[Any], Mapping[str, Any]], Awaitable[tuple[list[Any], Mapping[str, list[int]]]]]
    resolve_nodes: Callable[[Any, list[Any], Any, Sequence[Any], Mapping[str, Any]], Awaitable[tuple[list[Any], Mapping[str, str], list[Any]]]]
    extract_resolve_edges: Callable[[Any, Any, list[Any], Sequence[Any], list[Any], Mapping[str, str], Mapping[str, Any]], Awaitable[tuple[list[Any], list[Any], list[Any]]]]
    extract_attributes: Callable[[Any, list[Any], Any, Sequence[Any], list[Any], Mapping[str, Any]], Awaitable[list[Any]]]
    continuation_k: Callable[..., Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class BuildStageResult:
    episode: Any
    previous_episodes: tuple[Any, ...]
    extracted_nodes: tuple[Any, ...]
    nodes: tuple[Any, ...]
    entity_edges: tuple[Any, ...]
    node_episode_index_map: Mapping[str, list[int]]
    continuation_k: Mapping[str, Any]
    publication_calls: int = 0
    # Preserve the direct Node-resolution output separately from the hydrated
    # continuation nodes.  CUT-N ends at Node resolution; comparing it with
    # the post-Summary hydrated list would create a false continuation miss.
    resolved_nodes: tuple[Any, ...] = ()


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _assert_complete_embeddings(nodes: Sequence[Any], edges: Sequence[Any]) -> None:
    if any(_field(node, "name_embedding") is None for node in nodes):
        raise GraphitiObserverError("guarded continuation node embedding is missing")
    if any(_field(edge, "fact_embedding") is None for edge in edges):
        raise GraphitiObserverError("guarded continuation edge embedding is missing")


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _ensure_single_partition_provenance(
    graphiti: Any,
    episode: Any,
    extracted_nodes: Sequence[Any],
) -> None:
    """Bridge the 8B chunking seam when node extraction fits one partition.

    The local 8B runtime's edge partitioner requires source provenance even
    when the node prompt did not need to split.  Native V6 normally seeds this
    through its partition worker; the observer calls the same Graphiti
    functions directly, so seed the equivalent one-partition evidence only
    when the runtime exposes its private provenance maps.
    """

    client = getattr(graphiti, "llm_client", None)
    # Capture/instrumentation layers may wrap the runtime client more than
    # once.  Walk only the conventional ``inner`` chain and stop on cycles;
    # do not inspect arbitrary attributes or alter unrelated clients.
    seen: set[int] = set()
    sources_by_scope = hints_by_scope = None
    for _ in range(8):
        if client is None or id(client) in seen:
            break
        seen.add(id(client))
        sources_by_scope = getattr(client, "_membind_entity_partition_sources_by_scope", None)
        hints_by_scope = getattr(client, "_membind_entity_partition_hints_by_scope", None)
        if isinstance(sources_by_scope, dict) and isinstance(hints_by_scope, dict):
            break
        client = getattr(client, "inner", None)
    if not isinstance(sources_by_scope, dict) or not isinstance(hints_by_scope, dict):
        return
    try:
        from ..membind_v5.runtime.core.provider_admission import current_provider_scope

        scope = current_provider_scope()
    except Exception:
        scope = (None, None)
    content = getattr(episode, "content", None)
    if not isinstance(content, str) or not content:
        return
    scoped_sources = sources_by_scope.setdefault(scope, {})
    scoped_sources.setdefault(0, content)
    # A model entity can be semantically supported by the current source but
    # not appear verbatim in any turn segment (for example, a normalized
    # alias).  The runtime represents that conservative fallback as partition
    # ``-1``; retain a complete source-text witness so the edge call remains
    # auditable instead of failing before producing observer evidence.
    scoped_sources.setdefault(-1, content)
    scoped_hints = hints_by_scope.setdefault(scope, {})
    for node in extracted_nodes:
        name = node.get("name") if isinstance(node, Mapping) else getattr(node, "name", None)
        identity = " ".join(str(name or "").split()).casefold()
        if identity:
            values = scoped_hints.setdefault(identity, [])
            if 0 not in values:
                values.append(0)


def _default_bindings() -> BuildStageBindings:
    from graphiti_core.graphiti import (
        extract_attributes_from_nodes,
        extract_nodes,
        resolve_extracted_nodes,
    )
    from graphiti_core.nodes import EpisodeType, EpisodicNode
    from graphiti_core.search.search_utils import RELEVANT_SCHEMA_LIMIT
    from graphiti_core.utils.datetime_utils import utc_now

    async def retrieve(graphiti: Any, kwargs: Mapping[str, Any]) -> Sequence[Any]:
        explicit = kwargs.get("previous_episode_uuids")
        if explicit is not None:
            return await EpisodicNode.get_by_uuids(graphiti.driver, list(explicit))
        return await graphiti.retrieve_episodes(
            kwargs["reference_time"],
            last_n=RELEVANT_SCHEMA_LIMIT,
            group_ids=[kwargs["group_id"]],
            source=kwargs.get("source"),
        )

    def make_episode(_graphiti: Any, kwargs: Mapping[str, Any], now: Any) -> Any:
        fields = dict(
            name=kwargs["name"],
            group_id=kwargs["group_id"],
            labels=[],
            source=kwargs.get("source", EpisodeType.message),
            content=kwargs["episode_body"],
            source_description=kwargs["source_description"],
            created_at=now,
            valid_at=kwargs["reference_time"],
        )
        if kwargs.get("uuid") is not None:
            fields["uuid"] = kwargs["uuid"]
        return EpisodicNode(**fields)

    async def extract(graphiti: Any, episode: Any, previous: Sequence[Any], kwargs: Mapping[str, Any]) -> tuple[list[Any], Mapping[str, list[int]]]:
        return await extract_nodes(
            graphiti.clients,
            episode,
            list(previous),
            kwargs.get("entity_types"),
            kwargs.get("excluded_entity_types"),
            kwargs.get("custom_extraction_instructions"),
        )

    async def resolve(graphiti: Any, nodes: list[Any], episode: Any, previous: Sequence[Any], kwargs: Mapping[str, Any]) -> tuple[list[Any], Mapping[str, str], list[Any]]:
        return await resolve_extracted_nodes(
            graphiti.clients,
            nodes,
            episode,
            list(previous),
            kwargs.get("entity_types"),
        )

    async def edges(graphiti: Any, episode: Any, extracted: list[Any], previous: Sequence[Any], nodes: list[Any], uuid_map: Mapping[str, str], kwargs: Mapping[str, Any]) -> tuple[list[Any], list[Any], list[Any]]:
        edge_types = kwargs.get("edge_types")
        edge_map = kwargs.get("edge_type_map") or (
            {("Entity", "Entity"): list(edge_types)} if edge_types else {("Entity", "Entity"): []}
        )
        return await graphiti._extract_and_resolve_edges(
            episode,
            extracted,
            list(previous),
            edge_map,
            kwargs["group_id"],
            edge_types,
            nodes,
            dict(uuid_map),
            kwargs.get("custom_extraction_instructions"),
        )

    async def attributes(graphiti: Any, nodes: list[Any], episode: Any, previous: Sequence[Any], new_edges: list[Any], kwargs: Mapping[str, Any]) -> list[Any]:
        return await extract_attributes_from_nodes(
            graphiti.clients,
            nodes,
            episode,
            list(previous),
            kwargs.get("entity_types"),
            edges=new_edges,
        )

    def continuation_k(**kwargs: Any) -> Mapping[str, Any]:
        return {
            "schema_version": CONTINUATION_K_SCHEMA,
            "seam": CONTINUATION_SEAM,
            **_canonical(kwargs),
        }

    return BuildStageBindings(utc_now, retrieve, make_episode, extract, resolve, edges, attributes, continuation_k)


async def build_to_seam_async(
    graphiti: Any,
    episode_kwargs: Mapping[str, Any],
    *,
    publication_frontier: int,
    backend_epoch: str,
    bindings: BuildStageBindings | None = None,
) -> BuildStageResult:
    """Execute native build semantics and stop before ``_process_episode_data``."""

    kwargs = dict(episode_kwargs)
    group_id = kwargs.get("group_id")
    if not isinstance(group_id, str) or not group_id:
        raise GraphitiObserverError("observer build requires an explicit isolated group_id")
    if kwargs.get("saga") is not None or kwargs.get("saga_previous_episode_uuid") is not None:
        raise GraphitiObserverError("guarded observer build does not support saga")
    if kwargs.get("update_communities", False) is not False:
        raise GraphitiObserverError("guarded observer build does not support communities")
    if isinstance(publication_frontier, bool) or not isinstance(publication_frontier, int) or publication_frontier < 0:
        raise GraphitiObserverError("publication frontier is invalid")
    selected = bindings or _default_bindings()
    driver = getattr(graphiti, "driver", None)
    if driver is not None and getattr(driver, "_database", group_id) != group_id:
        cloned = driver.clone(database=group_id)
        graphiti.driver = cloned
        graphiti.clients.driver = cloned
    now = selected.now()
    previous = tuple(await _maybe_await(selected.retrieve_previous(graphiti, kwargs)))
    episode = selected.make_episode(graphiti, kwargs, now)
    extracted_nodes, index_map = await _maybe_await(selected.extract_nodes(graphiti, episode, previous, kwargs))
    _ensure_single_partition_provenance(graphiti, episode, extracted_nodes)
    nodes, uuid_map, _duplicates = await _maybe_await(
        selected.resolve_nodes(graphiti, extracted_nodes, episode, previous, kwargs)
    )
    resolved_edges, invalidated_edges, new_edges = await _maybe_await(
        selected.extract_resolve_edges(
            graphiti,
            episode,
            extracted_nodes,
            previous,
            nodes,
            uuid_map,
            kwargs,
        )
    )
    entity_edges = list(resolved_edges) + list(invalidated_edges)
    hydrated_nodes = await _maybe_await(
        selected.extract_attributes(graphiti, nodes, episode, previous, list(new_edges), kwargs)
    )
    _assert_complete_embeddings(hydrated_nodes, entity_edges)
    driver = getattr(graphiti, "driver", None)
    provider = getattr(getattr(driver, "provider", None), "value", getattr(driver, "provider", "neo4j"))
    database = getattr(driver, "_database", group_id)
    k = selected.continuation_k(
        episodes=[episode],
        nodes=hydrated_nodes,
        entity_edges=entity_edges,
        node_episode_index_map=dict(index_map),
        now=now,
        group_id=group_id,
        store_raw_episode_content=bool(getattr(graphiti, "store_raw_episode_content", True)),
        driver_provider=str(provider).lower(),
        driver_database=str(database),
        backend_epoch=backend_epoch,
        publication_frontier=publication_frontier,
        saga=None,
        saga_previous_episode_uuid=None,
        update_communities=False,
    )
    return BuildStageResult(
        episode,
        previous,
        tuple(extracted_nodes),
        tuple(hydrated_nodes),
        tuple(entity_edges),
        dict(index_map),
        dict(k),
        0,
        tuple(nodes),
    )


__all__ = [
    "BackendProjection",
    "BuildStageBindings",
    "BuildStageResult",
    "build_semantic_cost_dag",
    "GraphitiObserverError",
    "GraphitiCaptureInstallation",
    "RequestObservationClient",
    "build_projection_delta",
    "build_to_seam_async",
    "canonical_digest",
    "current_provider_observation_scope",
    "exact_cosine_domain",
    "load_backend_projection_async",
    "load_node_embedding_domain_async",
    "observe_node_similarity_async",
    "observer_capture_scope",
]
