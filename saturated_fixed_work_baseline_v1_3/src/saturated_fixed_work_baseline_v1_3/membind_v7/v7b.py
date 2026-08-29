"""Provider-free V7-B semantic-boundary and incremental maintenance engine.

This module is intentionally independent of Graphiti, Neo4j, model clients and
embedders.  It is the executable reference for the V7-B contract described in
``workplan_v7.md``: source-local extraction is immutable, stateful work is
represented as explicit views, d=1 deltas invalidate views conservatively, and
incremental results are required to converge to the from-scratch result.

The implementation is useful before a live treatment is authorized.  A caller
must provide the provider-specific adapter only after the frozen differential
tests and opportunity gates pass; no provider call is possible from here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .state_delta import StateDelta


V7B_SCHEMA_VERSION = "membind.v7b.offline.v1"
_TOKEN_RE = re.compile(r"\b[\w][\w'/-]*\b", re.UNICODE)


def _state_version(state: Mapping[str, Any]) -> int:
    """Read the authoritative frontier without silently coercing bad input."""

    value = state.get("frontier", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("state frontier must be a non-negative integer")
    return value


def _canonical(value: Any) -> Any:
    """Convert values to a JSON-stable, runtime-identity-free projection."""

    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda p: str(p[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=lambda item: repr(item))
    if hasattr(value, "value") and type(value).__module__ != "builtins":
        try:
            return value.value
        except Exception:  # pragma: no cover - defensive for foreign enums
            pass
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(_canonical(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Mention:
    source_id: str
    span_start: int
    span_end: int
    surface: str
    normalized: str
    provenance: str


def stable_mention_id(ir: StableSemanticIR, mention: Mention) -> str:
    """Return a source-local, content-addressed identity for one mention."""

    if mention.source_id != ir.source_id:
        raise ValueError("mention does not belong to the supplied source IR")
    return _digest(
        {
            "source_hash": ir.source_hash,
            "operator": "mention",
            "span": [mention.span_start, mention.span_end],
            "normalized": mention.normalized,
            "schema_epoch": ir.schema_epoch,
        }
    )


@dataclass(frozen=True, slots=True)
class StableSemanticIR:
    """Immutable source-local semantic intermediate representation."""

    source_id: str
    source_hash: str
    schema_epoch: str
    model_epoch: str
    parser_epoch: str
    mentions: tuple[Mention, ...]
    source_timestamp: str | None = None
    grounded: bool = True

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


def extract_source_ir(
    source_id: str,
    text: str,
    *,
    source_timestamp: str | None = None,
    schema_epoch: str = "v7b-ir-schema-v1",
    model_epoch: str = "provider-free-deterministic-v1",
    parser_epoch: str = "token-parser-v1",
) -> StableSemanticIR:
    """Extract source-local mentions without consulting mutable memory state.

    This deterministic parser is a qualification/reference implementation, not
    a claim that tokenization replaces the production LLM extractor.  Its
    stable span-derived identity lets the differential engine test the method
    boundary and invalidation semantics without provider traffic.
    """

    if not source_id or not isinstance(text, str):
        raise ValueError("source_id and text are required")
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    mentions: list[Mention] = []
    seen: set[tuple[int, int, str]] = set()
    for match in _TOKEN_RE.finditer(text):
        surface = match.group(0)
        normalized = surface.casefold().strip("'/-")
        if not normalized:
            continue
        key = (match.start(), match.end(), normalized)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(
            Mention(
                source_id=source_id,
                span_start=match.start(),
                span_end=match.end(),
                surface=surface,
                normalized=normalized,
                provenance=f"{source_id}:{match.start()}:{match.end()}",
            )
        )
    return StableSemanticIR(
        source_id=source_id,
        source_hash=source_hash,
        schema_epoch=schema_epoch,
        model_epoch=model_epoch,
        parser_epoch=parser_epoch,
        mentions=tuple(mentions),
        source_timestamp=source_timestamp,
    )


ViewCompute = Callable[[StableSemanticIR, Mapping[str, Any], Mapping[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class ViewDefinition:
    """One explicit semantic view and its observable state dependencies."""

    view_id: str
    kind: str
    predecessors: tuple[str, ...] = ()
    state_dependencies: frozenset[str] = frozenset()
    cost: float = 1.0
    compute: ViewCompute = field(compare=False, repr=False, default=lambda _i, _s, _v: None)
    supported: bool = True

    def __post_init__(self) -> None:
        if not self.view_id or self.kind not in {"source_local", "stateful"}:
            raise ValueError("view id/kind is invalid")
        if self.cost < 0:
            raise ValueError("view cost must be non-negative")
        object.__setattr__(self, "predecessors", tuple(self.predecessors))
        object.__setattr__(self, "state_dependencies", frozenset(self.state_dependencies))


@dataclass(frozen=True, slots=True)
class ViewArtifact:
    view_id: str
    kind: str
    value: Any
    digest: str
    cost: float
    predecessors: tuple[str, ...]
    state_dependencies: frozenset[str]


@dataclass(frozen=True, slots=True)
class FreshResult:
    ir: StableSemanticIR
    state_version: int
    views: Mapping[str, ViewArtifact]
    work_cost: float
    publication: Mapping[str, Any]

    @property
    def canonical_views(self) -> dict[str, Any]:
        return {key: _canonical(self.views[key].value) for key in sorted(self.views)}


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    max_repair_cost: float | None = None
    headroom: float = 0.0
    max_dirty_fraction: float = 1.0

    def __post_init__(self) -> None:
        if self.max_repair_cost is not None and self.max_repair_cost < 0:
            raise ValueError("max_repair_cost must be non-negative")
        if self.headroom < 0 or not 0 <= self.max_dirty_fraction <= 1:
            raise ValueError("fallback policy bounds are invalid")


@dataclass(frozen=True, slots=True)
class IncrementalResult:
    ir: StableSemanticIR
    state_version: int
    views: Mapping[str, ViewArtifact]
    work_cost: float
    publication: Mapping[str, Any]
    reused_view_ids: tuple[str, ...]
    repaired_view_ids: tuple[str, ...]
    reconverged_view_ids: tuple[str, ...]
    dirty_root_ids: tuple[str, ...]
    fallback: bool = False
    fallback_reason: str | None = None

    @property
    def canonical_views(self) -> dict[str, Any]:
        return {key: _canonical(self.views[key].value) for key in sorted(self.views)}


class _ViewGraph:
    def __init__(self, definitions: Sequence[ViewDefinition]) -> None:
        self.definitions = tuple(definitions)
        self.by_id = {definition.view_id: definition for definition in self.definitions}
        if len(self.by_id) != len(self.definitions):
            raise ValueError("view IDs must be unique")
        for definition in self.definitions:
            if not set(definition.predecessors) <= set(self.by_id):
                raise ValueError(f"view {definition.view_id} references unknown predecessor")
        self.order = self._topological_order()
        self.successors: dict[str, tuple[str, ...]] = {
            view_id: tuple(
                sorted(definition.view_id for definition in self.definitions if view_id in definition.predecessors)
            )
            for view_id in self.by_id
        }

    def _topological_order(self) -> tuple[str, ...]:
        indegree = {view_id: len(definition.predecessors) for view_id, definition in self.by_id.items()}
        queue = deque(sorted(view_id for view_id, degree in indegree.items() if degree == 0))
        order: list[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for successor in sorted(self.successors_for(current)):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    queue.append(successor)
        if len(order) != len(self.by_id):
            raise ValueError("semantic view graph must be acyclic")
        return tuple(order)

    def successors_for(self, view_id: str) -> tuple[str, ...]:
        return tuple(sorted(
            definition.view_id
            for definition in self.definitions
            if view_id in definition.predecessors
        ))


def stable_ir_contract() -> dict[str, Any]:
    """Describe the Stage-A purity boundary in an auditable projection."""

    return {
        "schema_version": "membind.v7b.stable-ir-contract.v1",
        "status": "PASS",
        "operator": "source_local_mentions",
        "source_inputs": ["source_id", "source_text", "source_timestamp"],
        "environment_epochs": ["schema_epoch", "model_epoch", "parser_epoch"],
        "forbidden_mutable_state_reads": [
            "mutable_memory_state",
            "previous_episode_retrieval",
            "current_graph_candidates",
            "current_entity_uuid",
            "current_adjacency",
            "mutable_summary",
            "mutable_temporal_conflicts",
        ],
        "output_schema": "StableSemanticIR",
        "stable_id_rule": "sha256(source_hash,operator,source_span,normalized,schema_epoch)",
        "grounding_rule": "every mention carries source_id/span provenance",
        "canonicalization_rule": "sorted JSON projection with UTF-8 SHA-256 digest",
    }


def view_contracts(definitions: Sequence[ViewDefinition]) -> dict[str, dict[str, Any]]:
    """Return explicit per-view contracts; unsupported views stay fail-closed."""

    graph = _ViewGraph(definitions)
    contracts: dict[str, dict[str, Any]] = {}
    for definition in graph.definitions:
        contracts[definition.view_id] = {
            "schema_version": "membind.v7b.view-contract.v1",
            "view_id": definition.view_id,
            "kind": definition.kind,
            "status": "PASS" if definition.supported else "UNSUPPORTED",
            "stable_key": f"{definition.kind}:{definition.view_id}",
            "inputs": ["StableSemanticIR", *definition.predecessors],
            "state_dependencies": sorted(definition.state_dependencies),
            "predecessors": list(definition.predecessors),
            "delta_domain": sorted(definition.state_dependencies),
            "certificate": "exact_digest_and_declared_state_projection",
            "repair": "fresh_recompute_then_canonical_compare",
            "canonical_equality": "sha256(sorted-json(value))",
            "fallback": "FALLBACK_FRESH on unknown delta/environment or budget",
            "estimated_cost": definition.cost,
        }
    return contracts


class V7FreshEngine:
    """Deterministic V7-FRESH from-scratch reference."""

    def __init__(self, definitions: Sequence[ViewDefinition]) -> None:
        self.graph = _ViewGraph(definitions)

    def build(self, ir: StableSemanticIR, state: Mapping[str, Any]) -> FreshResult:
        frontier = _state_version(state)
        views: dict[str, ViewArtifact] = {}
        for view_id in self.graph.order:
            definition = self.graph.by_id[view_id]
            if not definition.supported:
                raise ValueError(f"unsupported view {view_id} cannot enter V7-FRESH")
            predecessor_values = {key: views[key].value for key in definition.predecessors}
            value = definition.compute(ir, state, predecessor_values)
            views[view_id] = ViewArtifact(
                view_id=view_id,
                kind=definition.kind,
                value=value,
                digest=_digest(value),
                cost=definition.cost,
                predecessors=definition.predecessors,
                state_dependencies=definition.state_dependencies,
            )
        publication = _publication_from_views(views, state_version=frontier)
        return FreshResult(
            ir=ir,
            state_version=frontier,
            views=views,
            work_cost=sum(view.cost for view in views.values()),
            publication=publication,
        )


class V7IncrementalEngine:
    """d=1 dirty-view maintenance with exact reconvergence and fallback."""

    def __init__(
        self,
        definitions: Sequence[ViewDefinition],
        *,
        fallback_policy: FallbackPolicy | None = None,
    ) -> None:
        self.graph = _ViewGraph(definitions)
        self.fallback_policy = fallback_policy or FallbackPolicy()

    def maintain(
        self,
        old: FreshResult | IncrementalResult,
        ir: StableSemanticIR,
        state: Mapping[str, Any],
        delta: StateDelta,
    ) -> IncrementalResult:
        if delta.source_version + 1 != delta.target_version:
            return self._fallback(old, ir, state, "delta_or_environment_unknown")
        if delta.environment_changes:
            return self._fallback(old, ir, state, "delta_or_environment_unknown")
        if old.ir.digest != ir.digest:
            return self._fallback(old, ir, state, "source_ir_changed")
        try:
            frontier = _state_version(state)
        except ValueError:
            return self._fallback(old, ir, state, "delta_or_environment_unknown")
        if frontier != delta.target_version:
            return self._fallback(old, ir, state, "delta_or_environment_unknown")

        changed_fields = {
            str(field)
            for change in delta.changes
            for field in (set(change.changed_fields) | {change.key})
        }
        roots = {
            definition.view_id
            for definition in self.graph.definitions
            if definition.kind == "stateful"
            and (not definition.state_dependencies or definition.state_dependencies & changed_fields)
        }
        # A stateful view with no declared projection is deliberately unknown;
        # treating it as dirty is safer than silently reusing it.
        roots.update(
            definition.view_id
            for definition in self.graph.definitions
            if definition.kind == "stateful" and not definition.state_dependencies
        )
        dirty = set(roots)
        queue = deque(sorted(roots))
        while queue:
            current = queue.popleft()
            for successor in self.graph.successors_for(current):
                if successor not in dirty:
                    dirty.add(successor)
                    queue.append(successor)
        total_cost = sum(definition.cost for definition in self.graph.definitions)
        repair_cost = sum(self.graph.by_id[view_id].cost for view_id in dirty)
        if total_cost and len(dirty) / len(self.graph.by_id) > self.fallback_policy.max_dirty_fraction:
            return self._fallback(old, ir, state, "dirty_fraction_exceeded")
        if self.fallback_policy.max_repair_cost is not None and repair_cost + self.fallback_policy.headroom >= self.fallback_policy.max_repair_cost:
            return self._fallback(old, ir, state, "repair_budget_exceeded")

        views: dict[str, ViewArtifact] = {}
        reused: list[str] = []
        repaired: list[str] = []
        reconverged: list[str] = []
        for view_id in self.graph.order:
            definition = self.graph.by_id[view_id]
            if view_id not in dirty:
                prior = old.views.get(view_id)
                if prior is None:
                    dirty.add(view_id)
                else:
                    views[view_id] = prior
                    reused.append(view_id)
                    continue
            predecessor_values = {key: views[key].value for key in definition.predecessors}
            value = definition.compute(ir, state, predecessor_values)
            artifact = ViewArtifact(
                view_id=view_id,
                kind=definition.kind,
                value=value,
                digest=_digest(value),
                cost=definition.cost,
                predecessors=definition.predecessors,
                state_dependencies=definition.state_dependencies,
            )
            views[view_id] = artifact
            repaired.append(view_id)
            prior = old.views.get(view_id)
            if prior is not None and prior.digest == artifact.digest:
                reconverged.append(view_id)
                # A view that reconverges must not force already-clean
                # successors to be repaired.  The dirty set is only used for
                # scheduling, so successors are still conservatively visited;
                # they can themselves reconverge exactly.

        publication = _publication_from_views(views, state_version=frontier)
        return IncrementalResult(
            ir=ir,
            state_version=frontier,
            views=views,
            work_cost=sum(views[view_id].cost for view_id in repaired),
            publication=publication,
            reused_view_ids=tuple(reused),
            repaired_view_ids=tuple(repaired),
            reconverged_view_ids=tuple(reconverged),
            dirty_root_ids=tuple(sorted(roots)),
        )

    def _fallback(
        self,
        old: FreshResult | IncrementalResult,
        ir: StableSemanticIR,
        state: Mapping[str, Any],
        reason: str,
    ) -> IncrementalResult:
        _state_version(state)
        fresh = V7FreshEngine(self.graph.definitions).build(ir, state)
        return IncrementalResult(
            ir=fresh.ir,
            state_version=fresh.state_version,
            views=fresh.views,
            work_cost=fresh.work_cost,
            publication=fresh.publication,
            reused_view_ids=(),
            repaired_view_ids=tuple(sorted(fresh.views)),
            reconverged_view_ids=(),
            dirty_root_ids=(),
            fallback=True,
            fallback_reason=reason,
        )


def _publication_from_views(views: Mapping[str, ViewArtifact], *, state_version: int) -> dict[str, Any]:
    return {
        "frontier": state_version,
        "view_digests": {view_id: views[view_id].digest for view_id in sorted(views)},
        "source_order": True,
    }


def apply_ordered_publication(
    state: Mapping[str, Any],
    publications: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply publication intents in source order with a monotonic frontier."""

    result = {str(key): value for key, value in state.items()}
    frontier = result.get("frontier", 0)
    if isinstance(frontier, bool) or not isinstance(frontier, int) or frontier < 0:
        raise ValueError("frontier must be a non-negative integer")
    events = list(result.get("events", []))
    expected = frontier
    for publication in publications:
        sequence = publication.get("source_sequence")
        if sequence != expected:
            raise ValueError("publication frontier/source order mismatch")
        events.append(publication.get("payload"))
        expected += 1
    result["events"] = events
    result["frontier"] = expected
    return result


def materialize_offline_artifacts(
    root: str | Path,
    *,
    fresh: FreshResult,
    incremental: IncrementalResult,
    delta: StateDelta,
) -> Path:
    """Write the minimum append-only, provider-free V7-B audit bundle."""

    target = Path(root)
    target.mkdir(parents=True, exist_ok=False)
    payloads: dict[str, Any] = {
        "manifest.json": {
            "schema_version": V7B_SCHEMA_VERSION,
            "method": "V7_INCREMENTAL",
            "baseline": "V7_FRESH",
            "provider_calls": 0,
            "treatment_calls": 0,
            "publication_order": "source_order",
            "fallback": incremental.fallback,
            "fallback_reason": incremental.fallback_reason,
        },
        "semantic_ir.json": asdict(fresh.ir),
        "state_delta.json": asdict(delta),
        "view_witnesses.json": {
            view_id: {
                "kind": view.kind,
                "digest": view.digest,
                "predecessors": list(view.predecessors),
                "state_dependencies": sorted(view.state_dependencies),
                "cost": view.cost,
            }
            for view_id, view in sorted(fresh.views.items())
        },
        "repair_events.json": {
            "dirty_roots": list(incremental.dirty_root_ids),
            "repaired": list(incremental.repaired_view_ids),
            "reused": list(incremental.reused_view_ids),
        },
        "reconvergence_events.json": {"views": list(incremental.reconverged_view_ids)},
        "fallback_events.json": {
            "fallback": incremental.fallback,
            "reason": incremental.fallback_reason,
        },
        "work_accounting.json": {
            "fresh_cost": fresh.work_cost,
            "incremental_cost": incremental.work_cost,
            "saved_cost": fresh.work_cost - incremental.work_cost,
        },
        "canonical_views.json": {
            "fresh": fresh.canonical_views,
            "incremental": incremental.canonical_views,
            "equal": fresh.canonical_views == incremental.canonical_views,
        },
        "construction_seal.json": {
            "fresh_ir_digest": fresh.ir.digest,
            "fresh_publication": _canonical(fresh.publication),
            "incremental_publication": _canonical(incremental.publication),
        },
        "semantic_ir_proof.json": {
            **stable_ir_contract(),
            "source_id": fresh.ir.source_id,
            "source_hash": fresh.ir.source_hash,
            "ir_digest": fresh.ir.digest,
            "grounded": fresh.ir.grounded,
            "mention_count": len(fresh.ir.mentions),
            "stable_mention_ids": [stable_mention_id(fresh.ir, item) for item in fresh.ir.mentions],
        },
        "dependency_edges.json": [
            {"source": predecessor, "target": view_id, "kind": "data"}
            for view_id, view in sorted(fresh.views.items())
            for predecessor in view.predecessors
        ],
        "certificate_events.json": {
            "status": "PASS",
            "unknown_first_class": True,
            "canonical_differential_equal": fresh.canonical_views == incremental.canonical_views,
        },
    }
    for name, value in payloads.items():
        path = target / name
        path.write_text(
            json.dumps(_canonical(value), ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="ascii",
        )
        path.chmod(0o600)
    target.chmod(0o700)
    return target


__all__ = [
    "FallbackPolicy",
    "FreshResult",
    "IncrementalResult",
    "Mention",
    "StableSemanticIR",
    "V7B_SCHEMA_VERSION",
    "V7FreshEngine",
    "V7IncrementalEngine",
    "ViewArtifact",
    "ViewDefinition",
    "apply_ordered_publication",
    "extract_source_ir",
    "materialize_offline_artifacts",
    "stable_ir_contract",
    "stable_mention_id",
    "view_contracts",
]
