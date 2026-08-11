"""C2-only measurement adapters for Graphiti's state-dependent suffix.

The frozen C1 wrappers deliberately stay generic.  This module adds only the
Graphiti 0.29.3 boundaries required by E1: candidate embedding/search,
invalidation observations, and a group-scoped graph-prefix snapshot.  Every
patch is reversible and preserves the wrapped call's arguments, return value,
and exception object.
"""

from __future__ import annotations

import contextvars
import functools
import importlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from native_characterization_instrumentation import PatchHandle
from native_characterization_tracing import TraceRecorder


GRAPH_PREFIX_QUERY = """
CALL {
  MATCH (n)
  WHERE n.group_id = $group_id
  RETURN count(n) AS node_count
}
CALL {
  MATCH ()-[r]->()
  WHERE r.group_id = $group_id
  RETURN count(r) AS relationship_count
}
RETURN node_count, relationship_count
"""

_GRAPH_NAMESPACE_RE = re.compile(r"^nc-e1e2-[0-9a-f]{16}$")
_ACTIVE_INSTALLATIONS: dict[
    tuple[int, int, int, int], tuple[TraceRecorder, PatchHandle]
] = {}


class C2MeasurementError(RuntimeError):
    """Sanitized failure in the C2 measurement path."""


@dataclass
class _EdgeResolutionState:
    extracted_edge_count: int
    embedding_call_count: int = 0


def _replace_attribute(owner: Any, name: str, replacement: Any) -> Callable[[], None]:
    namespace = getattr(owner, "__dict__", {})
    had_own_attribute = name in namespace
    previous_own_value = namespace.get(name)
    setattr(owner, name, replacement)

    def restore() -> None:
        if had_own_attribute:
            setattr(owner, name, previous_own_value)
        else:
            try:
                delattr(owner, name)
            except AttributeError:
                pass

    return restore


def _sequence_count(value: Any) -> int:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return len(value)
    return 0


def _first_argument(
    args: tuple[Any, ...], kwargs: Mapping[str, Any], index: int, names: tuple[str, ...]
) -> Any:
    if len(args) > index:
        return args[index]
    for name in names:
        if name in kwargs:
            return kwargs[name]
    return None


def _candidate_result_count(result: Any) -> int:
    edges = getattr(result, "edges", None)
    if isinstance(edges, Sequence) and not isinstance(edges, str | bytes):
        return len(edges)
    if isinstance(result, Sequence) and not isinstance(result, str | bytes):
        if result and all(
            isinstance(item, Sequence) and not isinstance(item, str | bytes)
            for item in result
        ):
            return sum(len(item) for item in result)
        return len(result)
    return 0


def _edge_candidate_kind(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> str:
    search_filter = _first_argument(args, kwargs, 4, ("search_filter",))
    if isinstance(search_filter, str):
        return (
            "edge-dedup"
            if "dedup" in search_filter.casefold()
            else "edge-invalidation"
        )
    edge_uuids = getattr(search_filter, "edge_uuids", None)
    return "edge-dedup" if edge_uuids is not None else "edge-invalidation"


def _nonnegative_count(value: Any, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise C2MeasurementError(reason)
    return value


async def collect_graph_prefix_size(driver: Any, graph_namespace: str) -> dict[str, int]:
    """Read the actual group-scoped graph size outside ``add_episode`` timing."""

    if _GRAPH_NAMESPACE_RE.fullmatch(str(graph_namespace)) is None:
        raise C2MeasurementError("graph_prefix_namespace_invalid")
    execute_query = getattr(driver, "execute_query", None)
    if not callable(execute_query):
        raise C2MeasurementError("graph_prefix_driver_invalid")
    try:
        result = await execute_query(
            GRAPH_PREFIX_QUERY,
            params={"group_id": str(graph_namespace)},
        )
        records = getattr(result, "records", None)
        if (
            not isinstance(records, Sequence)
            or isinstance(records, str | bytes)
            or len(records) != 1
        ):
            raise C2MeasurementError("graph_prefix_result_invalid")
        record = records[0]
        node_count = _nonnegative_count(
            record["node_count"], "graph_prefix_node_count_invalid"
        )
        relationship_count = _nonnegative_count(
            record["relationship_count"],
            "graph_prefix_relationship_count_invalid",
        )
    except C2MeasurementError:
        raise
    except BaseException:
        raise C2MeasurementError("graph_prefix_query_failed") from None
    return {
        "graph_prefix_node_count": node_count,
        "graph_prefix_relationship_count": relationship_count,
    }


def install_c2_measurement_adapter(
    graphiti: Any,
    recorder: TraceRecorder,
    *,
    phase_module: Any | None = None,
    node_module: Any | None = None,
    edge_module: Any | None = None,
) -> PatchHandle:
    """Install the supplemental E1 boundaries without changing frozen phases."""

    phase_owner = phase_module or importlib.import_module("graphiti_core.graphiti")
    node_owner = node_module or importlib.import_module(
        "graphiti_core.utils.maintenance.node_operations"
    )
    edge_owner = edge_module or importlib.import_module(
        "graphiti_core.utils.maintenance.edge_operations"
    )
    key = (id(graphiti), id(phase_owner), id(node_owner), id(edge_owner))
    active = _ACTIVE_INSTALLATIONS.get(key)
    if active is not None:
        active_recorder, active_handle = active
        if active_recorder is not recorder:
            raise RuntimeError("C2 measurement adapter is active with another recorder")
        return active_handle

    required = (
        (phase_owner, "resolve_extracted_edges"),
        (node_owner, "_semantic_candidate_search"),
        (edge_owner, "search"),
        (edge_owner, "create_entity_edge_embeddings"),
        (edge_owner, "resolve_extracted_edge"),
        (edge_owner, "resolve_edge_contradictions"),
    )
    missing = [name for owner, name in required if not callable(getattr(owner, name, None))]
    if missing:
        raise AttributeError(
            "pinned Graphiti C2 measurement aliases missing: "
            + ",".join(sorted(set(missing)))
        )
    embedder = getattr(graphiti, "embedder", None)
    if embedder is None:
        raise AttributeError("pinned Graphiti embedder missing")

    candidate_embedding_kind: contextvars.ContextVar[str | None] = contextvars.ContextVar(
        f"native_characterization_candidate_embedding_{id(graphiti)}", default=None
    )
    edge_state: contextvars.ContextVar[_EdgeResolutionState | None] = contextvars.ContextVar(
        f"native_characterization_edge_resolution_{id(graphiti)}", default=None
    )
    handle = PatchHandle()

    def remove_active() -> None:
        current = _ACTIVE_INSTALLATIONS.get(key)
        if current is not None and current[1] is handle:
            _ACTIVE_INSTALLATIONS.pop(key, None)

    handle.add(remove_active)
    try:
        for method_name, batch in (("create", False), ("create_batch", True)):
            original_embedding = getattr(embedder, method_name, None)
            if not callable(original_embedding):
                continue

            def build_embedding_wrapper(
                bound_original: Callable[..., Any],
                operation: str,
                is_batch: bool,
            ) -> Callable[..., Any]:
                @functools.wraps(bound_original)
                async def measured(*args: Any, **kwargs: Any) -> Any:
                    kind = candidate_embedding_kind.get()
                    if kind is None:
                        return await bound_original(*args, **kwargs)
                    value = _first_argument(
                        args,
                        kwargs,
                        0,
                        ("input_data_list", "input_data")
                        if is_batch
                        else ("input_data",),
                    )
                    text_count = _sequence_count(value) if is_batch else 1
                    with recorder.span(
                        "candidate-embedding",
                        operation_class=kind,
                        metadata={
                            "embedding_operation": operation,
                            "text_count": text_count,
                        },
                    ):
                        return await bound_original(*args, **kwargs)

                return measured

            replacement = build_embedding_wrapper(
                original_embedding, method_name, batch
            )
            handle.add(_replace_attribute(embedder, method_name, replacement))

        original_node_search = node_owner._semantic_candidate_search

        @functools.wraps(original_node_search)
        async def semantic_candidate_search(*args: Any, **kwargs: Any) -> Any:
            extracted_nodes = _first_argument(
                args, kwargs, 1, ("extracted_nodes",)
            )
            query_count = _sequence_count(extracted_nodes)
            with recorder.span(
                "candidate-search",
                operation_class="node-dedup",
                metadata={
                    "candidate_count": 0,
                    "candidate_query_count": query_count,
                },
            ) as span:
                token = candidate_embedding_kind.set("node-dedup")
                try:
                    result = await original_node_search(*args, **kwargs)
                finally:
                    candidate_embedding_kind.reset(token)
                span.add_metadata("candidate_count", _candidate_result_count(result))
                if query_count == 0:
                    with recorder.span(
                        "candidate-embedding",
                        operation_class="node-dedup-empty",
                        metadata={"embedding_operation": "none", "text_count": 0},
                    ):
                        pass
                return result

        handle.add(
            _replace_attribute(
                node_owner, "_semantic_candidate_search", semantic_candidate_search
            )
        )

        original_edge_search = edge_owner.search

        @functools.wraps(original_edge_search)
        async def edge_candidate_search(*args: Any, **kwargs: Any) -> Any:
            kind = _edge_candidate_kind(args, kwargs)
            with recorder.span(
                "candidate-search",
                operation_class=kind,
                metadata={"candidate_count": 0, "candidate_query_count": 1},
            ) as span:
                token = candidate_embedding_kind.set(kind)
                try:
                    result = await original_edge_search(*args, **kwargs)
                finally:
                    candidate_embedding_kind.reset(token)
                span.add_metadata("candidate_count", _candidate_result_count(result))
                return result

        handle.add(_replace_attribute(edge_owner, "search", edge_candidate_search))

        original_edge_embeddings = edge_owner.create_entity_edge_embeddings

        @functools.wraps(original_edge_embeddings)
        async def edge_embeddings(*args: Any, **kwargs: Any) -> Any:
            state = edge_state.get()
            if state is None:
                return await original_edge_embeddings(*args, **kwargs)
            call_index = state.embedding_call_count
            state.embedding_call_count += 1
            if call_index != 0:
                return await original_edge_embeddings(*args, **kwargs)
            edges = _first_argument(args, kwargs, 1, ("edges",))
            with recorder.span(
                "candidate-embedding",
                operation_class="edge-presearch",
                metadata={
                    "embedding_operation": "create_entity_edge_embeddings",
                    "text_count": _sequence_count(edges),
                },
            ):
                return await original_edge_embeddings(*args, **kwargs)

        handle.add(
            _replace_attribute(
                edge_owner, "create_entity_edge_embeddings", edge_embeddings
            )
        )

        original_contradictions = edge_owner.resolve_edge_contradictions

        @functools.wraps(original_contradictions)
        def resolve_edge_contradictions(*args: Any, **kwargs: Any) -> Any:
            candidates = _first_argument(
                args, kwargs, 1, ("invalidation_candidates",)
            )
            with recorder.span(
                "invalidation-update",
                operation_class="existing-edge-mutation",
                metadata={
                    "invalidation_candidate_count": _sequence_count(candidates),
                    "invalidated_count": 0,
                    "new_edge_expired_count": 0,
                    "timing_scope": "timed-existing-edge-mutation",
                },
            ) as span:
                result = original_contradictions(*args, **kwargs)
                span.add_metadata("invalidated_count", _sequence_count(result))
                return result

        handle.add(
            _replace_attribute(
                edge_owner, "resolve_edge_contradictions", resolve_edge_contradictions
            )
        )

        original_edge_resolution = edge_owner.resolve_extracted_edge

        @functools.wraps(original_edge_resolution)
        async def resolve_extracted_edge(*args: Any, **kwargs: Any) -> Any:
            extracted_edge = _first_argument(args, kwargs, 1, ("extracted_edge",))
            existing_edges = _first_argument(args, kwargs, 3, ("existing_edges",))
            expired_before = getattr(extracted_edge, "expired_at", None)
            result = await original_edge_resolution(*args, **kwargs)
            resolved_edge = result[0] if isinstance(result, tuple) and result else None
            new_edge_expired = int(
                expired_before is None
                and getattr(resolved_edge, "expired_at", None) is not None
                and getattr(resolved_edge, "uuid", None)
                == getattr(extracted_edge, "uuid", None)
            )
            invalidated_result = (
                result[1] if isinstance(result, tuple) and len(result) > 1 else None
            )
            # Graphiti performs new-edge expiry in a small inline CPU block with
            # no callable boundary.  Record its outcome without attributing the
            # surrounding LLM dedup latency to invalidation.
            with recorder.span(
                "invalidation-update",
                operation_class="new-edge-expiration-observation",
                metadata={
                    "invalidation_candidate_count": _sequence_count(existing_edges),
                    "invalidated_result_count": _sequence_count(invalidated_result),
                    "new_edge_expired_count": new_edge_expired,
                    "timing_scope": "count-only-post-observation",
                },
            ):
                pass
            return result

        handle.add(
            _replace_attribute(
                edge_owner, "resolve_extracted_edge", resolve_extracted_edge
            )
        )

        original_phase_edge_resolution = phase_owner.resolve_extracted_edges

        @functools.wraps(original_phase_edge_resolution)
        async def phase_edge_resolution(*args: Any, **kwargs: Any) -> Any:
            extracted_edges = _first_argument(
                args, kwargs, 1, ("extracted_edges", "edges")
            )
            extracted_count = _sequence_count(extracted_edges)
            token = edge_state.set(_EdgeResolutionState(extracted_count))
            try:
                if extracted_count == 0:
                    with recorder.span(
                        "candidate-search",
                        operation_class="edge-empty",
                        metadata={
                            "candidate_count": 0,
                            "candidate_query_count": 0,
                        },
                    ):
                        pass
                    with recorder.span(
                        "invalidation-update",
                        operation_class="edge-empty",
                        metadata={
                            "invalidation_candidate_count": 0,
                            "invalidated_count": 0,
                            "new_edge_expired_count": 0,
                            "timing_scope": "explicit-no-op",
                        },
                    ):
                        pass
                return await original_phase_edge_resolution(*args, **kwargs)
            finally:
                edge_state.reset(token)

        handle.add(
            _replace_attribute(
                phase_owner, "resolve_extracted_edges", phase_edge_resolution
            )
        )
    except BaseException:
        handle.restore()
        raise
    _ACTIVE_INSTALLATIONS[key] = (recorder, handle)
    return handle
