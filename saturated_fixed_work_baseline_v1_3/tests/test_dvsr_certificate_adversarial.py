"""Provider-free adversarial contract for the DVSR C1 certificate.

These tests intentionally exercise boundaries where a set-only top-k check is
not a proof.  They are kept separate from legacy V7 tests so a future
operator-selection run can report this hard soundness target independently.
"""

from __future__ import annotations

from saturated_fixed_work_baseline_v1_3.membind_v7.certificates import (
    CertificateStatus,
    Witness,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_certificates import certify_dvsr_exact_topk
from saturated_fixed_work_baseline_v1_3.membind_v7.state_delta import DeltaChange, StateDelta


def _witness(**proof: object) -> Witness:
    return Witness(
        operator="node_cosine",
        query=(0.4, 0.6),
        result=("n1", "n2"),
        domain=("n1", "n2", "n3", "n4"),
        k=2,
        cutoff=0.80,
        ties=(),
        query_epoch="query-v1",
        index_epoch="index-v1",
        filter_fingerprint="filter-v1",
        ranking_fingerprint="ranking-v1",
        projection_fingerprint="projection-v1",
        proof_data={"tie_contract": True, **proof},
    )


def _delta(*changes: DeltaChange, environment_changes: frozenset[str] = frozenset()) -> StateDelta:
    return StateDelta(source_version=1, target_version=2, changes=tuple(changes), environment_changes=environment_changes)


def _nonmember_change(*fields: str, after: dict[str, object] | None = None, operation: str = "update") -> DeltaChange:
    return DeltaChange(
        kind="node",
        key="n3",
        changed_fields=frozenset(fields),
        before={field: "before" for field in fields},
        after=after if after is not None else {field: "after" for field in fields},
        operation=operation,
    )


def test_model_epoch_mismatch_is_unknown() -> None:
    result = certify_dvsr_exact_topk(_witness(post_scores={"n3": 0.1}), _delta(environment_changes=frozenset({"model_epoch"})))
    assert result.status is CertificateStatus.UNKNOWN


def test_embedder_and_config_epoch_mismatch_are_unknown() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": 0.1}),
        _delta(environment_changes=frozenset({"embedder_epoch", "config_epoch"})),
    )
    assert result.status is CertificateStatus.UNKNOWN


def test_filter_k_threshold_and_group_changes_are_not_set_only_proofs() -> None:
    for field in ("filter", "group", "k", "threshold", "min_score"):
        result = certify_dvsr_exact_topk(
            _witness(post_scores={"n3": 0.1}),
            _delta(_nonmember_change(field)),
        )
        assert result.status is CertificateStatus.UNKNOWN, field


def test_nonmember_prompt_payload_change_is_stable_when_strictly_below_cutoff() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": 0.1}),
        _delta(_nonmember_change("summary", after={"summary": "changed"})),
    )
    assert result.status is CertificateStatus.STABLE


def test_new_low_score_node_with_payload_is_stable() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n5": 0.2}),
        _delta(
            DeltaChange(
                kind="node",
                key="n5",
                changed_fields=frozenset({"name", "summary", "labels", "attributes", "name_embedding"}),
                after={
                    "name": "new",
                    "summary": "new summary",
                    "labels": ["Entity"],
                    "attributes": {"kind": "new"},
                    "name_embedding": [0.1, 0.2],
                },
                operation="insert",
            )
        ),
    )
    assert result.status is CertificateStatus.STABLE


def test_new_high_score_node_is_invalid() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n5": 0.9}),
        _delta(
            DeltaChange(
                kind="node",
                key="n5",
                changed_fields=frozenset({"summary", "name_embedding"}),
                after={"summary": "new", "name_embedding": [0.1, 0.2]},
                operation="insert",
            )
        ),
    )
    assert result.status is CertificateStatus.INVALID


def test_existing_member_payload_change_is_invalid() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n1": 0.95}),
        _delta(
            DeltaChange(
                kind="node",
                key="n1",
                changed_fields=frozenset({"summary"}),
                before={"summary": "old"},
                after={"summary": "new"},
                operation="update",
            )
        ),
    )
    assert result.status is CertificateStatus.INVALID


def test_nonmember_embedding_update_is_stable_with_post_score_bound() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": 0.79}),
        _delta(
            _nonmember_change(
                "name_embedding",
                after={"name_embedding": [0.3, 0.7]},
            )
        ),
    )
    assert result.status is CertificateStatus.STABLE


def test_nonmember_crossing_cutoff_is_invalid() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": 0.81}),
        _delta(_nonmember_change("name_embedding", after={"name_embedding": [0.8, 0.2]})),
    )
    assert result.status is CertificateStatus.INVALID


def test_nonmember_deletion_is_stable_but_member_deletion_is_invalid() -> None:
    nonmember = certify_dvsr_exact_topk(
        _witness(post_scores={}),
        _delta(
            DeltaChange(
                kind="node",
                key="n3",
                changed_fields=frozenset(),
                before={"name": "old"},
                operation="delete",
            )
        ),
    )
    member = certify_dvsr_exact_topk(
        _witness(post_scores={}),
        _delta(
            DeltaChange(
                kind="node",
                key="n2",
                changed_fields=frozenset(),
                before={"name": "old"},
                operation="delete",
            )
        ),
    )
    assert nonmember.status is CertificateStatus.STABLE
    assert member.status is CertificateStatus.INVALID


def test_batch_membership_or_order_change_is_unknown() -> None:
    for field in ("batch_membership", "batch_order", "deterministic_branch"):
        result = certify_dvsr_exact_topk(
            _witness(post_scores={"n3": 0.1}),
            _delta(_nonmember_change(field)),
        )
        assert result.status is CertificateStatus.UNKNOWN, field


def test_nan_or_nonfinite_score_is_unknown() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": float("nan")}),
        _delta(_nonmember_change("score", after={"score": float("nan")})),
    )
    assert result.status is CertificateStatus.UNKNOWN


def test_mixed_snapshot_epoch_is_unknown() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": 0.1}),
        _delta(environment_changes=frozenset({"read_epoch", "mixed_snapshot"})),
    )
    assert result.status is CertificateStatus.UNKNOWN


def test_incomplete_delta_is_unknown() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": 0.1}),
        _delta(_nonmember_change("summary", after={})),
    )
    assert result.status is CertificateStatus.UNKNOWN


def test_candidate_deletion_of_result_member_is_invalid() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": 0.1}),
        _delta(
            DeltaChange(
                kind="node",
                key="n2",
                changed_fields=frozenset(),
                before={"name": "old"},
                operation="delete",
            )
        ),
    )
    assert result.status is CertificateStatus.INVALID


def test_cutoff_boundary_requires_explicit_post_order_contract() -> None:
    result = certify_dvsr_exact_topk(
        _witness(post_scores={"n3": 0.80}, post_order=("n1", "n2"), tie_contract=False),
        _delta(_nonmember_change("score", after={"score": 0.80})),
    )
    assert result.status is CertificateStatus.UNKNOWN
