"""Schema-frozen, observer-only DVSR evidence records.

The observer does not call providers, publish speculative state, or decide an
operator.  It validates that a paired observation contains enough identity and
work accounting to make that decision later, while returning UNKNOWN for any
incomplete or mixed-snapshot record.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


DVSR_OBSERVER_SCHEMA = "membind.dvsr.cross-snapshot-observer.v1"
REQUIRED_DVSR_FIELDS = frozenset(
    {
        "prepared_artifact_identity",
        "read_epoch",
        "read_set",
        "operator_id",
        "operator_cut",
        "ordered_candidate_ids",
        "candidate_scores",
        "prompt_visible_projection_digest",
        "canonical_request",
        "canonical_request_digest",
        "result_digest",
        "continuation_digest",
        "actual_touched_write_delta",
        "no_write_proof",
        "semantic_critical_path",
        "validation_timing_ns",
        "repair_timing_ns",
        "status",
        "unknown_reasons",
    }
)


class DvsrObservationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DvsrObservation:
    payload_digest: str
    record: Mapping[str, Any]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _require_mapping(record: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = record.get(field)
    if not isinstance(value, Mapping):
        raise DvsrObservationError(f"{field} must be an object")
    return value


def validate_dvsr_observation(record: Mapping[str, Any]) -> None:
    missing = sorted(REQUIRED_DVSR_FIELDS - set(record))
    if missing:
        raise DvsrObservationError(f"required DVSR field missing: {missing[0]}")
    if record.get("status") not in {"VALID", "UNKNOWN", "INVALID"}:
        raise DvsrObservationError("invalid DVSR observation status")
    if not isinstance(record.get("read_epoch"), str) or not record["read_epoch"]:
        raise DvsrObservationError("read_epoch must be non-empty")
    if not isinstance(record.get("operator_id"), str) or not record["operator_id"]:
        raise DvsrObservationError("operator_id must be non-empty")
    if record.get("operator_cut") not in {"CUT-N", "CUT-D", "CUT-E"}:
        raise DvsrObservationError("unknown operator cut")
    for field in ("prepared_artifact_identity", "read_set", "canonical_request", "actual_touched_write_delta", "no_write_proof", "semantic_critical_path"):
        _require_mapping(record, field)
    prepared = _require_mapping(record, "prepared_artifact_identity")
    for field in ("artifact_digest", "source_sequence", "v6_identity", "clone_proof"):
        if field not in prepared:
            raise DvsrObservationError(f"prepared artifact identity is incomplete: {field}")
    read_set = _require_mapping(record, "read_set")
    if read_set.get("completeness") not in {"COMPLETE", "INCOMPLETE", "UNKNOWN"}:
        raise DvsrObservationError("invalid read_set completeness")
    if record.get("status") == "VALID" and read_set.get("completeness") != "COMPLETE":
        raise DvsrObservationError("VALID observation requires complete read_set")
    no_write = _require_mapping(record, "no_write_proof")
    if no_write.get("speculative_db_writes") != 0 or no_write.get("speculative_publications") != 0:
        raise DvsrObservationError("speculative write/publication proof failed")
    request = _require_mapping(record, "canonical_request")
    for field in ("model", "schema", "flags", "order", "messages"):
        if field not in request:
            raise DvsrObservationError(f"canonical request is incomplete: {field}")
    if record.get("canonical_request_digest") != _digest(request):
        raise DvsrObservationError("canonical request digest mismatch")
    for field in ("ordered_candidate_ids", "candidate_scores", "prompt_visible_projection_digest", "result_digest", "continuation_digest", "unknown_reasons"):
        if record.get(field) is None:
            raise DvsrObservationError(f"{field} is null")
    for field in ("validation_timing_ns", "repair_timing_ns"):
        value = record.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DvsrObservationError(f"{field} must be a non-negative integer")
    cp = _require_mapping(record, "semantic_critical_path")
    for field in ("hidden_cp_ns", "visible_repair_cp_ns", "failed_speculation_work_ns"):
        value = cp.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise DvsrObservationError(f"semantic critical path field is invalid: {field}")


def c1_valid_subset_of_c0(c0_result_digests: set[str] | frozenset[str], c1_valid_digests: set[str] | frozenset[str]) -> bool:
    """C1 may only certify a result already accepted by the C0 fresh oracle."""

    return set(c1_valid_digests) <= set(c0_result_digests)


class DvsrObserver:
    """Append-only in-memory capture used by provider-free and read-only runs."""

    def __init__(self) -> None:
        self._records: list[DvsrObservation] = []

    @property
    def records(self) -> tuple[DvsrObservation, ...]:
        return tuple(self._records)

    def append(self, record: Mapping[str, Any]) -> DvsrObservation:
        validate_dvsr_observation(record)
        frozen = copy.deepcopy(dict(record))
        observation = DvsrObservation(payload_digest=_digest(frozen), record=frozen)
        self._records.append(observation)
        return observation


__all__ = [
    "DVSR_OBSERVER_SCHEMA",
    "DvsrObservation",
    "DvsrObservationError",
    "DvsrObserver",
    "REQUIRED_DVSR_FIELDS",
    "c1_valid_subset_of_c0",
    "validate_dvsr_observation",
]
