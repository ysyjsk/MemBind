"""Provider-free contracts for the Graphiti 0.29.3 MEG runtime seam."""

from __future__ import annotations

from dataclasses import replace

import pytest

from paper_eval.membind_v4.mseg.continuation import (
    ContinuationValidationStatus,
    SemanticContinuation,
    SemanticRequestIdentity,
    validate_semantic_continuation,
)
from paper_eval.membind_v4.mseg.mutation_epoch import StateMutationEpoch
from paper_eval.membind_v4.mseg.passive_equivalence import (
    RuntimeExecutionSnapshot,
    compare_observe_only_execution,
    compare_shadow_read_execution,
)
from paper_eval.membind_v4.mseg.read_view import (
    CandidateSemanticRecord,
    ReadKind,
    ReadMaterialization,
    ReadViewStatus,
)
from paper_eval.membind_v4.mseg.runtime_instrumentation import (
    InstrumentationMode,
    MEGRuntimeRecorder,
    OperatorEventType,
    RuntimeInstrumentationError,
    SemanticDependencyTracker,
    SemanticOperatorClass,
    SemanticOperatorInstance,
    TransactionCommitObserver,
    WriterDomainCertificate,
    WriterDomainStatus,
    capture_runtime_read_view,
    classify_operator,
    precreate_edge_children,
)
from paper_eval.membind_v4.mseg.version_token import VersionTokenFactory


def _hash(character: str) -> str:
    return character * 64


def _writer(*, complete: bool = True) -> WriterDomainCertificate:
    return WriterDomainCertificate.create(
        namespace="meg-runtime-fixture",
        graph_backend="neo4j",
        authorized_writer_identity="membind-v31-construction",
        write_path_coverage=("bulk_utils.add_nodes_and_edges_bulk.execute_write",),
        expected_write_paths=("bulk_utils.add_nodes_and_edges_bulk.execute_write",),
        external_writer_policy="DENY",
        commit_observer_coverage="ALL_MANAGED_COMMITS" if complete else "PARTIAL",
        fresh_namespace=True,
        no_background_mutation=True,
    )


def _candidate(
    candidate_id: str,
    *,
    name: str,
    summary: str = "",
) -> CandidateSemanticRecord:
    return CandidateSemanticRecord.create(
        candidate_id=candidate_id,
        semantic_fields={"name": name, "summary": summary},
    )


def _materialization(
    candidates: tuple[CandidateSemanticRecord, ...],
    *,
    metadata: dict[str, object] | None = None,
) -> ReadMaterialization:
    return ReadMaterialization.create(
        query_identity="node-dedupe:alice",
        search_configuration_hash=_hash("a"),
        candidates=candidates,
        mutable_context_fragment_hash=_hash("b"),
        provenance_hash=_hash("c"),
        irrelevant_metadata=metadata or {},
        excluded_metadata_reasons=(
            {}
            if not metadata
            else {key: "not consumed by semantic decision" for key in metadata}
        ),
    )


def _version():
    return VersionTokenFactory(backend_id="neo4j", epoch="db-epoch").commit(
        namespace="meg-runtime-fixture",
        transaction_id="tx-0",
        evidence_hash=_hash("d"),
    )


def _view(
    candidates: tuple[CandidateSemanticRecord, ...],
    *,
    writer: WriterDomainCertificate | None = None,
    epoch: StateMutationEpoch | None = None,
    metadata: dict[str, object] | None = None,
):
    selected_epoch = epoch or StateMutationEpoch(
        namespace="meg-runtime-fixture", backend_id="neo4j", epoch="db-epoch"
    )
    return capture_runtime_read_view(
        graph_id="meg-runtime-fixture",
        stream_id="07741c45",
        source_sequence=1,
        operator_instance_id="operator-node-batch",
        memory_version_token=_version(),
        mutation_epoch=selected_epoch,
        writer_domain=writer or _writer(),
        read_kind=ReadKind.NODE_RESOLUTION,
        materialize=lambda: _materialization(candidates, metadata=metadata),
    )


def _operator(
    role: str,
    *,
    parents: tuple[str, ...] = (),
    classification: SemanticOperatorClass = SemanticOperatorClass.DERIVED_PRIVATE,
    ordinal: int = 0,
) -> SemanticOperatorInstance:
    return SemanticOperatorInstance.create(
        graph_id="meg-runtime-fixture",
        stream_id="07741c45",
        source_sequence=1,
        semantic_operator_type=role,
        classification=classification,
        parent_semantic_operator_ids=parents,
        child_ordinal=ordinal,
        semantic_input_identity={"role": role, "ordinal": ordinal},
    )


def _continuation(view):
    return SemanticContinuation.create(
        operator_instance_id="operator-node-batch",
        read_view=view.read_view,
        request_identity=SemanticRequestIdentity.create(
            rendered_request_hash=_hash("e"),
            model_id="controlled",
            schema_hash=_hash("f"),
            prompt_name="dedupe_nodes.nodes",
            prompt_hash=_hash("1"),
        ),
        llm_output_hash=_hash("2"),
        deterministic_post_processing_identity="graphiti-0.29.3-node-resolution",
        descendant_operator_ids=("identity-materialization",),
    )


def _snapshot(
    *,
    db_reads: tuple[str, ...] = (_hash("7"),),
    writes: tuple[str, ...] = (_hash("8"),),
    shadow_reads: tuple[str, ...] = (),
) -> RuntimeExecutionSnapshot:
    return RuntimeExecutionSnapshot(
        production_request_ids=("request-1",),
        production_prompt_hashes=(_hash("3"),),
        production_model_schema_hashes=(_hash("4"),),
        captured_response_hashes=(_hash("5"),),
        production_db_read_hashes=db_reads,
        shadow_db_read_hashes=shadow_reads,
        production_write_intent_hashes=writes,
        persistent_effect_hashes=(_hash("9"),),
        source_publication_order=(0,),
        source_sequences=(0,),
        source_exactly_once=True,
        production_llm_call_count=1,
        production_embedding_call_count=1,
        shadow_llm_call_count=0,
        shadow_embedding_call_count=0,
        shadow_persistent_write_count=0,
        publication_modification_count=0,
    )


def test_semantic_operator_identity_is_deterministic_and_not_request_identity() -> None:
    left = _operator("NODE_BATCH_RESOLUTION")
    right = _operator("NODE_BATCH_RESOLUTION")
    assert left.semantic_operator_id == right.semantic_operator_id
    assert left.semantic_operator_id.startswith("meg-runtime-op-")


def test_edge_children_are_identified_before_completion_order_exists() -> None:
    parent = _operator("EDGE_RESOLUTION_GROUP")
    edges = (
        {
            "uuid": "edge-a",
            "source_node_uuid": "node-a",
            "target_node_uuid": "node-b",
            "fact": "Alice works at Acme.",
        },
        {
            "uuid": "edge-b",
            "source_node_uuid": "node-b",
            "target_node_uuid": "node-c",
            "fact": "Acme owns Beta.",
        },
    )
    launched = precreate_edge_children(parent, edges)
    reversed_completion = tuple(reversed(launched))
    assert {item.semantic_operator_id for item in launched} == {
        item.semantic_operator_id for item in reversed_completion
    }
    assert [item.child_ordinal for item in launched] == [0, 1]


def test_one_semantic_operator_owns_multiple_request_spans() -> None:
    recorder = MEGRuntimeRecorder(mode=InstrumentationMode.OBSERVE_ONLY)
    operator = _operator("EDGE_RESOLUTION_CHILD")
    recorder.materialize(operator, immutable_inputs_exist=True, state_satisfiable=True)
    recorder.start(operator.semantic_operator_id)
    first = recorder.record_request(
        prompt_name="dedupe_edges.resolve_edge",
        prompt_hash=_hash("1"),
        model_schema_hash=_hash("2"),
        response_hash=_hash("3"),
    )
    second = recorder.record_request(
        prompt_name="extract_edges.extract_timestamps",
        prompt_hash=_hash("4"),
        model_schema_hash=_hash("5"),
        response_hash=_hash("6"),
    )
    assert first.semantic_operator_id == second.semantic_operator_id
    assert first.request_id != second.request_id
    assert [first.prompt_name, second.prompt_name] == [
        "dedupe_edges.resolve_edge",
        "extract_edges.extract_timestamps",
    ]


def test_ready_is_dependency_driven_and_emitted_exactly_once() -> None:
    tracker = SemanticDependencyTracker()
    predecessor_a = _operator("A")
    predecessor_b = _operator("B")
    consumer = _operator(
        "C", parents=(predecessor_a.semantic_operator_id, predecessor_b.semantic_operator_id)
    )
    for item in (predecessor_a, predecessor_b, consumer):
        tracker.materialize(item, immutable_inputs_exist=True, state_satisfiable=True)
    tracker.complete(predecessor_a.semantic_operator_id)
    assert not tracker.is_ready(consumer.semantic_operator_id)
    tracker.complete(predecessor_b.semantic_operator_id)
    tracker.complete(predecessor_b.semantic_operator_id)
    ready = [
        event
        for event in tracker.events
        if event.event_type is OperatorEventType.OPERATOR_READY
        and event.semantic_operator_id == consumer.semantic_operator_id
    ]
    assert len(ready) == 1


def test_recorder_event_sequences_are_global_across_tracker_and_runtime_events() -> None:
    clock_values = iter(range(100, 120))
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY,
        writer_domain=_writer(),
        clock_ns=lambda: next(clock_values),
    )
    operator = _operator("PERSIST_AND_PUBLISH")

    recorder.materialize(
        operator, immutable_inputs_exist=True, state_satisfiable=True
    )
    recorder.start(operator.semantic_operator_id)
    recorder.end(operator.semantic_operator_id)
    recorder.record_transaction_commit(transaction_id="tx-1")
    recorder.record_publication(source_sequence=1, transaction_id="tx-1")

    events = recorder.events
    assert [event.event_sequence for event in events] == list(range(len(events)))
    assert [event.event_type for event in events] == [
        OperatorEventType.OPERATOR_MATERIALIZED,
        OperatorEventType.OPERATOR_READY,
        OperatorEventType.OPERATOR_START,
        OperatorEventType.OPERATOR_END,
        OperatorEventType.TRANSACTION_COMMIT,
        OperatorEventType.PUBLICATION,
    ]
    assert [event.timestamp_ns for event in events[:4]] == [100, 101, 102, 103]


@pytest.mark.asyncio
async def test_successful_managed_transaction_increments_epoch_once() -> None:
    epoch = StateMutationEpoch(
        namespace="meg-runtime-fixture", backend_id="neo4j", epoch="db-epoch"
    )
    observer = TransactionCommitObserver(mutation_epoch=epoch)

    class Session:
        async def execute_write(self, callback, *args, **kwargs):
            return await callback(object(), *args, **kwargs)

    result = await observer.execute_write(Session(), lambda _tx: _async_value("ok"))
    assert result == "ok"
    assert epoch.snapshot().counter == 1


@pytest.mark.asyncio
async def test_managed_retry_increments_epoch_only_after_single_success() -> None:
    epoch = StateMutationEpoch(
        namespace="meg-runtime-fixture", backend_id="neo4j", epoch="db-epoch"
    )
    observer = TransactionCommitObserver(mutation_epoch=epoch)

    class RetryingSession:
        async def execute_write(self, callback, *args, **kwargs):
            await callback(object(), *args, **kwargs)
            return await callback(object(), *args, **kwargs)

    calls = 0

    async def callback(_tx):
        nonlocal calls
        calls += 1
        return "ok"

    assert await observer.execute_write(RetryingSession(), callback) == "ok"
    assert calls == 2
    assert epoch.snapshot().counter == 1


@pytest.mark.asyncio
async def test_failed_or_rolled_back_transaction_does_not_increment_epoch() -> None:
    epoch = StateMutationEpoch(
        namespace="meg-runtime-fixture", backend_id="neo4j", epoch="db-epoch"
    )
    observer = TransactionCommitObserver(mutation_epoch=epoch)

    class FailedSession:
        async def execute_write(self, _callback, *_args, **_kwargs):
            raise RuntimeError("rollback")

    with pytest.raises(RuntimeError, match="rollback"):
        await observer.execute_write(FailedSession(), lambda _tx: _async_value(None))
    assert epoch.snapshot().counter == 0


def test_incomplete_writer_domain_forces_opaque_readview() -> None:
    candidates = (_candidate("node-a", name="Alice"),)
    writer = _writer(complete=False)
    view = _view(candidates, writer=writer)
    assert writer.status is WriterDomainStatus.OPAQUE_WRITER_DOMAIN
    assert view.status is ReadViewStatus.OPAQUE
    assert view.read_view.read_view_digest is None


def test_epoch_change_during_read_is_unstable() -> None:
    epoch = StateMutationEpoch(
        namespace="meg-runtime-fixture", backend_id="neo4j", epoch="db-epoch"
    )
    candidates = (_candidate("node-a", name="Alice"),)

    def materialize():
        epoch.record_commit(transaction_id="concurrent-commit")
        return _materialization(candidates)

    view = capture_runtime_read_view(
        graph_id="meg-runtime-fixture",
        stream_id="07741c45",
        source_sequence=1,
        operator_instance_id="operator-node-batch",
        memory_version_token=_version(),
        mutation_epoch=epoch,
        writer_domain=_writer(),
        read_kind=ReadKind.NODE_RESOLUTION,
        materialize=materialize,
    )
    assert view.status is ReadViewStatus.INVALID_UNSTABLE_READ


def test_phantom_candidate_insertion_is_miss_even_with_disjoint_effect_uuid() -> None:
    stale = _view(
        (_candidate("node-a", name="Alice"), _candidate("node-b", name="Alice A."))
    )
    exact = _view(
        (
            _candidate("node-c", name="Alice Corp."),
            _candidate("node-a", name="Alice"),
            _candidate("node-b", name="Alice A."),
        )
    )
    result = validate_semantic_continuation(
        _continuation(stale), exact.read_view, effect_scopes_disjoint=True
    )
    assert result.status is ContinuationValidationStatus.VALIDATION_MISS
    assert result.independence_certified is False


def test_candidate_order_change_is_miss() -> None:
    candidates = (_candidate("node-a", name="Alice"), _candidate("node-b", name="Alice A."))
    stale = _view(candidates)
    exact = _view(tuple(reversed(candidates)))
    assert (
        validate_semantic_continuation(_continuation(stale), exact.read_view).status
        is ContinuationValidationStatus.VALIDATION_MISS
    )


def test_candidate_mutable_field_change_is_miss() -> None:
    stale = _view((_candidate("node-a", name="Alice", summary="old"),))
    exact = _view((_candidate("node-a", name="Alice", summary="new"),))
    assert (
        validate_semantic_continuation(_continuation(stale), exact.read_view).status
        is ContinuationValidationStatus.VALIDATION_MISS
    )


def test_explicitly_irrelevant_metadata_change_is_hit() -> None:
    candidates = (_candidate("node-a", name="Alice"),)
    stale = _view(candidates, metadata={"driver_debug_tag": "left"})
    exact = _view(candidates, metadata={"driver_debug_tag": "right"})
    assert (
        validate_semantic_continuation(_continuation(stale), exact.read_view).status
        is ContinuationValidationStatus.VALIDATION_HIT
    )


@pytest.mark.parametrize(
    "operator_type",
    ("NODE_ATTRIBUTE", "EDGE_ATTRIBUTE", "EDGE_TIMESTAMP", "NODE_SUMMARY"),
)
def test_evidence_private_attribute_timestamp_summary_does_not_require_readview(
    operator_type: str,
) -> None:
    contract = classify_operator(
        operator_type=operator_type,
        reads_mutable_persistent_state=False,
        consumes_only_immutable_evidence_or_parent_private_result=True,
        persistent_effect=False,
        publication=False,
    )
    assert contract.classification is SemanticOperatorClass.DERIVED_PRIVATE
    assert contract.read_view_required is False


def test_observe_only_requires_identical_db_query_count_and_semantics() -> None:
    baseline = _snapshot()
    instrumented = _snapshot()
    assert compare_observe_only_execution(baseline, instrumented).passed
    changed = replace(instrumented, production_db_read_hashes=instrumented.production_db_read_hashes * 2)
    assert not compare_observe_only_execution(baseline, changed).passed


def test_shadow_read_allows_extra_reads_but_no_write_or_production_llm_change() -> None:
    baseline = _snapshot()
    shadow = _snapshot(shadow_reads=(_hash("6"), _hash("7")))
    assert compare_shadow_read_execution(baseline, shadow).passed
    changed = replace(shadow, shadow_persistent_write_count=1)
    assert not compare_shadow_read_execution(baseline, changed).passed


@pytest.mark.asyncio
async def test_publication_cannot_precede_successful_transaction_commit() -> None:
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY,
        writer_domain=_writer(),
    )
    epoch = StateMutationEpoch(
        namespace="meg-runtime-fixture", backend_id="neo4j", epoch="db-epoch"
    )
    observer = TransactionCommitObserver(mutation_epoch=epoch, recorder=recorder)

    class Session:
        async def execute_write(self, callback, *args, **kwargs):
            return await callback(object(), *args, **kwargs)

    await observer.execute_write(Session(), lambda _tx: _async_value("ok"))
    recorder.record_publication(source_sequence=1, transaction_id=observer.last_transaction_id)
    event_types = [event.event_type for event in recorder.events]
    assert event_types.index(OperatorEventType.TRANSACTION_COMMIT) < event_types.index(
        OperatorEventType.PUBLICATION
    )


def test_unknown_write_path_makes_publication_opaque() -> None:
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY,
        writer_domain=_writer(complete=False),
    )
    recorder.record_publication(source_sequence=1, transaction_id="tx-unknown")
    publication = recorder.events[-1]
    assert publication.event_type is OperatorEventType.PUBLICATION
    assert publication.status == "OPAQUE"


async def _async_value(value):
    return value
