"""Closed semantic trace primitives for the V7 core theorems.

Runtime completion order is intentionally absent from this model.  Only typed
semantic edges and continuation-observable fields participate in equality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class NodeKind(str, Enum):
    INPUT = "Input"
    READ = "Read"
    PURE = "Pure"
    DEMAND = "Demand"
    RESPONSE = "Response"
    CONTROL = "Control"
    PLAN = "M2Plan"


@dataclass(frozen=True, slots=True)
class SnapshotToken:
    version: int
    epoch: str
    writer_fence: int

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise ValueError("snapshot version must be a non-negative integer")
        if not self.epoch:
            raise ValueError("snapshot epoch is required")
        if isinstance(self.writer_fence, bool) or not isinstance(self.writer_fence, int) or self.writer_fence < 0:
            raise ValueError("writer fence must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class TraceNode:
    node_id: str
    kind: NodeKind
    snapshot: SnapshotToken | None = None
    writes_state: bool = False
    canonical_value: Any = None
    metadata: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("trace node id is required")
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True, slots=True)
class SemanticTrace:
    nodes: tuple[TraceNode, ...]
    edges: tuple[tuple[str, str], ...]
    seam_snapshot: SnapshotToken | None = None
    seam_output: Any = None
    frontier: int | None = None


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        ignored_runtime_keys = {"runtime_uuid", "temporary_id", "runtime_id", "edge_endpoint_id"}
        return {
            str(key): _plain(item)
            for key, item in value.items()
            if str(key) not in ignored_runtime_keys and not str(key).endswith("_uuid")
        }
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, set):
        return frozenset(_plain(item) for item in value)
    if isinstance(value, Enum):
        return value.value
    return value


def alpha_equivalent(left: Any, right: Any) -> bool:
    """Compare logical values while erasing runtime-only UUIDs.

    Lists/tuples remain ordered and effect keys remain visible.  The function
    is intentionally conservative for unknown objects: they must compare
    equal directly rather than being guessed equivalent.
    """

    return _plain(left) == _plain(right)


def continuation_equivalent(left: Any, right: Any, *, observable_fields: set[str] | frozenset[str]) -> bool:
    """Apply the seam-specific continuation observable relation.

    A field listed in ``observable_fields`` is compared exactly, including an
    ID that normal alpha-equivalence would otherwise erase.  Missing fields are
    treated as unequal so the relation fails closed.
    """

    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return alpha_equivalent(left, right)
    for field in observable_fields:
        if field not in left or field not in right or left[field] != right[field]:
            return False
    return alpha_equivalent(left, right)


def validate_snapshot_soundness(trace: SemanticTrace) -> None:
    """Enforce T1 and A2 for a selected BuildTrace seam."""

    reads = [node for node in trace.nodes if node.kind == NodeKind.READ]
    if trace.seam_snapshot is None and reads:
        raise ValueError("snapshot seam token is required")
    if trace.seam_snapshot is not None:
        for node in reads:
            if node.snapshot != trace.seam_snapshot:
                raise ValueError("snapshot mismatch in BuildTrace")
        if any(node.writes_state for node in trace.nodes):
            raise ValueError("state write occurred before maintained seam")


def canonical_trace(trace: SemanticTrace) -> dict[str, Any]:
    """Return a stable JSON-like projection for frozen differential tests."""

    return {
        "nodes": [
            {
                "id": node.node_id,
                "kind": node.kind.value,
                "snapshot": _plain(node.snapshot),
                "writes_state": node.writes_state,
                "value": _plain(node.canonical_value),
                "metadata": _plain(node.metadata),
            }
            for node in trace.nodes
        ],
        "edges": list(trace.edges),
        "seam_output": _plain(trace.seam_output),
        "frontier": trace.frontier,
    }


__all__ = [
    "NodeKind",
    "SemanticTrace",
    "SnapshotToken",
    "TraceNode",
    "alpha_equivalent",
    "canonical_trace",
    "continuation_equivalent",
    "validate_snapshot_soundness",
]
