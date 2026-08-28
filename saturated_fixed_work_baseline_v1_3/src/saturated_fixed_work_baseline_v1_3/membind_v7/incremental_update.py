"""Provider-free reference planner for the V7 incremental-update module.

V6 MemBind-Core freezes the extraction overlap mechanism.  V7 studies a
separate question: after a new source arrives, which previously materialized
node/edge artifacts are provably unaffected and may be reused?  This module
contains only the deterministic contract and closure planner.  It does not
call Graphiti, an LLM, an embedder, or Neo4j, and therefore cannot accidentally
turn a development hypothesis into a live treatment.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping


V7_INCREMENTAL_MODULE_VERSION = "v7-incremental-update-v1"
V7_INCREMENTAL_BOUNDARY = "WORK_REDUCTION_EXTENSION"
V7_INCREMENTAL_SCOPE = "D1_AFFECTED_CLOSURE_AND_CONTENT_ADDRESSED_REUSE"


class IncrementalUpdateContractError(ValueError):
    """Raised when a reuse plan cannot establish its semantic closure."""


def _digest(value: Mapping[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactKey:
    """Content-addressed identity for one extraction/materialization artifact."""

    object_id: str
    source_hash: str
    schema_hash: str
    model_hash: str
    config_hash: str

    def __post_init__(self) -> None:
        if not self.object_id or any(
            not isinstance(value, str) or not value for value in (
                self.source_hash,
                self.schema_hash,
                self.model_hash,
                self.config_hash,
            )
        ):
            raise IncrementalUpdateContractError("artifact key fields must be non-empty")

    @property
    def digest(self) -> str:
        return _digest(
            {
                "object_id": self.object_id,
                "source_hash": self.source_hash,
                "schema_hash": self.schema_hash,
                "model_hash": self.model_hash,
                "config_hash": self.config_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    key: ArtifactKey
    frontier_version: int
    semantics_hash: str
    complete: bool = True

    def reusable_for(
        self,
        *,
        source_version: int,
        source_hash: str,
        schema_hash: str,
        model_hash: str,
        config_hash: str,
        affected_objects: frozenset[str],
    ) -> bool:
        """Reuse only when every semantic input and the affected closure match."""

        return bool(
            self.complete
            and self.frontier_version == source_version
            and self.key.object_id not in affected_objects
            and self.key.source_hash == source_hash
            and self.key.schema_hash == schema_hash
            and self.key.model_hash == model_hash
            and self.key.config_hash == config_hash
            and self.semantics_hash
        )


@dataclass(frozen=True, slots=True)
class IncrementalUpdatePlan:
    source_version: int
    target_version: int
    changed_objects: frozenset[str]
    affected_objects: frozenset[str]
    reusable_artifacts: tuple[str, ...]
    recompute_objects: tuple[str, ...]

    @property
    def status(self) -> str:
        return "READY" if self.target_version == self.source_version + 1 else "INVALID"


def affected_closure(
    changed_objects: Iterable[str],
    dependency_edges: Iterable[tuple[str, str]],
) -> frozenset[str]:
    """Compute the transitive successor closure in deterministic order."""

    adjacency: dict[str, set[str]] = {}
    for source, target in dependency_edges:
        if not source or not target:
            raise IncrementalUpdateContractError("dependency edge ids must be non-empty")
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())
    changed = frozenset(str(value) for value in changed_objects if str(value))
    # A changed object may be a new leaf not present in the old graph.  It is
    # still valid, but an empty graph with an empty change set is a no-op.
    queue = deque(sorted(changed))
    closure = set(changed)
    while queue:
        current = queue.popleft()
        for successor in sorted(adjacency.get(current, ())):
            if successor not in closure:
                closure.add(successor)
                queue.append(successor)
    return frozenset(closure)


def build_incremental_plan(
    *,
    source_version: int,
    target_version: int,
    changed_objects: Iterable[str],
    dependency_edges: Iterable[tuple[str, str]],
    artifacts: Iterable[ArtifactRecord],
    source_hash: str,
    schema_hash: str,
    model_hash: str,
    config_hash: str,
) -> IncrementalUpdatePlan:
    """Build a d=1 plan; unknown dependencies fail closed at the caller boundary."""

    if source_version < 0 or target_version != source_version + 1:
        raise IncrementalUpdateContractError("V7 incremental plans are restricted to d=1")
    changed = frozenset(str(value) for value in changed_objects if str(value))
    closure = affected_closure(changed, dependency_edges)
    records = tuple(artifacts)
    reusable = tuple(
        sorted(
            record.key.digest
            for record in records
            if record.reusable_for(
                source_version=source_version,
                source_hash=source_hash,
                schema_hash=schema_hash,
                model_hash=model_hash,
                config_hash=config_hash,
                affected_objects=closure,
            )
        )
    )
    recompute = tuple(sorted(closure))
    return IncrementalUpdatePlan(
        source_version=source_version,
        target_version=target_version,
        changed_objects=changed,
        affected_objects=closure,
        reusable_artifacts=reusable,
        recompute_objects=recompute,
    )


def incremental_module_identity() -> dict[str, object]:
    return {
        "version": V7_INCREMENTAL_MODULE_VERSION,
        "boundary": V7_INCREMENTAL_BOUNDARY,
        "scope": V7_INCREMENTAL_SCOPE,
        "provider_calls_allowed": False,
        "native_publication_allowed": False,
        "deltas_supported": "d=1",
        "reuse_requires_affected_closure": True,
    }


__all__ = [
    "ArtifactKey",
    "ArtifactRecord",
    "IncrementalUpdateContractError",
    "IncrementalUpdatePlan",
    "V7_INCREMENTAL_BOUNDARY",
    "V7_INCREMENTAL_MODULE_VERSION",
    "V7_INCREMENTAL_SCOPE",
    "affected_closure",
    "build_incremental_plan",
    "incremental_module_identity",
]
