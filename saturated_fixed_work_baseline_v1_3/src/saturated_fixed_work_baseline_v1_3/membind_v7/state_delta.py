"""Operator-scoped d=1 state delta and completeness checks (T2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class DeltaChange:
    kind: str
    key: str
    changed_fields: frozenset[str] = frozenset()
    before: Mapping[str, Any] = field(default_factory=dict)
    after: Mapping[str, Any] = field(default_factory=dict)
    operation: str = "update"

    def __post_init__(self) -> None:
        if not self.kind or not self.key:
            raise ValueError("delta change kind and key are required")
        object.__setattr__(self, "changed_fields", frozenset(self.changed_fields))
        operation = str(self.operation).lower()
        if operation not in {"insert", "create", "add", "update", "delete", "remove"}:
            raise ValueError("delta change operation is not recognized")
        object.__setattr__(self, "operation", operation)


@dataclass(frozen=True, slots=True)
class StateDelta:
    source_version: int
    target_version: int
    changes: tuple[DeltaChange, ...] = ()
    environment_changes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.source_version < 0 or self.target_version < self.source_version:
            raise ValueError("delta versions must be ordered")
        object.__setattr__(self, "changes", tuple(self.changes))
        object.__setattr__(self, "environment_changes", frozenset(self.environment_changes))


@dataclass(frozen=True, slots=True)
class ObservableSpec:
    operator: str
    required_fields: frozenset[str]
    entity_kind: str | None = None
    required_epochs: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_fields", frozenset(self.required_fields))
        object.__setattr__(self, "required_epochs", frozenset(self.required_epochs))

    @property
    def kind(self) -> str | None:
        if self.entity_kind:
            return self.entity_kind
        if self.operator.startswith("node_"):
            return "node"
        if self.operator.startswith("edge_"):
            return "edge"
        return None


@dataclass(frozen=True, slots=True)
class DeltaCompleteness:
    status: str
    missing_fields: frozenset[str] = frozenset()
    missing_epochs: frozenset[str] = frozenset()
    relevant_changes: tuple[DeltaChange, ...] = ()

    @property
    def complete(self) -> bool:
        return self.status == "COMPLETE"


def complete_delta(delta: StateDelta, spec: ObservableSpec) -> DeltaCompleteness:
    """Check completeness only for ``spec``; unrelated UNKNOWN must not poison it."""

    relevant = tuple(change for change in delta.changes if spec.kind is None or change.kind == spec.kind)
    changed_epochs = frozenset(delta.environment_changes & spec.required_epochs)
    if changed_epochs:
        # An epoch transition changes the observable contract even when no
        # entity row changed.  The delta names the transition but does not
        # prove that the old and new operator semantics are equivalent.
        return DeltaCompleteness(
            "UNKNOWN",
            missing_epochs=changed_epochs,
            relevant_changes=relevant,
        )
    changed_fields = frozenset(field for change in relevant for field in change.changed_fields)
    missing = frozenset(spec.required_fields - changed_fields) if relevant else frozenset()
    # For a changed field, after-values are required to establish exact native
    # projection equality.  A conservative missing field yields UNKNOWN.
    for change in relevant:
        for field_name in change.changed_fields:
            if field_name not in change.after and field_name not in change.before:
                missing = frozenset(set(missing) | {field_name})
    if missing:
        return DeltaCompleteness("UNKNOWN", missing_fields=missing, relevant_changes=relevant)
    return DeltaCompleteness("COMPLETE", relevant_changes=relevant)


def apply_state(state: Mapping[str, Any], delta: StateDelta) -> dict[str, Any]:
    """Apply the small reference-model projection used by theorem tests."""

    result = {str(key): value for key, value in state.items()}
    for change in delta.changes:
        if change.kind != "node":
            continue
        nodes = dict(result.get("nodes", {}))
        if change.operation in {"delete", "remove"}:
            nodes.pop(change.key, None)
            result["nodes"] = nodes
            continue
        current = dict(nodes.get(change.key, {}))
        current.update(change.after)
        nodes[change.key] = current
        result["nodes"] = nodes
    return result


__all__ = [
    "DeltaChange",
    "DeltaCompleteness",
    "ObservableSpec",
    "StateDelta",
    "apply_state",
    "complete_delta",
]
