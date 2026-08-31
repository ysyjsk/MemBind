"""Descendant-only DVSR repair reconvergence attribution."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Mapping, Sequence


RECONVERGENCE_SCHEMA = "membind.dvsr.descendant-reconvergence.v1"


def _base(*, status: str, repair_result: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": RECONVERGENCE_SCHEMA,
        "status": status,
        "repair_result": repair_result,
        "reason": reason,
        "reconvergence_digest": None,
        "saved_descendant_operator_ids": [],
        "reconvergence_saved_descendant_cp_ns": 0,
        "parent_repair_cp_credited_ns": 0,
        "operator_states": {},
    }


def attribute_descendant_reconvergence(
    *,
    parent_operator: str,
    repair_attempted: bool,
    old_parent_output_digest: str | None,
    repaired_parent_output_digest: str | None,
    operator_dag: Mapping[str, Any],
    descendant_certificate_valid_node_ids: Sequence[str],
) -> dict[str, Any]:
    """Credit only certified descendants preserved after exact parent repair."""

    if operator_dag.get("status") != "COMPLETE":
        return _base(
            status="UNKNOWN_INCOMPLETE_EVIDENCE",
            repair_result="UNKNOWN",
            reason="operator_dag_incomplete",
        )
    raw_nodes = operator_dag.get("nodes")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes, bytearray)):
        return _base(
            status="UNKNOWN_INCOMPLETE_EVIDENCE",
            repair_result="UNKNOWN",
            reason="operator_dag_nodes_missing",
        )
    nodes: dict[str, Mapping[str, Any]] = {}
    successors: dict[str, list[str]] = defaultdict(list)
    parent_ids: set[str] = set()
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            return _base(status="UNKNOWN_INCOMPLETE_EVIDENCE", repair_result="UNKNOWN", reason="operator_dag_node_invalid")
        node_id = raw.get("node_id")
        predecessors = raw.get("predecessors")
        cost = raw.get("cost_ns")
        if (
            not isinstance(node_id, str)
            or not node_id
            or node_id in nodes
            or not isinstance(predecessors, Sequence)
            or isinstance(predecessors, (str, bytes, bytearray))
            or isinstance(cost, bool)
            or not isinstance(cost, int)
            or cost < 0
        ):
            return _base(status="UNKNOWN_INCOMPLETE_EVIDENCE", repair_result="UNKNOWN", reason="operator_dag_node_invalid")
        nodes[node_id] = raw
        if raw.get("phase") == parent_operator:
            parent_ids.add(node_id)
    if not parent_ids:
        return _base(status="UNKNOWN_INCOMPLETE_EVIDENCE", repair_result="UNKNOWN", reason="parent_operator_missing")
    for node_id, raw in nodes.items():
        for predecessor in raw["predecessors"]:
            if predecessor not in nodes:
                return _base(status="UNKNOWN_INCOMPLETE_EVIDENCE", repair_result="UNKNOWN", reason="operator_dag_edge_invalid")
            successors[str(predecessor)].append(node_id)
    descendants: set[str] = set()
    queue: deque[str] = deque(parent_ids)
    while queue:
        current = queue.popleft()
        for child in successors[current]:
            if child not in descendants and child not in parent_ids:
                descendants.add(child)
                queue.append(child)
    valid = {
        str(node_id)
        for node_id in descendant_certificate_valid_node_ids
        if str(node_id) in descendants and nodes[str(node_id)].get("reusable") is True
    }
    states = {
        node_id: "EXACT_REUSE" if node.get("reusable") is True else "UNKNOWN"
        for node_id, node in nodes.items()
    }
    if not repair_attempted:
        result = _base(status="COMPLETE", repair_result="NOT_REPAIRED")
        result["operator_states"] = states
        return result
    if (
        not isinstance(old_parent_output_digest, str)
        or not old_parent_output_digest
        or not isinstance(repaired_parent_output_digest, str)
        or not repaired_parent_output_digest
    ):
        result = _base(
            status="UNKNOWN_INCOMPLETE_EVIDENCE",
            repair_result="UNKNOWN",
            reason="parent_repair_digest_missing",
        )
        result["operator_states"] = {node_id: "UNKNOWN" for node_id in nodes}
        return result
    reconverged = old_parent_output_digest == repaired_parent_output_digest
    for node_id in parent_ids:
        states[node_id] = "RECONVERGED" if reconverged else "REPAIRED_CHANGED"
    if not reconverged:
        for node_id in descendants:
            states[node_id] = "INVALIDATED"
        result = _base(status="COMPLETE", repair_result="REPAIRED_CHANGED")
        result["operator_states"] = states
        return result
    saved = sorted(valid)
    for node_id in descendants:
        states[node_id] = "EXACT_REUSE" if node_id in valid else "UNKNOWN"
    result = _base(status="COMPLETE", repair_result="RECONVERGED")
    result.update(
        {
            "reconvergence_digest": old_parent_output_digest,
            "saved_descendant_operator_ids": saved,
            "reconvergence_saved_descendant_cp_ns": sum(int(nodes[node_id]["cost_ns"]) for node_id in saved),
            "operator_states": states,
        }
    )
    return result


__all__ = ["RECONVERGENCE_SCHEMA", "attribute_descendant_reconvergence"]
