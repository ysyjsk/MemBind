"""Pure, fail-closed primitives for the bounded S4 edge diagnosis.

The live diagnosis runner may inspect Graphiti objects, but this module emits
only canonical hashes, counts, classifications, and side-effect evidence.  It
does not authorize cleanup, a retry, qualification, or any later stage.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256


SCHEMA_VERSION = "membind.paper-eval-v3.s4-edge-identity-diagnosis.v1"
EXPECTED_ATTEMPT_ID = "005"
EXPECTED_HISTORY_ID = "07741c45"
EXPECTED_REPLAY_RUN_ID = "s4-d0-replay-20260815-005"
EXPECTED_SOURCE_SEQUENCE = 7

SIDECAR_AMENDMENT_JUSTIFIED = "SIDECAR_AMENDMENT_JUSTIFIED"
LOGICAL_IDENTITY_STILL_AMBIGUOUS_STOP = (
    "LOGICAL_IDENTITY_STILL_AMBIGUOUS_STOP"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_EVIDENCE = {
    "capture_canonical_graph_sha256",
    "capture_phase_result_sha256",
    "dataset_sha256",
    "embedding_cache_sha256",
    "prompt_cache_sha256",
    "replay_checkpoint_sha256",
    "replay_events_sha256",
    "replay_phase_result_sha256",
    "split_sha256",
}
_REQUIRED_COUNTERS = {
    "cache_write_count",
    "cross_encoder_call_count",
    "db_write_count",
    "live_embedding_call_count",
    "live_llm_call_count",
    "network_call_count",
    "publication_count",
}
_STATE_FIELDS = {
    "canonical_snapshot_sha256",
    "episode_count",
    "episode_names_sha256",
    "node_count",
    "relationship_count",
}
_VOLATILE_ATTRIBUTE_KEYS = {
    "created_at",
    "embedding",
    "expired_at",
    "fact_embedding",
    "group_id",
    "neo4j_id",
    "position",
    "rank",
    "source_node_uuid",
    "target_node_uuid",
    "updated_at",
    "uuid",
}
_PRIVATE_KEYS = {
    "answer",
    "api_key",
    "content",
    "endpoint",
    "fact",
    "messages",
    "password",
    "prompt",
    "question",
    "response",
    "secret",
    "uuid",
}


class EdgeIdentityDiagnosisError(ValueError):
    """The diagnosis evidence does not satisfy the frozen public contract."""


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise EdgeIdentityDiagnosisError(f"{field} is not a lowercase SHA256")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise EdgeIdentityDiagnosisError(f"{field} must be nonempty text")
    return unicodedata.normalize("NFKC", value).strip()


def _summary_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise EdgeIdentityDiagnosisError(f"{field} must be text")
    return unicodedata.normalize("NFKC", value).strip()


def _hash_utf8(value: object, *, field: str) -> str:
    return hashlib.sha256(_text(value, field=field).encode("utf-8")).hexdigest()


def _canonical_value(value: Any) -> Any:
    """Return a deterministic JSON value after removing volatile attributes."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EdgeIdentityDiagnosisError("non-finite semantic attribute")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key).casefold() not in _VOLATILE_ATTRIBUTE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_canonical_value(child) for child in value]
    raise EdgeIdentityDiagnosisError("semantic attribute is not JSON-compatible")


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_time(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        selected = value
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            selected = datetime.fromisoformat(raw)
        except ValueError as error:
            raise EdgeIdentityDiagnosisError(f"{field} is not an ISO datetime") from error
    else:
        raise EdgeIdentityDiagnosisError(f"{field} is not a datetime")
    if selected.tzinfo is None:
        selected = selected.replace(tzinfo=timezone.utc)
    return selected.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _endpoint_projection(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EdgeIdentityDiagnosisError(f"{field} endpoint is missing")
    selected = dict(value)
    labels = selected.get("labels")
    if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes)):
        raise EdgeIdentityDiagnosisError(f"{field} endpoint labels are malformed")
    normalized_labels = sorted(
        {_text(label, field=f"{field} label") for label in labels}
    )
    return {
        "normalized_name": _text(
            selected.get("normalized_name"), field=f"{field} normalized name"
        ),
        "labels": normalized_labels,
        "summary": _summary_text(
            selected.get("summary"), field=f"{field} summary"
        ),
        "attributes": _canonical_value(selected.get("attributes", {})),
    }


def _provenance_projection(
    episode_ids: object,
    provenance_by_uuid: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(episode_ids, Sequence) or isinstance(episode_ids, (str, bytes)):
        raise EdgeIdentityDiagnosisError("edge provenance is malformed")
    projected: set[tuple[int, str]] = set()
    for raw_id in episode_ids:
        if not isinstance(raw_id, str) or raw_id not in provenance_by_uuid:
            raise EdgeIdentityDiagnosisError("edge provenance cannot be fully resolved")
        value = provenance_by_uuid[raw_id]
        if not isinstance(value, Mapping):
            raise EdgeIdentityDiagnosisError("edge provenance lookup is malformed")
        sequence = value.get("source_sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise EdgeIdentityDiagnosisError("provenance source sequence is invalid")
        projected.add(
            (
                sequence,
                _sha(value.get("source_hash"), field="provenance source hash"),
            )
        )
    return [
        {"source_hash": source_hash, "source_sequence": source_sequence}
        for source_sequence, source_hash in sorted(projected)
    ]


def edge_identity_projection(
    edge: Mapping[str, Any],
    endpoint_by_uuid: Mapping[str, Mapping[str, Any]],
    provenance_by_uuid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Project one physical Graphiti edge to stable, directed semantics."""

    if not isinstance(edge, Mapping):
        raise EdgeIdentityDiagnosisError("edge candidate is not a mapping")
    selected = dict(edge)
    source_id = selected.get("source_node_uuid")
    target_id = selected.get("target_node_uuid")
    if not isinstance(source_id, str) or source_id not in endpoint_by_uuid:
        raise EdgeIdentityDiagnosisError("source endpoint cannot be resolved")
    if not isinstance(target_id, str) or target_id not in endpoint_by_uuid:
        raise EdgeIdentityDiagnosisError("target endpoint cannot be resolved")

    source = _endpoint_projection(endpoint_by_uuid[source_id], field="source")
    target = _endpoint_projection(endpoint_by_uuid[target_id], field="target")
    provenance = _provenance_projection(
        selected.get("episodes", []), provenance_by_uuid
    )
    temporal = {
        name: _normalized_time(selected.get(name), field=name)
        for name in ("valid_at", "invalid_at", "reference_time")
    }
    return {
        "fact_sha256": _hash_utf8(selected.get("fact"), field="edge fact"),
        "relation_sha256": _hash_utf8(
            selected.get("name"), field="edge relation"
        ),
        "source_endpoint_sha256": _hash_json(source),
        "target_endpoint_sha256": _hash_json(target),
        "temporal_sha256": _hash_json(temporal),
        "expired": selected.get("expired_at") is not None,
        "semantic_attributes_sha256": _hash_json(selected.get("attributes", {})),
        "provenance_sha256": _hash_json(provenance),
    }


def edge_identity_sha256(
    edge: Mapping[str, Any],
    endpoint_by_uuid: Mapping[str, Mapping[str, Any]],
    provenance_by_uuid: Mapping[str, Mapping[str, Any]],
) -> str:
    """Hash the stable projection; position and physical IDs never enter it."""

    return payload_sha256(
        edge_identity_projection(edge, endpoint_by_uuid, provenance_by_uuid)
    )


def diagnose_candidate_partition(
    *,
    candidates: Sequence[Mapping[str, Any]],
    endpoint_by_uuid: Mapping[str, Mapping[str, Any]],
    provenance_by_uuid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify uniqueness inside one already-bound candidate partition."""

    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise EdgeIdentityDiagnosisError("candidate partition is malformed")
    projections = [
        edge_identity_projection(edge, endpoint_by_uuid, provenance_by_uuid)
        for edge in candidates
    ]
    identities = [payload_sha256(value) for value in projections]
    identity_counts = Counter(identities)
    fact_counts = Counter(value["fact_sha256"] for value in projections)
    ambiguous = sorted(
        count for count in identity_counts.values() if count > 1
    )
    duplicate_facts = sorted(count for count in fact_counts.values() if count > 1)
    classification = "EMPTY" if not identities else (
        "AMBIGUOUS" if ambiguous else "UNIQUE"
    )
    return {
        "schema_version": (
            "membind.paper-eval-v3.s4-edge-candidate-partition-diagnosis.v1"
        ),
        "classification": classification,
        "candidate_count": len(candidates),
        "identity_count": len(identity_counts),
        "identity_sha256": identities,
        "identity_multiplicity": sorted(identity_counts.values()),
        "ambiguous_group_count": len(ambiguous),
        "ambiguous_multiplicity": ambiguous,
        "fact_identity_count": len(fact_counts),
        "fact_sha256": [value["fact_sha256"] for value in projections],
        "duplicate_fact_group_count": len(duplicate_facts),
        "duplicate_fact_multiplicity": duplicate_facts,
    }


def _public_only(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).casefold()
            cache_hash_alias = (
                name in {"prompt", "embedding"}
                and isinstance(child, str)
                and _SHA256.fullmatch(child) is not None
            )
            if not name.endswith("sha256") and (
                name.startswith("raw_")
                or name.startswith("private_")
                or name in _PRIVATE_KEYS
            ) and not cache_hash_alias:
                raise EdgeIdentityDiagnosisError(
                    "public diagnosis contains raw or private data"
                )
            _public_only(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _public_only(child)


def _state(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EdgeIdentityDiagnosisError(f"{field} is not a mapping")
    selected = deepcopy(dict(value))
    if set(selected) != _STATE_FIELDS:
        raise EdgeIdentityDiagnosisError(f"{field} snapshot shape drift")
    for name in ("node_count", "relationship_count", "episode_count"):
        count = selected.get(name)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise EdgeIdentityDiagnosisError(f"{field} {name} is invalid")
    for name, value_sha in selected.items():
        if name.endswith("sha256"):
            _sha(value_sha, field=f"{field} {name}")
    return selected


def _partition_diagnosis(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EdgeIdentityDiagnosisError("candidate partition diagnosis is missing")
    selected = deepcopy(dict(value))
    expected_fields = {
        "ambiguous_group_count",
        "ambiguous_multiplicity",
        "candidate_count",
        "classification",
        "duplicate_fact_group_count",
        "duplicate_fact_multiplicity",
        "fact_identity_count",
        "fact_sha256",
        "identity_count",
        "identity_multiplicity",
        "identity_sha256",
        "schema_version",
    }
    if set(selected) != expected_fields or selected.get("schema_version") != (
        "membind.paper-eval-v3.s4-edge-candidate-partition-diagnosis.v1"
    ):
        raise EdgeIdentityDiagnosisError("candidate partition diagnosis shape drift")
    identities = selected.get("identity_sha256")
    facts = selected.get("fact_sha256")
    if (
        not isinstance(identities, list)
        or not isinstance(facts, list)
        or len(identities) != len(facts)
        or any(_SHA256.fullmatch(value) is None for value in identities + facts)
    ):
        raise EdgeIdentityDiagnosisError("candidate partition hashes are malformed")
    identity_counts = Counter(identities)
    fact_counts = Counter(facts)
    ambiguous = sorted(count for count in identity_counts.values() if count > 1)
    duplicate_facts = sorted(count for count in fact_counts.values() if count > 1)
    classification = (
        "EMPTY"
        if not identities
        else "AMBIGUOUS"
        if ambiguous
        else "UNIQUE"
    )
    expected = {
        "schema_version": selected["schema_version"],
        "classification": classification,
        "candidate_count": len(identities),
        "identity_count": len(identity_counts),
        "identity_sha256": identities,
        "identity_multiplicity": sorted(identity_counts.values()),
        "ambiguous_group_count": len(ambiguous),
        "ambiguous_multiplicity": ambiguous,
        "fact_identity_count": len(fact_counts),
        "fact_sha256": facts,
        "duplicate_fact_group_count": len(duplicate_facts),
        "duplicate_fact_multiplicity": duplicate_facts,
    }
    if selected != expected:
        raise EdgeIdentityDiagnosisError("candidate partition diagnosis is inconsistent")
    return selected


def _call_diagnoses(value: object) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list) or len(value) != 10:
        raise EdgeIdentityDiagnosisError("diagnosis requires exactly ten edge calls")
    selected_calls: list[dict[str, Any]] = []
    correlations: set[str] = set()
    any_ambiguous = False
    total_candidates = 0
    for call in value:
        if not isinstance(call, Mapping) or set(call) != {
            "call_correlation_sha256",
            "partitions",
        }:
            raise EdgeIdentityDiagnosisError("edge call diagnosis shape drift")
        correlation = _sha(
            call.get("call_correlation_sha256"), field="edge call correlation"
        )
        if correlation in correlations:
            raise EdgeIdentityDiagnosisError("duplicate edge call correlation")
        correlations.add(correlation)
        partitions = call.get("partitions")
        if not isinstance(partitions, Mapping) or set(partitions) != {
            "related",
            "invalidation",
        }:
            raise EdgeIdentityDiagnosisError("edge call partition coverage drift")
        selected_partitions = {
            name: _partition_diagnosis(partitions[name])
            for name in ("related", "invalidation")
        }
        total_candidates += sum(
            partition["candidate_count"]
            for partition in selected_partitions.values()
        )
        any_ambiguous = any_ambiguous or any(
            partition["classification"] == "AMBIGUOUS"
            for partition in selected_partitions.values()
        )
        selected_calls.append(
            {
                "call_correlation_sha256": correlation,
                "partitions": selected_partitions,
            }
        )
    if total_candidates == 0:
        raise EdgeIdentityDiagnosisError("diagnosis observed no edge candidates")
    return selected_calls, any_ambiguous


def _persisted_evidence_diagnosis(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EdgeIdentityDiagnosisError("persisted-evidence diagnosis is missing")
    selected = deepcopy(dict(value))
    expected_fields = {
        "ambiguous_prompt_count",
        "capture_replay_bijection_proved",
        "classification",
        "duplicate_fact_multiplicity",
        "duplicate_fact_sha256",
        "edge_extraction_record_count",
        "edge_resolution_prompt_count",
        "extracted_edge_count",
        "matching_capture_graph_edge_count",
        "matching_edges_directed_endpoints_distinct",
        "source_sequence",
    }
    if set(selected) != expected_fields:
        raise EdgeIdentityDiagnosisError("persisted-evidence diagnosis shape drift")
    if selected != {
        "classification": (
            "NON_INJECTIVE_FACT_ONLY_EDGE_CANDIDATE_IDENTITY_CONFIRMED"
        ),
        "source_sequence": EXPECTED_SOURCE_SEQUENCE,
        "edge_extraction_record_count": 1,
        "extracted_edge_count": 10,
        "edge_resolution_prompt_count": 10,
        "ambiguous_prompt_count": 9,
        "duplicate_fact_sha256": _sha(
            selected.get("duplicate_fact_sha256"), field="duplicate fact"
        ),
        "duplicate_fact_multiplicity": 2,
        "matching_capture_graph_edge_count": 2,
        "matching_edges_directed_endpoints_distinct": True,
        "capture_replay_bijection_proved": False,
    }:
        raise EdgeIdentityDiagnosisError("persisted-evidence diagnosis drift")
    return selected


def _cache_hashes(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"prompt", "embedding"}:
        raise EdgeIdentityDiagnosisError(f"{field} cache evidence shape drift")
    return {
        name: _sha(value[name], field=f"{field} {name} cache")
        for name in ("prompt", "embedding")
    }


def _counters(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping) or not _REQUIRED_COUNTERS.issubset(value):
        raise EdgeIdentityDiagnosisError("side-effect counter shape drift")
    selected: dict[str, int] = {}
    for name, count in value.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise EdgeIdentityDiagnosisError(f"side-effect counter {name} is invalid")
        selected[str(name)] = count
    return dict(sorted(selected.items()))


def build_edge_identity_diagnosis(
    *,
    replay_run_id: str,
    history_id: str,
    attempt_id: str,
    source_sequence: int,
    source_hash: str,
    episode_manifest_sha256: str,
    evidence_sha256: Mapping[str, str],
    persisted_evidence_diagnosis: Mapping[str, Any],
    candidate_call_diagnoses: Sequence[Mapping[str, Any]],
    pre_state: Mapping[str, Any],
    post_state: Mapping[str, Any],
    cache_sha256_before: Mapping[str, str],
    cache_sha256_after: Mapping[str, str],
    side_effect_counters: Mapping[str, int],
) -> dict[str, Any]:
    """Build a hash-only diagnosis draft; verification remains mandatory."""

    identity = {
        "attempt_id": str(attempt_id),
        "history_id": str(history_id),
        "replay_run_id": str(replay_run_id),
        "source_sequence": source_sequence,
        "source_hash": _sha(source_hash, field="source-7 hash"),
        "episode_manifest_sha256": _sha(
            episode_manifest_sha256, field="episode manifest"
        ),
    }
    evidence = dict(evidence_sha256)
    if set(evidence) != _REQUIRED_EVIDENCE:
        raise EdgeIdentityDiagnosisError("diagnosis evidence-file shape drift")
    evidence = {
        name: _sha(evidence[name], field=name) for name in sorted(evidence)
    }
    calls, any_ambiguous = _call_diagnoses(list(candidate_call_diagnoses))
    verdict = (
        LOGICAL_IDENTITY_STILL_AMBIGUOUS_STOP
        if any_ambiguous
        else SIDECAR_AMENDMENT_JUSTIFIED
    )
    reason = (
        "IDENTITY_AMBIGUOUS"
        if any_ambiguous
        else "REPLAY_PREFIX_IDENTITY_UNIQUE"
    )
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "stage": "S4_EDGE_IDENTITY_DIAGNOSIS",
        "execution_identity": identity,
        "evidence_sha256": evidence,
        "projection_schema_sha256": payload_sha256(
            {
                "directed_endpoints": True,
                "partition_is_structural_not_identity": True,
                "stable_components": [
                    "fact",
                    "relation",
                    "source_endpoint",
                    "target_endpoint",
                    "semantic_time",
                    "expired_boolean",
                    "semantic_attributes",
                    "stable_provenance",
                ],
                "excluded_components": sorted(_VOLATILE_ATTRIBUTE_KEYS),
            }
        ),
        "persisted_evidence_diagnosis": _persisted_evidence_diagnosis(
            persisted_evidence_diagnosis
        ),
        "candidate_call_diagnoses": calls,
        "read_only_evidence": {
            "pre_state": _state(pre_state, field="pre state"),
            "post_state": _state(post_state, field="post state"),
            "cache_sha256_before": _cache_hashes(
                cache_sha256_before, field="before"
            ),
            "cache_sha256_after": _cache_hashes(
                cache_sha256_after, field="after"
            ),
        },
        "side_effect_counters": _counters(side_effect_counters),
        "verdict": verdict,
        "reason": reason,
        "claim_limits": {
            "retry_005_capture_replay_bijection_proved": False,
            "retry_006_authorized": False,
            "cleanup_authorized": False,
            "fixed_four_qualification_authorized": False,
            "s5_authorized": False,
        },
    }
    _public_only(body)
    body["artifact_sha256"] = payload_sha256(body)
    return body


def verify_edge_identity_diagnosis(
    artifact: Mapping[str, Any],
    *,
    expected_evidence_sha256: Mapping[str, str],
    expected_source_hash: str,
    expected_episode_manifest_sha256: str,
) -> dict[str, Any]:
    """Recompute the public contract and every read-only hard gate."""

    if not isinstance(artifact, Mapping):
        raise EdgeIdentityDiagnosisError("diagnosis artifact is not a mapping")
    selected = deepcopy(dict(artifact))
    _public_only(selected)
    declared_hash = _sha(
        selected.pop("artifact_sha256", None), field="diagnosis artifact"
    )
    if payload_sha256(selected) != declared_hash:
        raise EdgeIdentityDiagnosisError("diagnosis artifact hash drift")
    selected["artifact_sha256"] = declared_hash

    identity = selected.get("execution_identity")
    if identity != {
        "attempt_id": EXPECTED_ATTEMPT_ID,
        "history_id": EXPECTED_HISTORY_ID,
        "replay_run_id": EXPECTED_REPLAY_RUN_ID,
        "source_sequence": EXPECTED_SOURCE_SEQUENCE,
        "source_hash": _sha(expected_source_hash, field="expected source-7 hash"),
        "episode_manifest_sha256": _sha(
            expected_episode_manifest_sha256,
            field="expected episode manifest",
        ),
    }:
        raise EdgeIdentityDiagnosisError("retry-005 execution identity drift")

    expected_evidence = dict(expected_evidence_sha256)
    if set(expected_evidence) != _REQUIRED_EVIDENCE:
        raise EdgeIdentityDiagnosisError("expected evidence-file shape drift")
    expected_evidence = {
        name: _sha(expected_evidence[name], field=f"expected {name}")
        for name in sorted(expected_evidence)
    }
    if selected.get("evidence_sha256") != expected_evidence:
        raise EdgeIdentityDiagnosisError("retry-005 evidence hash drift")

    _persisted_evidence_diagnosis(selected.get("persisted_evidence_diagnosis"))

    _, any_ambiguous = _call_diagnoses(selected.get("candidate_call_diagnoses"))
    expected_verdict = (
        LOGICAL_IDENTITY_STILL_AMBIGUOUS_STOP
        if any_ambiguous
        else SIDECAR_AMENDMENT_JUSTIFIED
    )
    expected_reason = (
        "IDENTITY_AMBIGUOUS"
        if any_ambiguous
        else "REPLAY_PREFIX_IDENTITY_UNIQUE"
    )
    if (
        selected.get("verdict") != expected_verdict
        or selected.get("reason") != expected_reason
    ):
        raise EdgeIdentityDiagnosisError("diagnosis verdict is inconsistent")

    read_only = selected.get("read_only_evidence")
    if not isinstance(read_only, Mapping):
        raise EdgeIdentityDiagnosisError("read-only evidence is missing")
    before_state = _state(read_only.get("pre_state"), field="pre state")
    after_state = _state(read_only.get("post_state"), field="post state")
    before_cache = _cache_hashes(
        read_only.get("cache_sha256_before"), field="before"
    )
    after_cache = _cache_hashes(
        read_only.get("cache_sha256_after"), field="after"
    )
    if before_state != after_state:
        raise EdgeIdentityDiagnosisError("diagnosis mutated the graph namespace")
    if before_cache != after_cache:
        raise EdgeIdentityDiagnosisError("diagnosis mutated a sealed cache")

    counters = _counters(selected.get("side_effect_counters"))
    if any(counters[name] != 0 for name in _REQUIRED_COUNTERS):
        raise EdgeIdentityDiagnosisError("diagnosis observed a forbidden side effect")
    if selected.get("claim_limits") != {
        "retry_005_capture_replay_bijection_proved": False,
        "retry_006_authorized": False,
        "cleanup_authorized": False,
        "fixed_four_qualification_authorized": False,
        "s5_authorized": False,
    }:
        raise EdgeIdentityDiagnosisError("diagnosis authority boundary drift")
    if selected.get("schema_version") != SCHEMA_VERSION:
        raise EdgeIdentityDiagnosisError("diagnosis schema drift")
    return selected


def write_edge_identity_diagnosis_exclusive(
    path: Path, artifact: Mapping[str, Any]
) -> None:
    """Create the public artifact exactly once and fsync file and directory."""

    selected_path = Path(path)
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(
        selected_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664
    )
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(selected_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
