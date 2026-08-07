"""Stable tie ordering for Graphiti search results used in cached prompts."""

from __future__ import annotations

import json
import re
from functools import wraps
from typing import Any, Awaitable, Callable


_STABILIZER_MARKER = "_membind_stable_edge_search"
_NODE_STABILIZER_MARKER = "_membind_stable_node_resolution"
_EDGE_QUERY_STABILIZER_MARKER = "_membind_stable_edge_query"
_NODE_QUERY_STABILIZER_MARKER = "_membind_stable_node_query"
_EDGE_MATCH_RE = re.compile(
    r"\(\s*n\s*:\s*Entity\s*\)\s*-\s*\[\s*e\s*:\s*RELATES_TO(?:\s*\{[^]]*\})?\s*\]"
    r"\s*->\s*\(\s*m\s*:\s*Entity\s*\)",
    re.IGNORECASE,
)
_SCORE_LIMIT_RE = re.compile(
    r"\bORDER\s+BY\s+score\s+DESC(?=\s+LIMIT\s+\$limit\b)",
    re.IGNORECASE,
)
_NODE_MATCH_RE = re.compile(
    r"(?:\bMATCH\s*\(\s*n\s*:\s*Entity\s*\)|"
    r"\bYIELD\s+node\s+AS\s+n\s*,\s*score\b)",
    re.IGNORECASE,
)
_EDGE_SCORE_ORDER = """ORDER BY score DESC, e.fact ASC,
            e.name ASC,
            coalesce(toString(e.valid_at), '') ASC,
            coalesce(toString(e.invalid_at), '') ASC,
            n.name ASC,
            m.name ASC"""
_NODE_SCORE_ORDER = """ORDER BY score DESC, toLower(coalesce(n.name, '')) ASC,
            coalesce(n.name, '') ASC,
            toLower(coalesce(n.summary, '')) ASC,
            coalesce(n.summary, '') ASC,
            labels(n) ASC"""


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


def _node_logical_key(node: Any) -> tuple[str, ...]:
    """Return the deterministic fields that Graphiti puts in dedupe prompts.

    Candidate IDs are assigned *after* this ordering, so UUIDs and backend
    result order must not participate in the key.  The fields mirror
    ``node_operations._resolve_with_llm``: name, labels, summary and
    attributes.  ``default=str`` keeps the key total for timestamp/UUID-like
    values without exposing those values as ordering inputs.
    """

    name = _stable_text(getattr(node, "name", ""))
    labels = tuple(
        sorted(
            (_stable_text(label) for label in (getattr(node, "labels", []) or [])),
            key=lambda value: (value.casefold(), value),
        )
    )
    summary = _stable_text(getattr(node, "summary", ""))
    attributes = getattr(node, "attributes", {}) or {}
    attributes_json = json.dumps(
        attributes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return (
        name.casefold(),
        name,
        json.dumps(labels, ensure_ascii=False, separators=(",", ":")),
        summary.casefold(),
        summary,
        attributes_json,
    )


def stabilize_node_candidates(candidates: list[Any]) -> list[Any]:
    """Sort a selected node candidate set without changing its membership.

    This is intentionally a presentation-only normalization.  Graphiti still
    decides the candidate cutoff and duplicate UUID filtering; this function
    only determines the order used when assigning prompt ``candidate_id``
    values and preserves the original list object mutation contract.
    """

    candidates[:] = sorted(candidates, key=_node_logical_key)
    return candidates


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


def stabilize_edge_search_query(query: Any) -> Any:
    """Add UUID-independent tie ordering before Neo4j edge-search cutoffs."""

    if not isinstance(query, str) or _EDGE_MATCH_RE.search(query) is None:
        return query
    return _SCORE_LIMIT_RE.sub(_EDGE_SCORE_ORDER, query, count=1)


def install_edge_query_stabilizer(driver: Any) -> bool:
    """Install the pre-limit edge-search ordering on one Graphiti driver."""

    if getattr(driver, _EDGE_QUERY_STABILIZER_MARKER, False):
        return False

    current: Callable[..., Awaitable[Any]] = driver.execute_query

    @wraps(current)
    async def stable_execute_query(cypher_query_: Any, *args: Any, **kwargs: Any) -> Any:
        return await current(stabilize_edge_search_query(cypher_query_), *args, **kwargs)

    driver.execute_query = stable_execute_query
    setattr(driver, _EDGE_QUERY_STABILIZER_MARKER, True)
    return True


def stabilize_node_search_query(query: Any) -> Any:
    """Add UUID-independent tie ordering before Neo4j node-search cutoffs."""

    if (
        not isinstance(query, str)
        or _EDGE_MATCH_RE.search(query) is not None
        or _NODE_MATCH_RE.search(query) is None
    ):
        return query
    return _SCORE_LIMIT_RE.sub(_NODE_SCORE_ORDER, query, count=1)


def install_node_query_stabilizer(driver: Any) -> bool:
    """Install the pre-limit node-search ordering on one Graphiti driver."""

    if getattr(driver, _NODE_QUERY_STABILIZER_MARKER, False):
        return False

    current: Callable[..., Awaitable[Any]] = driver.execute_query

    @wraps(current)
    async def stable_execute_query(cypher_query_: Any, *args: Any, **kwargs: Any) -> Any:
        return await current(stabilize_node_search_query(cypher_query_), *args, **kwargs)

    driver.execute_query = stable_execute_query
    setattr(driver, _NODE_QUERY_STABILIZER_MARKER, True)
    return True


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


def install_node_resolution_stabilizer(module: Any | None = None) -> bool:
    """Install deterministic ordering at Graphiti's node prompt boundary."""

    if module is None:
        from graphiti_core.utils.maintenance import node_operations as module

    current: Callable[..., Any] = module._merge_candidate_nodes
    if getattr(current, _NODE_STABILIZER_MARKER, False):
        return False

    @wraps(current)
    def stable_merge(*args: Any, **kwargs: Any) -> Any:
        merged = current(*args, **kwargs)
        return stabilize_node_candidates(merged)

    setattr(stable_merge, _NODE_STABILIZER_MARKER, True)
    module._merge_candidate_nodes = stable_merge
    return True
