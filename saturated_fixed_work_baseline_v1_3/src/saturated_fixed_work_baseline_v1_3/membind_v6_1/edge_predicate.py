"""V6.1 safe edge-resolution bypass with content-free evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, MutableSequence
from functools import wraps
from typing import Any


class EdgePredicatePushdownError(RuntimeError):
    pass


def _endpoint_pair(edge: Any) -> tuple[str, str] | None:
    source = getattr(edge, "source_node_uuid", None)
    target = getattr(edge, "target_node_uuid", None)
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        return None
    return source, target


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_TRANSITION_CUE = re.compile(
    r"\b(?:no longer|not anymore|switch(?:ed|es|ing)?|replac(?:e|ed|es|ing)|"
    r"mov(?:e|ed|es|ing)|chang(?:e|ed|es|ing)|stopp(?:ed|ing)|stops?|"
    r"ceas(?:e|ed|es|ing)|instead)\b",
    re.IGNORECASE,
)


def _relation_name(edge: Any) -> str | None:
    value = getattr(edge, "name", None)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold()


def _normalized_fact(edge: Any) -> str | None:
    value = getattr(edge, "fact", None)
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split()).casefold()


def _invalidation_acceptance(resolved_edge: Any, candidate: Any) -> tuple[bool, str]:
    resolved_uuid = getattr(resolved_edge, "uuid", None)
    candidate_uuid = getattr(candidate, "uuid", None)
    if resolved_edge is candidate or (
        isinstance(resolved_uuid, str)
        and resolved_uuid
        and resolved_uuid == candidate_uuid
    ):
        return False, "self_invalidation"
    resolved_pair = _endpoint_pair(resolved_edge)
    candidate_pair = _endpoint_pair(candidate)
    if resolved_pair is None or candidate_pair is None:
        return True, "malformed_conservative"
    if set(resolved_pair) == set(candidate_pair):
        resolved_fact = _normalized_fact(resolved_edge)
        candidate_fact = _normalized_fact(candidate)
        if resolved_fact is not None and resolved_fact == candidate_fact:
            return False, "idempotent_duplicate_invalidation"
        return True, "same_canonical_pair"
    if not set(resolved_pair).intersection(candidate_pair):
        return False, "disjoint_endpoints"
    resolved_relation = _relation_name(resolved_edge)
    candidate_relation = _relation_name(candidate)
    fact = getattr(resolved_edge, "fact", None)
    if (
        resolved_relation is not None
        and resolved_relation == candidate_relation
        and isinstance(fact, str)
        and _TRANSITION_CUE.search(fact) is not None
    ):
        return True, "explicit_relation_transition"
    return False, "unproven_cross_pair_transition"


def _could_accept_invalidation(extracted_edge: Any, candidate: Any) -> bool:
    """Return whether the existing post-LLM predicate could accept a candidate."""

    extracted_pair = _endpoint_pair(extracted_edge)
    candidate_pair = _endpoint_pair(candidate)
    if extracted_pair is None or candidate_pair is None:
        return True
    extracted_endpoints = set(extracted_pair)
    candidate_endpoints = set(candidate_pair)
    if extracted_endpoints == candidate_endpoints:
        extracted_fact = _normalized_fact(extracted_edge)
        candidate_fact = _normalized_fact(candidate)
        if extracted_fact is not None and extracted_fact == candidate_fact:
            return False
        return True
    if not extracted_endpoints.intersection(candidate_endpoints):
        return False

    extracted_relation = _relation_name(extracted_edge)
    candidate_relation = _relation_name(candidate)
    fact = getattr(extracted_edge, "fact", None)
    if (
        extracted_relation is None
        or candidate_relation is None
        or not isinstance(fact, str)
    ):
        return True
    return bool(
        extracted_relation == candidate_relation and _TRANSITION_CUE.search(fact)
    )


def install_edge_invalidation_predicate_pushdown(
    diagnostics: MutableSequence[dict[str, Any]],
) -> Callable[[], None]:
    """Bypass calls whose candidates cannot pass temporal acceptance.

    Candidate-list filtering changes the LLM prompt and can therefore change
    decisions for candidates that remain.  Preserve the complete original
    prompt whenever any candidate may be relevant; the only transformed case
    is a call for which the existing post-LLM acceptance predicate must reject
    every candidate.
    """

    from graphiti_core.utils.maintenance import edge_operations

    original = getattr(edge_operations, "resolve_extracted_edge", None)
    if not callable(original):
        raise EdgePredicatePushdownError("Graphiti edge-resolution seam is unavailable")
    if getattr(edge_operations, "_membind_edge_predicate_pushdown", None) is not None:
        raise EdgePredicatePushdownError("edge predicate pushdown is already installed")

    @wraps(original)
    async def resolve_extracted_edge(
        llm_client: Any,
        extracted_edge: Any,
        related_edges: list[Any],
        existing_edges: list[Any],
        episode: Any,
        edge_type_candidates: dict[str, Any] | None = None,
    ) -> Any:
        extracted_pair = _endpoint_pair(extracted_edge)
        candidate_pairs = [_endpoint_pair(candidate) for candidate in existing_edges]
        malformed_retained = sum(pair is None for pair in candidate_pairs)
        structurally_ineligible = [
            candidate
            for candidate in existing_edges
            if not _could_accept_invalidation(extracted_edge, candidate)
        ]
        safe_predicate_bypass = bool(
            not related_edges
            and existing_edges
            and len(structurally_ineligible) == len(existing_edges)
        )
        retained = [] if safe_predicate_bypass else list(existing_edges)
        rejected = list(existing_edges) if safe_predicate_bypass else []
        rejected_disjoint = 0
        if safe_predicate_bypass and extracted_pair is not None:
            extracted_endpoints = set(extracted_pair)
            rejected_disjoint = sum(
                pair is not None and not extracted_endpoints.intersection(pair)
                for pair in candidate_pairs
            )

        candidate_identity = [
            _endpoint_pair(candidate) or ("MALFORMED", str(index))
            for index, candidate in enumerate(existing_edges)
        ]
        audit = {
                "schema_version": "membind.v6.1.edge-invalidation-predicate.v1",
                "event": "EDGE_INVALIDATION_PREDICATE_AUDIT",
                "policy": "acceptance_aware_all_or_nothing_call_bypass_v3",
                "related_edge_count": len(related_edges),
                "invalidation_candidate_count": len(existing_edges),
                "retained_invalidation_candidate_count": len(retained),
                "rejected_structurally_ineligible_candidate_count": len(rejected),
                "rejected_disjoint_candidate_count": rejected_disjoint,
                "malformed_candidate_retained_count": malformed_retained,
                "newly_enabled_llm_bypass": safe_predicate_bypass,
                "original_prompt_context_preserved": not safe_predicate_bypass,
                "endpoint_pair_sha256": _sha256(extracted_pair),
                "candidate_endpoint_set_sha256": _sha256(candidate_identity),
            }
        diagnostics.append(audit)
        temporal_snapshot = {
            id(candidate): (
                getattr(candidate, "invalid_at", None),
                getattr(candidate, "expired_at", None),
            )
            for candidate in [*related_edges, *existing_edges]
        }
        result = await original(
            llm_client,
            extracted_edge,
            related_edges,
            retained,
            episode,
            edge_type_candidates,
        )
        if not isinstance(result, tuple) or len(result) != 3 or not isinstance(result[1], list):
            return result

        resolved_edge, proposed_invalidations, duplicate_edges = result
        accepted_invalidations: list[Any] = []
        rejected_invalidations: list[Any] = []
        reason_counts: dict[str, int] = {}
        for candidate in proposed_invalidations:
            accepted, reason = _invalidation_acceptance(resolved_edge, candidate)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if accepted:
                accepted_invalidations.append(candidate)
                continue
            rejected_invalidations.append(candidate)
            prior = temporal_snapshot.get(id(candidate))
            if prior is None:
                raise EdgePredicatePushdownError(
                    "rejected invalidation was not present in the candidate snapshot"
                )
            candidate.invalid_at, candidate.expired_at = prior
        resolved_prior = temporal_snapshot.get(id(resolved_edge))
        reused_resolved_mutation_rolled_back = False
        if resolved_prior is not None and id(resolved_edge) not in {
            id(candidate) for candidate in accepted_invalidations
        }:
            resolved_current = (
                getattr(resolved_edge, "invalid_at", None),
                getattr(resolved_edge, "expired_at", None),
            )
            if resolved_current != resolved_prior:
                resolved_edge.invalid_at, resolved_edge.expired_at = resolved_prior
                reused_resolved_mutation_rolled_back = True
        audit.update(
            {
                "llm_invalidation_proposal_count": len(proposed_invalidations),
                "accepted_invalidation_count": len(accepted_invalidations),
                "rejected_invalidation_count": len(rejected_invalidations),
                "reused_resolved_edge_temporal_snapshot_present": resolved_prior
                is not None,
                "reused_resolved_edge_temporal_mutation_rolled_back": (
                    reused_resolved_mutation_rolled_back
                ),
                "invalidation_acceptance_reason_counts": dict(sorted(reason_counts.items())),
                "accepted_invalidation_endpoint_sha256": _sha256(
                    [_endpoint_pair(candidate) for candidate in accepted_invalidations]
                ),
                "rejected_invalidation_endpoint_sha256": _sha256(
                    [_endpoint_pair(candidate) for candidate in rejected_invalidations]
                ),
            }
        )
        return resolved_edge, accepted_invalidations, duplicate_edges

    edge_operations.resolve_extracted_edge = resolve_extracted_edge
    edge_operations._membind_edge_predicate_pushdown = resolve_extracted_edge
    restored = False

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        if getattr(edge_operations, "resolve_extracted_edge", None) is not resolve_extracted_edge:
            raise EdgePredicatePushdownError("Graphiti edge-resolution seam changed while installed")
        edge_operations.resolve_extracted_edge = original
        delattr(edge_operations, "_membind_edge_predicate_pushdown")

    return restore


__all__ = [
    "EdgePredicatePushdownError",
    "install_edge_invalidation_predicate_pushdown",
]
