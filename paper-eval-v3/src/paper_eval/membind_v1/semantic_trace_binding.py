"""Passive phase tracing for MemBind-v1's direct Graphiti semantic binding.

Native ``Graphiti.add_episode`` uses aliases inside ``graphiti_core.graphiti``
and can be instrumented there.  The node-only adapter deliberately holds the
pinned semantic functions directly, so this decorator supplies the same phase
timing surface without changing arguments, return values, or exception flow.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding


_PHASES = {
    "extract_nodes": "node-extraction",
    "resolve_extracted_nodes": "node-resolution",
    "extract_edges": "edge-extraction",
    "resolve_edge_pointers": "edge-pointer-resolution",
    "resolve_extracted_edges": "edge-resolution",
    "extract_attributes_from_nodes": "attributes-summary",
    "process_episode_data": "publication",
}


class SemanticTraceBindingError(ValueError):
    """The direct semantic tracing boundary is not safe to install."""


def _fail(code: str) -> SemanticTraceBindingError:
    return SemanticTraceBindingError(code)


def _traced(operation: Callable[..., Any], *, phase: str, recorder: object) -> Callable[..., Any]:
    if not inspect.iscoroutinefunction(operation):
        @functools.wraps(operation)
        def wrapped_sync(*args: Any, **kwargs: Any) -> Any:
            with recorder.span(phase):
                value = operation(*args, **kwargs)
                if inspect.isawaitable(value):
                    raise _fail("semantic operation contract is ambiguous")
                return value

        return wrapped_sync

    @functools.wraps(operation)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        with recorder.span(phase):
            value = operation(*args, **kwargs)
            if not inspect.isawaitable(value):
                raise _fail("semantic operation must be async")
            return await value

    return wrapped


def trace_semantic_binding(
    binding: S5GraphitiSemanticBinding,
    recorder: object,
) -> S5GraphitiSemanticBinding:
    """Return an equivalent binding with content-free timing spans.

    The returned object is a separate frozen binding, so the original pinned
    binding remains available for identity checks and cannot be mutated by a
    live attempt.  When no ``episode_scope`` is active the recorder emits no
    rows, which preserves the adapter's existing pure/offline behavior.
    """

    if not isinstance(binding, S5GraphitiSemanticBinding):
        raise _fail("semantic binding invalid")
    if not callable(getattr(recorder, "span", None)):
        raise _fail("trace recorder invalid")
    traced = {
        name: _traced(getattr(binding, name), phase=phase, recorder=recorder)
        for name, phase in _PHASES.items()
    }
    return S5GraphitiSemanticBinding(
        **traced,
        loader_verified=binding.loader_verified,
    )


__all__ = ["SemanticTraceBindingError", "trace_semantic_binding"]
