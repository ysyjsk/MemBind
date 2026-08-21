from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_2.correctness import (
    DirectEvidenceError,
    reduce_direct_semantic_evidence,
)


def _span(**metadata: object) -> dict[str, object]:
    return {
        "phase": "candidate-search",
        "operation_class": "node-dedup",
        "metadata": metadata,
    }


def test_direct_evidence_reducer_counts_only_preregistered_causal_records() -> None:
    result = reduce_direct_semantic_evidence(
        [
            {
                "spans": [
                    _span(
                        direct_semantic_observation="future_persistent_state_read",
                        direct_causal_evidence=True,
                        direct_evidence_id="a" * 64,
                        observed_source_sequence=1,
                        causal_source_sequence=2,
                        persistent_object_ids=["node-1"],
                    ),
                    _span(ordering_observation="completion_inversion"),
                ]
            },
            {
                "spans": [
                    _span(
                        direct_semantic_observation="wrong_state_write",
                        direct_causal_evidence=True,
                        direct_evidence_id="b" * 64,
                        observed_source_sequence=3,
                        causal_source_sequence=2,
                        persistent_object_ids=["edge-9"],
                    )
                ]
            },
        ]
    )
    assert result["direct_semantic_violations"] == 2
    assert result["by_observation"] == {
        "future_persistent_state_read": 1,
        "stale_predecessor_write": 0,
        "wrong_state_write": 1,
    }
    assert result["ordering_observations_counted_as_direct"] == 0
    assert result["availability"] == "DERIVED"


def test_absence_of_direct_causal_records_is_derived_zero_not_ordering_inference() -> None:
    result = reduce_direct_semantic_evidence(
        [{"spans": [_span(ordering_observation="publication_inversion")]}]
    )
    assert result["direct_semantic_violations"] == 0
    assert result["direct_evidence_records"] == []
    assert result["ordering_observations_counted_as_direct"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"direct_causal_evidence": False},
        {"direct_evidence_id": "not-a-hash"},
        {"persistent_object_ids": []},
        {"causal_source_sequence": -1},
        {"direct_semantic_observation": "completion_inversion"},
    ],
)
def test_malformed_claimed_direct_evidence_fails_closed(changes: dict[str, object]) -> None:
    metadata: dict[str, object] = {
        "direct_semantic_observation": "stale_predecessor_write",
        "direct_causal_evidence": True,
        "direct_evidence_id": "c" * 64,
        "observed_source_sequence": 2,
        "causal_source_sequence": 1,
        "persistent_object_ids": ["node-2"],
    }
    metadata.update(changes)
    with pytest.raises(DirectEvidenceError, match="DIRECT_EVIDENCE_INVALID"):
        reduce_direct_semantic_evidence([{"spans": [_span(**metadata)]}])
