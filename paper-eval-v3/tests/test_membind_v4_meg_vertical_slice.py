from __future__ import annotations

import asyncio

from paper_eval.membind_v4.mseg.runtime_instrumentation import OperatorEventType
from paper_eval.membind_v4.mseg.vertical_slice import Graphiti0293BindVerticalSlice


def test_graphiti_0293_bind_vertical_slice_reaches_effect_commit_epoch_publication() -> None:
    result = asyncio.run(Graphiti0293BindVerticalSlice().run())
    assert result.prepared_artifact.raw_nodes
    assert result.bind_observation.source_sequence == 0
    assert result.mutation_epoch.snapshot().counter == 1
    assert result.recorder.persistent_effect_hashes
    commits = [event for event in result.recorder.events if event.event_type is OperatorEventType.TRANSACTION_COMMIT]
    publications = [event for event in result.recorder.events if event.event_type is OperatorEventType.PUBLICATION]
    assert len(commits) == len(publications) == 1
    assert commits[0].event_sequence < publications[0].event_sequence
    assert result.request_lineage_complete
    submitted = {
        event["request_id"]: event
        for event in result.request_events
        if event.get("event_type") == "llm_request_submitted"
    }
    assert set(submitted) == {span.request_id for span in result.recorder.request_spans}
    for span in result.recorder.request_spans:
        event = submitted[span.request_id]
        assert event["operator_id"] == span.semantic_operator_id
        assert event["prompt_name"] == span.prompt_name
        assert event["semantic_subrequest_role"] == span.semantic_subrequest_role


def test_graphiti_0293_edge_path_has_precreated_children_and_batch_node_operators() -> None:
    result = asyncio.run(Graphiti0293BindVerticalSlice(edge_fact="Alice works at Acme.").run())
    types = [operator.semantic_operator_type for operator in result.recorder.operators]
    assert "EDGE_RESOLUTION_GROUP" in types
    assert "EDGE_RESOLUTION_CHILD" in types
    assert "NODE_BATCH_RESOLUTION_DECISION" in types
    children = [operator for operator in result.recorder.operators if operator.semantic_operator_type == "EDGE_RESOLUTION_CHILD"]
    assert all(operator.materialized_before_coroutine for operator in children)
    assert len({operator.semantic_operator_id for operator in children}) == len(children)
    assert all(span.semantic_operator_id in {operator.semantic_operator_id for operator in result.recorder.operators} for span in result.recorder.request_spans)


def test_async_edge_children_keep_exact_lineage_when_completion_is_reversed() -> None:
    result = asyncio.run(
        Graphiti0293BindVerticalSlice(
            edge_facts=("Alice works at Acme.", "Alice leads Acme."),
            reverse_edge_completion=True,
        ).run()
    )
    children = [
        operator
        for operator in result.recorder.operators
        if operator.semantic_operator_type == "EDGE_RESOLUTION_CHILD"
    ]
    assert [child.child_ordinal for child in children] == [0, 1]
    child_ids = [child.semantic_operator_id for child in children]
    child_spans = [span for span in result.recorder.request_spans if span.semantic_operator_id in child_ids]
    dedupe = [span for span in child_spans if span.prompt_name == "dedupe_edges.resolve_edge"]
    assert [span.semantic_operator_id for span in dedupe] == list(reversed(child_ids))
    for child_id in child_ids:
        assert [
            span.prompt_name
            for span in child_spans
            if span.semantic_operator_id == child_id
        ] == [
            "dedupe_edges.resolve_edge",
            "extract_edges.extract_attributes",
            "extract_edges.extract_timestamps",
        ]
    submitted = {
        event["request_id"]: event
        for event in result.request_events
        if event.get("event_type") == "llm_request_submitted"
    }
    for span in child_spans:
        event = submitted[span.request_id]
        assert event["operator_id"] == span.semantic_operator_id
        assert event["semantic_subrequest_role"] == span.semantic_subrequest_role
        assert event["prompt_name"] == span.prompt_name


def test_node_resolution_preserves_one_multi_input_batch_request() -> None:
    result = asyncio.run(Graphiti0293BindVerticalSlice().run())
    types = [operator.semantic_operator_type for operator in result.recorder.operators]
    assert types.count("NODE_CANDIDATE_READ") == 1
    assert types.count("DETERMINISTIC_SIMILARITY") == 2
    assert types.count("UNRESOLVED_SET_FORMATION") == 1
    assert types.count("NODE_BATCH_RESOLUTION_DECISION") == 1
    assert types.count("IDENTITY_MATERIALIZATION") == 1
    batch = next(
        operator
        for operator in result.recorder.operators
        if operator.semantic_operator_type == "NODE_BATCH_RESOLUTION_DECISION"
    )
    batch_spans = [
        span
        for span in result.recorder.request_spans
        if span.semantic_operator_id == batch.semantic_operator_id
    ]
    assert [span.prompt_name for span in batch_spans] == ["dedupe_nodes.nodes"]
