"""Provider-free TDD for the DVSR cross-snapshot observer schema."""

from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_observer import (
    DVSR_OBSERVER_SCHEMA,
    DvsrObservationError,
    DvsrObserver,
    REQUIRED_DVSR_FIELDS,
    c1_valid_subset_of_c0,
    validate_dvsr_observation,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_seam import canonical_digest


def _record(**overrides: object) -> dict[str, object]:
    request = {"model": "qwen3-8b-awq", "schema": "node-v1", "flags": {"json": True}, "order": ["n1"], "messages": [{"role": "user", "content": "x"}]}
    value: dict[str, object] = {
        "prepared_artifact_identity": {"artifact_digest": "a" * 64, "source_sequence": 1, "v6_identity": "v6-sealed", "clone_proof": "isolated"},
        "read_epoch": "s0",
        "read_set": {"completeness": "COMPLETE", "keys": ["n1", "n2"]},
        "operator_id": "DVSR_OPERATOR_NEUTRAL_OBSERVER_V1",
        "operator_cut": "CUT-N",
        "ordered_candidate_ids": ["n1"],
        "candidate_scores": {"n1": 0.9},
        "prompt_visible_projection_digest": "b" * 64,
        "canonical_request": request,
        "canonical_request_digest": canonical_digest(request),
        "result_digest": "c" * 64,
        "continuation_digest": "d" * 64,
        "actual_touched_write_delta": {"complete": True, "writes": []},
        "no_write_proof": {"speculative_db_writes": 0, "speculative_publications": 0},
        "semantic_critical_path": {"hidden_cp_ns": 100, "visible_repair_cp_ns": 0, "failed_speculation_work_ns": 0},
        "validation_timing_ns": 10,
        "repair_timing_ns": 0,
        "status": "VALID",
        "unknown_reasons": [],
    }
    value.update(overrides)
    return value


def test_complete_record_is_accepted_and_append_only() -> None:
    observer = DvsrObserver()
    observation = observer.append(_record())
    assert observation.record["operator_cut"] == "CUT-N"
    assert len(observer.records) == 1
    assert DVSR_OBSERVER_SCHEMA.startswith("membind.dvsr")


def test_missing_fields_are_rejected_before_any_selection() -> None:
    record = _record()
    record.pop("read_set")
    with pytest.raises(DvsrObservationError, match="read_set"):
        validate_dvsr_observation(record)


def test_valid_requires_complete_read_set() -> None:
    with pytest.raises(DvsrObservationError, match="complete read_set"):
        validate_dvsr_observation(_record(read_set={"completeness": "UNKNOWN", "keys": []}))


def test_mixed_snapshot_or_epoch_is_unknown_not_valid() -> None:
    record = _record(status="UNKNOWN", read_epoch="mixed:s0|s1", unknown_reasons=["mixed_snapshot"])
    validate_dvsr_observation(record)
    with pytest.raises(DvsrObservationError, match="complete read_set"):
        validate_dvsr_observation(_record(read_epoch="mixed:s0|s1", unknown_reasons=["mixed_snapshot"], read_set={"completeness": "UNKNOWN", "keys": []}))


def test_speculative_write_or_publication_breaks_proof() -> None:
    with pytest.raises(DvsrObservationError, match="write/publication"):
        validate_dvsr_observation(_record(no_write_proof={"speculative_db_writes": 1, "speculative_publications": 0}))


def test_canonical_request_digest_and_required_fields_are_exact() -> None:
    record = _record()
    record["canonical_request_digest"] = "0" * 64
    with pytest.raises(DvsrObservationError, match="digest mismatch"):
        validate_dvsr_observation(record)
    request = dict(record["canonical_request"])
    request.pop("messages")
    record = _record(canonical_request=request, canonical_request_digest=canonical_digest(request))
    with pytest.raises(DvsrObservationError, match="canonical request is incomplete"):
        validate_dvsr_observation(record)


def test_c1_valid_set_cannot_exceed_c0_fresh_oracle() -> None:
    assert c1_valid_subset_of_c0({"r1", "r2"}, {"r1"}) is True
    assert c1_valid_subset_of_c0({"r1"}, {"r1", "r2"}) is False


def test_required_fields_cover_workplan_observability_contract() -> None:
    assert {"read_epoch", "read_set", "canonical_request", "actual_touched_write_delta", "no_write_proof", "semantic_critical_path"} <= REQUIRED_DVSR_FIELDS

