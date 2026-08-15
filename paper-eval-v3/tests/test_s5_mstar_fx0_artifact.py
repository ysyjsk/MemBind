"""TDD contracts for the independent production-path FX0 artifact.

The legacy ``fx0_mechanism_fixture`` artifact remains a test-double self-test.
These tests ensure a production artifact has a distinct schema, non-circular
core identity, hash-only case rows, and exact all-false authority.
"""

from __future__ import annotations

import copy
import asyncio

import pytest

from paper_eval.fx0_mechanism_fixture import (
    FX0_REQUIRED_FAILURE_MODES,
    FX0_REQUIRED_TRANSITIONS,
)
from paper_eval.s5_mstar_fx0_artifact import (
    PRODUCTION_FX0_SCHEMA,
    S5MStarProductionFx0ArtifactError,
    _validate_production_binding,
    verify_s5_mstar_fx0_artifact,
)
from paper_eval.s5_mstar_production_adapter import S5MStarProductionAdapter
from paper_eval.s5_mstar_production_core_identity import (
    build_s5_mstar_production_core_identity,
)


def _core_identity() -> dict[str, object]:
    return build_s5_mstar_production_core_identity(
        graphiti_version="0.29.3",
        graphiti_commit="021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        graphiti_semantic_api_sha256="a" * 64,
        graphiti_semantic_identity_artifact_sha256="b" * 64,
        runtime_factory_entrypoint="native_characterization_runtime.build_u0_graphiti_from_env",
        runtime_factory_source_sha256="c" * 64,
        pipeline_source_sha256="d" * 64,
        pipeline_test_source_sha256="e" * 64,
        adapter_source_sha256="f" * 64,
        adapter_test_source_sha256="1" * 64,
        semantic_runtime_source_sha256="2" * 64,
        semantic_runtime_test_source_sha256="3" * 64,
        semantic_binding_source_sha256="4" * 64,
        semantic_binding_test_source_sha256="5" * 64,
        durable_store_source_sha256="6" * 64,
        durable_store_test_source_sha256="7" * 64,
        runtime_config_sha256="8" * 64,
    )


def test_legacy_self_test_artifact_is_rejected_by_production_verifier() -> None:
    legacy = {
        "protocol_version": "membind-paper-eval-v3",
        "git_commit": "deadbeef",
        "run_id": "fx0-legacy",
        "status": "finalized",
        "payload": {
            "schema_version": "membind.paper-eval-v3.fx0-mechanism-fixture.v1",
            "lane": "FX0_DETERMINISTIC_MECHANISM_FIXTURE",
            "framework_verdict": "HARNESS_SELF_TEST_PASS",
        },
        "payload_sha256": "0" * 64,
    }
    with pytest.raises(S5MStarProductionFx0ArtifactError, match="schema"):
        verify_s5_mstar_fx0_artifact(legacy)


def test_production_verifier_requires_external_manifest_binding() -> None:
    with pytest.raises(S5MStarProductionFx0ArtifactError, match="context"):
        verify_s5_mstar_fx0_artifact({"schema_version": PRODUCTION_FX0_SCHEMA})


def test_transition_inventory_is_imported_from_frozen_fx0_contract() -> None:
    assert len(FX0_REQUIRED_TRANSITIONS) == 9
    assert set(FX0_REQUIRED_FAILURE_MODES) == {
        "LOST_PUBLICATION",
        "DUPLICATE_PUBLICATION",
        "PARTIAL_PUBLICATION",
    }


def test_production_binding_rejects_arbitrary_callback_doubles() -> None:
    core = _core_identity()

    async def prepare(*_args):
        return None

    async def bind(*_args):
        return None

    async def reset(_providers):
        return None

    adapter = S5MStarProductionAdapter(
        production_core_identity=core,
        production_core_identity_sha256=core["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=lambda: ({}, []),
        persist_event=lambda _event: asyncio.sleep(0),
        source_decoder=lambda _case, _providers: (),
        reset_case=reset,
        witness_snapshot=lambda _case_id: {},
    )
    with pytest.raises(
        S5MStarProductionFx0ArtifactError,
        match="pinned_graphiti_semantic_artifact|pinned_graphiti_runtime_binding",
    ):
        _validate_production_binding(adapter, production_core_identity=core)


def test_authority_tamper_is_fail_closed_even_when_hashes_are_recomputed() -> None:
    payload = {
        "schema_version": PRODUCTION_FX0_SCHEMA,
        "authority": {"s5_live_execution_authorized": True},
    }
    artifact = {
        "protocol_version": "membind-paper-eval-v3",
        "git_commit": "deadbeef",
        "run_id": "s5-mstar-fx0-test",
        "status": "finalized",
        "payload": payload,
        "payload_sha256": "0" * 64,
    }
    with pytest.raises(S5MStarProductionFx0ArtifactError):
        verify_s5_mstar_fx0_artifact(
            artifact,
            expected_input_bindings={"production_core_identity_sha256": "a" * 64},
            expected_fixture_manifest_sha256="b" * 64,
        )
