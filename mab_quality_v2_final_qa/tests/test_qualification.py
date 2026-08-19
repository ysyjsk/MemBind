from __future__ import annotations

from mab_quality_v2_final_qa.qualification import qualify_records

from .test_contracts_adapter import _official_like_record


def test_qualification_reports_all_failures_and_blocks_live() -> None:
    valid = _official_like_record()
    invalid = _official_like_record()
    invalid["context_id"] = "ctx-invalid"
    invalid["context"] = "not a chronological context"
    result = qualify_records([valid, invalid], source="longmemeval*")
    assert result["accepted_context_count"] == 1
    assert result["rejected_context_count"] == 1
    assert result["decision"] == "STOP_DATASET_MAPPING_UNQUALIFIED"
    assert result["live_authorized"] is False
