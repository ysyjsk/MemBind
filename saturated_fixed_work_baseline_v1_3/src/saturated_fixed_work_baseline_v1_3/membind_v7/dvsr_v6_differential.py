"""Machine-readable Frozen-V6/DVSR Prepared semantic differential.

The reducer is deliberately provider-free.  Dynamic runners produce one
digest-only evidence record for each path; this module applies the frozen
field classification and never accepts caller-declared semantic exceptions.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping


EVIDENCE_SCHEMA = "membind.dvsr.v6-prepared-path-evidence.v1"
DIFFERENTIAL_SCHEMA = "membind.dvsr.v6-prepared-differential.v1"

_PATHS = frozenset({"FROZEN_V6", "DVSR_PREPARED_NOREUSE"})
_RUNTIME_METADATA_FIELDS = frozenset(
    {
        "observer_enabled",
        "runtime_instance_id",
        "observer_version",
        "capture_id",
    }
)
_SEMANTIC_FIELDS = (
    "source_workload.history_id_digest",
    "source_workload.source_sequence",
    "source_workload.source_digest",
    "source_workload.workload_config_digest",
    "previous_context.policy",
    "previous_context.projection_digest",
    "previous_context.selection_events_digest",
    "extraction.canonical_request_sequence_digest",
    "extraction.transcript_identity_digest",
    "extraction.semantic_output_digest",
    "extraction.logical_call_sequence_digest",
    "extraction.physical_call_count",
    "routing.route_contract_digest",
    "routing.region_sequence_digest",
    "execution_binding.uuid_time_randomness_digest",
    "stateful.canonical_request_sequence_digest",
    "stateful.logical_call_sequence_digest",
    "stateful.db_read_inventory_digest",
    "continuation_k_digest",
    "canonical_graph_projection_digest",
    "publication_order_digest",
    "no_prepublication_write",
)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _lookup(record: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _validate_section(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return deepcopy(dict(value))


def build_prepared_path_evidence(
    *,
    path: str,
    source_workload: Mapping[str, Any],
    previous_context: Mapping[str, Any],
    extraction: Mapping[str, Any],
    routing: Mapping[str, Any],
    execution_binding: Mapping[str, Any],
    stateful: Mapping[str, Any],
    continuation_k_digest: str,
    canonical_graph_projection_digest: str,
    publication_order_digest: str,
    no_prepublication_write: bool,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a digest-only path record accepted by the differential reducer."""

    if path not in _PATHS:
        raise ValueError(f"unsupported differential path: {path}")
    runtime = dict(runtime_metadata or {})
    unknown_runtime = sorted(set(runtime) - _RUNTIME_METADATA_FIELDS)
    if unknown_runtime:
        raise ValueError(f"unsupported runtime metadata field(s): {','.join(unknown_runtime)}")
    record = {
        "schema_version": EVIDENCE_SCHEMA,
        "path": path,
        "source_workload": _validate_section("source_workload", source_workload),
        "previous_context": _validate_section("previous_context", previous_context),
        "extraction": _validate_section("extraction", extraction),
        "routing": _validate_section("routing", routing),
        "execution_binding": _validate_section("execution_binding", execution_binding),
        "stateful": _validate_section("stateful", stateful),
        "continuation_k_digest": str(continuation_k_digest),
        "canonical_graph_projection_digest": str(canonical_graph_projection_digest),
        "publication_order_digest": str(publication_order_digest),
        "no_prepublication_write": bool(no_prepublication_write),
        "runtime_metadata": deepcopy(runtime),
    }
    missing = [field for field in _SEMANTIC_FIELDS if not _lookup(record, field)[0]]
    if missing:
        raise ValueError(f"prepared path evidence is incomplete: {','.join(missing)}")
    record["evidence_digest"] = _canonical_digest(record)
    return record


def compare_prepared_paths(
    frozen_v6: Mapping[str, Any],
    dvsr_prepared_noreuse: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare two path records using the frozen semantic/non-semantic split."""

    missing: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for field in _SEMANTIC_FIELDS:
        left_found, left = _lookup(frozen_v6, field)
        right_found, right = _lookup(dvsr_prepared_noreuse, field)
        if not left_found or not right_found:
            missing.append(field)
            mismatches.append(
                {
                    "field": field,
                    "reason": "missing_required_evidence",
                    "frozen_v6_present": left_found,
                    "dvsr_present": right_found,
                }
            )
        elif left != right:
            mismatches.append(
                {
                    "field": field,
                    "reason": "semantic_value_changed",
                    "frozen_v6_digest": _canonical_digest(left),
                    "dvsr_digest": _canonical_digest(right),
                }
            )

    explained: list[dict[str, Any]] = []
    left_runtime = frozen_v6.get("runtime_metadata", {})
    right_runtime = dvsr_prepared_noreuse.get("runtime_metadata", {})
    if not isinstance(left_runtime, Mapping) or not isinstance(right_runtime, Mapping):
        mismatches.append(
            {
                "field": "runtime_metadata",
                "reason": "invalid_runtime_metadata",
            }
        )
    else:
        unknown = sorted((set(left_runtime) | set(right_runtime)) - _RUNTIME_METADATA_FIELDS)
        for field in unknown:
            mismatches.append(
                {
                    "field": f"runtime_metadata.{field}",
                    "reason": "unclassified_field_cannot_be_explained",
                }
            )
        for field in sorted(_RUNTIME_METADATA_FIELDS & (set(left_runtime) | set(right_runtime))):
            if left_runtime.get(field) != right_runtime.get(field):
                explained.append(
                    {
                        "field": f"runtime_metadata.{field}",
                        "reason": "frozen_observer_runtime_allowlist",
                        "frozen_v6_digest": _canonical_digest(left_runtime.get(field)),
                        "dvsr_digest": _canonical_digest(right_runtime.get(field)),
                    }
                )

    left_path = frozen_v6.get("path")
    right_path = dvsr_prepared_noreuse.get("path")
    if left_path != "FROZEN_V6" or right_path != "DVSR_PREPARED_NOREUSE":
        mismatches.append(
            {
                "field": "path",
                "reason": "differential_path_identity_invalid",
                "frozen_v6_path": left_path,
                "dvsr_path": right_path,
            }
        )

    if mismatches:
        status = "SEMANTIC_MISMATCH"
    elif explained:
        status = "EXPLAINED_NON_SEMANTIC_DIFFERENCE"
    else:
        status = "EXACT"
    result = {
        "schema_version": DIFFERENTIAL_SCHEMA,
        "status": status,
        "g1_eligible": status in {"EXACT", "EXPLAINED_NON_SEMANTIC_DIFFERENCE"},
        "missing_required_fields": sorted(set(missing)),
        "semantic_mismatches": mismatches,
        "explained_non_semantic_differences": explained,
        "field_contract": {
            "semantic_fields": list(_SEMANTIC_FIELDS),
            "runtime_metadata_allowlist": sorted(_RUNTIME_METADATA_FIELDS),
        },
        "input_evidence_digests": {
            "frozen_v6": frozen_v6.get("evidence_digest") or _canonical_digest(frozen_v6),
            "dvsr_prepared_noreuse": dvsr_prepared_noreuse.get("evidence_digest") or _canonical_digest(dvsr_prepared_noreuse),
        },
    }
    result["differential_digest"] = _canonical_digest(result)
    return result


__all__ = [
    "DIFFERENTIAL_SCHEMA",
    "EVIDENCE_SCHEMA",
    "build_prepared_path_evidence",
    "compare_prepared_paths",
]
