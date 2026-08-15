"""RED contract for binding the production adapter to real pinned Graphiti.

The FX0 case still carries only the public legacy provider projection.  A
production adapter must explicitly decode that projection into the typed
controlled Graphiti provider before invoking the real semantic runtime.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.fx0_mechanism_fixture import ControlledNondeterminism, Fx0ExecutionCase
from paper_eval.s5_graphiti_controlled_fixture import (
    ControlledGraphitiProviders,
    build_controlled_graphiti_fixture,
)
from paper_eval.s5_mstar_production_adapter import (
    Fx0DecodedSource,
    S5MStarProductionAdapter,
)
from paper_eval.s5_graphiti_mstar_semantics import S5GraphitiMStarSemanticRuntime
from graphiti_core.nodes import EpisodeType, EpisodicNode
from datetime import datetime, timezone
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


def test_production_adapter_binds_typed_provider_to_real_graphiti_fixture() -> None:
    fixture = build_controlled_graphiti_fixture(edge_types=("WorksAt",))
    callback_providers: list[object] = []
    journal: list[dict[str, object]] = []

    legacy_providers = ControlledNondeterminism(
        llm_responses={"opaque": "not an oracle"},
        embeddings={"opaque": [0.0, 1.0]},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={},
        candidate_sets=(),
    )

    def provider_factory(
        _providers: ControlledNondeterminism,
    ) -> ControlledGraphitiProviders:
        return fixture.providers

    def decode(
        case: Fx0ExecutionCase, providers: ControlledGraphitiProviders
    ) -> tuple[Fx0DecodedSource, ...]:
        assert isinstance(providers, ControlledGraphitiProviders)
        return (
            Fx0DecodedSource(
                source_sha256=payload_sha256(case.source),
                opaque_source=fixture._source(0),
                logical_time_ns=providers.logical_time_ns,
            ),
        )

    async def reset(_providers: ControlledGraphitiProviders) -> None:
        fixture.reset_case()

    async def prepare(source, logical_time_ns, providers):
        callback_providers.append(providers)
        return await fixture.runtime.prepare(source, logical_time_ns, providers)

    async def bind(prepared, logical_time_ns, source_sequence, prefix, providers):
        callback_providers.append(providers)
        return await fixture.runtime.bind(
            prepared, logical_time_ns, source_sequence, prefix, providers
        )

    async def persist(event: dict[str, object]) -> None:
        journal.append(deepcopy(event))

    def snapshot():
        return (
            {
                "nodes": sorted(fixture.durable_records["nodes"]),
                "relationships": sorted(fixture.durable_records["edges"]),
            },
            tuple(journal),
        )

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=CORE_IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=snapshot,
        persist_event=persist,
        source_decoder=decode,
        reset_case=reset,
        controlled_provider_factory=provider_factory,
    )

    case = Fx0ExecutionCase(
        case_id="real-graphiti-adapter",
        source_sequence=0,
        source={"opaque_case": "alice"},
    )
    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, legacy_providers)
    )

    assert execution.outcome.status == "PASS"
    assert fixture.call_order == [
        "extract_nodes",
        "resolve_extracted_nodes",
        "extract_edges",
        "resolve_edge_pointers",
        "resolve_extracted_edges",
        "extract_attributes_from_nodes",
        "process_episode_data",
    ]
    assert fixture.transaction_attempts == 1
    assert all(isinstance(value, ControlledGraphitiProviders) for value in callback_providers)
    assert fixture.active_providers is None
    assert journal


def test_production_adapter_executes_two_real_sources_in_source_order() -> None:
    fixture = build_controlled_graphiti_fixture()
    journal: list[dict[str, object]] = []
    legacy_providers = ControlledNondeterminism(
        llm_responses={"opaque": "not an oracle"},
        embeddings={"opaque": [0.0, 1.0]},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={},
        candidate_sets=(),
    )

    def provider_factory(_providers):
        return fixture.providers

    def decode(case, providers):
        assert isinstance(providers, ControlledGraphitiProviders)
        return tuple(
            Fx0DecodedSource(
                source_sha256=payload_sha256({"case": case.source, "index": index}),
                opaque_source=fixture._source(index),
                logical_time_ns=providers.logical_time_ns + index,
            )
            for index in range(2)
        )

    async def reset(_providers):
        fixture.reset_case()

    async def prepare(source, logical_time_ns, providers):
        # Keep both real prepares in flight long enough for the shared core to
        # observe overlap; this is scheduling instrumentation, not semantics.
        await asyncio.sleep(0.002)
        return await fixture.runtime.prepare(source, logical_time_ns, providers)

    async def bind(prepared, logical_time_ns, source_sequence, prefix, providers):
        return await fixture.runtime.bind(
            prepared, logical_time_ns, source_sequence, prefix, providers
        )

    async def persist(event):
        journal.append(deepcopy(event))

    def snapshot():
        return (
            {
                "nodes": sorted(fixture.durable_records["nodes"]),
                "relationships": sorted(fixture.durable_records["edges"]),
            },
            tuple(journal),
        )

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=CORE_IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=snapshot,
        persist_event=persist,
        source_decoder=decode,
        reset_case=reset,
        controlled_provider_factory=provider_factory,
    )
    case = Fx0ExecutionCase(
        case_id="real-graphiti-two-source",
        source_sequence=0,
        source={"opaque_case": "two"},
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, legacy_providers)
    )

    assert execution.outcome.status == "PASS"
    assert execution.source_count == 2
    assert execution.attempt_count == 1
    assert execution.execution_shape["prepare_overlap_observed"] is True
    assert execution.execution_shape["published_source_count"] == 2
    assert execution.execution_shape["published_source_order_observed"] is True
    assert execution.pipeline_evidence["summary"]["published_source_sequences"] == [0, 1]
    assert fixture.transaction_attempts == 2
    assert fixture.active_providers is None


def test_production_adapter_reports_real_graphiti_transaction_retry_witness() -> None:
    fixture = build_controlled_graphiti_fixture(
        retry_transaction_once=True,
        idempotent_retry=True,
    )
    journal: list[dict[str, object]] = []
    legacy_providers = ControlledNondeterminism(
        llm_responses={"opaque": "not an oracle"},
        embeddings={"opaque": [0.0, 1.0]},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={},
        candidate_sets=(),
    )

    def provider_factory(_providers):
        return fixture.providers

    def decode(case, providers):
        return (
            Fx0DecodedSource(
                source_sha256=payload_sha256(case.source),
                opaque_source=fixture._source(0),
                logical_time_ns=providers.logical_time_ns,
            ),
        )

    async def reset(_providers):
        fixture.reset_case()

    async def prepare(source, logical_time_ns, providers):
        return await fixture.runtime.prepare(source, logical_time_ns, providers)

    async def bind(prepared, logical_time_ns, source_sequence, prefix, providers):
        return await fixture.runtime.bind(
            prepared, logical_time_ns, source_sequence, prefix, providers
        )

    async def persist(event):
        journal.append(deepcopy(event))

    def snapshot():
        return (
            {
                "nodes": sorted(fixture.durable_records["nodes"]),
                "relationships": sorted(fixture.durable_records["edges"]),
            },
            tuple(journal),
        )

    def witness(_case_id):
        projections = fixture.retry_commit_projections
        return {
            "retry_replay_observed": len(projections) >= 2
            and projections[0] == projections[1],
            "transaction_attempt_count": fixture.transaction_attempts,
        }

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=CORE_IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=snapshot,
        persist_event=persist,
        source_decoder=decode,
        reset_case=reset,
        witness_snapshot=witness,
        controlled_provider_factory=provider_factory,
    )
    case = Fx0ExecutionCase(
        case_id="real-graphiti-retry",
        source_sequence=0,
        source={"opaque_case": "retry"},
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, legacy_providers)
    )

    assert execution.outcome.status == "PASS"
    assert fixture.transaction_attempts == 2
    assert execution.attempt_count == 2
    assert execution.execution_shape["attempt_count"] == 2
    assert execution.execution_shape["retry_replay_observed"] is True
    assert execution.execution_shape["single_logical_publication_observed"] is True


def test_production_adapter_bind_reads_state_changed_after_real_prepare() -> None:
    fixture = build_controlled_graphiti_fixture()
    latest_state: list[EpisodicNode] = []
    observed_latest: list[tuple[EpisodicNode, ...]] = []
    changed_episode = EpisodicNode(
        uuid="changed-after-prepare",
        name="changed-after-prepare",
        content="state changed after prepare",
        source=EpisodeType.text,
        source_description="controlled state change",
        group_id=fixture.group_id,
        valid_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    journal: list[dict[str, object]] = []
    legacy_providers = ControlledNondeterminism(
        llm_responses={"opaque": "not an oracle"},
        embeddings={"opaque": [0.0, 1.0]},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={},
        candidate_sets=(),
    )

    async def retrieve(_source):
        value = tuple(latest_state)
        observed_latest.append(value)
        return list(value)

    runtime = S5GraphitiMStarSemanticRuntime(
        graphiti=fixture.graphiti,
        binding=fixture.binding,
        latest_state_retriever=retrieve,
        controlled_provider_scope=fixture._provider_scope,
        call_observer=fixture.call_order.append,
        require_native_commit_shape=True,
    )

    def provider_factory(_providers):
        return fixture.providers

    def decode(case, providers):
        return (
            Fx0DecodedSource(
                source_sha256=payload_sha256(case.source),
                opaque_source=fixture._source(0),
                logical_time_ns=providers.logical_time_ns,
            ),
        )

    async def reset(_providers):
        fixture.reset_case()
        latest_state.clear()
        observed_latest.clear()

    async def prepare(source, logical_time_ns, providers):
        prepared = await runtime.prepare(source, logical_time_ns, providers)
        latest_state.append(changed_episode)
        return prepared

    async def bind(prepared, logical_time_ns, source_sequence, prefix, providers):
        return await runtime.bind(
            prepared, logical_time_ns, source_sequence, prefix, providers
        )

    async def persist(event):
        journal.append(deepcopy(event))

    def snapshot():
        return (
            {
                "nodes": sorted(fixture.durable_records["nodes"]),
                "relationships": sorted(fixture.durable_records["edges"]),
            },
            tuple(journal),
        )

    def witness(_case_id):
        return {
            "prepare_to_bind_state_change_observed": bool(
                observed_latest
                and observed_latest[0][0].uuid == "changed-after-prepare"
            ),
        }

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=CORE_IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=snapshot,
        persist_event=persist,
        source_decoder=decode,
        reset_case=reset,
        witness_snapshot=witness,
        controlled_provider_factory=provider_factory,
    )
    case = Fx0ExecutionCase(
        case_id="real-graphiti-state-change",
        source_sequence=0,
        source={"opaque_case": "state-change"},
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, legacy_providers)
    )

    assert execution.outcome.status == "PASS"
    assert len(observed_latest) == 1
    assert observed_latest[0][0].uuid == "changed-after-prepare"
    assert execution.execution_shape["prepare_to_bind_state_change_observed"] is True


def test_production_adapter_detects_lost_publication_from_independent_history() -> None:
    fixture = build_controlled_graphiti_fixture()
    external_history: list[dict[str, object]] = []
    legacy_providers = ControlledNondeterminism(
        llm_responses={"opaque": "not an oracle"},
        embeddings={"opaque": [0.0, 1.0]},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={},
        candidate_sets=(),
    )

    def provider_factory(_providers):
        return fixture.providers

    def decode(case, providers):
        return (
            Fx0DecodedSource(
                source_sha256=payload_sha256(case.source),
                opaque_source=fixture._source(0),
                logical_time_ns=providers.logical_time_ns,
            ),
        )

    async def reset(_providers):
        fixture.reset_case()
        external_history.clear()

    async def prepare(source, logical_time_ns, providers):
        return await fixture.runtime.prepare(source, logical_time_ns, providers)

    async def bind(prepared, logical_time_ns, source_sequence, prefix, providers):
        return await fixture.runtime.bind(
            prepared, logical_time_ns, source_sequence, prefix, providers
        )

    async def persist(event):
        # The sink silently loses publication while returning success; the
        # independent detector must catch the durable-history mismatch.
        if event["event_type"] != "publication":
            external_history.append(deepcopy(event))

    def snapshot():
        return (
            {
                "nodes": sorted(fixture.durable_records["nodes"]),
                "relationships": sorted(fixture.durable_records["edges"]),
            },
            tuple(external_history),
        )

    def detect(source_count, _state, history):
        publications = [
            event for event in history if event.get("event_type") == "publication"
        ]
        if source_count == 1 and not publications:
            return "LOST_PUBLICATION"
        return None

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=CORE_IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=snapshot,
        persist_event=persist,
        source_decoder=decode,
        reset_case=reset,
        publication_fault_detector=detect,
        controlled_provider_factory=provider_factory,
    )
    case = Fx0ExecutionCase(
        case_id="real-graphiti-lost-publication",
        source_sequence=0,
        source={"opaque_case": "lost-publication"},
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, legacy_providers)
    )

    assert execution.outcome.status == "FAIL_CLOSED"
    assert execution.outcome.error_code == "LOST_PUBLICATION"
    assert execution.execution_shape["publication_fault_detection_observed"] is True


@pytest.mark.parametrize(
    ("fault_mode", "source_count", "expected_error"),
    (
        ("duplicate", 1, "DUPLICATE_PUBLICATION"),
        ("partial", 2, "PARTIAL_PUBLICATION"),
    ),
)
def test_production_adapter_detects_publication_history_faults(
    fault_mode: str,
    source_count: int,
    expected_error: str,
) -> None:
    fixture = build_controlled_graphiti_fixture()
    external_history: list[dict[str, object]] = []
    legacy_providers = ControlledNondeterminism(
        llm_responses={"opaque": "not an oracle"},
        embeddings={"opaque": [0.0, 1.0]},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={},
        candidate_sets=(),
    )

    def provider_factory(_providers):
        return fixture.providers

    def decode(case, providers):
        return tuple(
            Fx0DecodedSource(
                source_sha256=payload_sha256({"case": case.source, "index": index}),
                opaque_source=fixture._source(index),
                logical_time_ns=providers.logical_time_ns + index,
            )
            for index in range(source_count)
        )

    async def reset(_providers):
        fixture.reset_case()
        external_history.clear()

    async def prepare(source, logical_time_ns, providers):
        if source_count > 1:
            await asyncio.sleep(0.002)
        return await fixture.runtime.prepare(source, logical_time_ns, providers)

    async def bind(prepared, logical_time_ns, source_sequence, prefix, providers):
        return await fixture.runtime.bind(
            prepared, logical_time_ns, source_sequence, prefix, providers
        )

    async def persist(event):
        row = deepcopy(event)
        if event["event_type"] != "publication":
            external_history.append(row)
            return
        if fault_mode == "duplicate":
            external_history.extend((row, deepcopy(row)))
        elif fault_mode == "partial" and event["source_sequence"] == 0:
            external_history.append(row)

    def snapshot():
        return (
            {
                "nodes": sorted(fixture.durable_records["nodes"]),
                "relationships": sorted(fixture.durable_records["edges"]),
            },
            tuple(external_history),
        )

    def detect(expected_source_count, _state, history):
        publications = [
            event for event in history if event.get("event_type") == "publication"
        ]
        published_sources = [event["source_sequence"] for event in publications]
        if len(published_sources) != len(set(published_sources)):
            return "DUPLICATE_PUBLICATION"
        if 0 < len(published_sources) < expected_source_count:
            return "PARTIAL_PUBLICATION"
        if not published_sources:
            return "LOST_PUBLICATION"
        return None

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=CORE_IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=snapshot,
        persist_event=persist,
        source_decoder=decode,
        reset_case=reset,
        publication_fault_detector=detect,
        controlled_provider_factory=provider_factory,
    )
    case = Fx0ExecutionCase(
        case_id=f"real-graphiti-{fault_mode}-publication",
        source_sequence=0,
        source={"opaque_case": f"{fault_mode}-publication"},
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, legacy_providers)
    )

    assert execution.outcome.status == "FAIL_CLOSED"
    assert execution.outcome.error_code == expected_error
    assert execution.execution_shape["publication_fault_detection_observed"] is True


def test_real_graphiti_conflict_maps_only_to_registered_failure() -> None:
    fixture = build_controlled_graphiti_fixture(
        conflicting_candidate_projections=True,
    )
    journal: list[dict[str, object]] = []
    providers = ControlledNondeterminism(
        llm_responses={"provider_plan_sha256": "a" * 64},
        embeddings={"provider_plan_sha256": "b" * 64},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={"provider_plan_sha256": "c" * 64},
        candidate_sets=({"provider_plan_sha256": "d" * 64},),
    )

    def provider_factory(_providers):
        return fixture.providers

    def decode(case, active_providers):
        return (
            Fx0DecodedSource(
                source_sha256=payload_sha256(case.source),
                opaque_source=fixture._source(0),
                logical_time_ns=active_providers.logical_time_ns,
            ),
        )

    async def reset(_providers):
        fixture.reset_case()
        journal.clear()

    async def persist(event):
        journal.append(deepcopy(event))

    def snapshot():
        history = tuple(
            {
                "source_sequence": event["source_sequence"],
                "event": "publish",
            }
            for event in journal
            if event["event_type"] == "publication"
        )
        return fixture.canonical_logical_state(), history

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=CORE_IDENTITY["identity_sha256"],
        semantic_prepare=fixture.runtime.prepare,
        latest_state_bind=fixture.runtime.bind,
        snapshot=snapshot,
        persist_event=persist,
        source_decoder=decode,
        reset_case=reset,
        witness_snapshot=lambda _case_id: {},
        controlled_provider_factory=provider_factory,
        publication_fault_detector=lambda _count, _state, _history: None,
    )
    case = Fx0ExecutionCase(
        case_id="real-graphiti-conflicting-duplicate",
        source_sequence=0,
        source={"provider_plan_sha256": "e" * 64},
    )

    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, providers)
    )

    assert execution.outcome.status == "FAIL_CLOSED"
    assert execution.outcome.error_code == "CONFLICTING_DUPLICATE_UUID"
    assert execution.outcome.canonical_logical_state == {
        "nodes": [],
        "relationships": [],
    }
    assert execution.outcome.publication_history == ()
