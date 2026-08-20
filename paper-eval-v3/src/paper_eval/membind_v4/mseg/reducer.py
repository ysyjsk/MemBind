"""Observability-aware reduction of sealed request traces for the MSEG gate."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256, sha256_file


class MSEGReducerError(ValueError):
    """The sealed trace is malformed or fails integrity validation."""


def _fail(code: str) -> MSEGReducerError:
    return MSEGReducerError(code)


_IDENTITY_FIELDS = (
    "history_id",
    "operator_id",
    "operator_role",
    "parent_operator_id",
    "parent_bind_id",
)
_OPERATOR_TIMING_FIELDS = (
    "episode_arrival_ns",
    "operator_ready_ns",
    "operator_start_ns",
    "operator_end_ns",
    "request_materialized_ns",
    "admission_enqueue_ns",
)
_VERSION_FIELDS = (
    "memory_version_required",
    "memory_version_observed",
    "publication_frontier_at_ready",
    "publication_frontier_at_materialization",
)
_DEPENDENCY_FIELDS = (
    "dependency_knowledge_state",
    "data_dependency_ids",
    "version_dependency_ids",
    "effect_conflict_dependency_ids",
    "publication_dependency_ids",
    "read_scope",
    "effect_scope",
)
_OTHER_CAUSAL_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "execution_mode",
)
_AUDITED_FIELDS = (
    *_IDENTITY_FIELDS,
    *_OPERATOR_TIMING_FIELDS,
    *_VERSION_FIELDS,
    *_DEPENDENCY_FIELDS,
    *_OTHER_CAUSAL_FIELDS,
    "prompt_name",
)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("llm_trace_unreadable") from None
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            wrapper = json.loads(line)
            record = wrapper["record"]
            row = record["row"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise _fail("llm_trace_record_invalid") from None
        if not isinstance(record, dict) or not isinstance(row, dict):
            raise _fail("llm_trace_record_invalid")
        if wrapper.get("record_sha256") != payload_sha256(record):
            raise _fail("llm_trace_record_hash_mismatch")
        rows.append(dict(row))
    if not rows:
        raise _fail("llm_trace_empty")
    return rows


def _coverage(
    submissions: list[dict[str, Any]],
    field: str,
) -> dict[str, object]:
    observed = sum(field in row for row in submissions)
    total = len(submissions)
    if observed == 0:
        status = "NOT_OBSERVABLE"
    elif observed == total:
        status = "OBSERVED"
    else:
        status = "PARTIAL"
    return {
        "status": status,
        "observed_count": observed,
        "request_count": total,
        "coverage_fraction": observed / total if total else None,
    }


def audit_llm_trace_observability(
    path: Path,
    *,
    history_id: str,
) -> dict[str, object]:
    """Audit direct causal metadata without guessing roles from request shape."""

    if not isinstance(history_id, str) or not history_id:
        raise _fail("history_id_invalid")
    source_path = Path(path)
    rows = _read_rows(source_path)
    submissions = [row for row in rows if row.get("event_type") == "llm_request_submitted"]
    if not submissions:
        raise _fail("llm_submission_empty")
    request_ids: set[str] = set()
    for row in submissions:
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not request_id or request_id in request_ids:
            raise _fail("llm_request_identity_invalid")
        request_ids.add(request_id)

    starts = Counter(
        row.get("request_id")
        for row in rows
        if row.get("event_type") == "llm_request_start"
    )
    terminals = Counter(
        row.get("request_id")
        for row in rows
        if row.get("event_type") == "llm_request_terminal"
    )
    complete_lifecycle = sum(
        starts[request_id] == 1 and terminals[request_id] == 1
        for request_id in request_ids
    )
    field_coverage = {field: _coverage(submissions, field) for field in _AUDITED_FIELDS}
    identity_recovered = all(
        field_coverage[field]["status"] == "OBSERVED" for field in _IDENTITY_FIELDS
    )
    timing_recovered = all(
        field_coverage[field]["status"] == "OBSERVED"
        for field in _OPERATOR_TIMING_FIELDS
    )
    version_recovered = all(
        field_coverage[field]["status"] == "OBSERVED" for field in _VERSION_FIELDS
    )
    dependency_recovered = all(
        field_coverage[field]["status"] == "OBSERVED" for field in _DEPENDENCY_FIELDS
    )

    blocking_reasons: list[str] = []
    if not identity_recovered:
        blocking_reasons.append("operator_identity_missing")
    if not timing_recovered:
        blocking_reasons.append("operator_ready_materialization_timing_missing")
    if not version_recovered:
        blocking_reasons.append("memory_version_evidence_missing")
    if not dependency_recovered:
        blocking_reasons.append("dependency_and_effect_scope_missing")
    blocking_reasons.append("deterministic_operator_trace_missing")
    blocking_reasons.append("persistent_effect_and_publication_trace_missing")

    request_kind_counts = Counter(str(row.get("request_kind")) for row in submissions)
    event_fields: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        event_type = str(row.get("event_type"))
        event_fields[event_type].update(row)
    role_counts: object
    if identity_recovered:
        role_counts = dict(Counter(str(row["operator_role"]) for row in submissions))
    else:
        role_counts = "NOT_OBSERVABLE"

    return {
        "schema_version": "membind.paper-eval-v4.mseg-trace-observability.v1",
        "history_id": history_id,
        "source_trace": str(source_path),
        "source_trace_sha256": sha256_file(source_path),
        "request_count": len(submissions),
        "request_kind_counts": dict(sorted(request_kind_counts.items())),
        "complete_client_lifecycle_count": complete_lifecycle,
        "event_fields": {
            event_type: sorted(fields) for event_type, fields in sorted(event_fields.items())
        },
        "field_coverage": field_coverage,
        "fine_grained_identity_recovered": identity_recovered,
        "operator_timing_recovered": timing_recovered,
        "memory_version_recovered": version_recovered,
        "dependency_effect_scope_recovered": dependency_recovered,
        "deterministic_operator_instances_observable": False,
        "persistent_effect_instances_observable": False,
        "mseg_recovered": False,
        "blocking_reasons": blocking_reasons,
        "role_attribution_method": "DIRECT_METADATA" if identity_recovered else "NONE",
        "role_count": role_counts,
        "prohibited_inferences": [
            "prompt_or_token_length",
            "request_order",
            "prompt_or_prefix_similarity",
        ],
        "interpretation_boundary": {
            "client_running_is_vllm_batch_membership": False,
            "client_running_is_gpu_execution": False,
            "llm_request_is_complete_operator_graph": False,
            "manifest_history_binding_is_direct_per_request_metadata": False,
        },
    }
