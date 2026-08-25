from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.campaign_reducer import (
    CampaignReductionError,
    arm_order_for,
    reduce_campaign,
    validate_block_status,
)


AUTHORITY = "a" * 64


def _block(
    *, method: str = "B0", context_index: int = 0, repeat: int = 0,
    context_id: str = "ctx0", t_build_ns: int = 100, qa_status: str = "PASS",
    config_hash: str = "c" * 64, scope: str = "FORMAL", sealed: bool = True,
    order_status: str = "PASS", refinement_status: str = "N/A",
) -> dict:
    if method == "V6" and refinement_status == "N/A":
        refinement_status = "PASS"
    if method == "B1" and order_status == "PASS":
        order_status = "NOT_REQUIRED"
    return {
        "block_id": f"{method}-{context_index}-{repeat}",
        "method": method,
        "semantic_class": {"B0": "ORDERED_REFERENCE", "B1": "RELAXED_ORDER_REFERENCE", "V6": "ORDERED_REFINEMENT"}[method],
        "context_id": context_id,
        "context_index": context_index,
        "repeat": repeat,
        "scope": scope,
        "dataset_authority_sha256": AUTHORITY,
        "workload_hash": "w" * 64,
        "config_hash": config_hash,
        "sealed": sealed,
        "expected_episode_count": 2,
        "submitted_count": 2,
        "completed_count": 2,
        "t_build_ns": t_build_ns,
        "artifact_status": "PASS" if sealed else "FAIL",
        "contract_status": "PASS",
        "order_contract_status": order_status,
        "refinement_status": refinement_status,
        "quality_status": qa_status,
        "qa": {"status": qa_status, "overall_accuracy": None if qa_status != "PASS" else 0.5},
        "inversion_count": 2 if method == "B1" else 0,
    }


def test_reducer_separates_quality_failure_from_performance_gate() -> None:
    block = _block(qa_status="INVALID")
    status = validate_block_status(block)
    assert status["artifact_status"] == "PASS"
    assert status["contract_status"] == "PASS"
    assert status["quality_status"] == "INVALID"
    assert status["inclusion_status"] == "PERFORMANCE_AND_CORRECTNESS"


def test_reducer_rejects_four_context_and_prefix_formal_claims() -> None:
    blocks = [_block(context_index=i, context_id=f"ctx{i}") for i in range(4)]
    with pytest.raises(CampaignReductionError, match="five contexts"):
        reduce_campaign(blocks, formal_context_count=5)
    with pytest.raises(CampaignReductionError, match="prefix"):
        validate_block_status(_block(scope="ENGINEERING_DIAGNOSTIC"))


def test_reducer_excludes_mixed_config_but_keeps_exclusion_ledger() -> None:
    valid = _block()
    mismatch = _block(method="B1", config_hash="d" * 64)
    result = reduce_campaign([valid, mismatch], formal_context_count=1, required_methods=("B0", "B1"))
    assert result["status"] == "PARTIAL"
    assert result["exclusion_ledger"][0]["reason"] == "CONFIG_MISMATCH"


def test_arm_order_is_counterbalanced_by_context_and_repeat() -> None:
    expected = (("B0", "B1", "V6"), ("B1", "V6", "B0"), ("V6", "B0", "B1"))
    for context_index in range(5):
        orders = [arm_order_for(context_index, repeat) for repeat in range(3)]
        assert sorted(orders) == sorted(expected)


def test_primary_table_reports_speedup_and_context_macro() -> None:
    blocks = []
    for method, times in {"B0": (100, 120), "B1": (50, 60), "V6": (80, 90)}.items():
        for context_index, t_build in enumerate(times):
            blocks.append(_block(method=method, context_index=context_index, context_id=f"ctx{context_index}", t_build_ns=t_build))
    result = reduce_campaign(blocks, formal_context_count=2, required_methods=("B0", "B1", "V6"))
    assert result["status"] == "PASS"
    assert result["primary"]["B0"]["speedup_vs_b0"] == pytest.approx(1.0)
    assert result["primary"]["V6"]["speedup_vs_b0"] == pytest.approx(220 / 170)
    assert result["quality"]["context_count"] == 2
