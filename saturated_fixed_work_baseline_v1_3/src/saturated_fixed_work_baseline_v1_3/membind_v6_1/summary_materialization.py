"""Deterministic, source-grounded entity-summary materialization for V6.1."""

from __future__ import annotations

import hashlib
import inspect
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


SUMMARY_POLICY_ID = "provenance_grounded_incremental_materialized_summary_v3"
MAX_MENTION_SPAN_CHARS = 320
MAX_MENTION_SPANS_PER_EPISODE = 1
MAX_REGISTERED_UNITS = 64


class GroundedSummaryCompatibilityError(RuntimeError):
    """Raised when the installed Graphiti summary seam no longer matches."""


@dataclass(frozen=True)
class CertifiedSummaryUnit:
    text: str
    provenance_kind: Literal["edge_fact", "episode_span"]
    source_sha256: str
    truncated: bool = False


@dataclass(frozen=True)
class _CandidateUnit:
    unit: CertifiedSummaryUnit
    origin: Literal["current_edge", "current_episode", "prior_certified"]


SummaryRegistry = dict[tuple[str, str], tuple[CertifiedSummaryUnit, ...]]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _node_key(node: Any) -> tuple[str, str]:
    return (str(getattr(node, "group_id", "")), str(getattr(node, "uuid", "")))


def _episode_contents(episode: Any | Sequence[Any] | None) -> list[str]:
    if episode is None:
        return []
    values = episode if isinstance(episode, (list, tuple)) else [episode]
    return [
        content
        for value in values
        if isinstance((content := getattr(value, "content", None)), str) and content
    ]


def _mention_pattern(name: str) -> re.Pattern[str] | None:
    tokens = name.split()
    if not tokens:
        return None
    body = r"\s+".join(re.escape(token) for token in tokens)
    if tokens[0][0].isalnum():
        body = rf"(?<!\w){body}"
    if tokens[-1][-1].isalnum():
        body = rf"{body}(?!\w)"
    return re.compile(body, flags=re.IGNORECASE | re.UNICODE)


def _bounded_source_span(content: str, start: int, end: int) -> tuple[str, int, int]:
    line_start = content.rfind("\n", 0, start) + 1
    line_end_pos = content.find("\n", end)
    line_end = len(content) if line_end_pos < 0 else line_end_pos
    boundary_marks = (".", "!", "?", ";", "。", "！", "？", "；")
    left_boundaries = [content.rfind(mark, line_start, start) for mark in boundary_marks]
    right_candidates = [
        position
        for mark in boundary_marks
        if (position := content.find(mark, end, line_end)) >= 0
    ]
    sentence_start = max(left_boundaries) + 1
    sentence_end = min(right_candidates) + 1 if right_candidates else line_end
    if sentence_end - sentence_start <= MAX_MENTION_SPAN_CHARS:
        span_start, span_end = sentence_start, sentence_end
    else:
        mention_length = end - start
        remaining = max(0, MAX_MENTION_SPAN_CHARS - mention_length)
        before = min(start - sentence_start, remaining // 2)
        after = min(sentence_end - end, remaining - before)
        before = min(start - sentence_start, remaining - after)
        span_start, span_end = start - before, end + after
        if span_start > sentence_start and not content[span_start - 1].isspace():
            next_space = content.find(" ", span_start, start)
            if next_space >= 0:
                span_start = next_space + 1
        if span_end < sentence_end and span_end < len(content) and not content[span_end].isspace():
            previous_space = content.rfind(" ", end, span_end)
            if previous_space >= end:
                span_end = previous_space
    raw = content[span_start:span_end]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    span_start += leading
    span_end -= trailing
    return content[span_start:span_end], span_start, span_end


def _episode_mention_units(node_name: str, contents: Sequence[str]) -> list[CertifiedSummaryUnit]:
    pattern = _mention_pattern(node_name)
    if pattern is None:
        return []
    units: list[CertifiedSummaryUnit] = []
    seen: set[str] = set()
    for content in contents:
        source_hash = _sha256(content)
        emitted = 0
        for match in pattern.finditer(content):
            span, _, _ = _bounded_source_span(content, match.start(), match.end())
            key = _normalized(span)
            if not key or key in seen:
                continue
            seen.add(key)
            units.append(
                CertifiedSummaryUnit(
                    text=span,
                    provenance_kind="episode_span",
                    source_sha256=source_hash,
                )
            )
            emitted += 1
            if emitted >= MAX_MENTION_SPANS_PER_EPISODE:
                break
    return units


def _edge_fact_units(node: Any, edges_by_node: Mapping[str, Sequence[Any]]) -> list[CertifiedSummaryUnit]:
    facts = {
        fact.strip()
        for edge in edges_by_node.get(str(getattr(node, "uuid", "")), ())
        if isinstance((fact := getattr(edge, "fact", None)), str) and fact.strip()
    }
    return [
        CertifiedSummaryUnit(
            text=fact,
            provenance_kind="edge_fact",
            source_sha256=_sha256(fact),
        )
        for fact in sorted(facts, key=lambda value: (_normalized(value), value))
    ]


def _deduplicate(candidates: Sequence[_CandidateUnit]) -> list[_CandidateUnit]:
    result: list[_CandidateUnit] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _normalized(candidate.unit.text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _select_units(
    candidates: Sequence[_CandidateUnit], max_summary_chars: int
) -> tuple[list[_CandidateUnit], list[_CandidateUnit]]:
    selected: list[_CandidateUnit] = []
    dropped: list[_CandidateUnit] = []
    used = 0
    for candidate in candidates:
        separator = 1 if selected else 0
        available = max_summary_chars - used - separator
        if available <= 0:
            dropped.append(candidate)
            continue
        if len(candidate.unit.text) <= available:
            selected.append(candidate)
            used += separator + len(candidate.unit.text)
            continue
        if selected:
            dropped.append(candidate)
            continue
        # A bounded prefix remains an exact substring of its certified source.
        truncated = _CandidateUnit(
            unit=CertifiedSummaryUnit(
                text=candidate.unit.text[:available],
                provenance_kind=candidate.unit.provenance_kind,
                source_sha256=candidate.unit.source_sha256,
                truncated=True,
            ),
            origin=candidate.origin,
        )
        selected.append(truncated)
        dropped.append(candidate)
        used += len(truncated.unit.text)
    return selected, dropped


def _unit_evidence(candidate: _CandidateUnit) -> dict[str, Any]:
    return {
        "origin": candidate.origin,
        "provenance_kind": candidate.unit.provenance_kind,
        "source_sha256": candidate.unit.source_sha256,
        "span_sha256": _sha256(candidate.unit.text),
        "chars": len(candidate.unit.text),
        "truncated": candidate.unit.truncated,
    }


def _estimated_upstream_flights(
    eligible: Sequence[Any],
    edges_by_node: Mapping[str, Sequence[Any]],
    *,
    episode_present: bool,
    max_summary_chars: int,
    max_nodes_per_flight: int,
) -> int:
    needing_llm = 0
    for node in eligible:
        node_edges = edges_by_node.get(str(getattr(node, "uuid", "")), ())
        edge_facts = "\n".join(
            fact
            for edge in node_edges
            if isinstance((fact := getattr(edge, "fact", None)), str) and fact
        )
        summary = str(getattr(node, "summary", "") or "")
        summary_with_edges = f"{summary}\n{edge_facts}".strip() if edge_facts else summary
        if summary_with_edges and len(summary_with_edges) <= max_summary_chars * 2:
            continue
        if not summary_with_edges and not episode_present:
            continue
        needing_llm += 1
    return math.ceil(needing_llm / max_nodes_per_flight) if needing_llm else 0


async def materialize_grounded_summaries(
    nodes: Sequence[Any],
    episode: Any | Sequence[Any] | None,
    should_summarize_node: Callable[[Any], Awaitable[bool]] | None,
    edges_by_node: Mapping[str, Sequence[Any]],
    *,
    registry: SummaryRegistry,
    evidence_sink: Callable[[dict[str, Any]], None],
    max_summary_chars: int,
    max_nodes_per_flight: int,
) -> None:
    """Materialize summaries from exact edge facts, episode spans, and certified prior units."""

    contents = _episode_contents(episode)
    eligible: list[Any] = []
    for node in nodes:
        if should_summarize_node is not None and not await should_summarize_node(node):
            continue
        eligible.append(node)

    estimated_flights = _estimated_upstream_flights(
        eligible,
        edges_by_node,
        episode_present=bool(contents),
        max_summary_chars=max_summary_chars,
        max_nodes_per_flight=max_nodes_per_flight,
    )
    totals = {
        "materialized_node_count": 0,
        "unchanged_node_count": 0,
        "empty_grounding_node_count": 0,
        "edge_fact_unit_count": 0,
        "episode_span_unit_count": 0,
        "prior_certified_unit_count": 0,
        "selected_unit_count": 0,
        "dropped_unit_count": 0,
    }
    episode_hashes = [_sha256(content) for content in contents]
    for node in eligible:
        key = _node_key(node)
        prior = registry.get(key, ())
        candidates = _deduplicate(
            [
                *[
                    _CandidateUnit(unit=unit, origin="current_edge")
                    for unit in _edge_fact_units(node, edges_by_node)
                ],
                *[
                    _CandidateUnit(unit=unit, origin="current_episode")
                    for unit in _episode_mention_units(str(getattr(node, "name", "")), contents)
                ],
                *[
                    _CandidateUnit(unit=unit, origin="prior_certified")
                    for unit in prior
                ],
            ]
        )
        selected, dropped = _select_units(candidates, max_summary_chars)
        previous = str(getattr(node, "summary", "") or "")
        summary = "\n".join(candidate.unit.text for candidate in selected)
        if selected:
            setattr(node, "summary", summary)
            registry[key] = tuple(
                candidate.unit for candidate in candidates[:MAX_REGISTERED_UNITS]
            )
            totals["materialized_node_count"] += 1
        else:
            totals["empty_grounding_node_count"] += 1
        if summary == previous:
            totals["unchanged_node_count"] += 1
        for candidate in candidates:
            if candidate.origin == "current_edge":
                totals["edge_fact_unit_count"] += 1
            elif candidate.origin == "current_episode":
                totals["episode_span_unit_count"] += 1
            else:
                totals["prior_certified_unit_count"] += 1
        totals["selected_unit_count"] += len(selected)
        totals["dropped_unit_count"] += len(dropped)
        evidence_sink(
            {
                "event": "GROUNDED_SUMMARY_NODE",
                "schema_version": "membind.v6.1.grounded-summary-node.v1",
                "policy": SUMMARY_POLICY_ID,
                "node_uuid_sha256": _sha256(str(getattr(node, "uuid", ""))),
                "node_name_sha256": _sha256(str(getattr(node, "name", ""))),
                "node_name_canonical_sha256": _sha256(
                    _normalized(str(getattr(node, "name", "")))
                ),
                "episode_source_sha256": episode_hashes,
                "previous_summary_sha256": _sha256(previous),
                "previous_summary_chars": len(previous),
                "previous_summary_certified": bool(prior),
                "selected_summary_sha256": _sha256(summary),
                "selected_summary_chars": len(summary),
                "candidate_unit_count": len(candidates),
                "selected_unit_count": len(selected),
                "dropped_unit_count": len(dropped),
                "selected_units": [_unit_evidence(value) for value in selected],
                "dropped_units": [_unit_evidence(value) for value in dropped],
                "status": "materialized" if selected else "no_grounded_units",
                "content_omitted": True,
            }
        )
    evidence_sink(
        {
            "event": "GROUNDED_SUMMARY_BATCH",
            "schema_version": "membind.v6.1.grounded-summary-batch.v1",
            "policy": SUMMARY_POLICY_ID,
            "input_node_count": len(nodes),
            "eligible_node_count": len(eligible),
            "skipped_by_filter_count": len(nodes) - len(eligible),
            "summary_llm_flights_bypassed": estimated_flights,
            "fallback_to_upstream": False,
            "monotonic_ns": time.monotonic_ns(),
            **totals,
        }
    )


def install_grounded_summary_materialization(
    *, evidence_sink: Callable[[dict[str, Any]], None] | None = None
) -> tuple[Callable[[], None], list[dict[str, Any]]]:
    """Patch Graphiti's private batch seam with drift checks and an idempotent restore."""

    from graphiti_core.utils.maintenance import node_operations as operations
    from graphiti_core.utils.text_utils import MAX_SUMMARY_CHARS

    original = getattr(operations, "_extract_entity_summaries_batch", None)
    if not callable(original):
        raise GroundedSummaryCompatibilityError("Graphiti summary seam is unavailable")
    expected = (
        "llm_client",
        "nodes",
        "episode",
        "previous_episodes",
        "should_summarize_node",
        "edges_by_node",
        "skip_fact_appending",
        "entity_types",
    )
    signature = inspect.signature(original)
    if tuple(signature.parameters) != expected:
        raise GroundedSummaryCompatibilityError(
            f"Graphiti summary seam signature drifted: {signature}"
        )
    if getattr(original, "_membind_grounded_summary_patch", False):
        raise GroundedSummaryCompatibilityError("grounded summary patch is already installed")
    if operations.extract_attributes_from_nodes.__globals__.get(
        "_extract_entity_summaries_batch"
    ) is not original:
        raise GroundedSummaryCompatibilityError("Graphiti summary call site drifted")

    evidence: list[dict[str, Any]] = []
    registry: SummaryRegistry = {}

    def emit(row: dict[str, Any]) -> None:
        evidence.append(row)
        if evidence_sink is not None:
            evidence_sink(row)

    async def grounded(
        llm_client: Any,
        nodes: list[Any],
        episode: Any | list[Any] | None,
        previous_episodes: list[Any] | None,
        should_summarize_node: Callable[[Any], Awaitable[bool]] | None,
        edges_by_node: dict[str, list[Any]],
        *,
        skip_fact_appending: bool = False,
        entity_types: dict[str, type[Any]] | None = None,
    ) -> None:
        if skip_fact_appending:
            emit(
                {
                    "event": "GROUNDED_SUMMARY_BATCH",
                    "schema_version": "membind.v6.1.grounded-summary-batch.v1",
                    "policy": SUMMARY_POLICY_ID,
                    "input_node_count": len(nodes),
                    "eligible_node_count": 0,
                    "skipped_by_filter_count": 0,
                    "summary_llm_flights_bypassed": 0,
                    "fallback_to_upstream": True,
                    "fallback_reason": "skip_fact_appending_requires_episode_summary_contract",
                    "monotonic_ns": time.monotonic_ns(),
                }
            )
            await original(
                llm_client,
                nodes,
                episode,
                previous_episodes,
                should_summarize_node,
                edges_by_node,
                skip_fact_appending=skip_fact_appending,
                entity_types=entity_types,
            )
            return
        await materialize_grounded_summaries(
            nodes,
            episode,
            should_summarize_node,
            edges_by_node,
            registry=registry,
            evidence_sink=emit,
            max_summary_chars=int(MAX_SUMMARY_CHARS),
            max_nodes_per_flight=int(operations.MAX_NODES),
        )

    grounded._membind_grounded_summary_patch = True  # type: ignore[attr-defined]
    operations._extract_entity_summaries_batch = grounded
    restored = False

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        if operations._extract_entity_summaries_batch is not grounded:
            raise GroundedSummaryCompatibilityError(
                "Graphiti summary seam changed before restore"
            )
        operations._extract_entity_summaries_batch = original
        registry.clear()
        restored = True

    return restore, evidence


__all__ = [
    "CertifiedSummaryUnit",
    "GroundedSummaryCompatibilityError",
    "SUMMARY_POLICY_ID",
    "install_grounded_summary_materialization",
    "materialize_grounded_summaries",
]
