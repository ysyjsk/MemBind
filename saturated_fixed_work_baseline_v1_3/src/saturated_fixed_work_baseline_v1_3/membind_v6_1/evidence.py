"""Corrected V6.1 response, span, and admission evidence."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump(mode="json"))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def response_sha256(response: Any) -> str:
    payload = json.dumps(
        _jsonable(response),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def extraction_work_inventory(diagnostics: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Aggregate content-free extraction progress into the sealed work schema."""

    pages = [row for row in diagnostics if row.get("event") == "EDGE_PAGINATION_PAGE"]
    summary_audits = [
        row for row in diagnostics if row.get("event") == "SUMMARY_RESPONSE_AUDIT"
    ]
    node_audits = [row for row in diagnostics if row.get("event") == "NODE_RESPONSE_AUDIT"]
    node_partition_pipelines = [
        row
        for row in diagnostics
        if row.get("schema_version") == "membind.v6.1.node-partition-pipeline.v1"
    ]
    edge_predicate_audits = [
        row
        for row in diagnostics
        if row.get("event") == "EDGE_INVALIDATION_PREDICATE_AUDIT"
    ]
    grounded_summary_batches = [
        row for row in diagnostics if row.get("event") == "GROUNDED_SUMMARY_BATCH"
    ]
    grounded_summary_nodes = [
        row for row in diagnostics if row.get("event") == "GROUNDED_SUMMARY_NODE"
    ]
    capacities = {int(row.get("page_capacity", 0) or 0) for row in pages}
    capacities.discard(0)
    if len(capacities) > 1:
        raise ValueError("extraction diagnostics mix edge page capacities")
    return {
        "pagination_requests": len(pages),
        "pagination_continuation_requests": sum(
            1 for row in pages if int(row.get("page_index", 0) or 0) > 0
        ),
        "pagination_raw_unique_progress_edges": sum(
            int(row.get("raw_unique_progress_edge_count", 0) or 0) for row in pages
        ),
        "pagination_unique_delta_edges": sum(
            int(row.get("delta_edge_count", 0) or 0) for row in pages
        ),
        "pagination_duplicate_edges": sum(
            int(row.get("duplicate_edge_count", 0) or 0) for row in pages
        ),
        "pagination_duplicate_recovery_requests": sum(
            1 for row in pages if row.get("duplicate_recovery_request") is True
        ),
        "pagination_duplicate_recovery_successes": sum(
            1 for row in pages if row.get("duplicate_recovery_succeeded") is True
        ),
        "pagination_invalid_endpoint_edges": sum(
            int(row.get("invalid_endpoint_edge_count", 0) or 0) for row in pages
        ),
        "pagination_zero_delta_terminations": sum(
            1 for row in diagnostics if row.get("event") == "EDGE_PAGINATION_ZERO_DELTA"
        ),
        "pagination_empty_terminations": sum(
            1 for row in diagnostics if row.get("event") == "EDGE_PAGINATION_EMPTY_PAGE"
        ),
        "pagination_page_capacity": next(iter(capacities), 0),
        "node_response_audits": len(node_audits),
        "node_returned_entities": sum(
            int(row.get("returned_entity_count", 0) or 0) for row in node_audits
        ),
        "node_accepted_entities": sum(
            int(row.get("accepted_entity_count", 0) or 0) for row in node_audits
        ),
        "node_ungrounded_rejected": sum(
            int(row.get("ungrounded_entity_count", 0) or 0) for row in node_audits
        ),
        "node_duplicate_rejected": sum(
            int(row.get("duplicate_entity_count", 0) or 0) for row in node_audits
        ),
        "node_malformed_rejected": sum(
            int(row.get("malformed_entity_count", 0) or 0) for row in node_audits
        ),
        "node_partition_pipeline_calls": len(node_partition_pipelines),
        "node_partition_pipeline_partitions": sum(
            int(row.get("partition_count", 0) or 0) for row in node_partition_pipelines
        ),
        "node_partition_pipeline_max_active": max(
            (
                int(row.get("shared_max_active_partition_requests", 0) or 0)
                for row in node_partition_pipelines
            ),
            default=0,
        ),
        "summary_response_audits": len(summary_audits),
        "summary_unknown_rejected": sum(
            int(row.get("unknown_summary_count", 0) or 0) for row in summary_audits
        ),
        "summary_duplicate_rejected": sum(
            int(row.get("duplicate_summary_count", 0) or 0) for row in summary_audits
        ),
        "summary_omitted_requested": sum(
            int(row.get("omitted_requested_count", 0) or 0) for row in summary_audits
        ),
        "grounded_summary_materializations": sum(
            1
            for row in grounded_summary_batches
            if row.get("fallback_to_upstream") is False
        ),
        "grounded_summary_nodes": sum(
            int(row.get("materialized_node_count", 0) or 0)
            for row in grounded_summary_batches
        ),
        "grounded_summary_edge_fact_units": sum(
            int(row.get("edge_fact_unit_count", 0) or 0)
            for row in grounded_summary_batches
        ),
        "grounded_summary_episode_span_units": sum(
            int(row.get("episode_span_unit_count", 0) or 0)
            for row in grounded_summary_batches
        ),
        "grounded_summary_prior_units": sum(
            int(row.get("prior_certified_unit_count", 0) or 0)
            for row in grounded_summary_batches
        ),
        "grounded_summary_selected_units": sum(
            int(row.get("selected_unit_count", 0) or 0)
            for row in grounded_summary_batches
        ),
        "grounded_summary_dropped_units": sum(
            int(row.get("dropped_unit_count", 0) or 0)
            for row in grounded_summary_batches
        ),
        "grounded_summary_empty_nodes": sum(
            int(row.get("empty_grounding_node_count", 0) or 0)
            for row in grounded_summary_batches
        ),
        "grounded_summary_node_evidence": len(grounded_summary_nodes),
        "summary_llm_bypasses": sum(
            int(row.get("summary_llm_flights_bypassed", 0) or 0)
            for row in grounded_summary_batches
        ),
        "summary_upstream_fallbacks": sum(
            1
            for row in grounded_summary_batches
            if row.get("fallback_to_upstream") is True
        ),
        "edge_invalidation_predicate_audits": len(edge_predicate_audits),
        "edge_invalidation_candidates": sum(
            int(row.get("invalidation_candidate_count", 0) or 0)
            for row in edge_predicate_audits
        ),
        "edge_invalidation_candidates_retained": sum(
            int(row.get("retained_invalidation_candidate_count", 0) or 0)
            for row in edge_predicate_audits
        ),
        "edge_invalidation_structurally_ineligible_candidates_rejected": sum(
            int(row.get("rejected_structurally_ineligible_candidate_count", 0) or 0)
            for row in edge_predicate_audits
        ),
        "edge_invalidation_disjoint_candidates_rejected": sum(
            int(row.get("rejected_disjoint_candidate_count", 0) or 0)
            for row in edge_predicate_audits
        ),
        "edge_invalidation_malformed_candidates_retained": sum(
            int(row.get("malformed_candidate_retained_count", 0) or 0)
            for row in edge_predicate_audits
        ),
        "edge_dedupe_llm_bypasses_from_predicate": sum(
            1 for row in edge_predicate_audits if row.get("newly_enabled_llm_bypass") is True
        ),
        "edge_invalidation_llm_proposals": sum(
            int(row.get("llm_invalidation_proposal_count", 0) or 0)
            for row in edge_predicate_audits
        ),
        "edge_invalidations_accepted": sum(
            int(row.get("accepted_invalidation_count", 0) or 0)
            for row in edge_predicate_audits
        ),
        "edge_invalidations_rejected_by_temporal_acceptance": sum(
            int(row.get("rejected_invalidation_count", 0) or 0)
            for row in edge_predicate_audits
        ),
        "edge_reused_resolved_temporal_snapshots": sum(
            1
            for row in edge_predicate_audits
            if row.get("reused_resolved_edge_temporal_snapshot_present") is True
        ),
        "edge_reused_resolved_temporal_mutations_rolled_back": sum(
            1
            for row in edge_predicate_audits
            if row.get("reused_resolved_edge_temporal_mutation_rolled_back") is True
        ),
    }


def span_work_inventory(records: Sequence[Any]) -> dict[str, Any]:
    """Summarize realized work from the common native trace schema."""

    phases = [str(getattr(row, "phase", "")) for row in records]
    logical_requests = 0
    transport_attempts = 0
    transport_failed = 0
    prompt_tokens = 0
    completion_tokens = 0
    finish_reason_length = 0
    embedding_calls = 0
    embedding_items = 0
    db_reads = 0
    db_write_statements = 0
    db_write_transactions = 0
    prompt_counts: dict[str, int] = {}
    transport_groups: dict[str, list[Any]] = {}
    for row in records:
        phase = str(getattr(row, "phase", ""))
        metadata = dict(getattr(row, "metadata", {}) or {})
        if phase == "llm":
            logical_requests += 1
            prompt_name = str(metadata.get("prompt_name") or "UNKNOWN")
            prompt_counts[prompt_name] = prompt_counts.get(prompt_name, 0) + 1
        if phase == "llm-transport":
            transport_attempts += 1
            parent_span_id = getattr(row, "parent_span_id", None)
            group_key = str(parent_span_id) if parent_span_id is not None else f"orphan:{id(row)}"
            transport_groups.setdefault(group_key, []).append(row)
            prompt_tokens += int(metadata.get("input_tokens", 0) or 0)
            completion_tokens += int(metadata.get("output_tokens", 0) or 0)
            if str(getattr(row, "status", "ok")).casefold() != "ok":
                transport_failed += 1
            if str(metadata.get("finish_reason") or "").casefold() == "length":
                finish_reason_length += 1
        if phase == "embedding":
            embedding_calls += 1
            embedding_items += int(metadata.get("text_count", 0) or 0)
        operation_class = getattr(row, "operation_class", None)
        if operation_class is None:
            operation_class = metadata.get("operation_class")
        operation = str(operation_class or "").casefold()
        if phase == "database":
            if operation == "write":
                db_write_statements += 1
            elif operation == "query":
                db_reads += 1
        elif phase == "database-transaction" and operation in {"write", "transaction"}:
            db_write_transactions += 1
    transport_true_retries = 0
    compatibility_expansions = 0
    for group in transport_groups.values():
        ordered = sorted(
            group,
            key=lambda row: (
                int(getattr(row, "sequence", 0)),
                int(dict(getattr(row, "metadata", {}) or {}).get("attempt_index", 0) or 0),
            ),
        )
        group_retries = sum(
            1
            for previous, current in zip(ordered, ordered[1:])
            if str(getattr(previous, "status", "ok")).casefold() != "ok"
            and int(dict(getattr(current, "metadata", {}) or {}).get("attempt_index", 0) or 0) > 0
        )
        transport_true_retries += group_retries
        compatibility_expansions += max(0, len(ordered) - 1 - group_retries)

    return {
        "llm_logical_requests": logical_requests,
        "llm_logical_requests_by_prompt": dict(sorted(prompt_counts.items())),
        "transport_attempts": transport_attempts,
        "transport_failed_attempts": transport_failed,
        "transport_true_retry_attempts": transport_true_retries,
        "compatibility_expansion_attempts": compatibility_expansions,
        # Backward-compatible key with corrected semantics.
        "transport_retry_attempts": transport_true_retries,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason_length_count": finish_reason_length,
        "embedding_calls": embedding_calls,
        "embedding_items": embedding_items,
        "db_reads": db_reads,
        "db_write_statements": db_write_statements,
        "db_write_transactions": db_write_transactions,
        # Retained for artifact-schema compatibility; the decomposition above
        # prevents statement and transaction envelopes from being conflated.
        "db_writes": db_write_statements + db_write_transactions,
    }


def provider_proof(
    events: Sequence[Mapping[str, Any]],
    *,
    capacity: int,
    future_cap: int,
    arbiter_instance_id: str,
    token_budget: int | None = None,
    phase_isolated: bool = False,
    bootstrap_future_borrow: bool = False,
) -> dict[str, Any]:
    rows = [dict(row) for row in events]
    if not rows:
        raise ValueError("provider evidence is empty")
    if any(row.get("arbiter_instance_id") != arbiter_instance_id for row in rows):
        raise ValueError("provider evidence mixes arbiter instances")
    admits = [row for row in rows if row.get("event") == "ADMISSION_ADMIT"]
    releases = [row for row in rows if row.get("event") == "ADMISSION_RELEASE"]
    if len(admits) != len(releases):
        raise ValueError("provider evidence is unbalanced")
    admit_permits = {int(row["ticket"]) for row in admits}
    release_permits = {int(row["permit_id"]) for row in releases}
    if len(admit_permits) != len(admits) or admit_permits != release_permits:
        raise ValueError("provider permit identities are unbalanced")
    source_admits = [row for row in rows if row.get("event") == "SOURCE_LEASE_ADMIT"]
    source_releases = [row for row in rows if row.get("event") == "SOURCE_LEASE_RELEASE"]
    if len(source_admits) != len(source_releases):
        raise ValueError("source lease evidence is unbalanced")
    source_admit_ids = {int(row["lease_id"]) for row in source_admits}
    source_release_ids = {int(row["lease_id"]) for row in source_releases}
    if len(source_admit_ids) != len(source_admits) or source_admit_ids != source_release_ids:
        raise ValueError("source lease identities are unbalanced")
    if any(int(row.get("provider_capacity", -1)) != capacity for row in rows):
        raise ValueError("provider capacity evidence is missing or inconsistent")
    bootstrap_borrows = [row for row in admits if row.get("bootstrap_borrowed") is True]
    if bootstrap_borrows and not bootstrap_future_borrow:
        raise ValueError("provider evidence contains undeclared bootstrap borrowing")
    if any(
        row.get("admission_class") != "FUTURE_PREPARE"
        or row.get("phase_isolated") is not True
        or int(row.get("durable_frontier", -2)) != -1
        or row.get("native_guard_active") is True
        or int(row.get("future_outstanding", 0)) != future_cap + 1
        for row in bootstrap_borrows
    ):
        raise ValueError("bootstrap borrowing violates the pre-publication contract")
    event_positions = {id(row): index for index, row in enumerate(rows)}
    admit_by_permit = {int(row["ticket"]): row for row in admits}
    release_by_permit = {int(row["permit_id"]): row for row in releases}
    source_admit_by_id = {int(row["lease_id"]): row for row in source_admits}
    source_release_by_id = {int(row["lease_id"]): row for row in source_releases}
    source_promotions = [
        row for row in rows if row.get("event") == "SOURCE_LEASE_ACTIVE_RECLASSIFY"
    ]
    promoted_source_leases: set[int] = set()
    for promotion in source_promotions:
        lease_id = int(promotion.get("lease_id", -1))
        if (
            lease_id in promoted_source_leases
            or lease_id not in source_admit_by_id
            or lease_id not in source_release_by_id
        ):
            raise ValueError("source lease promotion has an invalid identity")
        promoted_source_leases.add(lease_id)
        admit = source_admit_by_id[lease_id]
        release = source_release_by_id[lease_id]
        if not (
            admit.get("admission_class") == "FUTURE_PREPARE"
            and promotion.get("from_admission_class") == "FUTURE_PREPARE"
            and promotion.get("admission_class") == "FRONTIER_PREPARE"
            and promotion.get("acquired_admission_class") == "FUTURE_PREPARE"
            and release.get("admission_class") == "FRONTIER_PREPARE"
            and release.get("acquired_admission_class") == "FUTURE_PREPARE"
            and int(promotion.get("source_sequence", -1))
            == int(promotion.get("trigger_frontier_sequence", -2)) + 1
            and int(admit.get("source_sequence", -1))
            == int(promotion.get("source_sequence", -2))
            and event_positions[id(admit)] < event_positions[id(promotion)]
            < event_positions[id(release)]
        ):
            raise ValueError("source lease promotion violates frontier provenance")
    promotions = [
        row for row in rows if row.get("event") == "ADMISSION_ACTIVE_RECLASSIFY"
    ]
    promoted_permits: set[int] = set()
    for promotion in promotions:
        ticket = int(promotion.get("ticket", -1))
        if ticket in promoted_permits or ticket not in admit_by_permit:
            raise ValueError("active promotion has an invalid permit identity")
        promoted_permits.add(ticket)
        admit = admit_by_permit[ticket]
        release = release_by_permit[ticket]
        if not (
            admit.get("admission_class") == "FUTURE_PREPARE"
            and promotion.get("from_admission_class") == "FUTURE_PREPARE"
            and promotion.get("admission_class") == "FRONTIER_PREPARE"
            and promotion.get("acquired_admission_class") == "FUTURE_PREPARE"
            and release.get("admission_class") == "FRONTIER_PREPARE"
            and release.get("acquired_admission_class") == "FUTURE_PREPARE"
            and int(promotion.get("source_sequence", -1))
            == int(promotion.get("trigger_frontier_sequence", -2)) + 1
            and int(admit.get("source_sequence", -1))
            == int(promotion.get("source_sequence", -2))
            and event_positions[id(admit)] < event_positions[id(promotion)]
            < event_positions[id(release)]
        ):
            raise ValueError("active promotion violates frontier permit provenance")
    counters = (
        "outstanding",
        "future_outstanding",
        "physical_future_outstanding",
        "native_outstanding",
        "tokens_outstanding",
        "source_outstanding",
        "future_source_outstanding",
    )
    if any(int(row.get(field, 0)) < 0 for row in rows for field in counters):
        raise ValueError("provider evidence contains a negative resource counter")
    max_outstanding = max((int(row.get("outstanding", 0)) for row in rows), default=0)
    max_future = max((int(row.get("future_outstanding", 0)) for row in rows), default=0)
    max_physical_future = max(
        (int(row.get("physical_future_outstanding", 0)) for row in rows), default=0
    )
    has_source_lease_evidence = any(
        row.get("event", "").startswith("SOURCE_LEASE_")
        or row.get("event") == "SOURCE_LEASE_ACTIVE_RECLASSIFY"
        for row in rows
    )
    max_future_source = max(
        (int(row.get("future_source_outstanding", 0)) for row in rows), default=0
    )
    max_source = max((int(row.get("source_outstanding", 0)) for row in rows), default=0)
    if max_outstanding > capacity * (2 if phase_isolated else 1):
        raise ValueError("provider capacity was exceeded")
    if max_source > capacity:
        raise ValueError("source lease capacity was exceeded")
    max_prepare = max((int(row.get("prepare_outstanding", 0)) for row in rows), default=0)
    if phase_isolated and max_prepare > capacity:
        raise ValueError("prepare endpoint capacity was exceeded")
    max_future_limit = future_cap + (1 if bootstrap_future_borrow else 0)
    if has_source_lease_evidence and max_future_source > max_future_limit:
        raise ValueError("future source lease cap was exceeded")
    if not has_source_lease_evidence and max_future > max_future_limit:
        raise ValueError("future provider cap was exceeded")
    max_native = max((int(row.get("native_outstanding", 0)) for row in rows), default=0)
    if max_native > capacity:
        raise ValueError("native provider capacity was exceeded")
    observed_token_budget = token_budget
    if observed_token_budget is None:
        observed_token_budget = max(
            (int(row.get("token_budget", 0)) for row in rows), default=0
        ) or None
    max_tokens_outstanding = max(
        (int(row.get("tokens_outstanding", 0)) for row in rows), default=0
    )
    max_prepare_tokens = max(
        (int(row.get("prepare_tokens_outstanding", 0)) for row in rows), default=0
    )
    max_native_tokens = max(
        (int(row.get("native_tokens_outstanding", 0)) for row in rows), default=0
    )
    if observed_token_budget is not None:
        if phase_isolated:
            if max_prepare_tokens > observed_token_budget or max_native_tokens > observed_token_budget:
                raise ValueError("phase-isolated weighted provider budget was exceeded")
        elif max_tokens_outstanding > observed_token_budget:
            raise ValueError("weighted provider budget was exceeded")
    guard_ready = [row for row in rows if row.get("event") == "NATIVE_GUARD_READY"]
    guard_exits = [row for row in rows if row.get("event") == "NATIVE_GUARD_EXIT"]
    if len(guard_ready) != len(guard_exits):
        raise ValueError("native guard evidence is unbalanced")
    if not phase_isolated and any(
        row.get("event") == "ADMISSION_ADMIT"
        and row.get("admission_class") == "FUTURE_PREPARE"
        and row.get("native_guard_active") is True
        for row in rows
    ):
        raise ValueError("future provider work was admitted during native guard")
    terminal = rows[-1]
    if any(int(terminal.get(field, 0)) != 0 for field in counters):
        raise ValueError("provider evidence has live resources at seal time")
    return {
        "schema_version": "membind.v6.1.provider-proof.v5",
        "status": "PASS",
        "arbiter_instance_id": arbiter_instance_id,
        "event_count": len(rows),
        "admission_count": len(admits),
        "source_lease_count": len(source_admits),
        "native_guard_count": len(guard_ready),
        "capacity": capacity,
        "phase_isolated": bool(phase_isolated),
        "future_cap": future_cap,
        "bootstrap_future_borrow": bool(bootstrap_future_borrow),
        "bootstrap_borrow_count": len(bootstrap_borrows),
        "max_future_limit": max_future_limit,
        "max_outstanding": max_outstanding,
        "max_prepare_outstanding": max_prepare,
        "max_future_outstanding": max_future,
        "max_physical_future_outstanding": max_physical_future,
        "max_future_source_outstanding": max_future_source,
        "max_source_outstanding": max_source,
        "active_promotion_count": len(promotions),
        "active_promoted_permits": sorted(promoted_permits),
        "active_source_promotion_count": len(source_promotions),
        "active_promoted_source_leases": sorted(promoted_source_leases),
        "max_native_outstanding": max_native,
        "max_active_future_at_guard_ready": max(
            (int(row.get("active_future_calls", 0)) for row in guard_ready), default=0
        ),
        "drained_future_call_count": sum(
            int(row.get("drained_future_calls", 0)) for row in guard_ready
        ),
        "max_admission_queue_wait_ns": max(
            (int(row.get("queue_wait_ns", 0)) for row in admits), default=0
        ),
        "token_budget": observed_token_budget,
        "max_tokens_outstanding": max_tokens_outstanding,
        "max_prepare_tokens_outstanding": max_prepare_tokens,
        "max_native_tokens_outstanding": max_native_tokens,
    }


__all__ = ["provider_proof", "response_sha256", "span_work_inventory"]
