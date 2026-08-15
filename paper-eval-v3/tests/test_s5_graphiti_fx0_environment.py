"""TDD contracts for the generic production FX0 controlled environment."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from paper_eval.fx0_mechanism_fixture import ControlledNondeterminism, Fx0ExecutionCase
from paper_eval.s5_graphiti_controlled_fixture import build_controlled_graphiti_fixture
from paper_eval.s5_graphiti_fx0_environment import (
    S5GraphitiFx0ControlledEnvironment,
    S5GraphitiFx0EnvironmentError,
)
from paper_eval.s5_mstar_production_core_identity import (
    build_s5_mstar_production_core_identity,
)


CORE_IDENTITY = build_s5_mstar_production_core_identity(
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


def _source(content: str = "Alice works at Acme.") -> dict[str, object]:
    return {
        "episodes": [
            {
                "uuid": "episode-0",
                "name": "episode-0",
                "content": content,
                "source": "text",
                "source_description": "controlled FX0 episode",
                "group_id": "controlled-db",
                "valid_at": "2026-01-01T00:00:00Z",
                "edge_types": [],
            }
        ]
    }


def _providers() -> ControlledNondeterminism:
    fixture = build_controlled_graphiti_fixture()
    return ControlledNondeterminism(
        llm_responses=fixture.providers.llm_responses,
        embeddings={"vector": [1.0, 0.0]},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={"nodes": []},
        candidate_sets=(),
        transaction_io_schedule={"fail_after_callback_attempts": []},
        publication_sink_schedule={"actions_by_source": ["APPEND"]},
    )


def _case(*, case_id: str = "case-a", content: str = "Alice works at Acme."):
    return Fx0ExecutionCase(
        case_id=case_id,
        source_sequence=0,
        source=_source(content),
    )


def test_environment_factory_is_independent_of_case_identity_and_source() -> None:
    environment = S5GraphitiFx0ControlledEnvironment()
    providers = _providers()

    active_a = environment.controlled_provider_factory(providers)
    active_b = environment.controlled_provider_factory(providers)

    assert active_a.provider_plan_sha256 == active_b.provider_plan_sha256
    assert active_a.logical_times_ns == active_b.logical_times_ns
    assert active_a.transaction_io_schedule == active_b.transaction_io_schedule
    assert active_a.publication_sink_schedule == active_b.publication_sink_schedule

    decoded_a = environment.source_decoder(_case(), active_a)
    decoded_b = environment.source_decoder(
        _case(case_id="unrelated-id", content="Bob joined Beta."), active_b
    )
    assert decoded_a[0].source_sha256 != decoded_b[0].source_sha256
    assert active_a.provider_plan_sha256 == active_b.provider_plan_sha256


@pytest.mark.parametrize(
    "forbidden",
    (
        "transition",
        "error_code",
        "expected_status",
        "expected_error_code",
        "expected_state",
        "expected_history",
        "fault_mode",
        "raise",
        "result",
        "verdict",
    ),
)
def test_source_decoder_rejects_transition_error_and_fault_directives(
    forbidden: str,
) -> None:
    environment = S5GraphitiFx0ControlledEnvironment()
    active = environment.controlled_provider_factory(_providers())
    source = _source()
    source[forbidden] = "injected"
    case = Fx0ExecutionCase(
        case_id=f"bad-{forbidden}", source_sequence=0, source=source
    )

    with pytest.raises(
        S5GraphitiFx0EnvironmentError,
        match="FX0_SOURCE_DIRECTIVE_FORBIDDEN",
    ):
        environment.source_decoder(case, active)


def test_source_decoder_rejects_nested_control_directive() -> None:
    environment = S5GraphitiFx0ControlledEnvironment()
    active = environment.controlled_provider_factory(_providers())
    source = deepcopy(_source())
    source["episodes"][0]["metadata"] = {"expected_status": "PASS"}
    case = Fx0ExecutionCase(case_id="nested-directive", source_sequence=0, source=source)

    with pytest.raises(
        S5GraphitiFx0EnvironmentError,
        match="FX0_SOURCE_DIRECTIVE_FORBIDDEN",
    ):
        environment.source_decoder(case, active)


def test_environment_runs_real_graphiti_and_publishes_canonical_snapshot() -> None:
    environment = S5GraphitiFx0ControlledEnvironment()
    adapter = environment.build_adapter(production_core_identity=CORE_IDENTITY)

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(_case(), _providers())
    )

    assert execution.outcome.status == "PASS"
    assert execution.outcome.error_code is None
    assert execution.outcome.publication_history == (
        {"source_sequence": 0, "event": "publish"},
    )
    assert environment.fixture.call_order == [
        "extract_nodes",
        "resolve_extracted_nodes",
        "extract_edges",
        "resolve_edge_pointers",
        "resolve_extracted_edges",
        "extract_attributes_from_nodes",
        "process_episode_data",
    ]


def test_environment_publication_schedule_is_detected_from_observed_history() -> None:
    environment = S5GraphitiFx0ControlledEnvironment()
    adapter = environment.build_adapter(production_core_identity=CORE_IDENTITY)
    providers = _providers()
    providers = ControlledNondeterminism(
        llm_responses=providers.llm_responses,
        embeddings=providers.embeddings,
        logical_times=providers.logical_times,
        initial_state=providers.initial_state,
        candidate_sets=providers.candidate_sets,
        transaction_io_schedule=providers.transaction_io_schedule,
        publication_sink_schedule={"actions_by_source": ["DROP"]},
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(_case(), providers)
    )

    assert execution.outcome.status == "FAIL_CLOSED"
    assert execution.outcome.error_code == "LOST_PUBLICATION"
    assert execution.outcome.publication_history == ()
    assert execution.execution_shape["publication_fault_detection_observed"] is True


def test_environment_transaction_schedule_drives_real_idempotent_retry() -> None:
    environment = S5GraphitiFx0ControlledEnvironment()
    adapter = environment.build_adapter(production_core_identity=CORE_IDENTITY)
    providers = _providers()
    providers = ControlledNondeterminism(
        llm_responses=providers.llm_responses,
        embeddings=providers.embeddings,
        logical_times=providers.logical_times,
        initial_state=providers.initial_state,
        candidate_sets=providers.candidate_sets,
        transaction_io_schedule={"fail_after_callback_attempts": [1]},
        publication_sink_schedule=providers.publication_sink_schedule,
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(_case(), providers)
    )

    assert execution.outcome.status == "PASS"
    assert execution.attempt_count == 2
    assert execution.execution_shape["transaction_attempt_count"] == 2
    assert execution.execution_shape["retry_replay_observed"] is True
    assert execution.outcome.publication_history == (
        {"source_sequence": 0, "event": "publish"},
    )


def test_two_real_sources_overlap_publish_in_order_and_observe_state_change() -> None:
    environment = S5GraphitiFx0ControlledEnvironment()
    adapter = environment.build_adapter(production_core_identity=CORE_IDENTITY)
    providers = _providers()
    llm_responses = deepcopy(dict(providers.llm_responses))
    llm_responses["__prepare_rendezvous_parties__"] = 2
    providers = ControlledNondeterminism(
        llm_responses=llm_responses,
        embeddings=providers.embeddings,
        logical_times=(
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:01Z",
        ),
        initial_state=providers.initial_state,
        candidate_sets=providers.candidate_sets,
        transaction_io_schedule=providers.transaction_io_schedule,
        publication_sink_schedule={"actions_by_source": ["APPEND", "APPEND"]},
    )
    source = _source()
    second = deepcopy(source["episodes"][0])
    second.update(
        {
            "uuid": "episode-1",
            "name": "episode-1",
            "content": "Alice joined Acme one second later.",
            "valid_at": "2026-01-01T00:00:01Z",
        }
    )
    source["episodes"].append(second)
    case = Fx0ExecutionCase(
        case_id="two-source-state-change",
        source_sequence=0,
        source=source,
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, providers)
    )

    assert execution.outcome.status == "PASS"
    assert execution.source_count == 2
    assert execution.execution_shape["prepare_overlap_observed"] is True
    assert execution.execution_shape["published_source_order_observed"] is True
    assert execution.execution_shape["prepare_to_bind_state_change_observed"] is True
    assert execution.outcome.publication_history == (
        {"source_sequence": 0, "event": "publish"},
        {"source_sequence": 1, "event": "publish"},
    )
