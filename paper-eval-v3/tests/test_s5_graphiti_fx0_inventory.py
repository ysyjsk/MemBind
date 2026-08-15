"""TDD contracts for the complete production Graphiti FX0 inventory."""

from __future__ import annotations

import asyncio

from paper_eval.fx0_mechanism_fixture import (
    FX0_REQUIRED_FAILURE_MODES,
    FX0_REQUIRED_TRANSITIONS,
)
from paper_eval.s5_graphiti_fx0_environment import S5GraphitiFx0ControlledEnvironment
from paper_eval.s5_graphiti_fx0_inventory import build_s5_graphiti_fx0_inventory
from paper_eval.s5_mstar_fx0_artifact import (
    PINNED_GRAPHITI_SEMANTIC_API_SHA256,
    PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256,
    build_s5_mstar_fx0_artifact,
    derive_s5_mstar_fx0_fixture_manifest,
    verify_s5_mstar_fx0_artifact,
)
from paper_eval.s5_mstar_production_core_identity import (
    build_s5_mstar_production_core_identity,
)


CORE_IDENTITY = build_s5_mstar_production_core_identity(
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


def _spec():
    return build_s5_graphiti_fx0_inventory(
        run_id="s5-graphiti-fx0-inventory-test",
        parent_protocol_sha256="a" * 64,
        amendment_sha256="b" * 64,
        current_stage_pointer_sha256="c" * 64,
        production_core_identity_sha256=CORE_IDENTITY["identity_sha256"],
    )


def test_inventory_is_exactly_the_frozen_eleven_case_transition_set() -> None:
    spec = _spec()
    assert len(spec.cases) == 11
    assert {case.transition for case in spec.cases} == set(FX0_REQUIRED_TRANSITIONS)
    assert {
        case.expected_error_code
        for case in spec.cases
        if case.expected_error_code in FX0_REQUIRED_FAILURE_MODES
    } == set(FX0_REQUIRED_FAILURE_MODES)
    assert all(case.source_sequence == 0 for case in spec.cases)
    assert all(set(case.source) == {"episodes"} for case in spec.cases)
    assert all(
        not any(
            token in repr(case.providers.transaction_io_schedule).casefold()
            or token in repr(case.providers.publication_sink_schedule).casefold()
            for token in (
                "expected",
                "error_code",
                "lost_publication",
                "duplicate_publication",
                "partial_publication",
                "verdict",
            )
        )
        for case in spec.cases
    )


def test_inventory_exact_parity_runs_through_one_pinned_environment() -> None:
    spec = _spec()
    environment = S5GraphitiFx0ControlledEnvironment()
    adapter = environment.build_adapter(production_core_identity=CORE_IDENTITY)

    for case in spec.cases:
        execution = asyncio.run(
            adapter.execute_fixture_case_with_evidence(
                case.execution_input(), case.providers
            )
        )
        assert execution.outcome.status == case.expected_status, case.case_id
        assert execution.outcome.error_code == case.expected_error_code, case.case_id
        assert (
            execution.outcome.canonical_logical_state
            == case.expected_canonical_logical_state
        ), case.case_id
        assert (
            execution.outcome.publication_history
            == case.expected_publication_history
        ), case.case_id

        if case.transition == "COMPATIBLE_DUPLICATE_UUID_COALESCING":
            assert environment.runtime.resolved_node_coalescing_observations == [
                {"pre_count": 2, "post_count": 1}
            ]
        if case.transition == "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED":
            assert environment.runtime.resolved_node_coalescing_observations == [
                {"pre_count": 2, "post_count": None}
            ]
            assert not any(
                event.get("event") == "commit_completed"
                for event in environment.fixture.events
            )

        if case.transition == "SOURCE_ORDERED_PUBLICATION":
            assert execution.source_count == 2
            assert execution.execution_shape["prepare_overlap_observed"] is True
            assert execution.execution_shape["published_source_order_observed"] is True
        if case.transition == "PREPARE_TO_BIND_STATE_CHANGE":
            assert execution.source_count == 2
            assert (
                execution.execution_shape[
                    "prepare_to_bind_state_change_observed"
                ]
                is True
            )
        if case.transition == "RETRY_IDEMPOTENCE":
            assert execution.attempt_count == 2
            assert execution.execution_shape["retry_replay_observed"] is True
            assert execution.execution_shape["single_logical_publication_observed"] is True
        if case.transition == "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION":
            assert (
                execution.execution_shape["publication_fault_detection_observed"]
                is True
            )


def test_complete_inventory_builds_independently_verifiable_artifact() -> None:
    spec = _spec()
    manifest = derive_s5_mstar_fx0_fixture_manifest(spec)
    bindings = {
        "parent_protocol_sha256": spec.parent_protocol_sha256,
        "amendment_sha256": spec.amendment_sha256,
        "current_stage_pointer_sha256": spec.current_stage_pointer_sha256,
        "production_core_identity_sha256": CORE_IDENTITY["identity_sha256"],
        "graphiti_semantic_api_identity_sha256": CORE_IDENTITY[
            "graphiti_semantic_api_sha256"
        ],
        "graphiti_semantic_identity_artifact_sha256": CORE_IDENTITY[
            "graphiti_semantic_identity_artifact_sha256"
        ],
        "fx0_fixture_manifest_sha256": manifest[
            "fx0_fixture_manifest_sha256"
        ],
        "execution_input_set_sha256": manifest["execution_input_set_sha256"],
        "oracle_set_sha256": manifest["oracle_set_sha256"],
        "controlled_provider_set_sha256": manifest[
            "controlled_provider_set_sha256"
        ],
        "adapter_source_sha256": CORE_IDENTITY["adapter_source_sha256"],
        "pipeline_source_sha256": CORE_IDENTITY["pipeline_source_sha256"],
        "semantic_runtime_source_sha256": CORE_IDENTITY[
            "semantic_runtime_source_sha256"
        ],
        "semantic_binding_source_sha256": CORE_IDENTITY[
            "semantic_binding_source_sha256"
        ],
    }
    environment = S5GraphitiFx0ControlledEnvironment()
    artifact = build_s5_mstar_fx0_artifact(
        spec=spec,
        mechanism=environment.build_adapter(
            production_core_identity=CORE_IDENTITY
        ),
        production_core_identity=CORE_IDENTITY,
        expected_input_bindings=bindings,
        git_commit="deadbeef",
    )

    verified = verify_s5_mstar_fx0_artifact(
        artifact,
        expected_input_bindings=bindings,
        expected_fixture_manifest_sha256=bindings[
            "fx0_fixture_manifest_sha256"
        ],
    )
    assert verified["payload"]["fixture_count"] == 11
    assert verified["payload"]["verdict"] == (
        "PRODUCTION_PATH_EXACT_PARITY_PASS"
    )
