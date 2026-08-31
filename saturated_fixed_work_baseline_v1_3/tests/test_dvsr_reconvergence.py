"""Provider-free descendant-only reconvergence attribution contract."""

from __future__ import annotations

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_reconvergence import (
    attribute_descendant_reconvergence,
)


def _dag() -> dict:
    return {
        "status": "COMPLETE",
        "nodes": [
            {"node_id": "node", "phase": "node-resolution", "predecessors": [], "cost_ns": 40, "reusable": False},
            {"node_id": "edge", "phase": "edge-extraction", "predecessors": ["node"], "cost_ns": 60, "reusable": True},
            {"node_id": "summary", "phase": "attributes-summary", "predecessors": ["edge"], "cost_ns": 20, "reusable": True},
        ],
    }


def test_repaired_parent_reconvergence_saves_only_certified_descendants() -> None:
    result = attribute_descendant_reconvergence(
        parent_operator="node-resolution",
        repair_attempted=True,
        old_parent_output_digest="a" * 64,
        repaired_parent_output_digest="a" * 64,
        operator_dag=_dag(),
        descendant_certificate_valid_node_ids=("edge", "summary"),
    )

    assert result["repair_result"] == "RECONVERGED"
    assert result["saved_descendant_operator_ids"] == ["edge", "summary"]
    assert result["reconvergence_saved_descendant_cp_ns"] == 80
    assert "node" not in result["saved_descendant_operator_ids"]
    assert result["parent_repair_cp_credited_ns"] == 0
    assert result["operator_states"]["node"] == "RECONVERGED"


def test_changed_repair_invalidates_descendants_and_saves_nothing() -> None:
    result = attribute_descendant_reconvergence(
        parent_operator="node-resolution",
        repair_attempted=True,
        old_parent_output_digest="a" * 64,
        repaired_parent_output_digest="b" * 64,
        operator_dag=_dag(),
        descendant_certificate_valid_node_ids=("edge", "summary"),
    )

    assert result["repair_result"] == "REPAIRED_CHANGED"
    assert result["saved_descendant_operator_ids"] == []
    assert result["reconvergence_saved_descendant_cp_ns"] == 0
    assert result["operator_states"]["edge"] == "INVALIDATED"


def test_uncertified_descendant_never_receives_reconvergence_credit() -> None:
    result = attribute_descendant_reconvergence(
        parent_operator="node-resolution",
        repair_attempted=True,
        old_parent_output_digest="a" * 64,
        repaired_parent_output_digest="a" * 64,
        operator_dag=_dag(),
        descendant_certificate_valid_node_ids=("edge",),
    )

    assert result["saved_descendant_operator_ids"] == ["edge"]
    assert result["reconvergence_saved_descendant_cp_ns"] == 60
    assert result["operator_states"]["summary"] == "UNKNOWN"


def test_parent_cannot_be_smuggled_into_descendant_credit() -> None:
    result = attribute_descendant_reconvergence(
        parent_operator="node-resolution",
        repair_attempted=True,
        old_parent_output_digest="a" * 64,
        repaired_parent_output_digest="a" * 64,
        operator_dag=_dag(),
        descendant_certificate_valid_node_ids=("node", "edge"),
    )

    assert result["saved_descendant_operator_ids"] == ["edge"]
    assert result["reconvergence_saved_descendant_cp_ns"] == 60


def test_missing_repair_digest_is_unknown_and_zero_credit() -> None:
    result = attribute_descendant_reconvergence(
        parent_operator="node-resolution",
        repair_attempted=True,
        old_parent_output_digest=None,
        repaired_parent_output_digest="a" * 64,
        operator_dag=_dag(),
        descendant_certificate_valid_node_ids=("edge",),
    )

    assert result["status"] == "UNKNOWN_INCOMPLETE_EVIDENCE"
    assert result["repair_result"] == "UNKNOWN"
    assert result["reconvergence_saved_descendant_cp_ns"] == 0


def test_no_repair_does_not_relabel_exact_reuse_as_reconvergence() -> None:
    result = attribute_descendant_reconvergence(
        parent_operator="node-resolution",
        repair_attempted=False,
        old_parent_output_digest="a" * 64,
        repaired_parent_output_digest="a" * 64,
        operator_dag=_dag(),
        descendant_certificate_valid_node_ids=("edge", "summary"),
    )

    assert result["repair_result"] == "NOT_REPAIRED"
    assert result["reconvergence_saved_descendant_cp_ns"] == 0
    assert result["operator_states"]["edge"] == "EXACT_REUSE"
