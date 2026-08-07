"""Stable tie ordering for Graphiti search results used in cached prompts."""

from __future__ import annotations

from functools import wraps
from typing import Any, Awaitable, Callable


_STABILIZER_MARKER = "_membind_stable_edge_search"


def _stable_text(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _edge_logical_key(edge: Any) -> tuple[str, ...]:
    fact = _stable_text(getattr(edge, "fact", ""))
    name = _stable_text(getattr(edge, "name", ""))
    return (
        fact.casefold(),
        fact,
        name.casefold(),
        name,
        _stable_text(getattr(edge, "valid_at", None)),
        _stable_text(getattr(edge, "invalid_at", None)),
    )


def stabilize_search_results(results: Any) -> Any:
    """Canonically present an already-selected edge candidate set."""

    edges = list(getattr(results, "edges", []) or [])
    scores = list(getattr(results, "edge_reranker_scores", []) or [])
    if len(edges) != len(scores):
        return results

    ranked = sorted(
        zip(edges, scores, strict=True),
        key=lambda pair: _edge_logical_key(pair[0]),
    )
    results.edges = [edge for edge, _ in ranked]
    results.edge_reranker_scores = [score for _, score in ranked]
    return results


def install_edge_search_stabilizer(module: Any | None = None) -> bool:
    """Install the deterministic result wrapper on Graphiti edge maintenance search."""

    if module is None:
        from graphiti_core.utils.maintenance import edge_operations as module

    current: Callable[..., Awaitable[Any]] = module.search
    if getattr(current, _STABILIZER_MARKER, False):
        return False

    @wraps(current)
    async def stable_search(*args: Any, **kwargs: Any) -> Any:
        return stabilize_search_results(await current(*args, **kwargs))

    setattr(stable_search, _STABILIZER_MARKER, True)
    module.search = stable_search
    return True
