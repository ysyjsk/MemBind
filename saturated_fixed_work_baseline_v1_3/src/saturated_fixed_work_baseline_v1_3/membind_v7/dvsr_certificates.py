"""DVSR-scoped fail-closed C1 certificates.

Legacy V7 certificates remain hash-frozen for their historical protocols.
This module gives the new DVSR identity its stricter semantic contract without
mutating that legacy boundary.
"""

from __future__ import annotations

import math

from .certificates import (
    CertificateResult,
    CertificateStatus,
    Witness,
    _changed_relevant,
    _operation,
)
from .state_delta import StateDelta


def certify_dvsr_exact_topk(witness: Witness, delta: StateDelta) -> CertificateResult:
    """Certify only when all semantic inputs remain exact.

    A score sidecar can prove a changed ``name_embedding`` candidate remains
    below the cutoff.  It cannot prove changes to query/filter/payload/batch
    semantics, epochs, or an incomplete update image.
    """

    if witness.operator not in {"node_cosine", "edge_cosine"}:
        return CertificateResult(CertificateStatus.UNKNOWN, "operator is not exact cosine")
    if not witness.query_epoch or not witness.index_epoch:
        return CertificateResult(CertificateStatus.UNKNOWN, "query/index epoch is missing")
    if delta.environment_changes:
        return CertificateResult(
            CertificateStatus.UNKNOWN,
            "semantic environment epoch changed",
            tuple(sorted(delta.environment_changes)),
        )
    changed = _changed_relevant(witness, delta)
    if not changed:
        return CertificateResult(CertificateStatus.STABLE, "no domain observable changed")
    global_semantic_fields = frozenset(
        {
            "query",
            "query_embedding",
            "filter",
            "filter_fingerprint",
            "group",
            "group_ids",
            "k",
            "limit",
            "threshold",
            "min_score",
            "model",
            "embedder",
            "index",
            "batch_membership",
            "batch_order",
            "deterministic_branch",
            "serialization",
            "read_epoch",
        }
    )
    for change in changed:
        operation = _operation(change)
        fields = frozenset(str(field) for field in getattr(change, "changed_fields", ()))
        if fields & global_semantic_fields:
            return CertificateResult(CertificateStatus.UNKNOWN, "global query or consumer contract changed")
        if operation == "update" and any(
            field not in change.before or field not in change.after for field in fields
        ):
            return CertificateResult(CertificateStatus.UNKNOWN, "delta image is incomplete")
        if operation in {"insert", "create", "add"} and any(
            field not in change.after for field in fields
        ):
            return CertificateResult(CertificateStatus.UNKNOWN, "delta image is incomplete")
    invalid = tuple(change.key for change in changed if change.key in witness.result)
    if invalid:
        return CertificateResult(CertificateStatus.INVALID, "result member changed", invalid)
    if witness.ties:
        return CertificateResult(CertificateStatus.UNKNOWN, "consumer-visible tie order has no contract")
    if len(witness.result) < witness.k or witness.cutoff is None:
        if not any(_operation(change) not in {"delete", "remove"} for change in changed):
            return CertificateResult(CertificateStatus.STABLE, "only short-result nonmembers were deleted")
        scores = witness.proof_data.get("post_scores", {})
        min_score = witness.proof_data.get("min_score")
        if (
            witness.proof_data.get("no_new_eligible") is True
            and witness.proof_data.get("tie_contract")
            and isinstance(scores, dict)
            and isinstance(min_score, (int, float))
            and not isinstance(min_score, bool)
        ):
            try:
                excluded = all(
                    change.key in scores
                    and math.isfinite(float(scores[change.key]))
                    and float(scores[change.key]) < float(min_score)
                    for change in changed
                )
            except (TypeError, ValueError):
                excluded = False
            if excluded:
                return CertificateResult(CertificateStatus.STABLE, "explicit short-result exclusion proof")
        return CertificateResult(CertificateStatus.UNKNOWN, "short result has no kth cutoff")
    changed_nonmembers = tuple(
        change for change in changed if _operation(change) not in {"delete", "remove"}
    )
    if not changed_nonmembers:
        return CertificateResult(CertificateStatus.STABLE, "only non-result candidates were deleted")
    scores = witness.proof_data.get("post_scores", {})
    if not isinstance(scores, dict):
        return CertificateResult(CertificateStatus.UNKNOWN, "non-member score bound is unavailable")
    for change in changed_nonmembers:
        if change.key not in scores:
            return CertificateResult(CertificateStatus.UNKNOWN, "non-member score bound is unavailable")
        try:
            score = float(scores[change.key])
            cutoff = float(witness.cutoff)
        except (TypeError, ValueError):
            return CertificateResult(CertificateStatus.UNKNOWN, "non-member score bound is invalid")
        if not math.isfinite(score) or not math.isfinite(cutoff):
            return CertificateResult(CertificateStatus.UNKNOWN, "non-finite score or cutoff")
        if score > cutoff:
            return CertificateResult(CertificateStatus.INVALID, "new candidate exceeds kth cutoff", (change.key,))
        if score == cutoff:
            if witness.proof_data.get("tie_contract") != "consumer-order-frozen":
                return CertificateResult(CertificateStatus.UNKNOWN, "new candidate reaches kth cutoff")
            post_order = witness.proof_data.get("post_order")
            if post_order is None or tuple(post_order) != tuple(witness.result):
                return CertificateResult(CertificateStatus.UNKNOWN, "post-delta boundary order is unavailable")
    if witness.proof_data.get("tie_contract"):
        return CertificateResult(
            CertificateStatus.STABLE,
            "post-delta score bounds prove changed nonmembers cannot affect the result",
        )
    return CertificateResult(CertificateStatus.UNKNOWN, "non-member score bound is unavailable")


__all__ = ["certify_dvsr_exact_topk"]
