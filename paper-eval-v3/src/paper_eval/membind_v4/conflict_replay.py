"""Deterministic, read-only replay for conflict-aware v4 speculation.

The replay has no adapter, model, database, or transport dependency.  It
derives timing from a sealed event ledger, verifies PreparedArtifacts before
classification, and accepts later-stage facts only through an explicitly
sealed optional fact document.  Evidence that is not reached is never
reported as observed.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.membind_v31.prepared_artifact import (
    PreparedArtifact,
    PreparedArtifactError,
)


_AUDIT_SCHEMA = "membind.paper-eval-v4.a1-opportunity-audit.v1"
_REPLAY_SCHEMA = "membind.paper-eval-v4.conflict-offline-replay.v1"
_FACT_SCHEMA = "membind.paper-eval-v4.conflict-offline-facts.v1"
_EVENT_SCHEMA = "membind.paper-eval-v3.membind-v31-block-event.v1"
_HISTORY_ID = "07741c45"
_CONFLICT_CLASSES = ("LOW_CONFLICT", "HIGH_CONFLICT", "UNKNOWN")
_EXECUTION_MODES = ("LLM", "NO_LLM")
_EXACT_OUTCOMES = ("HIT", "MISS")
_REQUIRED_EVENTS = (
    "ARRIVAL",
    "COMPILE_STARTED",
    "PREPARED_DURABLE",
    "BIND_STARTED",
    "COMMIT_RETURNED",
    "PUBLICATION_DURABLE",
)
_EVENT_KEYS = {
    "event_sequence",
    "event_type",
    "schema_version",
    "source_sequence",
    "source_sha256",
    "telemetry",
    "timestamp_ns",
}
_PREPARED_KEYS = {
    "artifact_sha256",
    "certification_sha256",
    "evidence_sha256",
    "pure_intermediates",
    "raw_edges",
    "raw_nodes",
    "source_sequence",
    "source_sha256",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ConflictReplayError(ValueError):
    """A replay input was malformed, unsealed, or identity-inconsistent."""


def _fail(code: str) -> ConflictReplayError:
    return ConflictReplayError(code)


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class ReplayFactProvenance:
    """Seals supporting each optional per-candidate replay fact."""

    conflict_sha256: str | None = None
    semantic_sha256: str | None = None
    resource_sha256: str | None = None
    value_sha256: str | None = None
    exact_outcome_sha256: str | None = None

    def verify(self) -> "ReplayFactProvenance":
        values = (
            self.conflict_sha256,
            self.semantic_sha256,
            self.resource_sha256,
            self.value_sha256,
            self.exact_outcome_sha256,
        )
        if any(value is not None and not _valid_sha256(value) for value in values):
            raise _fail("observation_provenance_invalid")
        return self

    def to_document(self) -> dict[str, str | None]:
        return {
            "conflict_sha256": self.conflict_sha256,
            "semantic_sha256": self.semantic_sha256,
            "resource_sha256": self.resource_sha256,
            "value_sha256": self.value_sha256,
            "exact_outcome_sha256": self.exact_outcome_sha256,
        }


@dataclass(frozen=True, slots=True)
class ReplayObservation:
    """Content-free decision facts for one distance-one future candidate."""

    source_sequence: int
    potential_opportunity: bool
    conflict_class: str | None
    execution_mode: str | None
    resource_admissible: bool | None
    value_admissible: bool | None
    exact_outcome: str | None
    provenance: ReplayFactProvenance | None

    def verify(self) -> "ReplayObservation":
        if (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, int)
            or self.source_sequence < 1
        ):
            raise _fail("observation_source_sequence_invalid")
        if not isinstance(self.potential_opportunity, bool):
            raise _fail("observation_opportunity_invalid")
        downstream = (
            self.conflict_class,
            self.execution_mode,
            self.resource_admissible,
            self.value_admissible,
            self.exact_outcome,
            self.provenance,
        )
        if not self.potential_opportunity:
            if self.conflict_class is not None:
                raise _fail("observation_conflict_without_opportunity")
            if any(value is not None for value in downstream):
                raise _fail("observation_fact_without_opportunity")
            return self
        if self.conflict_class not in _CONFLICT_CLASSES:
            raise _fail("observation_conflict_class_invalid")
        if self.provenance is None:
            raise _fail("observation_provenance_missing")
        provenance = self.provenance.verify()
        if provenance.conflict_sha256 is None:
            raise _fail("observation_provenance_missing")
        if (
            self.execution_mode is not None
            and self.execution_mode not in _EXECUTION_MODES
        ):
            raise _fail("observation_execution_mode_invalid")
        if (self.execution_mode is None) != (provenance.semantic_sha256 is None):
            raise _fail("observation_provenance_missing")
        if self.resource_admissible is not None:
            if not isinstance(self.resource_admissible, bool):
                raise _fail("observation_resource_admissible_invalid")
            if self.conflict_class != "LOW_CONFLICT" or self.execution_mode != "LLM":
                raise _fail("observation_resource_without_low_llm")
        if (self.resource_admissible is None) != (
            provenance.resource_sha256 is None
        ):
            raise _fail("observation_provenance_missing")
        if self.value_admissible is not None:
            if not isinstance(self.value_admissible, bool):
                raise _fail("observation_value_admissible_invalid")
            if self.resource_admissible is not True:
                raise _fail("observation_value_without_resource")
        if (self.value_admissible is None) != (provenance.value_sha256 is None):
            raise _fail("observation_provenance_missing")
        if self.exact_outcome is not None and self.exact_outcome not in _EXACT_OUTCOMES:
            raise _fail("observation_exact_outcome_invalid")
        if (self.exact_outcome is None) != (
            provenance.exact_outcome_sha256 is None
        ):
            raise _fail("observation_provenance_missing")
        return self


@dataclass(frozen=True, slots=True)
class OfflineGateEvidence:
    """Sealed architectural evidence needed in addition to replay counts."""

    exact_validation_enforced: bool | None
    exact_validation_provenance_sha256: str | None
    resource_policy_nonstarving: bool | None
    resource_policy_provenance_sha256: str | None

    def verify(self) -> "OfflineGateEvidence":
        pairs = (
            (
                self.exact_validation_enforced,
                self.exact_validation_provenance_sha256,
            ),
            (
                self.resource_policy_nonstarving,
                self.resource_policy_provenance_sha256,
            ),
        )
        for fact, digest in pairs:
            if fact is not None and not isinstance(fact, bool):
                raise _fail("gate_evidence_fact_invalid")
            if (fact is None) != (digest is None):
                raise _fail("gate_evidence_provenance_missing")
            if digest is not None and not _valid_sha256(digest):
                raise _fail("gate_evidence_provenance_invalid")
        return self


def derive_prepared_opportunity(
    prepared_timestamp_ns: int,
    predecessor_publication_timestamp_ns: int | None,
) -> tuple[int | None, bool]:
    """Return ``publication(i-1) - prepared(i)`` and its strict sign test."""

    if (
        isinstance(prepared_timestamp_ns, bool)
        or not isinstance(prepared_timestamp_ns, int)
        or prepared_timestamp_ns < 0
    ):
        raise _fail("prepared_timestamp_invalid")
    if predecessor_publication_timestamp_ns is None:
        return None, False
    if (
        isinstance(predecessor_publication_timestamp_ns, bool)
        or not isinstance(predecessor_publication_timestamp_ns, int)
        or predecessor_publication_timestamp_ns < 0
    ):
        raise _fail("predecessor_publication_timestamp_invalid")
    lead = predecessor_publication_timestamp_ns - prepared_timestamp_ns
    return lead, lead > 0


def _exact_summary(
    observations: Sequence[ReplayObservation], conflict_class: str
) -> dict[str, int | float | None]:
    outcomes = [
        item.exact_outcome
        for item in observations
        if item.potential_opportunity
        and item.conflict_class == conflict_class
        and item.exact_outcome is not None
    ]
    hits = outcomes.count("HIT")
    misses = outcomes.count("MISS")
    return {
        "observed_count": len(outcomes),
        "hit_count": hits,
        "miss_count": misses,
        "hit_rate": hits / len(outcomes) if outcomes else None,
    }


def reduce_replay_observations(
    observations: Sequence[ReplayObservation],
) -> dict[str, object]:
    """Reduce verified observations into the P7 conflict/resource/value funnel."""

    verified = tuple(item.verify() for item in observations)
    sequences = [item.source_sequence for item in verified]
    if len(sequences) != len(set(sequences)):
        raise _fail("observation_source_duplicate")
    opportunities = [item for item in verified if item.potential_opportunity]
    low = [item for item in opportunities if item.conflict_class == "LOW_CONFLICT"]
    high = [item for item in opportunities if item.conflict_class == "HIGH_CONFLICT"]
    llm_required = [item for item in opportunities if item.execution_mode == "LLM"]
    low_llm = [item for item in low if item.execution_mode == "LLM"]
    resource_admissible = [
        item for item in low_llm if item.resource_admissible is True
    ]
    value_admissible = [
        item for item in resource_admissible if item.value_admissible is True
    ]
    counts = {
        "total_future_prepared_opportunities": len(opportunities),
        "low_conflict_count": len(low),
        "high_conflict_count": len(high),
        "unknown_conflict_count": sum(
            item.conflict_class == "UNKNOWN" for item in opportunities
        ),
        "llm_required_count": len(llm_required),
        "resource_admissible_count": len(resource_admissible),
        "would_launch_count": len(value_admissible),
    }
    classified = (*low, *high)
    provenance = {
        "conflict_complete": (
            all(
                item.provenance is not None
                and item.provenance.conflict_sha256 is not None
                for item in opportunities
            )
            if opportunities
            else None
        ),
        "semantic_complete": (
            all(
                item.execution_mode is not None
                and item.provenance is not None
                and item.provenance.semantic_sha256 is not None
                for item in opportunities
            )
            if opportunities
            else None
        ),
        "resource_complete": (
            all(
                item.resource_admissible is not None
                and item.provenance is not None
                and item.provenance.resource_sha256 is not None
                for item in low_llm
            )
            if low_llm
            else None
        ),
        "value_complete": (
            all(
                item.value_admissible is not None
                and item.provenance is not None
                and item.provenance.value_sha256 is not None
                for item in resource_admissible
            )
            if resource_admissible
            else None
        ),
        "selectivity_complete": (
            all(
                item.exact_outcome is not None
                and item.provenance is not None
                and item.provenance.exact_outcome_sha256 is not None
                for item in classified
            )
            if classified
            else None
        ),
    }
    return {
        "counts": counts,
        "opportunity_funnel": {
            "before_conflict_filter": len(opportunities),
            "after_conflict_filter": len(low),
            "after_llm_required_filter": len(low_llm),
            "after_resource_admission": len(resource_admissible),
            "after_value_admission": len(value_admissible),
        },
        "exact_outcomes": {
            conflict_class: _exact_summary(verified, conflict_class)
            for conflict_class in _CONFLICT_CLASSES
        },
        "provenance_completeness": provenance,
    }


def evaluate_offline_gate(
    reduced: Mapping[str, object],
    evidence: OfflineGateEvidence,
) -> dict[str, object]:
    """Apply the complete fail-closed P8 gate to an offline reduction."""

    evidence.verify()
    counts = reduced.get("counts")
    outcomes = reduced.get("exact_outcomes")
    provenance = reduced.get("provenance_completeness")
    if not all(isinstance(item, Mapping) for item in (counts, outcomes, provenance)):
        raise _fail("replay_reduction_invalid")
    assert isinstance(counts, Mapping)
    assert isinstance(outcomes, Mapping)
    assert isinstance(provenance, Mapping)
    if counts.get("low_conflict_count") == 0:
        return {
            "decision": "STOP_CONFLICT_AWARE_NODE_RESOLVE",
            "final_outcome": "STOP_V4_NODE_RESOLVE",
            "live_authorized": False,
            "reason": "low_conflict_opportunities_zero",
        }
    if evidence.exact_validation_enforced is not True:
        return {
            "decision": "STOP_CONFLICT_AWARE_NODE_RESOLVE",
            "final_outcome": "STOP_V4_NODE_RESOLVE",
            "live_authorized": False,
            "reason": "exact_validation_safety_evidence_missing_or_failed",
        }
    if not provenance.get("conflict_complete") or not provenance.get(
        "semantic_complete"
    ):
        return {
            "decision": "STOP_CONFLICT_PREDICTOR",
            "final_outcome": "STOP_V4_NODE_RESOLVE",
            "live_authorized": False,
            "reason": "conflict_or_semantic_provenance_incomplete",
        }
    low = outcomes.get("LOW_CONFLICT")
    high = outcomes.get("HIGH_CONFLICT")
    if (
        not provenance.get("selectivity_complete")
        or not isinstance(low, Mapping)
        or not isinstance(high, Mapping)
        or low.get("hit_rate") is None
        or high.get("hit_rate") is None
        or low.get("hit_rate") <= high.get("hit_rate")
    ):
        return {
            "decision": "STOP_CONFLICT_PREDICTOR",
            "final_outcome": "STOP_V4_NODE_RESOLVE",
            "live_authorized": False,
            "reason": "low_conflict_not_selective_over_high_conflict",
        }
    if (
        evidence.resource_policy_nonstarving is not True
        or not provenance.get("resource_complete")
        or counts.get("resource_admissible_count") == 0
    ):
        return {
            "decision": "RESOURCE_GATE_BUG_OR_POLICY_LIMIT",
            "final_outcome": None,
            "live_authorized": False,
            "reason": "resource_gate_not_proven_admissible_and_nonstarving",
        }
    if not provenance.get("value_complete"):
        return {
            "decision": "STOP_CONFLICT_AWARE_NODE_RESOLVE",
            "final_outcome": "STOP_V4_NODE_RESOLVE",
            "live_authorized": False,
            "reason": "profitability_provenance_incomplete",
        }
    if counts.get("would_launch_count") == 0:
        return {
            "decision": "RESOURCE_GATE_BUG_OR_POLICY_LIMIT",
            "final_outcome": None,
            "live_authorized": False,
            "reason": "selective_low_conflict_exists_but_would_launch_zero",
        }
    return {
        "decision": "GO_C01_CA_LIVE",
        "final_outcome": None,
        "live_authorized": True,
        "reason": "all_offline_gates_passed",
    }


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail(code) from error
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _verify_payload_document(
    path: Path,
    code: str,
    schema: str | None = None,
) -> dict[str, Any]:
    document = _read_json(path, f"{code}_unreadable")
    digest = document.get("payload_sha256")
    body = {
        key: value for key, value in document.items() if key != "payload_sha256"
    }
    if not _valid_sha256(digest) or digest != payload_sha256(body):
        raise _fail(f"{code}_payload_hash_mismatch")
    if schema is not None and document.get("schema_version") != schema:
        raise _fail(f"{code}_schema_invalid")
    return document


def _verify_manifest_document(path: Path, code: str) -> dict[str, Any]:
    document = _read_json(path, f"{code}_unreadable")
    digest = document.get("manifest_sha256")
    body = {
        key: value for key, value in document.items() if key != "manifest_sha256"
    }
    if not _valid_sha256(digest) or digest != payload_sha256(body):
        raise _fail(f"{code}_hash_mismatch")
    return document


def _verify_audit(audit: dict[str, Any]) -> None:
    digest = audit.get("payload_sha256")
    body = {key: value for key, value in audit.items() if key != "payload_sha256"}
    if not _valid_sha256(digest) or digest != payload_sha256(body):
        raise _fail("audit_payload_hash_mismatch")
    if (
        audit.get("schema_version") != _AUDIT_SCHEMA
        or audit.get("status") != "SEALED_DEVELOPMENT_EVIDENCE"
        or audit.get("history_id") != _HISTORY_ID
        or audit.get("source_count") != 49
    ):
        raise _fail("audit_identity_invalid")
    derivation = audit.get("derivation")
    expected = {
        "prepared_timestamp_rule": "PREPARED_DURABLE.event.timestamp_ns",
        "publication_timestamp_rule": "PUBLICATION_DURABLE.event.timestamp_ns",
        "prepared_lead_formula": "publication(i-1) - prepared(i)",
        "potential_opportunity_formula": "prepared_lead_ns > 0",
        "treatment_independent": True,
    }
    if not isinstance(derivation, Mapping) or any(
        derivation.get(key) != value for key, value in expected.items()
    ):
        raise _fail("audit_derivation_invalid")


def _verify_cross_identity(
    audit: Mapping[str, object],
    manifest: Mapping[str, object],
    manifest_path: Path,
    events_path: Path,
) -> None:
    audit_input = audit.get("input")
    if not isinstance(audit_input, Mapping):
        raise _fail("audit_input_identity_invalid")
    if audit_input.get("events_file_sha256") != sha256_file(events_path):
        raise _fail("events_file_hash_mismatch")
    pairs = (
        (audit_input.get("block_manifest_file_sha256"), sha256_file(manifest_path)),
        (audit_input.get("block_manifest_sha256"), manifest.get("manifest_sha256")),
        (audit.get("source_manifest_sha256"), manifest.get("source_manifest_sha256")),
        (
            audit.get("execution_identity_sha256"),
            manifest.get("execution_identity_sha256"),
        ),
        (
            audit.get("history_arrival_trace_sha256"),
            manifest.get("history_arrival_trace_sha256"),
        ),
        (
            audit.get("state_cut_certification_sha256"),
            manifest.get("state_cut_certification_sha256"),
        ),
        (
            audit.get("global_llm_admission_k"),
            manifest.get("global_llm_admission_k"),
        ),
        (audit.get("lookahead"), manifest.get("lookahead")),
        (audit.get("compile_workers"), manifest.get("compile_workers")),
    )
    if any(left != right for left, right in pairs):
        raise _fail("audit_manifest_identity_conflict")
    if (
        manifest.get("global_llm_admission_k") != 2
        or manifest.get("lookahead") != 2
        or manifest.get("compile_workers") != 2
    ):
        raise _fail("frozen_execution_shape_invalid")


def _timestamp(event: Mapping[str, object], code: str) -> int:
    value = event.get("timestamp_ns")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _derive_event_rows(
    events_path: Path,
    manifest: Mapping[str, object],
) -> tuple[list[dict[str, object]], int]:
    source_count = manifest.get("source_count")
    source_hashes = manifest.get("source_sha256s")
    if (
        isinstance(source_count, bool)
        or not isinstance(source_count, int)
        or source_count < 1
        or not isinstance(source_hashes, list)
        or len(source_hashes) != source_count
    ):
        raise _fail("event_source_inventory_invalid")
    by_source: dict[int, dict[str, dict[str, object]]] = defaultdict(dict)
    try:
        lines = Path(events_path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise _fail("events_unreadable") from error
    event_sequence = 0
    previous_timestamp: int | None = None
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise _fail(f"event_row_empty:{line_number}")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise _fail(f"event_row_invalid:{line_number}") from error
        if not isinstance(row, dict) or set(row) != {"event", "event_sha256"}:
            raise _fail(f"event_row_invalid:{line_number}")
        event = row.get("event")
        if not isinstance(event, dict) or set(event) != _EVENT_KEYS:
            raise _fail(f"event_schema_invalid:{line_number}")
        if row.get("event_sha256") != payload_sha256(event):
            raise _fail(f"event_hash_mismatch:{line_number}")
        if (
            event.get("schema_version") != _EVENT_SCHEMA
            or event.get("event_sequence") != event_sequence
        ):
            raise _fail(f"event_identity_invalid:{line_number}")
        source = event.get("source_sequence")
        event_type = event.get("event_type")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source < 0
            or source >= source_count
            or event_type not in _REQUIRED_EVENTS
            or not isinstance(event.get("telemetry"), Mapping)
        ):
            raise _fail(f"event_identity_invalid:{line_number}")
        timestamp = _timestamp(event, f"event_timestamp_invalid:{line_number}")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise _fail(f"event_timestamp_order_invalid:{line_number}")
        previous_timestamp = timestamp
        if event_type in by_source[source]:
            raise _fail(f"event_duplicate:{source}:{event_type}")
        by_source[source][str(event_type)] = event
        event_sequence += 1
    if event_sequence != source_count * len(_REQUIRED_EVENTS):
        raise _fail("event_count_invalid")

    rows: list[dict[str, object]] = []
    previous_publication: int | None = None
    for source in range(source_count):
        events = by_source[source]
        if set(events) != set(_REQUIRED_EVENTS):
            raise _fail(f"event_set_invalid:{source}")
        source_hash = source_hashes[source]
        if any(event.get("source_sha256") != source_hash for event in events.values()):
            raise _fail(f"event_source_hash_invalid:{source}")
        lifecycle_timestamps = [
            _timestamp(events[event_type], f"event_{event_type.lower()}_timestamp_invalid")
            for event_type in _REQUIRED_EVENTS
        ]
        if any(
            right < left
            for left, right in zip(lifecycle_timestamps, lifecycle_timestamps[1:])
        ):
            raise _fail(f"event_lifecycle_order_invalid:{source}")
        arrival = events["ARRIVAL"]
        telemetry = arrival["telemetry"]
        assert isinstance(telemetry, Mapping)
        arrival_target = telemetry.get("arrival_time_ns")
        if isinstance(arrival_target, bool) or not isinstance(arrival_target, int):
            raise _fail(f"event_arrival_target_invalid:{source}")
        arrival_ns = _timestamp(arrival, "event_arrival_timestamp_invalid")
        prepared_ns = _timestamp(
            events["PREPARED_DURABLE"], "event_prepared_timestamp_invalid"
        )
        publication_ns = _timestamp(
            events["PUBLICATION_DURABLE"], "event_publication_timestamp_invalid"
        )
        lead_ns, potential = derive_prepared_opportunity(
            prepared_ns, previous_publication
        )
        rows.append(
            {
                "source_sequence": source,
                "source_sha256": source_hash,
                "arrival_event_timestamp_ns": arrival_ns,
                "arrival_target_timestamp_ns": arrival_target,
                "arrival_timestamp_ns": arrival_ns,
                "compile_started_timestamp_ns": _timestamp(
                    events["COMPILE_STARTED"], "event_compile_timestamp_invalid"
                ),
                "prepared_durable_timestamp_ns": prepared_ns,
                "bind_started_timestamp_ns": _timestamp(
                    events["BIND_STARTED"], "event_bind_timestamp_invalid"
                ),
                "commit_returned_timestamp_ns": _timestamp(
                    events["COMMIT_RETURNED"], "event_commit_timestamp_invalid"
                ),
                "predecessor_publication_durable_timestamp_ns": previous_publication,
                "publication_durable_timestamp_ns": publication_ns,
                "arrival_lead_ns": (
                    previous_publication - arrival_ns
                    if previous_publication is not None
                    else None
                ),
                "arrival_target_lead_ns": (
                    previous_publication - arrival_target
                    if previous_publication is not None
                    else None
                ),
                "prepared_lead_ns": lead_ns,
                "potential_opportunity": potential,
            }
        )
        previous_publication = publication_ns
    return rows, event_sequence


def _load_prepared(
    prepared_dir: Path,
    manifest: Mapping[str, object],
    source_count: int,
) -> tuple[list[PreparedArtifact], list[dict[str, object]]]:
    compile_hashes = manifest.get("compile_source_sha256s")
    certification = manifest.get("state_cut_certification_sha256")
    if not isinstance(compile_hashes, list) or len(compile_hashes) < source_count:
        raise _fail("compile_source_inventory_invalid")
    artifacts: list[PreparedArtifact] = []
    identities: list[dict[str, object]] = []
    for source in range(source_count):
        path = Path(prepared_dir) / f"{source:08d}.json"
        document = _read_json(path, f"prepared_artifact_unreadable:{source}")
        if set(document) != _PREPARED_KEYS:
            raise _fail(f"prepared_artifact_schema_invalid:{source}")
        if document.get("source_sequence") != source:
            raise _fail(f"prepared_source_sequence_invalid:{source}")
        try:
            artifact = PreparedArtifact.create(
                source_sequence=document["source_sequence"],
                source_sha256=document["source_sha256"],
                evidence_sha256=document["evidence_sha256"],
                certification_sha256=document["certification_sha256"],
                raw_nodes=document["raw_nodes"],
                raw_edges=document["raw_edges"],
                pure_intermediates=document["pure_intermediates"],
            )
            artifact.verify(
                expected_source_sha256=compile_hashes[source],
                expected_certification_sha256=certification,
            )
        except (KeyError, TypeError, PreparedArtifactError) as error:
            raise _fail(f"prepared_artifact_identity_invalid:{source}") from error
        if artifact.artifact_sha256 != document.get("artifact_sha256"):
            raise _fail(f"prepared_artifact_hash_mismatch:{source}")
        artifacts.append(artifact)
        identities.append(
            {
                "source_sequence": source,
                "file_sha256": sha256_file(path),
                "source_sha256": artifact.source_sha256,
                "evidence_sha256": artifact.evidence_sha256,
                "certification_sha256": artifact.certification_sha256,
                "artifact_sha256": artifact.artifact_sha256,
                "raw_node_count": len(artifact.raw_nodes),
                "raw_edge_count": len(artifact.raw_edges or ()),
            }
        )
    return artifacts, identities


def _bound_file(
    value: object,
    code: str,
) -> tuple[Path, str]:
    if not isinstance(value, Mapping):
        raise _fail(f"{code}_binding_invalid")
    path_value = value.get("absolute_path")
    expected = value.get("sha256")
    if not isinstance(path_value, str) or not _valid_sha256(expected):
        raise _fail(f"{code}_binding_invalid")
    path = Path(path_value)
    actual = sha256_file(path)
    if actual != expected:
        raise _fail(f"{code}_file_hash_mismatch")
    return path, actual


def _baseline_inventory(binding_path: Path) -> dict[str, object]:
    binding = _verify_payload_document(
        binding_path,
        "baseline_binding",
        "membind.paper-eval-v3.membind-v4-baseline-binding.v1",
    )
    if (
        binding.get("status") != "PASS"
        or binding.get("binding_mode") != "READ_ONLY_ABSOLUTE_PATH_SHA256"
        or binding.get("live_rerun_performed") is not False
    ):
        raise _fail("baseline_binding_identity_invalid")
    artifacts = binding.get("artifacts")
    baseline = artifacts.get("baseline") if isinstance(artifacts, Mapping) else None
    if not isinstance(baseline, list) or len(baseline) != 3:
        raise _fail("baseline_inventory_invalid")
    expected_methods = ["U0-aligned", "A0-aligned", "P(C=2)-aligned"]
    traces: list[dict[str, object]] = []
    for index, item in enumerate(baseline):
        if not isinstance(item, Mapping) or item.get("method") != expected_methods[index]:
            raise _fail("baseline_inventory_invalid")
        if item.get("status") != "PASS" or item.get("history_id") != _HISTORY_ID:
            raise _fail("baseline_inventory_invalid")
        result_path, result_sha = _bound_file(item, f"baseline_result:{index}")
        companions = item.get("companions")
        if not isinstance(companions, Mapping):
            raise _fail("baseline_companion_inventory_invalid")
        companion_inventory: dict[str, object] = {}
        manifest: dict[str, Any] | None = None
        for name in ("events.jsonl", "manifest.json"):
            companion = companions.get(name)
            path, digest = _bound_file(
                companion, f"baseline_companion:{index}:{name}"
            )
            companion_inventory[name] = {
                "absolute_path": str(path.resolve()),
                "file_sha256": digest,
            }
            if name == "manifest.json":
                manifest = _verify_manifest_document(
                    path, f"baseline_manifest:{index}"
                )
        assert manifest is not None
        if (
            manifest.get("method") != item.get("method")
            or manifest.get("history_id") != _HISTORY_ID
            or manifest.get("source_manifest_sha256")
            != item.get("identities", {}).get("source_manifest_sha256")
        ):
            raise _fail("baseline_manifest_identity_invalid")
        traces.append(
            {
                "method": item["method"],
                "result_absolute_path": str(result_path.resolve()),
                "result_file_sha256": result_sha,
                "companions": companion_inventory,
                "manifest_sha256": manifest["manifest_sha256"],
            }
        )
    v31 = artifacts.get("v3_1_success") if isinstance(artifacts, Mapping) else None
    v31_path, v31_sha = _bound_file(v31, "baseline_v31_reference")
    consistency = binding.get("identity_consistency")
    if not isinstance(consistency, Mapping) or consistency.get("status") != (
        "MIXED_ENVELOPES_NOT_FORMAL_COMPARISON"
    ):
        raise _fail("baseline_comparison_limit_invalid")
    return {
        "binding_absolute_path": str(Path(binding_path).resolve()),
        "binding_file_sha256": sha256_file(binding_path),
        "binding_payload_sha256": binding["payload_sha256"],
        "traces": traces,
        "v3_1_reference": {
            "absolute_path": str(v31_path.resolve()),
            "file_sha256": v31_sha,
        },
        "usable_facts": {
            "history_id": _HISTORY_ID,
            "methods": expected_methods,
            "all_status_pass": True,
            "formal_comparison_eligible": False,
            "reason": "mixed_execution_envelopes",
        },
    }


def _old_c01_inventory(candidate_dir: Path) -> dict[str, object]:
    candidate_dir = Path(candidate_dir)
    candidate = _verify_payload_document(
        candidate_dir / "candidate.json",
        "old_c01_candidate",
        "membind.paper-eval-v4.candidate.v1",
    )
    summary = _verify_payload_document(
        candidate_dir / "summary.json",
        "old_c01_summary",
        "membind.paper-eval-v4.summary.v1",
    )
    reduction = _verify_payload_document(
        candidate_dir / "reduction.json",
        "old_c01_reduction",
        "membind.paper-eval-v4.candidate-reduction.v1",
    )
    block_result = _verify_payload_document(
        candidate_dir / "block/V4_BLOCK_RESULT.json",
        "old_c01_block_result",
        "membind.paper-eval-v4.live-block-result.v1",
    )
    native_result = _verify_payload_document(
        candidate_dir / "block/result.json",
        "old_c01_native_result",
        "membind.paper-eval-v3.membind-v31-live-block-result.v1",
    )
    block_manifest_path = candidate_dir / "block/manifest.json"
    block_manifest = _verify_manifest_document(
        block_manifest_path, "old_c01_block_manifest"
    )
    identity_values = (candidate, summary, reduction, block_result, native_result)
    if any(item.get("source_count") != 6 for item in identity_values):
        raise _fail("old_c01_source_count_invalid")
    if (
        candidate.get("candidate_id") != "c01"
        or summary.get("candidate_id") != "c01"
        or reduction.get("candidate_id") != "c01"
        or summary.get("history_id") != _HISTORY_ID
        or block_manifest.get("history_id") != _HISTORY_ID
        or block_manifest.get("source_count") != 6
    ):
        raise _fail("old_c01_identity_invalid")
    mechanism = reduction.get("mechanism")
    decision = reduction.get("decision")
    expected_facts = {
        "source_count": 6,
        "qualified_node_resolve_count": 0,
        "speculation_launch_count": 0,
        "semantic_hit_count": 0,
        "semantic_miss_count": 0,
        "decision": "STOP_V4_NODE_RESOLVE",
        "reason": "no_qualified_node_resolve",
        "conflict_selectivity_available": False,
    }
    if (
        not isinstance(mechanism, Mapping)
        or not isinstance(decision, Mapping)
        or summary.get("qualified_node_resolve_count") != 0
        or summary.get("speculation_launch_count") != 0
        or summary.get("semantic_hit_count") != 0
        or summary.get("semantic_miss_count") != 0
        or any(
            mechanism.get(key) != 0
            for key in (
                "qualified_node_resolve_count",
                "speculation_launch_count",
                "semantic_hit_count",
                "semantic_miss_count",
            )
        )
        or decision.get("decision") != expected_facts["decision"]
        or decision.get("reason") != expected_facts["reason"]
    ):
        raise _fail("old_c01_fact_conflict")
    block_events = candidate_dir / "block/events.jsonl"
    _, block_event_count = _derive_event_rows(block_events, block_manifest)
    runtime_events = candidate_dir / "events.jsonl"
    try:
        runtime_rows = [
            json.loads(line)
            for line in runtime_events.read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail("old_c01_runtime_events_invalid") from error
    if (
        len(runtime_rows) != 12
        or [row.get("event_sequence") for row in runtime_rows] != list(range(12))
        or sum(row.get("event_type") == "exact_node_resolve" for row in runtime_rows)
        != 6
        or sum(row.get("event_type") == "native_continuation" for row in runtime_rows)
        != 6
    ):
        raise _fail("old_c01_runtime_events_invalid")
    file_names = (
        "candidate.json",
        "summary.json",
        "reduction.json",
        "events.jsonl",
        "block/V4_BLOCK_RESULT.json",
        "block/result.json",
        "block/manifest.json",
        "block/events.jsonl",
        "block/llm.jsonl",
    )
    files = {
        name: {
            "absolute_path": str((candidate_dir / name).resolve()),
            "file_sha256": sha256_file(candidate_dir / name),
        }
        for name in file_names
    }
    if any(item["file_sha256"] == "missing" for item in files.values()):
        raise _fail("old_c01_evidence_file_missing")
    return {
        "candidate_absolute_path": str(candidate_dir.resolve()),
        "files": files,
        "manifest_sha256": block_manifest["manifest_sha256"],
        "validated_block_event_count": block_event_count,
        "validated_runtime_event_count": len(runtime_rows),
        "usable_facts": expected_facts,
        "use_in_current_replay": "not_reached_no_timing_opportunity",
    }


def _conflict_name(value: object) -> str:
    candidate = getattr(value, "value", value)
    if candidate not in _CONFLICT_CLASSES:
        raise _fail("classifier_result_invalid")
    return str(candidate)


def _load_optional_facts(
    path: Path,
) -> tuple[dict[int, Mapping[str, object]], OfflineGateEvidence, str]:
    facts = _verify_payload_document(path, "offline_facts", _FACT_SCHEMA)
    if facts.get("history_id") != _HISTORY_ID or facts.get("source_count") != 12:
        raise _fail("offline_facts_identity_invalid")
    rows = facts.get("rows")
    if not isinstance(rows, list):
        raise _fail("offline_facts_rows_invalid")
    by_source: dict[int, Mapping[str, object]] = {}
    for row in rows:
        source = row.get("source_sequence") if isinstance(row, Mapping) else None
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source < 1
            or source >= 12
            or source in by_source
        ):
            raise _fail("offline_facts_rows_invalid")
        by_source[source] = row
    digest = facts["payload_sha256"]
    evidence = OfflineGateEvidence(
        exact_validation_enforced=facts.get("exact_validation_enforced"),
        exact_validation_provenance_sha256=digest,
        resource_policy_nonstarving=facts.get("resource_policy_nonstarving"),
        resource_policy_provenance_sha256=digest,
    ).verify()
    return by_source, evidence, str(digest)


def build_conflict_offline_replay(
    *,
    audit_path: Path,
    block_manifest_path: Path,
    events_path: Path,
    prepared_dir: Path,
    baseline_binding_path: Path,
    old_c01_candidate_dir: Path,
    source_count: int = 12,
    classifier: Callable[[dict[str, object], dict[str, object]], object] | None = None,
    offline_facts_path: Path | None = None,
) -> dict[str, object]:
    """Replay the registered 12-source prefix without invoking a live service."""

    if source_count != 12 or isinstance(source_count, bool):
        raise _fail("registered_prefix_required")
    audit_path = Path(audit_path)
    manifest_path = Path(block_manifest_path)
    events_path = Path(events_path)
    audit = _read_json(audit_path, "audit_unreadable")
    _verify_audit(audit)
    manifest = _verify_manifest_document(manifest_path, "block_manifest")
    if manifest.get("history_id") != _HISTORY_ID or manifest.get("source_count") != 49:
        raise _fail("block_manifest_identity_invalid")
    _verify_cross_identity(audit, manifest, manifest_path, events_path)
    event_rows, event_count = _derive_event_rows(events_path, manifest)
    audit_rows = audit.get("source_rows")
    if not isinstance(audit_rows, list) or audit_rows != event_rows:
        raise _fail("audit_event_rows_mismatch")
    prepared, prepared_identities = _load_prepared(
        Path(prepared_dir), manifest, source_count
    )
    baseline_inventory = _baseline_inventory(Path(baseline_binding_path))
    old_c01_inventory = _old_c01_inventory(Path(old_c01_candidate_dir))

    opportunity_sources = [
        source
        for source in range(1, source_count)
        if event_rows[source]["potential_opportunity"] is True
    ]
    fact_rows: dict[int, Mapping[str, object]] = {}
    fact_digest: str | None = None
    gate_evidence = OfflineGateEvidence(None, None, None, None)
    if opportunity_sources and offline_facts_path is not None:
        fact_rows, gate_evidence, fact_digest = _load_optional_facts(
            Path(offline_facts_path)
        )

    observations: list[ReplayObservation] = []
    replay_rows: list[dict[str, object]] = []
    for source, timing in enumerate(event_rows[:source_count]):
        potential = timing["potential_opportunity"] is True
        conflict_class: str | None = None
        execution_mode: str | None = None
        resource_admissible: bool | None = None
        value_admissible: bool | None = None
        exact_outcome: str | None = None
        provenance: ReplayFactProvenance | None = None
        if source > 0 and potential:
            if classifier is None:
                conflict_class = "UNKNOWN"
            else:
                conflict_class = _conflict_name(
                    classifier(
                        prepared[source - 1].to_document(),
                        prepared[source].to_document(),
                    )
                )
            conflict_digest = payload_sha256(
                {
                    "classifier": "conflict-aware-v1",
                    "frontier_artifact_sha256": prepared[source - 1].artifact_sha256,
                    "candidate_artifact_sha256": prepared[source].artifact_sha256,
                    "conflict_class": conflict_class,
                }
            )
            fact = fact_rows.get(source, {})
            execution_mode = fact.get("execution_mode")
            resource_admissible = fact.get("resource_admissible")
            value_admissible = fact.get("value_admissible")
            exact_outcome = fact.get("exact_outcome")
            provenance = ReplayFactProvenance(
                conflict_sha256=conflict_digest,
                semantic_sha256=(
                    fact_digest if execution_mode is not None else None
                ),
                resource_sha256=(
                    fact_digest if resource_admissible is not None else None
                ),
                value_sha256=(fact_digest if value_admissible is not None else None),
                exact_outcome_sha256=(
                    fact_digest if exact_outcome is not None else None
                ),
            )
        if source > 0:
            observations.append(
                ReplayObservation(
                    source,
                    potential,
                    conflict_class,
                    execution_mode,
                    resource_admissible,
                    value_admissible,
                    exact_outcome,
                    provenance,
                ).verify()
            )
        replay_rows.append(
            {
                "source_sequence": source,
                "prepared_durable_timestamp_ns": timing[
                    "prepared_durable_timestamp_ns"
                ],
                "predecessor_publication_durable_timestamp_ns": timing[
                    "predecessor_publication_durable_timestamp_ns"
                ],
                "prepared_lead_ns": timing["prepared_lead_ns"],
                "potential_opportunity": potential,
                "conflict_class": conflict_class,
                "execution_mode": execution_mode,
                "resource_admissible": resource_admissible,
                "value_admissible": value_admissible,
                "exact_outcome": exact_outcome,
                "provenance": (
                    provenance.to_document() if provenance is not None else None
                ),
            }
        )

    reduced = reduce_replay_observations(observations)
    expected = audit.get("expected_opportunity_counts")
    if (
        not isinstance(expected, Mapping)
        or expected.get("sources_0_11")
        != reduced["counts"]["total_future_prepared_opportunities"]
    ):
        raise _fail("sealed_prefix_opportunity_count_mismatch")
    gate = evaluate_offline_gate(reduced, gate_evidence)
    optional_fact_evidence: dict[str, object]
    if not opportunity_sources:
        optional_fact_evidence = {
            "status": "not_reached",
            "reason": "no_future_prepared_opportunities",
            "facts_used": False,
        }
        old_c01_inventory["use_in_current_replay"] = (
            "not_reached_no_timing_opportunity"
        )
    elif offline_facts_path is None:
        optional_fact_evidence = {
            "status": "unavailable",
            "reason": "sealed_offline_facts_not_provided",
            "facts_used": False,
        }
        old_c01_inventory["use_in_current_replay"] = (
            "inventory_only_no_conflict_selectivity"
        )
    else:
        optional_fact_evidence = {
            "status": "verified",
            "absolute_path": str(Path(offline_facts_path).resolve()),
            "file_sha256": sha256_file(Path(offline_facts_path)),
            "payload_sha256": fact_digest,
            "facts_used": True,
        }

    result: dict[str, object] = {
        "schema_version": _REPLAY_SCHEMA,
        "status": "PASS_OFFLINE_REPLAY",
        "history_id": _HISTORY_ID,
        "source_count": source_count,
        "source_sequences": list(range(source_count)),
        "speculation_distance": 1,
        "operator": "NodeResolve",
        "global_llm_admission_k": 2,
        "network_calls": 0,
        "persistent_writes": 0,
        "input_identity": {
            "audit_file_sha256": sha256_file(audit_path),
            "audit_payload_sha256": audit["payload_sha256"],
            "block_manifest_file_sha256": sha256_file(manifest_path),
            "block_manifest_sha256": manifest["manifest_sha256"],
            "events_file_sha256": sha256_file(events_path),
            "execution_identity_sha256": manifest["execution_identity_sha256"],
            "source_manifest_sha256": manifest["source_manifest_sha256"],
            "state_cut_certification_sha256": manifest[
                "state_cut_certification_sha256"
            ],
        },
        "event_evidence": {
            "absolute_path": str(events_path.resolve()),
            "validated_event_count": event_count,
            "audit_rows_match_derived_events": True,
        },
        "prepared_artifacts": prepared_identities,
        "read_only_evidence_inventory": {
            "frozen_baselines": baseline_inventory,
            "old_blind_c01_6_source": old_c01_inventory,
        },
        "optional_fact_evidence": optional_fact_evidence,
        "source_rows": replay_rows,
        **reduced,
        "gate": gate,
    }
    result["payload_sha256"] = payload_sha256(result)
    return result


__all__ = [
    "ConflictReplayError",
    "OfflineGateEvidence",
    "ReplayFactProvenance",
    "ReplayObservation",
    "build_conflict_offline_replay",
    "derive_prepared_opportunity",
    "evaluate_offline_gate",
    "reduce_replay_observations",
]
