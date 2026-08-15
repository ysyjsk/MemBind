"""RED contracts for the non-circular M* production-core identity.

These tests intentionally describe the production boundary before the
implementation exists.  The legacy FX0 self-test identity must remain a
separate contract and cannot be promoted by changing one status field.
"""

from __future__ import annotations

import pytest

from paper_eval.s5_mstar_production_core_identity import (
    S5MStarProductionCoreIdentityError,
    build_s5_mstar_production_core_identity,
    verify_s5_mstar_production_core_identity,
)


SHA = "a" * 64


def _kwargs() -> dict[str, object]:
    return {
        "graphiti_version": "0.29.3",
        "graphiti_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        "graphiti_semantic_api_sha256": SHA,
        "graphiti_semantic_identity_artifact_sha256": "b" * 64,
        "runtime_factory_entrypoint": "native_characterization_runtime.build_u0_graphiti_from_env",
        "runtime_factory_source_sha256": "c" * 64,
        "pipeline_source_sha256": "d" * 64,
        "pipeline_test_source_sha256": "e" * 64,
        "adapter_source_sha256": "f" * 64,
        "adapter_test_source_sha256": "1" * 64,
        "semantic_runtime_source_sha256": "2" * 64,
        "semantic_runtime_test_source_sha256": "3" * 64,
        "semantic_binding_source_sha256": "4" * 64,
        "semantic_binding_test_source_sha256": "5" * 64,
        "durable_store_source_sha256": "6" * 64,
        "durable_store_test_source_sha256": "7" * 64,
        "runtime_config_sha256": "8" * 64,
    }


def test_core_identity_is_frozen_without_fx0_self_reference() -> None:
    identity = build_s5_mstar_production_core_identity(**_kwargs())
    assert identity["status"] == "FROZEN"
    assert identity["method"] == "M*"
    assert "fx0_parity_artifact_sha256" not in identity
    assert identity["identity_sha256"] == verify_s5_mstar_production_core_identity(
        identity
    )["identity_sha256"]


def test_core_identity_rejects_private_or_hash_mutation() -> None:
    identity = build_s5_mstar_production_core_identity(**_kwargs())
    tampered = dict(identity)
    tampered["runtime_config"] = {"api_key": "must-not-be-accepted"}
    with pytest.raises(S5MStarProductionCoreIdentityError):
        verify_s5_mstar_production_core_identity(tampered)


def test_core_identity_rejects_fx0_field_injection() -> None:
    identity = build_s5_mstar_production_core_identity(**_kwargs())
    tampered = dict(identity)
    tampered["fx0_parity_artifact_sha256"] = "9" * 64
    with pytest.raises(S5MStarProductionCoreIdentityError):
        verify_s5_mstar_production_core_identity(tampered)

