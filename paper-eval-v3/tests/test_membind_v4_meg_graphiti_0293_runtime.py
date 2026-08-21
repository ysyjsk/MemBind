"""Actual pinned Graphiti provider-free parity for MEG OBSERVE_ONLY."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from paper_eval.membind_v4.mseg.graphiti_0293_audit import audit_graphiti_0293
from paper_eval.membind_v4.mseg.graphiti_0293_runtime import (
    build_observe_only_binding,
    snapshot_controlled_execution,
)
from paper_eval.membind_v4.mseg.mutation_epoch import StateMutationEpoch
from paper_eval.membind_v4.mseg.passive_equivalence import compare_observe_only_execution
from paper_eval.membind_v4.mseg.runtime_instrumentation import (
    InstrumentationMode,
    MEGRuntimeRecorder,
    OperatorEventType,
    SemanticOperatorClass,
    WriterDomainCertificate,
)
from paper_eval.s5_graphiti_controlled_fixture import (
    ControlledGraphitiFixtureError,
    build_controlled_graphiti_fixture,
)


def _writer() -> WriterDomainCertificate:
    return WriterDomainCertificate.create(
        namespace="controlled-db",
        graph_backend="neo4j",
        authorized_writer_identity="controlled-membind-construction",
        write_path_coverage=("bulk_utils.add_nodes_and_edges_bulk.execute_write",),
        expected_write_paths=("bulk_utils.add_nodes_and_edges_bulk.execute_write",),
        external_writer_policy="DENY",
        commit_observer_coverage="ALL_MANAGED_COMMITS",
        fresh_namespace=True,
        no_background_mutation=True,
    )


def test_actual_graphiti_0293_observe_only_passive_equivalence() -> None:
    options = {
        "canonical_candidate": True,
        "edge_types": ("WorksAt",),
        "edge_fact": "Alice works at Acme.",
        "invalidation_candidate": True,
    }
    baseline_fixture = build_controlled_graphiti_fixture(**options)
    baseline_result = asyncio.run(baseline_fixture.run_episode())
    baseline = snapshot_controlled_execution(baseline_fixture, baseline_result)

    observed_fixture = build_controlled_graphiti_fixture(**options)
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY,
        writer_domain=_writer(),
    )
    epoch = StateMutationEpoch(
        namespace="controlled-db", backend_id="neo4j", epoch="controlled-db-epoch"
    )
    observed_fixture.runtime.binding = build_observe_only_binding(
        observed_fixture.binding,
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=_writer(),
        stream_id="controlled-stream",
    )
    observed_result = asyncio.run(observed_fixture.run_episode())
    observed = snapshot_controlled_execution(
        observed_fixture, observed_result, recorder=recorder
    )

    certificate = compare_observe_only_execution(baseline, observed)
    assert certificate.passed, certificate.violations
    assert epoch.snapshot().counter == 1
    assert any(
        event.event_type is OperatorEventType.OPERATOR_READY for event in recorder.events
    )
    assert any(event.event_type is OperatorEventType.PUBLICATION for event in recorder.events)
    assert recorder.request_spans
    assert all(span.prompt_name for span in recorder.request_spans)
    read_view_operator_ids = {
        view.read_view.operator_instance_id for view in recorder.read_views
    }
    state_derived = {
        operator.semantic_operator_id
        for operator in recorder.operators
        if operator.classification is SemanticOperatorClass.STATE_DERIVED
    }
    assert read_view_operator_ids == state_derived
    edge_extraction = next(
        operator
        for operator in recorder.operators
        if operator.semantic_operator_type == "EDGE_EXTRACTION"
    )
    assert edge_extraction.classification is SemanticOperatorClass.DERIVED_PRIVATE


def test_actual_graphiti_edge_child_identity_survives_completion_reversal() -> None:
    fixture = build_controlled_graphiti_fixture(
        edge_types=("WorksAt",), edge_fact="Alice works at Acme."
    )
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY,
        writer_domain=_writer(),
    )
    epoch = StateMutationEpoch(
        namespace="controlled-db", backend_id="neo4j", epoch="controlled-db-epoch"
    )
    fixture.runtime.binding = build_observe_only_binding(
        fixture.binding,
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=_writer(),
        stream_id="controlled-stream",
    )
    asyncio.run(fixture.run_episode())
    first = {
        operator.semantic_operator_id
        for operator in recorder.operators
        if operator.semantic_operator_type == "EDGE_RESOLUTION_CHILD"
    }
    assert first
    assert all(
        operator.materialized_before_coroutine
        for operator in recorder.operators
        if operator.semantic_operator_type == "EDGE_RESOLUTION_CHILD"
    )


def test_actual_v31_compile_only_clients_do_not_require_driver_or_embedder() -> None:
    from graphiti_core.nodes import EpisodeType, EpisodicNode

    fixture = build_controlled_graphiti_fixture()
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY,
        writer_domain=_writer(),
    )
    epoch = StateMutationEpoch(
        namespace="controlled-db", backend_id="neo4j", epoch="controlled-db-epoch"
    )
    binding = build_observe_only_binding(
        fixture.binding,
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=_writer(),
        stream_id="controlled-stream",
    )

    @dataclass
    class CompileOnlyClients:
        llm_client: object

    episode = EpisodicNode(
        uuid="controlled-episode-0",
        name="controlled-0",
        content="Alice works at Acme.",
        source=EpisodeType.text,
        source_description="controlled fixture",
        group_id="controlled-db",
        valid_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    with fixture._provider_scope(fixture.providers):
        nodes, node_map = asyncio.run(
            binding.extract_nodes(
                CompileOnlyClients(fixture.llm),
                episode,
                [],
                None,
                [],
                None,
            )
        )

    assert nodes
    assert node_map
    assert [span.prompt_name for span in recorder.request_spans] == [
        "extract_nodes.extract_text"
    ]


def test_actual_managed_retry_advances_epoch_once_after_success() -> None:
    fixture = build_controlled_graphiti_fixture(
        retry_transaction_once=True, idempotent_retry=True
    )
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY, writer_domain=_writer()
    )
    epoch = StateMutationEpoch(
        namespace="controlled-db", backend_id="neo4j", epoch="controlled-db-epoch"
    )
    fixture.runtime.binding = build_observe_only_binding(
        fixture.binding,
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=_writer(),
        stream_id="controlled-stream",
    )
    result = asyncio.run(fixture.run_episode())
    assert result.transaction_attempts == 2
    assert epoch.snapshot().counter == 1


def test_actual_failed_transaction_has_no_epoch_or_publication() -> None:
    fixture = build_controlled_graphiti_fixture(fail_transaction=True)
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY, writer_domain=_writer()
    )
    epoch = StateMutationEpoch(
        namespace="controlled-db", backend_id="neo4j", epoch="controlled-db-epoch"
    )
    fixture.runtime.binding = build_observe_only_binding(
        fixture.binding,
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=_writer(),
        stream_id="controlled-stream",
    )
    with pytest.raises(ControlledGraphitiFixtureError, match="COMMIT_FAILED"):
        asyncio.run(fixture.run_episode())
    assert epoch.snapshot().counter == 0
    assert not any(
        event.event_type is OperatorEventType.PUBLICATION for event in recorder.events
    )


def test_static_graphiti_0293_write_path_coverage_is_complete_and_narrow() -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
    )
    audit = audit_graphiti_0293(root)
    assert audit["graphiti_version"] == "0.29.3"
    assert audit["status"] == "PASS"
    assert audit["covered_write_paths"] == audit["relevant_write_paths"]
    assert audit["coverage_ratio"] == 1.0
    inventory = audit["write_path_inventory"]
    assert any(
        row["call"] == "execute_write" and row["relevance"] == "RELEVANT_COVERED"
        for row in inventory
    )
    assert any(
        "saga" in row["file"]
        and row["relevance"] == "CONFIG_GUARDED_OUT_OF_SCOPE"
        for row in inventory
    )
