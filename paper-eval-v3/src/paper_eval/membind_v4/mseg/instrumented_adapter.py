"""Passive Graphiti operator/effect telemetry for the V4-MSEG-Q0 run.

This module wraps the pinned v3.1 semantic binding and adapter. It does not
change dependency, admission, scheduling, prompt, model, or persistence
behavior. Read scope remains explicitly unobservable because Graphiti's exact
candidate IDs are not returned by the wrapped public operator boundary.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding

from .observability import (
    MSEGOperatorContext,
    MSEGOperatorTraceObserver,
    current_trace_observer,
    trace_observer_scope,
    workflow_scope,
)


class MSEGInstrumentedAdapterError(ValueError):
    """The Q0 wrapper could not establish an exact, content-safe boundary."""


def _fail(code: str) -> MSEGInstrumentedAdapterError:
    return MSEGInstrumentedAdapterError(code)


def _member(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _uuid(value: object, code: str) -> str:
    selected = _member(value, "uuid")
    if not isinstance(selected, str) or not selected:
        raise _fail(code)
    return selected


def _sequence(value: object, code: str) -> list[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    return list(value)


def _uuid_list(value: object, code: str) -> list[str]:
    return sorted({_uuid(item, code) for item in _sequence(value, code)})


def _edge(value: object, code: str) -> dict[str, str]:
    edge_uuid = _uuid(value, code)
    source = _member(value, "source_node_uuid")
    target = _member(value, "target_node_uuid")
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        raise _fail(code)
    return {
        "edge_uuid": edge_uuid,
        "source_node_uuid": source,
        "target_node_uuid": target,
    }


def _edges(value: object, code: str) -> list[dict[str, str]]:
    selected = [_edge(item, code) for item in _sequence(value, code)]
    return sorted(
        selected,
        key=lambda item: (
            item["edge_uuid"],
            item["source_node_uuid"],
            item["target_node_uuid"],
        ),
    )


def _episodes(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def _effect_scope(
    operation: str,
    result: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> dict[str, object]:
    if operation == "extract_nodes":
        if not isinstance(result, tuple) or len(result) != 2:
            raise _fail("extract_nodes_result_invalid")
        return {"extracted_node_uuids": _uuid_list(result[0], "node_uuid_missing")}
    if operation == "resolve_extracted_nodes":
        if not isinstance(result, tuple) or len(result) != 3:
            raise _fail("resolve_nodes_result_invalid")
        nodes, uuid_map, duplicates = result
        if not isinstance(uuid_map, Mapping):
            raise _fail("resolve_nodes_uuid_map_invalid")
        targets = list(uuid_map.values())
        if any(not isinstance(value, str) or not value for value in targets):
            raise _fail("resolve_nodes_uuid_map_invalid")
        pairs: list[dict[str, str]] = []
        for item in _sequence(duplicates, "resolve_nodes_duplicates_invalid"):
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
                raise _fail("resolve_nodes_duplicates_invalid")
            pairs.append(
                {
                    "source_node_uuid": _uuid(item[0], "duplicate_node_uuid_missing"),
                    "target_node_uuid": _uuid(item[1], "duplicate_node_uuid_missing"),
                }
            )
        return {
            "resolved_node_uuids": _uuid_list(nodes, "node_uuid_missing"),
            "uuid_map_target_uuids": sorted(set(targets)),
            "duplicate_node_pairs": sorted(
                pairs,
                key=lambda item: (
                    item["source_node_uuid"], item["target_node_uuid"]
                ),
            ),
        }
    if operation in {"extract_edges", "resolve_edge_pointers"}:
        key = "extracted_edges" if operation == "extract_edges" else "pointer_edges"
        return {key: _edges(result, "edge_identity_missing")}
    if operation == "resolve_extracted_edges":
        if not isinstance(result, tuple) or len(result) != 3:
            raise _fail("resolve_edges_result_invalid")
        return {
            "resolved_edges": _edges(result[0], "edge_identity_missing"),
            "invalidated_edges": _edges(result[1], "edge_identity_missing"),
            "new_edges": _edges(result[2], "edge_identity_missing"),
        }
    if operation == "extract_attributes_from_nodes":
        return {"materialized_node_uuids": _uuid_list(result, "node_uuid_missing")}
    if operation == "process_episode_data":
        episode = args[1] if len(args) > 1 else kwargs.get("episode")
        nodes = args[2] if len(args) > 2 else kwargs.get("nodes")
        entity_edges = args[3] if len(args) > 3 else kwargs.get("entity_edges")
        if episode is None or nodes is None or entity_edges is None:
            raise _fail("process_effect_inputs_missing")
        if not isinstance(result, tuple) or len(result) != 2:
            raise _fail("process_effect_result_invalid")
        return {
            "episode_uuids": sorted(
                {_uuid(item, "episode_uuid_missing") for item in _episodes(episode)}
            ),
            "node_uuids": _uuid_list(nodes, "node_uuid_missing"),
            "entity_edges": _edges(entity_edges, "edge_identity_missing"),
            "episodic_edges": _edges(result[0], "edge_identity_missing"),
            "committed_episode_uuid": _uuid(result[1], "episode_uuid_missing"),
        }
    raise _fail("operator_unsupported")


async def _observe_async(
    operation: str,
    call: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object:
    if not callable(call):
        raise _fail("semantic_call_invalid")
    observer = current_trace_observer()
    if observer is None:
        value = call(*args, **kwargs)
        if not inspect.isawaitable(value):
            raise _fail("semantic_call_not_awaitable")
        return await value
    with observer.span(operation) as context:
        value = call(*args, **kwargs)
        if not inspect.isawaitable(value):
            raise _fail("semantic_call_not_awaitable")
        result = await value
        observer.record_effect(
            context,
            effect_scope=_effect_scope(operation, result, args, kwargs),
            persistent_write=operation == "process_episode_data",
        )
        return result


def _observe_sync(
    operation: str,
    call: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object:
    if not callable(call):
        raise _fail("semantic_call_invalid")
    observer = current_trace_observer()
    if observer is None:
        return call(*args, **kwargs)
    with observer.span(operation) as context:
        result = call(*args, **kwargs)
        observer.record_effect(
            context,
            effect_scope=_effect_scope(operation, result, args, kwargs),
            persistent_write=False,
        )
        return result


def instrument_graphiti_semantic_binding(
    binding: S5GraphitiSemanticBinding,
) -> S5GraphitiSemanticBinding:
    """Return an identity-preserving callable overlay around the pinned binding."""

    if not isinstance(binding, S5GraphitiSemanticBinding):
        raise _fail("semantic_binding_invalid")

    def async_wrapper(operation: str, call: object):
        async def wrapped(*args: object, **kwargs: object) -> object:
            return await _observe_async(operation, call, args, dict(kwargs))

        return wrapped

    def sync_wrapper(operation: str, call: object):
        def wrapped(*args: object, **kwargs: object) -> object:
            return _observe_sync(operation, call, args, dict(kwargs))

        return wrapped

    return S5GraphitiSemanticBinding(
        extract_nodes=async_wrapper("extract_nodes", binding.extract_nodes),
        resolve_extracted_nodes=async_wrapper(
            "resolve_extracted_nodes", binding.resolve_extracted_nodes
        ),
        extract_attributes_from_nodes=async_wrapper(
            "extract_attributes_from_nodes", binding.extract_attributes_from_nodes
        ),
        extract_edges=async_wrapper("extract_edges", binding.extract_edges),
        resolve_extracted_edges=async_wrapper(
            "resolve_extracted_edges", binding.resolve_extracted_edges
        ),
        resolve_edge_pointers=sync_wrapper(
            "resolve_edge_pointers", binding.resolve_edge_pointers
        ),
        process_episode_data=async_wrapper(
            "process_episode_data", binding.process_episode_data
        ),
        loader_verified=binding.loader_verified,
    )


class MSEGInstrumentedAdapter:
    """Establish workflow/observer scopes around an unchanged v3.1 adapter."""

    def __init__(
        self,
        *,
        inner: object,
        stream_id: str,
        observer: MSEGOperatorTraceObserver,
    ) -> None:
        if inner is None or not callable(getattr(inner, "prepare", None)) or not callable(
            getattr(inner, "bind", None)
        ):
            raise _fail("inner_adapter_invalid")
        if not isinstance(stream_id, str) or not stream_id:
            raise _fail("stream_id_invalid")
        if not isinstance(observer, MSEGOperatorTraceObserver):
            raise _fail("operator_observer_invalid")
        self._inner = inner
        self._stream_id = stream_id
        self._observer = observer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @staticmethod
    def _source_sequence(compile_input: object) -> int:
        source = getattr(compile_input, "source", None)
        value = getattr(source, "source_sequence", None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _fail("source_sequence_invalid")
        return value

    async def prepare(self, compile_input: object) -> object:
        sequence = self._source_sequence(compile_input)
        with workflow_scope(
            stream_id=self._stream_id,
            source_sequence=sequence,
            phase="COMPILE",
        ):
            with trace_observer_scope(self._observer):
                return await self._inner.prepare(compile_input)

    async def bind(
        self,
        compile_input: object,
        artifact: object,
        *,
        logical_time_ns: int,
    ) -> object:
        sequence = self._source_sequence(compile_input)
        with workflow_scope(
            stream_id=self._stream_id,
            source_sequence=sequence,
            phase="FRONTIER",
        ):
            with trace_observer_scope(self._observer):
                return await self._inner.bind(
                    compile_input,
                    artifact,
                    logical_time_ns=logical_time_ns,
                )


__all__ = [
    "MSEGInstrumentedAdapter",
    "MSEGInstrumentedAdapterError",
    "instrument_graphiti_semantic_binding",
]
