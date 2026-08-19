from __future__ import annotations

from mab_quality_v2_final_qa.compatibility import to_quality_v1_record
from mab_quality_v2_final_qa.dataset_adapter import MABDatasetAdapter

from .test_contracts_adapter import _official_like_record


def test_quality_v1_projection_preserves_alignment() -> None:
    context = MABDatasetAdapter.from_records([_official_like_record()]).contexts[0]
    record = to_quality_v1_record(context)
    assert list(record) == [
        "haystack_session_ids",
        "haystack_dates",
        "haystack_sessions",
    ]
    assert (
        len(record["haystack_session_ids"])
        == len(record["haystack_dates"])
        == len(record["haystack_sessions"])
        == 3
    )
    assert all(
        "has_answer" not in turn
        for session in record["haystack_sessions"]
        for turn in session
    )
