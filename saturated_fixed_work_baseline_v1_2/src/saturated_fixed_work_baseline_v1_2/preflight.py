"""Pure fail-closed L0 acceptance contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class PreflightError(ValueError):
    """A live qualification prerequisite is absent or drifted."""


@dataclass(frozen=True)
class SamplerQualification:
    duration_s: float
    expected_samples: int
    actual_samples: int
    coverage: float
    gap_p95_s: float
    gap_max_s: float


@dataclass(frozen=True)
class PreflightEvidence:
    tests_all_green: bool
    repository_identity_verified: bool
    data_identity_verified: bool
    provider_identity_verified: bool
    qa_identity_verified: bool
    historical_resource_match: bool
    live_resource_envelope_verified: bool
    construction_canary_passed: bool
    embedding_canary_passed: bool
    neo4j_canary_passed: bool
    construction_cache_salt_passed: bool
    embedding_cache_salt_passed: bool
    warmup_manifest_verified: bool
    construction_idle_samples: tuple[bool, ...]
    embedding_idle: bool
    neo4j_idle: bool
    no_other_clients: bool
    sampler: SamplerQualification


def validate_preflight(evidence: PreflightEvidence) -> dict[str, Any]:
    if not isinstance(evidence, PreflightEvidence):
        raise PreflightError("PREFLIGHT_EVIDENCE_INVALID")
    gates = (
        ("tests_all_green", "TEST_GATE_NOT_GREEN"),
        ("repository_identity_verified", "REPOSITORY_IDENTITY_UNVERIFIED"),
        ("data_identity_verified", "DATA_IDENTITY_UNVERIFIED"),
        ("provider_identity_verified", "PROVIDER_IDENTITY_UNVERIFIED"),
        ("qa_identity_verified", "QA_IDENTITY_UNVERIFIED"),
        ("historical_resource_match", "HISTORICAL_RESOURCE_MISMATCH"),
        ("live_resource_envelope_verified", "LIVE_RESOURCE_ENVELOPE_UNVERIFIED"),
        ("construction_canary_passed", "CONSTRUCTION_CANARY_FAILED"),
        ("embedding_canary_passed", "EMBEDDING_CANARY_FAILED"),
        ("neo4j_canary_passed", "NEO4J_CANARY_FAILED"),
        ("construction_cache_salt_passed", "CONSTRUCTION_CACHE_SALT_UNQUALIFIED"),
        ("embedding_cache_salt_passed", "EMBEDDING_CACHE_SALT_UNQUALIFIED"),
        ("warmup_manifest_verified", "WARMUP_MANIFEST_UNVERIFIED"),
        ("embedding_idle", "EMBEDDING_NOT_IDLE"),
        ("neo4j_idle", "NEO4J_NOT_IDLE"),
        ("no_other_clients", "OTHER_CLIENT_CONTAMINATION"),
    )
    for field, code in gates:
        if getattr(evidence, field) is not True:
            raise PreflightError(code)
    if evidence.construction_idle_samples != (True, True):
        raise PreflightError("CONSTRUCTION_NOT_IDLE_TWO_SAMPLES")
    sampler = evidence.sampler
    if (
        not isinstance(sampler, SamplerQualification)
        or sampler.duration_s < 60.0
        or sampler.expected_samples < 60
        or sampler.actual_samples < 1
        or not 0.0 <= sampler.coverage <= 1.0
        or sampler.coverage < 0.9
        or sampler.gap_p95_s > 1.5
        or sampler.gap_max_s > 2.5
    ):
        raise PreflightError("SAMPLER_QUALIFICATION_FAILED")
    return {
        "schema_version": "membind.saturated-fixed-work.preflight.v1",
        "status": "PASS",
        "formal_run_authorized": True,
        "evidence": asdict(evidence),
    }


__all__ = [
    "PreflightError",
    "PreflightEvidence",
    "SamplerQualification",
    "validate_preflight",
]
