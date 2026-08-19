from __future__ import annotations

from mab_quality_v2_final_qa.autoresearch import (
    AutoResearchController,
    select_probe_qa,
)
from mab_quality_v2_final_qa.dataset_adapter import MABDatasetAdapter

from .test_contracts_adapter import _official_like_record


def test_probe_selection_is_deterministic_and_ledger_is_bounded(tmp_path) -> None:
    context = MABDatasetAdapter.from_records([_official_like_record()]).contexts[0]
    assert [item.qa_pair_id for item in select_probe_qa(context, count=2)] == [
        item.qa_pair_id for item in select_probe_qa(context, count=2)
    ]
    controller = AutoResearchController(tmp_path / "results.tsv")
    result = controller.evaluate(
        [{"candidate_id": "c00", "code_sha256": "code", "description": "baseline"}],
        evaluator=lambda _: {
            "pipeline_valid": True,
            "gold_blind_valid": True,
            "construction_count": 1,
            "qa_count": 2,
            "diagnosed_engineering_fix": True,
            "semantics_unchanged": True,
        },
    )
    assert result[0]["status"] == "keep"
    assert result[0]["merge_authority"].startswith("NONE_")
