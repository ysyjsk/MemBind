"""RED/GREEN tests for the design-only MEG semantic adapter/compiler.

These tests deliberately use pure records.  They must remain runnable without
Graphiti, Neo4j, vLLM, a network, or a model client.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from paper_eval.membind_v4.mseg.semantic_evidence import (
    AdapterProvenance,
    CertificationLevel,
)
from paper_eval.membind_v4.mseg.effect_journal import (
    EffectCertification,
    EffectScope,
    MemoryEffectJournal,
    MemoryEffectJournalEntry,
    validate_effect_entry,
)
from paper_eval.membind_v4.mseg.passive_equivalence import (
    PassiveExecutionSnapshot,
    compare_passive_execution,
)
from paper_eval.membind_v4.mseg.publication import (
    PublicationCertification,
    PublicationEvent,
    PublicationJournal,
    validate_publication_event,
)
from paper_eval.membind_v4.mseg.semantic_adapter import (
    BoundaryStatus,
    ChildKey,
    GraphitiOperatorKind,
    LineageBuilder,
    OperatorLineage,
    RequestLineage,
    StaticSemanticContract,
    derive_operator_instance_id,
    graphiti_operator_catalog,
    qualify_operator_boundary,
)
from paper_eval.membind_v4.mseg.semantic_compiler import (
    CompilationStatus,
    DependencySpec,
    DynamicOperatorEvidence,
    OperatorInput,
    SemanticCompiler,
)
from paper_eval.membind_v4.mseg.semantic_contract import (
    EffectKind,
    OperatorType,
    Visibility,
)
from paper_eval.membind_v4.mseg.version_token import (
    MemoryVersionToken,
    VersionTokenFactory,
    VersionTokenValidation,
    validate_version_token,
)


def _token_factory() -> VersionTokenFactory:
    return VersionTokenFactory(backend_id="fixture-backend", epoch="fixture-epoch")


def _tokens() -> tuple[MemoryVersionToken, MemoryVersionToken]:
    factory = _token_factory()
    first = factory.commit(
        namespace="graph-a",
        transaction_id="tx-1",
        evidence_hash="a" * 64,
    )
    second = factory.commit(
        namespace="graph-a",
        transaction_id="tx-2",
        predecessor=first,
        evidence_hash="b" * 64,
    )
    return first, second


def _lineage(
    *,
    role: str = "graphiti.resolve_extracted_edge",
    parent: str | None = None,
    key: ChildKey | None = None,
) -> OperatorLineage:
    return OperatorLineage.create(
        graph_id="graph-a",
        stream_id="stream-a",
        source_sequence=3,
        semantic_role=role,
        adapter_revision="adapter-v0",
        parent_operator_instance_id=parent,
        child_key=key,
        created_ns=100,
        ready_ns=110,
        enqueue_ns=111,
        start_ns=112,
        end_ns=139,
        coroutine_id="coroutine-0",
    )


def _static(
    *,
    role: str = "graphiti.resolve_extracted_edge",
    operator_type: OperatorType = OperatorType.RESOLUTION,
    effect_kind: EffectKind = EffectKind.UPDATE,
    state_bound: bool = True,
    publication: bool = False,
) -> StaticSemanticContract:
    return StaticSemanticContract(
        operator_role=role,
        operator_type=operator_type,
        namespace="graph-a",
        state_bound=state_bound,
        effect_kind=effect_kind,
        visibility=(
            Visibility.PUBLISHED_STATE if publication else Visibility.PRIVATE_INTERMEDIATE
        ),
        atomic=True,
        idempotent=True,
        retry_safe=True,
        publication_boundary=publication,
        dependency_class="explicit",
        resource_class="llm",
        child_identity_mode="structured_input"
        if "edge" in role
        else "single",
    )


def _provenance(role: str) -> AdapterProvenance:
    return AdapterProvenance(
        adapter_id="graphiti-adapter-v0",
        backend_name="graphiti",
        backend_version="0.29.3",
        contract_id=f"meg.{role}.v0",
        schema_fingerprint="a" * 64,
        source_fingerprint="b" * 64,
        level=CertificationLevel.VALIDATED,
    )


def _effect(
    *,
    operator_id: str,
    before: MemoryVersionToken | None,
    after: MemoryVersionToken | None,
    scope: EffectScope | None = None,
    committed: bool = True,
    publication_visible: bool = False,
    durable: bool = True,
) -> MemoryEffectJournalEntry:
    return MemoryEffectJournalEntry(
        effect_id=f"effect-{operator_id}",
        graph_id="graph-a",
        source_sequence=3,
        operator_instance_id=operator_id,
        state_version_before=before,
        effect_type=EffectKind.UPDATE,
        effect_scope=scope or EffectScope.entities("graph-a", {"entity:alice"}),
        mutation_started_ns=120,
        mutation_committed_ns=130 if committed else None,
        mutation_committed=committed,
        publication_visible=publication_visible,
        state_version_after=after,
        transaction_id=after.transaction_id if committed and after is not None else None,
        evidence_hash="c" * 64,
        durable=durable,
    )


def _requests(
    lineage: OperatorLineage,
    *,
    count: int = 1,
) -> tuple[RequestLineage, ...]:
    return tuple(
        RequestLineage.create(
            operator_instance_id=lineage.instance_id,
            semantic_subrole=f"llm-step-{index}",
            request_ordinal=index,
            coroutine_id=lineage.coroutine_id or "coroutine-0",
            created_ns=112 + index * 10,
            enqueue_ns=113 + index * 10,
            start_ns=114 + index * 10,
            end_ns=118 + index * 10,
            transport_request_id=f"transport-{index}",
        )
        for index in range(count)
    )


def test_child_identity_is_content_deterministic_and_completion_order_independent() -> None:
    key = ChildKey.from_mapping(
        {
            "extracted_edge_uuid": "edge-7",
            "source_node_uuid": "node-a",
            "target_node_uuid": "node-b",
            "fact_hash": "f" * 64,
        }
    )
    parent = _lineage(role="graphiti.resolve_extracted_edges")
    left = OperatorLineage.child(parent, semantic_role="graphiti.resolve_extracted_edge", child_key=key)
    right = OperatorLineage.child(parent, semantic_role="graphiti.resolve_extracted_edge", child_key=key)
    assert left.instance_id == right.instance_id
    assert left.child_key == key
    assert "completion" not in key.canonical_json().lower()


def test_identity_rejects_completion_order_and_ambiguous_child_keys() -> None:
    with pytest.raises(ValueError, match="heuristic_identity_forbidden"):
        derive_operator_instance_id(
            graph_id="g",
            stream_id="s",
            source_sequence=0,
            semantic_role="role",
            adapter_revision="r",
            completion_order=1,
        )
    parent = _lineage(role="group")
    builder = LineageBuilder(parent)
    key = ChildKey.from_mapping({"edge_uuid": "e1"})
    builder.add_child(semantic_role="child", child_key=key)
    with pytest.raises(ValueError, match="duplicate_child_key"):
        builder.add_child(semantic_role="child", child_key=key)
    with pytest.raises(ValueError, match="missing_child_key"):
        builder.add_child(semantic_role="child", child_key=None)
    with pytest.raises(ValueError, match="unexpected_child_key"):
        builder.finalize(expected_keys=())


def test_duplicate_structured_input_requires_explicit_ordinal() -> None:
    key0 = ChildKey.from_mapping({"fact_hash": "same"}, duplicate_ordinal=0)
    key1 = ChildKey.from_mapping({"fact_hash": "same"}, duplicate_ordinal=1)
    assert key0 != key1
    assert key0.canonical_json() != key1.canonical_json()


def test_sequential_subrequests_have_deterministic_child_lineage() -> None:
    lineage = _lineage()
    first = _requests(lineage, count=2)
    second = _requests(lineage, count=2)
    assert [request.request_instance_id for request in first] == [
        request.request_instance_id for request in second
    ]
    assert [request.request_ordinal for request in first] == [0, 1]
    assert first[0].request_instance_id != first[1].request_instance_id


def test_l0_contract_cannot_claim_dynamic_state_or_effect_scope() -> None:
    contract = _static()
    assert contract.state_version is None
    assert contract.effect_scope is None
    with pytest.raises(ValueError, match="dynamic_fact_in_static_contract"):
        StaticSemanticContract(
            operator_role="bad",
            operator_type=OperatorType.RESOLUTION,
            namespace="graph-a",
            state_bound=True,
            effect_kind=EffectKind.UPDATE,
            visibility=Visibility.PRIVATE_INTERMEDIATE,
            atomic=True,
            idempotent=True,
            retry_safe=True,
            publication_boundary=False,
            dependency_class="explicit",
            resource_class="llm",
            child_identity_mode="single",
            state_version="m-1",  # type: ignore[arg-type]
        )


def test_graphiti_catalog_distinguishes_helpers_and_child_boundaries() -> None:
    catalog = {entry.operation: entry for entry in graphiti_operator_catalog()}
    assert catalog["extract_nodes"].kind is GraphitiOperatorKind.SEMANTIC
    assert catalog["resolve_extracted_edges"].requires_child_boundary is True
    assert catalog["resolve_edge_pointers"].kind is GraphitiOperatorKind.HELPER
    assert catalog["process_episode_data"].kind is GraphitiOperatorKind.MUTATION
    assert catalog["add_nodes_and_edges_bulk_tx"].kind is GraphitiOperatorKind.TRANSACTION
    assert qualify_operator_boundary(catalog["resolve_extracted_edges"]).status is BoundaryStatus.QUALIFIED


def test_unknown_effect_scope_is_opaque_not_certified() -> None:
    before, after = _tokens()
    entry = _effect(
        operator_id="op-unknown",
        before=before,
        after=after,
        scope=EffectScope.unknown("graph-a"),
    )
    result = validate_effect_entry(entry)
    assert result.status is EffectCertification.OPAQUE
    assert "effect_scope_unknown" in result.codes


def test_namespace_only_effect_scope_is_opaque_not_empty_nonconflict() -> None:
    before, after = _tokens()
    entry = _effect(
        operator_id="op-namespace-only",
        before=before,
        after=after,
        scope=EffectScope.mixed("graph-a"),
    )
    result = validate_effect_entry(entry)
    assert result.status is EffectCertification.OPAQUE
    assert "effect_scope_namespace_only" in result.codes


def test_effect_journal_requires_commit_before_publication() -> None:
    before, after = _tokens()
    with pytest.raises(ValueError, match="publication_without_commit"):
        _effect(
            operator_id="op-bad",
            before=before,
            after=after,
            committed=False,
            publication_visible=True,
            durable=False,
        )
    journal = MemoryEffectJournal()
    entry = _effect(
        operator_id="op-good",
        before=before,
        after=after,
        publication_visible=True,
    )
    journal.append(entry)
    assert journal.for_operator("op-good") == (entry,)


def test_version_token_is_logical_and_rejects_wall_clock_only_evidence() -> None:
    before, _ = _tokens()
    assert validate_version_token(before).status is VersionTokenValidation.CERTIFIED
    with pytest.raises(ValueError, match="logical_counter_required"):
        MemoryVersionToken(
            namespace="graph-a",
            backend_id="fixture-backend",
            epoch="fixture-epoch",
            counter=None,  # type: ignore[arg-type]
            transaction_id="tx",
            evidence_hash="a" * 64,
        )
    with pytest.raises(ValueError, match="wall_clock_only_version_forbidden"):
        MemoryVersionToken.from_external("2026-08-21T00:00:00Z")


def test_commit_version_cannot_be_relabelled_as_another_transaction() -> None:
    before, after = _tokens()
    entry = _effect(operator_id="op-transaction", before=before, after=after)
    with pytest.raises(ValueError, match="after_version_transaction_mismatch"):
        replace(entry, transaction_id="different-transaction")
    with pytest.raises(ValueError, match="publication_version_transaction_mismatch"):
        PublicationEvent.create(
            graph_id="graph-a",
            stream_id="stream-a",
            source_sequence=3,
            predecessor_version=before,
            publication_version=after,
            effect_ids=(entry.effect_id,),
            causal_operator_ids=(entry.operator_instance_id,),
            transaction_id="different-transaction",
            durable_timestamp_ns=140,
            frontier_position=3,
            durable=True,
        )


def test_publication_requires_durable_committed_effect_and_causal_ids() -> None:
    before, after = _tokens()
    operator_id = "op-publish"
    entry = _effect(
        operator_id=operator_id,
        before=before,
        after=after,
        publication_visible=True,
    )
    journal = MemoryEffectJournal()
    journal.append(entry)
    event = PublicationEvent.create(
        graph_id="graph-a",
        stream_id="stream-a",
        source_sequence=3,
        predecessor_version=before,
        publication_version=after,
        effect_ids=(entry.effect_id,),
        causal_operator_ids=(operator_id,),
        transaction_id=entry.transaction_id,
        durable_timestamp_ns=200,
        frontier_position=3,
        durable=True,
    )
    result = validate_publication_event(event, journal)
    assert result.status is PublicationCertification.CERTIFIED
    publication_journal = PublicationJournal()
    publication_journal.append(event, journal=journal)
    assert publication_journal.events == (event,)


def test_publication_rejects_uncommitted_or_wrong_causal_effect() -> None:
    before, after = _tokens()
    entry = _effect(
        operator_id="op-not-visible",
        before=before,
        after=after,
        publication_visible=False,
    )
    journal = MemoryEffectJournal()
    journal.append(entry)
    event = PublicationEvent.create(
        graph_id="graph-a",
        stream_id="stream-a",
        source_sequence=3,
        predecessor_version=before,
        publication_version=after,
        effect_ids=(entry.effect_id,),
        causal_operator_ids=("other-op",),
        transaction_id=entry.transaction_id,
        durable_timestamp_ns=200,
        frontier_position=3,
        durable=True,
    )
    result = validate_publication_event(event, journal)
    assert result.status is PublicationCertification.INVALID
    assert "causal_operator_mismatch" in result.codes


def test_compiler_handles_graphiti_edge_fanout_without_completion_order() -> None:
    before, after = _tokens()
    parent = _lineage(role="graphiti.resolve_extracted_edges")
    child_keys = [
        ChildKey.from_mapping(
            {
                "extracted_edge_uuid": f"edge-{idx}",
                "source_node_uuid": "node-a",
                "target_node_uuid": f"node-{idx}",
                "fact_hash": f"{idx:064x}",
            }
        )
        for idx in range(2)
    ]
    children = [
        OperatorLineage.child(
            parent,
            semantic_role="graphiti.resolve_extracted_edge",
            child_key=key,
            ready_ns=111 + idx,
            enqueue_ns=113,
            start_ns=114,
            end_ns=139,
            coroutine_id=f"edge-coroutine-{idx}",
        )
        for idx, key in enumerate(child_keys)
    ]
    child_entries = [
        _effect(
            operator_id=child.instance_id,
            before=before,
            after=after,
            scope=EffectScope.edges("graph-a", {f"edge:{idx}"}),
        )
        for idx, child in enumerate(children)
    ]
    journal = MemoryEffectJournal()
    for entry in child_entries:
        journal.append(entry)
    inputs = [
        OperatorInput(
            static_contract=_static(role="graphiti.resolve_extracted_edge"),
            lineage=child,
            dynamic=DynamicOperatorEvidence(
                state_version=before,
                read_scope=frozenset({"entity:node-a"}),
                effect_entry=entry,
                terminal=True,
                child_identity_complete=True,
                hidden_effects_possible=False,
                request_lineage=_requests(child, count=2),
                dependency_evidence_complete=True,
                provenance=_provenance("graphiti.resolve_extracted_edge"),
            ),
        )
        for child, entry in zip(children, child_entries, strict=True)
    ]
    # Parent orchestration has no persistent effect in this fixture and is
    # intentionally represented as a private, no-op semantic node.
    inputs.insert(
        0,
        OperatorInput(
            static_contract=StaticSemanticContract(
                operator_role="graphiti.resolve_extracted_edges",
                operator_type=OperatorType.RESOLUTION,
                namespace="graph-a",
                state_bound=True,
                effect_kind=EffectKind.NONE,
                visibility=Visibility.PRIVATE_INTERMEDIATE,
                atomic=True,
                idempotent=True,
                retry_safe=True,
                publication_boundary=False,
                dependency_class="explicit",
                resource_class="cpu",
                child_identity_mode="structured_input",
            ),
            lineage=parent,
            dynamic=DynamicOperatorEvidence(
                state_version=before,
                read_scope=frozenset({"entity:node-a"}),
                effect_entry=None,
                terminal=True,
                child_identity_complete=True,
                hidden_effects_possible=False,
                dependency_evidence_complete=True,
                provenance=_provenance("graphiti.resolve_extracted_edges"),
            ),
        ),
    )
    graph = SemanticCompiler().compile(
        tuple(inputs),
        dependencies=tuple(
            DependencySpec(
                predecessor_id=parent.instance_id,
                successor_id=child.instance_id,
                dependency_type="DATA",
            )
            for child in children
        ),
        effect_journal=journal,
    )
    assert graph.status is CompilationStatus.CERTIFIED
    assert len(graph.operators) == 3
    assert all(item.status.value.startswith("CERTIFIED") for item in graph.operators)


def test_compiler_fails_closed_when_lineage_or_effect_is_missing() -> None:
    before, after = _tokens()
    lineage = _lineage()
    graph = SemanticCompiler().compile(
        (
            OperatorInput(
                static_contract=_static(),
                lineage=lineage,
                dynamic=DynamicOperatorEvidence(
                    state_version=before,
                    read_scope=None,
                    effect_entry=_effect(
                        operator_id=lineage.instance_id,
                        before=before,
                        after=after,
                        scope=EffectScope.unknown("graph-a"),
                    ),
                    terminal=True,
                    child_identity_complete=False,
                    hidden_effects_possible=True,
                    request_lineage=_requests(lineage),
                    dependency_evidence_complete=False,
                ),
            ),
        ),
        dependencies=(),
        effect_journal=None,
    )
    assert graph.status is CompilationStatus.OPAQUE
    assert graph.operators[0].status.value == "OPAQUE"
    assert "effect_scope_unknown" in graph.operators[0].codes


def _snapshot(**overrides: object) -> PassiveExecutionSnapshot:
    base = dict(
        request_count=2,
        prompt_hashes=("a" * 64, "b" * 64),
        model_ids=("model-x", "model-x"),
        response_hashes=("c" * 64, "d" * 64),
        db_query_hashes=("e" * 64,),
        published_graph_hash="f" * 64,
        publication_order=(0, 1),
        source_sequences=(0, 1),
        llm_call_count=2,
        embedding_call_count=1,
        mutation_count=1,
        source_exactly_once=True,
    )
    base.update(overrides)
    return PassiveExecutionSnapshot(**base)


def test_passive_equivalence_detects_no_semantic_change() -> None:
    result = compare_passive_execution(_snapshot(), _snapshot())
    assert result.passed is True
    assert result.violations == ()


def test_passive_equivalence_rejects_extra_llm_or_publication_reordering() -> None:
    result = compare_passive_execution(
        _snapshot(),
        _snapshot(
            request_count=3,
            llm_call_count=3,
            publication_order=(1, 0),
        ),
    )
    assert result.passed is False
    assert "request_count_changed" in result.violations
    assert "extra_llm_call" in result.violations
    assert "publication_order_changed" in result.violations


def test_operator_input_rejects_l0_l1_role_or_namespace_mismatch() -> None:
    lineage = _lineage()
    with pytest.raises(ValueError, match="operator_role_lineage_mismatch"):
        OperatorInput(
            static_contract=_static(role="graphiti.other"),
            lineage=lineage,
            dynamic=DynamicOperatorEvidence(
                state_version=None,
                read_scope=None,
                effect_entry=None,
                terminal=False,
                child_identity_complete=False,
                hidden_effects_possible=True,
            ),
        )
    wrong_graph = OperatorLineage.create(
        graph_id="graph-b",
        stream_id="stream-a",
        source_sequence=3,
        semantic_role="graphiti.resolve_extracted_edge",
        adapter_revision="adapter-v0",
        created_ns=100,
        ready_ns=110,
    )
    with pytest.raises(ValueError, match="operator_namespace_lineage_mismatch"):
        OperatorInput(
            static_contract=_static(),
            lineage=wrong_graph,
            dynamic=DynamicOperatorEvidence(
                state_version=None,
                read_scope=None,
                effect_entry=None,
                terminal=False,
                child_identity_complete=False,
                hidden_effects_possible=True,
            ),
        )


def test_compiler_rejects_effect_read_version_mismatch() -> None:
    before, after = _tokens()
    lineage = _lineage()
    entry = _effect(operator_id=lineage.instance_id, before=before, after=after)
    journal = MemoryEffectJournal()
    journal.append(entry)
    result = SemanticCompiler().compile(
        (
            OperatorInput(
                static_contract=_static(),
                lineage=lineage,
                dynamic=DynamicOperatorEvidence(
                    state_version=after,
                    read_scope=frozenset({"entity:alice"}),
                    effect_entry=entry,
                    terminal=True,
                    child_identity_complete=True,
                    hidden_effects_possible=False,
                    request_lineage=_requests(lineage),
                    dependency_evidence_complete=True,
                ),
            ),
        ),
        dependencies=(),
        effect_journal=journal,
    )
    assert result.status is CompilationStatus.INVALID
    assert "effect_before_version_mismatch" in result.operators[0].codes


def test_compiler_rejects_missing_parent_dependency_provenance() -> None:
    before, after = _tokens()
    parent = _lineage(role="graphiti.resolve_extracted_edges")
    child = OperatorLineage.child(
        parent,
        semantic_role="graphiti.resolve_extracted_edge",
        child_key=ChildKey.from_mapping({"edge_uuid": "edge-1"}),
        ready_ns=111,
        enqueue_ns=112,
        start_ns=113,
        end_ns=119,
        coroutine_id="edge-coroutine",
    )
    journal = MemoryEffectJournal()
    entry = _effect(operator_id=child.instance_id, before=before, after=after)
    journal.append(entry)
    result = SemanticCompiler().compile(
        (
            OperatorInput(
                static_contract=_static(),
                lineage=child,
                dynamic=DynamicOperatorEvidence(
                    state_version=before,
                    read_scope=frozenset({"entity:alice"}),
                    effect_entry=entry,
                    terminal=True,
                    child_identity_complete=True,
                    hidden_effects_possible=False,
                    request_lineage=_requests(child),
                    dependency_evidence_complete=True,
                ),
            ),
        ),
        dependencies=(),
        effect_journal=journal,
    )
    assert result.status is CompilationStatus.INVALID
    assert "lineage_parent_missing" in result.codes


def test_compiler_fails_closed_without_lifecycle_or_request_lineage() -> None:
    before, after = _tokens()
    lineage = OperatorLineage.create(
        graph_id="graph-a",
        stream_id="stream-a",
        source_sequence=3,
        semantic_role="graphiti.resolve_extracted_edge",
        adapter_revision="adapter-v0",
        created_ns=100,
        ready_ns=None,
    )
    entry = _effect(operator_id=lineage.instance_id, before=before, after=after)
    journal = MemoryEffectJournal()
    journal.append(entry)
    result = SemanticCompiler().compile(
        (
            OperatorInput(
                static_contract=_static(),
                lineage=lineage,
                dynamic=DynamicOperatorEvidence(
                    state_version=before,
                    read_scope=frozenset({"entity:alice"}),
                    effect_entry=entry,
                    terminal=True,
                    child_identity_complete=True,
                    hidden_effects_possible=False,
                    dependency_evidence_complete=True,
                ),
            ),
        ),
        dependencies=(),
        effect_journal=journal,
    )
    assert result.status is CompilationStatus.OPAQUE
    assert "ready_time_missing" in result.operators[0].codes
    assert "completion_time_missing" in result.operators[0].codes
    assert "request_lineage_missing" in result.operators[0].codes


def test_compiler_treats_invalid_publication_causality_as_invalid() -> None:
    before, after = _tokens()
    lineage = _lineage(role="graphiti.publication")
    entry = _effect(
        operator_id=lineage.instance_id,
        before=before,
        after=after,
        publication_visible=True,
    )
    journal = MemoryEffectJournal()
    journal.append(entry)
    publication = PublicationEvent.create(
        graph_id="graph-a",
        stream_id="stream-a",
        source_sequence=3,
        predecessor_version=before,
        publication_version=after,
        effect_ids=(entry.effect_id,),
        causal_operator_ids=("other-operator",),
        transaction_id=entry.transaction_id,
        durable_timestamp_ns=140,
        frontier_position=3,
        durable=True,
    )
    result = SemanticCompiler().compile(
        (
            OperatorInput(
                static_contract=_static(
                    role="graphiti.publication",
                    operator_type=OperatorType.PUBLICATION,
                    publication=True,
                ),
                lineage=lineage,
                dynamic=DynamicOperatorEvidence(
                    state_version=before,
                    read_scope=frozenset({"entity:alice"}),
                    effect_entry=entry,
                    terminal=True,
                    child_identity_complete=True,
                    hidden_effects_possible=False,
                    publication=publication,
                    request_lineage=_requests(lineage),
                    dependency_evidence_complete=True,
                ),
            ),
        ),
        dependencies=(),
        effect_journal=journal,
    )
    assert result.status is CompilationStatus.INVALID
    assert "causal_operator_mismatch" in result.operators[0].codes


def test_compiler_certifies_commit_linked_publishable_operator() -> None:
    before, after = _tokens()
    role = "graphiti.persist_and_publish"
    lineage = _lineage(role=role)
    entry = _effect(
        operator_id=lineage.instance_id,
        before=before,
        after=after,
        publication_visible=True,
    )
    journal = MemoryEffectJournal()
    journal.append(entry)
    publication = PublicationEvent.create(
        graph_id="graph-a",
        stream_id="stream-a",
        source_sequence=3,
        predecessor_version=before,
        publication_version=after,
        effect_ids=(entry.effect_id,),
        causal_operator_ids=(lineage.instance_id,),
        transaction_id=entry.transaction_id,
        durable_timestamp_ns=140,
        frontier_position=3,
        durable=True,
    )
    result = SemanticCompiler().compile(
        (
            OperatorInput(
                static_contract=_static(
                    role=role,
                    operator_type=OperatorType.MUTATION,
                    publication=True,
                ),
                lineage=lineage,
                dynamic=DynamicOperatorEvidence(
                    state_version=before,
                    read_scope=frozenset({"entity:alice"}),
                    effect_entry=entry,
                    terminal=True,
                    child_identity_complete=True,
                    hidden_effects_possible=False,
                    publication=publication,
                    request_lineage=_requests(lineage),
                    dependency_evidence_complete=True,
                    provenance=_provenance(role),
                ),
            ),
        ),
        dependencies=(),
        effect_journal=journal,
    )
    assert result.status is CompilationStatus.CERTIFIED
    assert result.operators[0].status.value == "CERTIFIED_PUBLISHABLE"


def test_passive_certificate_has_stable_artifact_type() -> None:
    certificate = compare_passive_execution(_snapshot(), _snapshot())
    assert certificate.certificate_type == "PASSIVE_EQUIVALENCE_CERTIFICATE"


def test_compiler_does_not_self_validate_missing_adapter_provenance() -> None:
    before, after = _tokens()
    lineage = _lineage()
    entry = _effect(operator_id=lineage.instance_id, before=before, after=after)
    journal = MemoryEffectJournal()
    journal.append(entry)
    result = SemanticCompiler().compile(
        (
            OperatorInput(
                static_contract=_static(),
                lineage=lineage,
                dynamic=DynamicOperatorEvidence(
                    state_version=before,
                    read_scope=frozenset({"entity:alice"}),
                    effect_entry=entry,
                    terminal=True,
                    child_identity_complete=True,
                    hidden_effects_possible=False,
                    request_lineage=_requests(lineage),
                    dependency_evidence_complete=True,
                ),
            ),
        ),
        dependencies=(),
        effect_journal=journal,
    )
    assert result.status is CompilationStatus.OPAQUE
    assert "adapter_provenance_not_validated" in result.operators[0].codes


def test_passive_certificate_rejects_internally_incomplete_capture() -> None:
    result = compare_passive_execution(
        _snapshot(),
        _snapshot(request_count=3),
    )
    assert result.passed is False
    assert "instrumented_request_evidence_count_mismatch" in result.violations


def test_compiler_rejects_journal_write_hidden_by_noop_declaration() -> None:
    before, after = _tokens()
    lineage = _lineage(role="graphiti.resolve_extracted_edges")
    hidden = _effect(operator_id=lineage.instance_id, before=before, after=after)
    journal = MemoryEffectJournal()
    journal.append(hidden)
    result = SemanticCompiler().compile(
        (
            OperatorInput(
                static_contract=StaticSemanticContract(
                    operator_role="graphiti.resolve_extracted_edges",
                    operator_type=OperatorType.RESOLUTION,
                    namespace="graph-a",
                    state_bound=True,
                    effect_kind=EffectKind.NONE,
                    visibility=Visibility.PRIVATE_INTERMEDIATE,
                    atomic=True,
                    idempotent=True,
                    retry_safe=True,
                    publication_boundary=False,
                    dependency_class="explicit",
                    resource_class="cpu",
                    child_identity_mode="structured_input",
                ),
                lineage=lineage,
                dynamic=DynamicOperatorEvidence(
                    state_version=before,
                    read_scope=frozenset({"entity:alice"}),
                    effect_entry=None,
                    terminal=True,
                    child_identity_complete=True,
                    hidden_effects_possible=False,
                    dependency_evidence_complete=True,
                    provenance=_provenance("graphiti.resolve_extracted_edges"),
                ),
            ),
        ),
        dependencies=(),
        effect_journal=journal,
    )
    assert result.status is CompilationStatus.INVALID
    assert "unexpected_effect_journal_entry" in result.operators[0].codes
