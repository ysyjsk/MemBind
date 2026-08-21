from __future__ import annotations

from dataclasses import dataclass

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.semantic_fingerprint import (
    SemanticFingerprintError,
    fingerprint_records,
    semantic_fingerprint,
)


FIELDS = ("candidate_id", "name", "score")


def test_equal_semantic_objects_have_equal_fingerprints_without_repr_or_address() -> None:
    left = {"candidate_id": "n-1", "name": "Alpha", "score": 0.91, "object_id": 1}
    right = {"score": 0.91, "object_id": 999, "name": "Alpha", "candidate_id": "n-1"}
    assert semantic_fingerprint(left, boundary="NODE_CANDIDATE", semantic_fields=FIELDS) == semantic_fingerprint(
        right, boundary="NODE_CANDIDATE", semantic_fields=FIELDS
    )


def test_dict_iteration_order_is_ignored_but_ordered_records_are_not() -> None:
    left = {"candidate_id": "n-1", "name": "Alpha", "score": 0.91}
    reordered_mapping = {"score": 0.91, "name": "Alpha", "candidate_id": "n-1"}
    assert semantic_fingerprint(left, boundary="NODE_CANDIDATE", semantic_fields=FIELDS) == semantic_fingerprint(
        reordered_mapping, boundary="NODE_CANDIDATE", semantic_fields=FIELDS
    )

    first = [{"candidate_id": "n-1", "name": "Alpha", "score": 0.91}, {"candidate_id": "n-2", "name": "Beta", "score": 0.88}]
    second = list(reversed(first))
    left_record = fingerprint_records(first, boundary="NODE_CANDIDATE_SET", semantic_fields=FIELDS)
    right_record = fingerprint_records(second, boundary="NODE_CANDIDATE_SET", semantic_fields=FIELDS)
    assert left_record["ordered_identity_sha256"] != right_record["ordered_identity_sha256"]
    assert left_record["membership_identity_sha256"] == right_record["membership_identity_sha256"]


def test_candidate_membership_batch_membership_and_decision_changes_are_visible() -> None:
    base = [{"candidate_id": "n-1", "name": "Alpha", "score": 0.91}, {"candidate_id": "n-2", "name": "Beta", "score": 0.88}]
    changed_membership = [*base, {"candidate_id": "n-3", "name": "Gamma", "score": 0.82}]
    assert fingerprint_records(base, boundary="NODE_CANDIDATE_SET", semantic_fields=FIELDS)["membership_identity_sha256"] != fingerprint_records(
        changed_membership, boundary="NODE_CANDIDATE_SET", semantic_fields=FIELDS
    )["membership_identity_sha256"]

    batch_a = fingerprint_records(base, boundary="NODE_RESOLUTION_BATCH", semantic_fields=("candidate_id",))
    batch_b = fingerprint_records([base[0]], boundary="NODE_RESOLUTION_BATCH", semantic_fields=("candidate_id",))
    assert batch_a["ordered_identity_sha256"] != batch_b["ordered_identity_sha256"]

    decision_a = {"candidate_id": "n-1", "decision": "MERGE"}
    decision_b = {"candidate_id": "n-1", "decision": "CREATE"}
    assert semantic_fingerprint(decision_a, boundary="NODE_RESOLUTION_DECISION", semantic_fields=("candidate_id", "decision")) != semantic_fingerprint(
        decision_b, boundary="NODE_RESOLUTION_DECISION", semantic_fields=("candidate_id", "decision")
    )


def test_runtime_metadata_does_not_change_semantic_fingerprint() -> None:
    left = {"candidate_id": "n-1", "name": "Alpha", "run_id": "run-a", "namespace": "ns-a"}
    right = {"candidate_id": "n-1", "name": "Alpha", "run_id": "run-b", "namespace": "ns-b"}
    assert semantic_fingerprint(left, boundary="NODE_EXTRACTION_OUTPUT", semantic_fields=("candidate_id", "name")) == semantic_fingerprint(
        right, boundary="NODE_EXTRACTION_OUTPUT", semantic_fields=("candidate_id", "name")
    )


def test_ambiguous_or_undeclared_object_fails_closed() -> None:
    @dataclass
    class RuntimeObject:
        candidate_id: str
        name: str
        namespace: str

    with pytest.raises(SemanticFingerprintError, match="SEMANTIC_FIELDS_REQUIRED"):
        semantic_fingerprint(RuntimeObject("n-1", "Alpha", "ns"), boundary="NODE_CANDIDATE", semantic_fields=())
    with pytest.raises(SemanticFingerprintError, match="RUNTIME_METADATA_FIELD_DECLARED_SEMANTIC"):
        semantic_fingerprint(
            {"candidate_id": "n-1", "namespace": "ns"},
            boundary="NODE_CANDIDATE",
            semantic_fields=("candidate_id", "namespace"),
        )
    with pytest.raises(SemanticFingerprintError, match="SEMANTIC_OBJECT_MAPPING_REQUIRED"):
        semantic_fingerprint(object(), boundary="NODE_CANDIDATE", semantic_fields=("candidate_id",))


def test_nested_semantic_values_are_canonical_and_nonfinite_values_fail() -> None:
    left = {"decision": {"target": "n-1", "labels": ["A", "B"]}}
    right = {"decision": {"labels": ["A", "B"], "target": "n-1"}}
    assert semantic_fingerprint(left, boundary="DECISION", semantic_fields=("decision",)) == semantic_fingerprint(
        right, boundary="DECISION", semantic_fields=("decision",)
    )
    with pytest.raises(SemanticFingerprintError, match="NONFINITE_NUMBER"):
        semantic_fingerprint({"score": float("nan")}, boundary="CANDIDATE", semantic_fields=("score",))
