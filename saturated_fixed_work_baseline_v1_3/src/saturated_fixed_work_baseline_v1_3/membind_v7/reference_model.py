"""Small deterministic BuildTrace/MaintainTrace model for frozen differential tests."""

from __future__ import annotations

from typing import Any, Mapping

from .semantics import NodeKind, SemanticTrace, SnapshotToken, TraceNode
from .state_delta import StateDelta, apply_state


def build_trace(state: Mapping[str, Any], episode: str, snapshot: SnapshotToken) -> SemanticTrace:
    nodes = state.get("nodes", {})
    ordered = tuple(sorted(nodes.items(), key=lambda item: str(item[0])))
    read = TraceNode("read:nodes", NodeKind.READ, snapshot=snapshot, canonical_value=ordered)
    demand = TraceNode(
        f"demand:{episode}",
        NodeKind.DEMAND,
        snapshot=snapshot,
        canonical_value={"episode": episode, "nodes": ordered},
    )
    seam = {"logical_nodes": tuple(key for key, _ in ordered), "episode": episode}
    return SemanticTrace(
        nodes=(read, demand),
        edges=((read.node_id, demand.node_id),),
        seam_snapshot=snapshot,
        seam_output=seam,
        frontier=snapshot.version,
    )


def maintain_trace(state: Mapping[str, Any], delta: StateDelta, episode: str, snapshot: SnapshotToken) -> SemanticTrace:
    # This is a reference projection only: it computes the maintained result
    # from the d=1 post-state and performs no native reuse or publication.
    return build_trace(apply_state(state, delta), episode, snapshot)


__all__ = ["build_trace", "maintain_trace"]
