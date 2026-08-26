from __future__ import annotations

from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v7.selected_development_campaign import (
    load_selected_development_protocol,
)


PROJECT = Path(__file__).resolve().parents[1]
PROTOCOL = PROJECT / "v7/BAILIAN_122B_SILICONFLOW_V7_DEVELOPMENT_PROTOCOL.json"


def test_selected_development_protocol_reauthorizes_exact_2_6_6_only() -> None:
    frozen = load_selected_development_protocol(PROTOCOL)

    assert frozen["campaign_scope"] == "TEMPORARY_PROVIDER_DEVELOPMENT"
    assert frozen["provider_identity_kind"] == (
        "COMPOSITE_DEVELOPMENT_SELECTED_TEMPORARY"
    )
    assert frozen["selected_runtime_freeze"]["path"] == (
        "BAILIAN_122B_SILICONFLOW_DEVELOPMENT_RUNTIME_FREEZE.json"
    )
    assert frozen["construction"]["model"] == "qwen3.5-122b-a10b"
    assert frozen["embedding"]["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert frozen["embedding"]["dimension"] == 1024
    assert frozen["workload"]["r1_r2"]["source_count"] == 2
    assert [row["source_count"] for row in frozen["workload"]["r3_blocks"]] == [6, 6]
    assert frozen["formal_r1_r3_eligible"] is False
    assert frozen["live_treatment_authorized"] is False
    assert frozen["scientific_method_selection_update_allowed"] is False
    assert frozen["provider_swap_requires_new_formal_campaign"] is True
