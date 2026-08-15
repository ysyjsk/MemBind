"""TDD contracts for the independent production-path FX0 artifact.

The legacy ``fx0_mechanism_fixture`` artifact remains a test-double self-test.
These tests ensure a production artifact has a distinct schema, non-circular
core identity, hash-only case rows, and exact all-false authority.
"""

from __future__ import annotations

import copy
import asyncio
from dataclasses import replace

import pytest

import paper_eval.s5_mstar_fx0_artifact as fx0_artifact
from paper_eval.artifacts import finalize_envelope, payload_sha256
from paper_eval.fx0_mechanism_fixture import (
    ControlledNondeterminism,
    FX0_REQUIRED_FAILURE_MODES,
    FX0_REQUIRED_TRANSITIONS,
    Fx0FixtureCase,
    Fx0FixtureSpec,
)
from paper_eval.s5_mstar_fx0_artifact import (
    PINNED_GRAPHITI_SEMANTIC_API_SHA256,
    PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256,
    PRODUCTION_FX0_SCHEMA,
    S5MStarProductionFx0ArtifactError,
    _validate_production_binding,
    verify_s5_mstar_fx0_artifact,
)
from paper_eval.s5_graphiti_fx0_environment import (
    S5GraphitiFx0ControlledEnvironment,
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


def _pinned_core_identity() -> dict[str, object]:
    return build_s5_mstar_production_core_identity(
        graphiti_version="0.29.3",
        graphiti_commit="021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        graphiti_semantic_api_sha256=PINNED_GRAPHITI_SEMANTIC_API_SHA256,
        graphiti_semantic_identity_artifact_sha256=(
            PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256
        ),
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


def _fixture_spec() -> Fx0FixtureSpec:
    core = _core_identity()
    cases = []
    inventory = [
        ("alias", "ENTITY_ALIAS_CANONICAL_MERGE", "PASS", None),
        (
            "compatible-duplicate",
            "COMPATIBLE_DUPLICATE_UUID_COALESCING",
            "PASS",
            None,
        ),
        (
            "conflicting-duplicate",
            "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED",
            "FAIL_CLOSED",
            "CONFLICTING_DUPLICATE_UUID",
        ),
        ("relation", "RELATION_RESOLUTION", "PASS", None),
        ("temporal", "TEMPORAL_INVALIDATION_UPDATE", "PASS", None),
        ("prepare-bind", "PREPARE_TO_BIND_STATE_CHANGE", "PASS", None),
        ("source-order", "SOURCE_ORDERED_PUBLICATION", "PASS", None),
        ("retry", "RETRY_IDEMPOTENCE", "PASS", None),
        (
            "publication-lost",
            "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
            "FAIL_CLOSED",
            "LOST_PUBLICATION",
        ),
        (
            "publication-duplicate",
            "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
            "FAIL_CLOSED",
            "DUPLICATE_PUBLICATION",
        ),
        (
            "publication-partial",
            "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
            "FAIL_CLOSED",
            "PARTIAL_PUBLICATION",
        ),
    ]
    for source_sequence, (case_id, transition, status, error_code) in enumerate(
        inventory
    ):
        cases.append(
            Fx0FixtureCase(
                case_id=case_id,
                transition=transition,
                source_sequence=source_sequence,
                source={"value": f"source-{case_id}"},
                providers=ControlledNondeterminism(
                    llm_responses={"value": f"llm-{case_id}"},
                    transaction_io_schedule={"attempt": source_sequence + 1},
                    publication_sink_schedule={"mode": case_id},
                ),
                expected_status=status,
                expected_error_code=error_code,
                expected_canonical_logical_state={
                    "nodes": [{"key": case_id}] if status == "PASS" else [],
                    "relationships": [],
                },
                expected_publication_history=(
                    ({"source_sequence": source_sequence, "event": "publish"},)
                    if status == "PASS"
                    else ()
                ),
            )
        )
    return Fx0FixtureSpec(
        run_id="fx0-production-binding-test",
        parent_protocol_sha256="b" * 64,
        amendment_sha256="c" * 64,
        current_stage_pointer_sha256="d" * 64,
        production_path_identity={
            "status": "FROZEN",
            "method": "M_STAR",
            "identity_sha256": core["identity_sha256"],
        },
        cases=tuple(cases),
    )


def _input_bindings(spec: Fx0FixtureSpec) -> dict[str, str]:
    core = _core_identity()
    manifest = fx0_artifact.derive_s5_mstar_fx0_fixture_manifest(spec)
    return {
        "parent_protocol_sha256": spec.parent_protocol_sha256,
        "amendment_sha256": spec.amendment_sha256,
        "current_stage_pointer_sha256": spec.current_stage_pointer_sha256,
        "production_core_identity_sha256": str(core["identity_sha256"]),
        "graphiti_semantic_api_identity_sha256": str(
            core["graphiti_semantic_api_sha256"]
        ),
        "graphiti_semantic_identity_artifact_sha256": str(
            core["graphiti_semantic_identity_artifact_sha256"]
        ),
        "fx0_fixture_manifest_sha256": manifest["fx0_fixture_manifest_sha256"],
        "execution_input_set_sha256": manifest["execution_input_set_sha256"],
        "oracle_set_sha256": manifest["oracle_set_sha256"],
        "controlled_provider_set_sha256": manifest[
            "controlled_provider_set_sha256"
        ],
        "adapter_source_sha256": str(core["adapter_source_sha256"]),
        "pipeline_source_sha256": str(core["pipeline_source_sha256"]),
        "semantic_runtime_source_sha256": str(
            core["semantic_runtime_source_sha256"]
        ),
        "semantic_binding_source_sha256": str(
            core["semantic_binding_source_sha256"]
        ),
    }


def _valid_production_artifact(
    spec: Fx0FixtureSpec,
) -> tuple[dict[str, object], dict[str, str]]:
    core = _core_identity()
    bindings = _input_bindings(spec)
    manifest = fx0_artifact.derive_s5_mstar_fx0_fixture_manifest(spec)
    bindings_by_case = {
        row["case_identity_sha256"]: row for row in manifest["case_bindings"]
    }
    rows = []
    for case in spec.cases:
        case_identity = payload_sha256({"case_id": case.case_id})
        bound = bindings_by_case[case_identity]
        source_count = (
            2
            if case.transition
            in {"SOURCE_ORDERED_PUBLICATION", "PREPARE_TO_BIND_STATE_CHANGE"}
            else 1
        )
        passing = case.expected_status == "PASS"
        shape = {
            "source_count": source_count,
            "attempt_count": 2 if case.transition == "RETRY_IDEMPOTENCE" else 1,
            "transaction_attempt_count": 1,
            "prepare_overlap_observed": (
                case.transition == "SOURCE_ORDERED_PUBLICATION"
            ),
            "published_source_count": source_count if passing else 0,
            "published_source_order_observed": (
                case.transition == "SOURCE_ORDERED_PUBLICATION"
            ),
            "prepare_to_bind_state_change_observed": (
                case.transition == "PREPARE_TO_BIND_STATE_CHANGE"
            ),
            "single_logical_publication_observed": (
                case.transition == "RETRY_IDEMPOTENCE"
            ),
            "retry_replay_observed": (
                case.transition == "RETRY_IDEMPOTENCE"
            ),
            "publication_fault_detection_observed": (
                case.transition
                == "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION"
            ),
        }
        rows.append(
            {
                **bound,
                "observed_outcome_sha256": bound["oracle_outcome_sha256"],
                "outcome_class_sha256": payload_sha256(
                    {
                        "status": case.expected_status,
                        "error_code": case.expected_error_code,
                    }
                ),
                "pipeline_evidence_sha256": payload_sha256(
                    {"case_identity_sha256": case_identity}
                ),
                "execution_shape": shape,
                "execution_shape_sha256": payload_sha256(shape),
                "exact_status_error_parity": True,
                "exact_canonical_state_parity": True,
                "exact_publication_history_parity": True,
            }
        )
    payload = {
        "schema_version": fx0_artifact.PRODUCTION_FX0_SCHEMA,
        "lane": fx0_artifact.PRODUCTION_FX0_LANE,
        "verdict": fx0_artifact.PRODUCTION_FX0_VERDICT,
        "evidence_scope": fx0_artifact.PRODUCTION_FX0_SCOPE,
        "fixture_count_policy": fx0_artifact.FIXTURE_COUNT_POLICY,
        "fixture_count": len(rows),
        "covered_transitions": sorted(FX0_REQUIRED_TRANSITIONS),
        "covered_publication_failure_mode_hashes": sorted(
            payload_sha256({"status": "FAIL_CLOSED", "error_code": mode})
            for mode in FX0_REQUIRED_FAILURE_MODES
        ),
        "controlled_nondeterminism_providers": list(
            fx0_artifact.PRODUCTION_CONTROLLED_PROVIDER_NAMES
        ),
        "production_core_identity": core,
        "input_bindings": bindings,
        "case_evidence": rows,
        "case_evidence_sha256": payload_sha256(rows),
        "parity": copy.deepcopy(fx0_artifact._PARITY),
        "claims": copy.deepcopy(fx0_artifact._CLAIMS),
        "legacy_boundary": copy.deepcopy(fx0_artifact._LEGACY_BOUNDARY),
        "authority": copy.deepcopy(fx0_artifact._AUTHORITY),
    }
    return (
        finalize_envelope(
            payload=payload,
            protocol_version=fx0_artifact.PROTOCOL_VERSION,
            git_commit="deadbeef",
            run_id=spec.run_id,
        ),
        bindings,
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
        match=(
            "production_adapter_boundary_incomplete|"
            "pinned_graphiti_semantic_artifact|pinned_graphiti_runtime_binding"
        ),
    ):
        _validate_production_binding(adapter, production_core_identity=core)


def test_production_binding_accepts_one_generic_environment_owner() -> None:
    core = _pinned_core_identity()
    environment = S5GraphitiFx0ControlledEnvironment()
    adapter = environment.build_adapter(production_core_identity=core)

    assert _validate_production_binding(
        adapter, production_core_identity=core
    ) is adapter


def test_production_binding_rejects_mixed_controlled_environment_hook_owners() -> None:
    core = _pinned_core_identity()
    environment = S5GraphitiFx0ControlledEnvironment()
    other = S5GraphitiFx0ControlledEnvironment()
    adapter = S5MStarProductionAdapter(
        production_core_identity=core,
        production_core_identity_sha256=core["identity_sha256"],
        semantic_prepare=environment.runtime.prepare,
        latest_state_bind=environment.runtime.bind,
        snapshot=environment.snapshot,
        persist_event=environment.persist_event,
        clock_ns=environment.clock_ns,
        source_decoder=environment.source_decoder,
        reset_case=environment.reset_case,
        witness_snapshot=other.witness_snapshot,
        controlled_provider_factory=environment.controlled_provider_factory,
        publication_fault_detector=environment.publication_fault_detector,
    )

    with pytest.raises(
        S5MStarProductionFx0ArtifactError,
        match="controlled_environment_binding_invalid",
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


def test_fixture_manifest_is_deterministic_and_binds_production_schedules() -> None:
    spec = _fixture_spec()
    forward = fx0_artifact.derive_s5_mstar_fx0_fixture_manifest(spec)
    reverse = fx0_artifact.derive_s5_mstar_fx0_fixture_manifest(
        replace(spec, cases=tuple(reversed(spec.cases)))
    )

    assert forward == reverse
    assert len(forward["case_bindings"]) == 11
    assert list(forward["case_bindings"]) == sorted(
        forward["case_bindings"], key=lambda row: row["case_identity_sha256"]
    )
    assert fx0_artifact.derive_s5_mstar_fx0_fixture_manifest(
        replace(spec, run_id="fx0-production-binding-retry")
    ) == forward

    first = spec.cases[0]
    changed = replace(
        first,
        providers=replace(
            first.providers,
            transaction_io_schedule={"attempt": 999},
            publication_sink_schedule={"mode": "dropped"},
        ),
    )
    changed_manifest = fx0_artifact.derive_s5_mstar_fx0_fixture_manifest(
        replace(spec, cases=(changed, *spec.cases[1:]))
    )
    assert changed_manifest["execution_input_set_sha256"] == forward[
        "execution_input_set_sha256"
    ]
    assert changed_manifest["oracle_set_sha256"] == forward["oracle_set_sha256"]
    assert changed_manifest["controlled_provider_set_sha256"] != forward[
        "controlled_provider_set_sha256"
    ]
    assert changed_manifest["fx0_fixture_manifest_sha256"] != forward[
        "fx0_fixture_manifest_sha256"
    ]


def test_fixture_manifest_preserves_case_to_provider_association() -> None:
    spec = _fixture_spec()
    first, second, *rest = spec.cases
    swapped = replace(
        spec,
        cases=(
            replace(first, providers=second.providers),
            replace(second, providers=first.providers),
            *rest,
        ),
    )

    baseline = fx0_artifact.derive_s5_mstar_fx0_fixture_manifest(spec)
    changed = fx0_artifact.derive_s5_mstar_fx0_fixture_manifest(swapped)

    assert changed["controlled_provider_set_sha256"] != baseline[
        "controlled_provider_set_sha256"
    ]
    assert changed["fx0_fixture_manifest_sha256"] != baseline[
        "fx0_fixture_manifest_sha256"
    ]


def test_production_verifier_rederives_fixture_bindings_from_case_evidence() -> None:
    artifact, bindings = _valid_production_artifact(_fixture_spec())
    verified = verify_s5_mstar_fx0_artifact(
        artifact,
        expected_input_bindings=bindings,
        expected_fixture_manifest_sha256=bindings[
            "fx0_fixture_manifest_sha256"
        ],
    )
    assert verified["payload"]["input_bindings"] == bindings

    tampered = copy.deepcopy(artifact)
    tampered["payload"]["case_evidence"][0]["execution_input_sha256"] = "0" * 64
    tampered["payload"]["case_evidence_sha256"] = payload_sha256(
        tampered["payload"]["case_evidence"]
    )
    tampered["payload_sha256"] = payload_sha256(tampered["payload"])
    with pytest.raises(
        S5MStarProductionFx0ArtifactError,
        match="artifact_fixture_binding_mismatch",
    ):
        verify_s5_mstar_fx0_artifact(
            tampered,
            expected_input_bindings=bindings,
            expected_fixture_manifest_sha256=bindings[
                "fx0_fixture_manifest_sha256"
            ],
        )


@pytest.mark.parametrize(
    "field",
    [
        "parent_protocol_sha256",
        "amendment_sha256",
        "current_stage_pointer_sha256",
        "fx0_fixture_manifest_sha256",
        "execution_input_set_sha256",
        "oracle_set_sha256",
        "controlled_provider_set_sha256",
    ],
)
def test_fixture_binding_drift_fails_before_adapter_validation_or_execution(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    spec = _fixture_spec()
    bindings = _input_bindings(spec)
    bindings[field] = "0" * 64
    adapter_validation_called = False

    def forbidden_adapter_validation(*_args, **_kwargs):
        nonlocal adapter_validation_called
        adapter_validation_called = True
        raise AssertionError("adapter validation reached before fixture binding check")

    monkeypatch.setattr(
        fx0_artifact,
        "_validate_production_binding",
        forbidden_adapter_validation,
    )

    with pytest.raises(
        S5MStarProductionFx0ArtifactError,
        match="fixture_binding_mismatch",
    ):
        asyncio.run(
            fx0_artifact.build_s5_mstar_fx0_artifact_async(
                spec=spec,
                mechanism=object(),
                production_core_identity=_core_identity(),
                expected_input_bindings=bindings,
                git_commit="deadbeef",
            )
        )
    assert adapter_validation_called is False
