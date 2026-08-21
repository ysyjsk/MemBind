"""Pre-registered separation of harness, ordering, and semantic outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping, Sequence
import re
from typing import Any


class DirectEvidenceError(ValueError):
    """A span claimed direct causal evidence without satisfying its contract."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIRECT_OBSERVATIONS = (
    "future_persistent_state_read",
    "stale_predecessor_write",
    "wrong_state_write",
)


class CorrectnessClass(str, Enum):
    HARNESS_VIOLATION = "harness_violation"
    ORDERING_OBSERVATION = "ordering_observation"
    DIRECT_SEMANTIC_VIOLATION = "direct_semantic_violation"


@dataclass(frozen=True, slots=True)
class CorrectnessOutcome:
    observation: str
    classification: CorrectnessClass
    protocol_valid: bool
    direct_causal_evidence: bool


def classify_observation(
    observation: str, *, direct_causal_evidence: bool = False
) -> CorrectnessOutcome:
    if observation == "future_source_payload_read":
        classification = CorrectnessClass.HARNESS_VIOLATION
        valid = False
    elif observation in {
        "future_persistent_state_read",
        "stale_predecessor_write",
        "wrong_state_write",
    } and direct_causal_evidence:
        classification = CorrectnessClass.DIRECT_SEMANTIC_VIOLATION
        valid = True
    else:
        classification = CorrectnessClass.ORDERING_OBSERVATION
        valid = True
    return CorrectnessOutcome(
        observation=observation,
        classification=classification,
        protocol_valid=valid,
        direct_causal_evidence=direct_causal_evidence,
    )


def reduce_direct_semantic_evidence(
    envelopes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Count only explicitly instrumented, replayable causal evidence records."""

    if isinstance(envelopes, (str, bytes)) or not isinstance(envelopes, Sequence):
        raise DirectEvidenceError("DIRECT_EVIDENCE_INPUT_INVALID")
    by_id: dict[str, dict[str, Any]] = {}
    for envelope in envelopes:
        if not isinstance(envelope, Mapping):
            raise DirectEvidenceError("DIRECT_EVIDENCE_INPUT_INVALID")
        spans = envelope.get("spans", [])
        if isinstance(spans, (str, bytes)) or not isinstance(spans, Sequence):
            raise DirectEvidenceError("DIRECT_EVIDENCE_INPUT_INVALID")
        for span in spans:
            if not isinstance(span, Mapping):
                raise DirectEvidenceError("DIRECT_EVIDENCE_INPUT_INVALID")
            metadata = span.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            claimed = any(
                field in metadata
                for field in (
                    "direct_semantic_observation",
                    "direct_causal_evidence",
                    "direct_evidence_id",
                    "persistent_object_ids",
                )
            )
            if not claimed:
                continue
            observation = metadata.get("direct_semantic_observation")
            evidence_id = metadata.get("direct_evidence_id")
            observed_sequence = metadata.get("observed_source_sequence")
            causal_sequence = metadata.get("causal_source_sequence")
            object_ids = metadata.get("persistent_object_ids")
            if (
                observation not in _DIRECT_OBSERVATIONS
                or metadata.get("direct_causal_evidence") is not True
                or not isinstance(evidence_id, str)
                or _SHA256.fullmatch(evidence_id) is None
                or isinstance(observed_sequence, bool)
                or not isinstance(observed_sequence, int)
                or observed_sequence < 0
                or isinstance(causal_sequence, bool)
                or not isinstance(causal_sequence, int)
                or causal_sequence < 0
                or observed_sequence == causal_sequence
                or isinstance(object_ids, (str, bytes))
                or not isinstance(object_ids, Sequence)
                or not object_ids
                or any(not isinstance(value, str) or not value for value in object_ids)
            ):
                raise DirectEvidenceError("DIRECT_EVIDENCE_INVALID")
            record = {
                "evidence_id": evidence_id,
                "observation": observation,
                "observed_source_sequence": observed_sequence,
                "causal_source_sequence": causal_sequence,
                "persistent_object_ids": sorted(set(object_ids)),
                "phase": span.get("phase"),
                "operation_class": span.get("operation_class"),
            }
            previous = by_id.get(evidence_id)
            if previous is not None and previous != record:
                raise DirectEvidenceError("DIRECT_EVIDENCE_INVALID")
            by_id[evidence_id] = record
    records = [by_id[key] for key in sorted(by_id)]
    counts = {
        observation: sum(row["observation"] == observation for row in records)
        for observation in _DIRECT_OBSERVATIONS
    }
    return {
        "schema_version": "membind.saturated-fixed-work.direct-evidence.v1",
        "availability": "DERIVED",
        "direct_semantic_violations": len(records),
        "by_observation": counts,
        "direct_evidence_records": records,
        "ordering_observations_counted_as_direct": 0,
    }


__all__ = [
    "CorrectnessClass",
    "CorrectnessOutcome",
    "DirectEvidenceError",
    "classify_observation",
    "reduce_direct_semantic_evidence",
]
