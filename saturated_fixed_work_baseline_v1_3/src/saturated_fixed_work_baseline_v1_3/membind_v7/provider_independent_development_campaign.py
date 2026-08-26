"""Provider-independent V7 development artifact materialization."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import development_campaign as base


class ProviderIndependentDevelopmentCampaignError(base.DevelopmentCampaignError):
    """An explicit temporary-provider development identity failed closed."""


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _matches(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return bool(expected) and all(actual.get(key) == value for key, value in expected.items())


def validate_provider_development_campaign_identity(
    value: Mapping[str, Any],
    *,
    expected_provider_identity_kind: str,
    expected_construction_identity: Mapping[str, Any],
    expected_embedding_identity: Mapping[str, Any],
) -> None:
    """Validate a development identity against explicit frozen provider lanes."""

    construction = _mapping(value.get("construction"))
    embedding = _mapping(value.get("embedding"))
    harness = _mapping(value.get("observer_harness"))
    source_sha256 = _mapping(harness.get("source_sha256")) if harness else None
    valid_expected = (
        isinstance(expected_provider_identity_kind, str)
        and bool(expected_provider_identity_kind)
        and isinstance(expected_construction_identity, Mapping)
        and bool(expected_construction_identity)
        and isinstance(expected_embedding_identity, Mapping)
        and bool(expected_embedding_identity)
    )
    if (
        not valid_expected
        or value.get("schema_version")
        != "membind.v7.development-campaign-identity.v2"
        or not isinstance(value.get("run_id"), str)
        or not value.get("run_id")
        or value.get("campaign_scope") != "TEMPORARY_PROVIDER_DEVELOPMENT"
        or value.get("provider_identity_kind") != expected_provider_identity_kind
        or construction is None
        or embedding is None
        or not _matches(construction, expected_construction_identity)
        or not _matches(embedding, expected_embedding_identity)
        or construction.get("authority") == embedding.get("authority")
        or harness is None
        or harness.get("status") != "PASS"
        or source_sha256 is None
        or not source_sha256
        or value.get("formal_r1_r3_eligible") is not False
        or value.get("live_treatment_authorized") is not False
        or value.get("provider_swap_requires_new_formal_campaign") is not True
        or value.get("treatment_calls") != 0
        or value.get("response_replay_calls") != 0
    ):
        raise ProviderIndependentDevelopmentCampaignError(
            "provider-independent development campaign identity is invalid"
        )


def materialize_provider_development_artifacts(
    root: str | Path,
    *,
    r1: Mapping[str, Any],
    r2: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    characterization: Mapping[str, Any],
    campaign_identity: Mapping[str, Any],
    expected_provider_identity_kind: str,
    expected_construction_identity: Mapping[str, Any],
    expected_embedding_identity: Mapping[str, Any],
    scientific_method_selection_path: str | Path,
    expected_scientific_method_selection_sha256: str,
) -> dict[str, Any]:
    """Seal development evidence without assuming a construction provider."""

    if len(blocks) != 2:
        raise ProviderIndependentDevelopmentCampaignError(
            "development R3 requires exactly two blocks"
        )
    if (
        not isinstance(expected_scientific_method_selection_sha256, str)
        or base._DIGEST.fullmatch(expected_scientific_method_selection_sha256) is None
    ):
        raise ProviderIndependentDevelopmentCampaignError(
            "scientific method-selection digest is invalid"
        )
    method_path = Path(scientific_method_selection_path)
    before = base._sha256(method_path)
    if before != expected_scientific_method_selection_sha256:
        raise ProviderIndependentDevelopmentCampaignError(
            "scientific method selection changed before development materialization"
        )
    validate_provider_development_campaign_identity(
        campaign_identity,
        expected_provider_identity_kind=expected_provider_identity_kind,
        expected_construction_identity=expected_construction_identity,
        expected_embedding_identity=expected_embedding_identity,
    )
    sanitized_blocks = [base._sanitize_block(block) for block in blocks]
    artifacts: dict[str, Any] = {
        "R1_ASSUMPTION_AUDIT.json": base._jsonable(dict(r1)),
        "R2_TWO_SOURCE_CAUSAL_TRACE.json": base._sanitize_r2(r2),
        "R3_BLOCKS.json": sanitized_blocks,
        "PROPAGATION_MATRIX.json": {
            "schema_version": "membind.v7.propagation-matrix.v1",
            "rows": list(characterization.get("pair_analyses") or ()),
        },
        "CERTIFICATE_CONFUSION.json": {
            "schema_version": "membind.v7.certificate-confusion.v1",
            "matrix": dict(characterization.get("certificate_confusion") or {}),
            "false_unaffected_count": characterization.get("false_unaffected_count"),
        },
        "AFFECTED_SET_ORACLE.json": {
            "schema_version": "membind.v7.affected-set-oracle.v1",
            "pair_analyses": list(characterization.get("pair_analyses") or ()),
        },
        "CSP_SCA.json": {
            "schema_version": "membind.v7.csp-sca.v1",
            "csp": characterization.get("csp"),
            "semantic_change_amplification": dict(
                characterization.get("semantic_change_amplification") or {}
            ),
            "reconvergence": dict(characterization.get("reconvergence") or {}),
        },
        "CRITICAL_OPPORTUNITY.json": {
            "schema_version": "membind.v7.critical-opportunity.v1",
            **dict(characterization.get("critical_opportunity") or {}),
        },
        "WORK_AMPLIFICATION.json": {
            "schema_version": "membind.v7.work-amplification.v1",
            **dict(characterization.get("semantic_change_amplification") or {}),
        },
    }
    evidence_manifest, evidence_digest = base._evidence_manifest(artifacts)
    decision = dict(characterization.get("decision_input") or {})
    decision.update(
        {
            "sealed_manifest_sha256": evidence_digest,
            "observer_harness_bound": True,
            "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
            "formal_provider_evidence": False,
            "formal_r1_r3_eligible": False,
        }
    )
    provisional = base.evaluate_opportunity_gates(decision)
    selection = base.build_development_selection(provisional)
    artifacts.update(
        {
            "EVIDENCE_MANIFEST.json": evidence_manifest,
            "R3_DECISION_INPUT.json": decision,
            "PROVISIONAL_GATE_RESULT.json": base._development_gate_envelope(provisional),
            "DEVELOPMENT_METHOD_SELECTION.json": selection,
        }
    )
    if "METHOD_SELECTION.json" in artifacts:
        raise ProviderIndependentDevelopmentCampaignError(
            "development campaign attempted formal selection"
        )
    seal = base.write_observer_artifacts(
        root,
        artifacts,
        campaign_identity=base._jsonable(campaign_identity),
    )
    target = Path(root)
    os.chmod(target, 0o700)
    for path in target.glob("*.json"):
        os.chmod(path, 0o600)
    verification = base.verify_observer_manifest(root)
    after = base._sha256(method_path)
    if after != before:
        raise ProviderIndependentDevelopmentCampaignError(
            "scientific method selection changed during development materialization"
        )
    return {
        **seal,
        "verification": verification,
        "decision_input": decision,
        "provisional_gate_result": provisional,
        "development_method_selection": selection,
        "scientific_method_selection_sha256_before": before,
        "scientific_method_selection_sha256_after": after,
    }


def record_provider_development_success(
    journal: Any,
    materialization: Mapping[str, Any],
) -> None:
    """Finalize an attempt with the sealed manifest through the journal success API."""

    digest = materialization.get("manifest_sha256")
    record_success = getattr(journal, "record_success", None)
    if (
        materialization.get("status") != "SEALED"
        or not isinstance(digest, str)
        or base._DIGEST.fullmatch(digest) is None
        or not callable(record_success)
    ):
        raise ProviderIndependentDevelopmentCampaignError(
            "provider development success evidence is invalid"
        )
    record_success(manifest_sha256=digest)


__all__ = [
    "ProviderIndependentDevelopmentCampaignError",
    "materialize_provider_development_artifacts",
    "record_provider_development_success",
    "validate_provider_development_campaign_identity",
]
