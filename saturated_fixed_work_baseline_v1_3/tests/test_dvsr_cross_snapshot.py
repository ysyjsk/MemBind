from __future__ import annotations

from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_cross_snapshot import (
    DvsrCrossSnapshotError,
    build_operator_dag,
    build_offline_benefit,
    derive_offline_benefit_components,
    compare_cross_snapshot,
    resolve_prepared_to_seam_async,
    sanitize_observer_capture,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_workload import (
    DEV_COUNTS,
    DvsrWorkloadError,
    load_development_history_episodes,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import (
    BuildStageBindings,
    BuildStageResult,
    _ensure_single_partition_provenance,
)


def _capture(*, phase: str = "OLD", changed: bool = False, writes: int = 0) -> dict:
    read = {
        "operator": "node_cosine",
        "occurrence": 0,
        "group_ids": ["g"],
        "limit": 10,
        "min_score": 0.6,
        "actual_result": ["n1"],
        "reference_result": ["n1"],
        "cutoff": 0.8,
        "boundary_ties": [],
        "tie_contract": "NO_BOUNDARY_TIE_OBSERVED",
        "query_digest": "q",
        "filter_fingerprint": "f",
        "completeness_status": "COMPLETE",
    }
    request = {
        "prompt_name": "dedupe_nodes.nodes",
        "ordinal": 0,
        "request_identity": "request-a" if not changed else "request-b",
        "field_digests": {"messages": "m" if not changed else "m2"},
    }
    continuation = {"nodes": ["n1"], "cut": "CUT-N"}
    if changed:
        read = {**read, "actual_result": ["n2"]}
        continuation = {"nodes": ["n2"], "cut": "CUT-N"}
    return {
        "phase": phase,
        "source_sequence": 1,
        "reads": [read],
        "requests": [request],
        "continuation_k": continuation,
        "publication_calls": writes,
    }


def test_stable_pair_is_valid_and_has_no_write() -> None:
    result = compare_cross_snapshot(
        _capture(),
        _capture(phase="FRESH_NATIVE"),
        operator_cut="CUT-N",
        prepared_artifact_digest="a" * 64,
    )

    assert result["status"] == "VALID"
    assert result["continuation_exact"] is True
    assert result["no_write"] is True
    assert result["unknown_reasons"] == []
    assert result["reusable_read_keys"] == [["node_cosine", 0]]
    assert result["reusable_request_keys"] == [["dedupe_nodes.nodes", 0]]


def test_changed_result_or_pre_seam_write_never_becomes_valid() -> None:
    result = compare_cross_snapshot(
        _capture(),
        _capture(phase="FRESH_NATIVE", changed=True, writes=1),
        operator_cut="CUT-N",
        prepared_artifact_digest="b" * 64,
    )

    assert result["status"] == "UNKNOWN"
    assert "read_changed:node_cosine:0" in result["unknown_reasons"]
    assert "pre_seam_publication_detected" in result["unknown_reasons"]


def test_read_environment_epoch_change_is_fail_closed() -> None:
    fresh = _capture(phase="FRESH_NATIVE")
    fresh["reads"][0]["config_epoch"] = "different-config"
    result = compare_cross_snapshot(
        _capture(),
        fresh,
        operator_cut="CUT-N",
        prepared_artifact_digest="b" * 64,
    )
    assert result["status"] == "UNKNOWN"
    assert "read_changed:node_cosine:0" in result["unknown_reasons"]


def test_stable_read_and_continuation_make_valid_operator_evidence() -> None:
    old = _capture()
    fresh = _capture(phase="FRESH_NATIVE")
    for capture in (old, fresh):
        capture["requests"] = []
        capture["trace"] = [
            {"phase": "node-resolution", "status": "ok", "start_ns": 0, "end_ns": 100},
        ]
        capture["reads"][0].update(
            {
                "observer_start_ns": 10,
                "native_start_ns": 20,
                "native_end_ns": 40,
                "observer_end_ns": 50,
            }
        )
    result = compare_cross_snapshot(
        old,
        fresh,
        operator_cut="CUT-N",
        prepared_artifact_digest="c" * 64,
    )
    assert result["status"] == "VALID"
    assert result["reusable_read_keys"] == [["node_cosine", 0]]
    dag = build_operator_dag(
        fresh,
        cut="CUT-N",
        reusable_read_keys=[("node_cosine", 0)],
    )
    assert sum(node["cost_ns"] for node in dag["nodes"] if node["reusable"]) == 20


def test_offline_benefit_matches_preregistered_formula() -> None:
    result = build_offline_benefit(
        reuse_hidden_cp_ns=100,
        reconvergence_saved_descendant_cp_ns=30,
        validation_cost_ns=10,
        visible_repair_cp_ns=5,
        failed_speculation_work_ns=20,
        seam_tax_ns=3,
        failed_work_lambda=0.5,
    )

    assert result["offline_benefit_ns"] == pytest.approx(102.0)


def test_offline_benefit_rejects_non_finite_inputs() -> None:
    with pytest.raises(DvsrCrossSnapshotError):
        build_offline_benefit(
            reuse_hidden_cp_ns=float("nan"),
            reconvergence_saved_descendant_cp_ns=0,
            validation_cost_ns=0,
            visible_repair_cp_ns=0,
            failed_speculation_work_ns=0,
            seam_tax_ns=0,
            failed_work_lambda=0,
        )


def test_offline_mismatch_does_not_charge_baseline_fresh_work_as_visible_repair() -> None:
    old_dag = {"status": "COMPLETE", "baseline_cp_ns": 70, "nodes": []}
    fresh_dag = {"status": "COMPLETE", "baseline_cp_ns": 90, "nodes": []}
    result = derive_offline_benefit_components(
        comparison_status="UNKNOWN",
        old_dag=old_dag,
        fresh_dag=fresh_dag,
        validation_cost_ns=5,
        seam_tax_ns=3,
        failed_work_lambda=0.5,
    )

    assert result["failed_speculation_work_ns"] == 70
    # The 90 ns fresh branch is the no-reuse baseline's required work, not
    # treatment-only repair.  Only an explicitly measured extra repair may be
    # charged here.
    assert result["visible_repair_cp_ns"] == 0
    assert result["offline_benefit_ns"] == pytest.approx(-43.0)


def test_capture_sanitizer_drops_prompt_payloads_and_keeps_provenance() -> None:
    capture = _capture()
    capture["episode_kwargs"] = {
        "episode_body": "secret prompt text",
        "api_key": "secret",
        "group_id": "isolated",
    }
    capture["previous_episode"] = {
        "window": [{"content": "old prompt"}],
        "order": ["p1"],
        "projection_digest": "p" * 64,
        "start_ns": 1,
        "end_ns": 2,
    }
    sanitized = sanitize_observer_capture(capture)

    assert "episode_kwargs" not in sanitized
    assert "old prompt" not in str(sanitized)
    assert "secret prompt text" not in str(sanitized)
    assert "api_key" not in str(sanitized)
    assert sanitized["previous_episode"]["order"] == ["p1"]
    assert sanitized["requests"][0]["request_identity"] == "request-a"
    assert sanitized["continuation_k"]["payload_digest"]


def test_continuation_digest_binds_redacted_semantics_not_hidden_object_payload() -> None:
    left = sanitize_observer_capture(
        {"phase": "OLD", "reads": [], "requests": []},
        continuation={"cut": "CUT-N", "nodes": [{"uuid": "n1", "runtime_nonce": "a"}]},
    )
    right = sanitize_observer_capture(
        {"phase": "OLD", "reads": [], "requests": []},
        continuation={"cut": "CUT-N", "nodes": [{"uuid": "n1", "runtime_nonce": "b"}]},
    )
    changed = sanitize_observer_capture(
        {"phase": "OLD", "reads": [], "requests": []},
        continuation={"cut": "CUT-N", "nodes": [{"uuid": "n2", "runtime_nonce": "b"}]},
    )
    assert left["continuation_k"]["payload_digest"] == right["continuation_k"]["payload_digest"]
    assert left["continuation_k"]["payload_digest"] != changed["continuation_k"]["payload_digest"]


def _semantic_continuation(*, nodes: list[dict] | None = None, edges: list[dict] | None = None) -> dict:
    return {
        "schema_version": "membind.dvsr.test-continuation.v1",
        "seam": "before-publication",
        "cut": "CUT-D",
        "source_sequence": 1,
        "group_id": "private-group",
        "backend_epoch": "backend-1",
        "publication_frontier": 1,
        "episodes": [{"uuid": "episode-1", "runtime_nonce": "episode-noise"}],
        "nodes": nodes
        if nodes is not None
        else [
            {
                "uuid": "node-1",
                "name": "Alice",
                "labels": ["Person"],
                "summary": "Alice likes databases.",
                "attributes": {"role": "engineer"},
                "name_embedding": [0.1, 0.2],
                "runtime_nonce": "node-noise",
            }
        ],
        "entity_edges": edges
        if edges is not None
        else [
            {
                "uuid": "edge-1",
                "source_node_uuid": "node-1",
                "target_node_uuid": "node-2",
                "name": "LIKES",
                "fact": "Alice likes databases.",
                "valid_at": "2026-01-01T00:00:00Z",
                "invalid_at": None,
                "expired_at": None,
                "reference_time": "2026-01-01T00:00:00Z",
                "attributes": {"confidence": "high"},
                "fact_embedding": [0.3, 0.4],
                "runtime_nonce": "edge-noise",
            }
        ],
    }


def _continuation_digest(value: dict) -> str:
    sanitized = sanitize_observer_capture(
        {"phase": "OLD", "reads": [], "requests": []},
        continuation=value,
    )
    encoded = __import__("json").dumps(sanitized["continuation_k"], sort_keys=True)
    assert "Alice likes databases" not in encoded
    assert "private-group" not in encoded
    return sanitized["continuation_k"]["payload_digest"]


def test_continuation_same_node_uuid_but_summary_change_is_not_exact() -> None:
    left = _semantic_continuation()
    right = _semantic_continuation()
    right["nodes"][0]["summary"] = "Alice now studies compilers."
    assert _continuation_digest(left) != _continuation_digest(right)


def test_continuation_same_node_uuid_but_labels_change_is_not_exact() -> None:
    left = _semantic_continuation()
    right = _semantic_continuation()
    right["nodes"][0]["labels"] = ["Person", "Researcher"]
    assert _continuation_digest(left) != _continuation_digest(right)


def test_continuation_same_edge_uuid_but_temporal_change_is_not_exact() -> None:
    left = _semantic_continuation()
    right = _semantic_continuation()
    right["entity_edges"][0]["invalid_at"] = "2026-02-01T00:00:00Z"
    assert _continuation_digest(left) != _continuation_digest(right)


def test_continuation_nonsemantic_runtime_fields_do_not_create_false_mismatch() -> None:
    left = _semantic_continuation()
    right = _semantic_continuation()
    right["nodes"][0]["runtime_nonce"] = "different-node-noise"
    right["entity_edges"][0]["runtime_nonce"] = "different-edge-noise"
    right["episodes"][0]["runtime_nonce"] = "different-episode-noise"
    assert _continuation_digest(left) == _continuation_digest(right)


def test_continuation_semantic_order_change_is_not_exact() -> None:
    first = _semantic_continuation()["nodes"][0]
    second = {**first, "uuid": "node-2", "name": "Bob"}
    left = _semantic_continuation(nodes=[first, second])
    right = _semantic_continuation(nodes=[second, first])
    assert _continuation_digest(left) != _continuation_digest(right)


def test_continuation_identical_semantic_projection_is_exact() -> None:
    left = _semantic_continuation()
    right = _semantic_continuation()
    assert _continuation_digest(left) == _continuation_digest(right)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("name", "Alice Cooper"),
        ("attributes", {"role": "researcher"}),
        ("name_embedding", [0.1, 0.25]),
    ],
)
def test_continuation_node_downstream_semantics_are_digest_bound(field: str, changed: object) -> None:
    left = _semantic_continuation()
    right = _semantic_continuation()
    right["nodes"][0][field] = changed
    assert _continuation_digest(left) != _continuation_digest(right)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("source_node_uuid", "node-3"),
        ("target_node_uuid", "node-4"),
        ("fact", "Alice studies databases."),
        ("attributes", {"confidence": "low"}),
        ("fact_embedding", [0.3, 0.45]),
    ],
)
def test_continuation_edge_downstream_semantics_are_digest_bound(field: str, changed: object) -> None:
    left = _semantic_continuation()
    right = _semantic_continuation()
    right["entity_edges"][0][field] = changed
    assert _continuation_digest(left) != _continuation_digest(right)


def test_continuation_publication_input_mapping_is_digest_bound() -> None:
    left = _semantic_continuation()
    right = _semantic_continuation()
    left["node_episode_index_map"] = {"node-1": [0]}
    right["node_episode_index_map"] = {"node-1": [1]}
    assert _continuation_digest(left) != _continuation_digest(right)


def test_capture_sanitizer_preserves_read_timing_for_semantic_dag() -> None:
    capture = _capture()
    capture["reads"][0].update(
        {
            "observer_start_ns": 10,
            "native_start_ns": 12,
            "native_end_ns": 20,
            "observer_end_ns": 22,
            "query": [1.0, 0.0],
        }
    )
    sanitized = sanitize_observer_capture(capture)
    read = sanitized["reads"][0]
    assert (read["observer_start_ns"], read["native_start_ns"], read["native_end_ns"], read["observer_end_ns"]) == (10, 12, 20, 22)
    assert "query" not in read


def test_operator_dag_uses_phase_wall_time_and_does_not_double_count_overlaps() -> None:
    capture = {
        "trace": [
            {"phase": "node-resolution", "status": "ok", "start_ns": 0, "end_ns": 100},
            {"phase": "edge-extraction", "status": "ok", "start_ns": 100, "end_ns": 220},
            {"phase": "edge-resolution", "status": "ok", "start_ns": 220, "end_ns": 300},
            {"phase": "attributes-summary", "status": "ok", "start_ns": 300, "end_ns": 360},
        ],
        "requests": [
            {"prompt_name": "dedupe_nodes.nodes", "cut_occurrence": 0, "start_ns": 10, "end_ns": 80},
            {"prompt_name": "dedupe_nodes.nodes", "cut_occurrence": 1, "start_ns": 20, "end_ns": 90},
            {"prompt_name": "extract_edges.edge", "cut_occurrence": 0, "start_ns": 120, "end_ns": 180},
        ],
        "reads": [
            {"operator": "node_cosine", "occurrence": 0, "observer_start_ns": 0, "native_start_ns": 10, "native_end_ns": 30, "observer_end_ns": 50},
            {"operator": "node_cosine", "occurrence": 1, "observer_start_ns": 5, "native_start_ns": 15, "native_end_ns": 35, "observer_end_ns": 55},
        ],
    }
    dag = build_operator_dag(
        capture,
        cut="CUT-D",
        reusable_request_keys=[("dedupe_nodes.nodes", 0), ("dedupe_nodes.nodes", 1)],
        reusable_read_keys=[("node_cosine", 0), ("node_cosine", 1)],
    )
    assert dag["status"] == "COMPLETE"
    assert dag["baseline_cp_ns"] == 360
    # The two overlapping Node requests save only their union (10..90), not
    # 70+70; the Edge request remains non-removable.
    assert sum(node["cost_ns"] for node in dag["nodes"] if node["reusable"]) == 80
    # C0 validation is the union of native requery intervals [10,35), while
    # observer-only domain/proof overhead is reported separately as
    # [0,15)+[30,55).  Neither interval is double counted.
    assert dag["certificate_cost_ub_ns"] == 25
    assert dag["c0_validation_cost_ns"] == 25
    assert dag["observer_only_overhead_ns"] == 40


def test_operator_dag_marks_stable_semantic_read_as_reusable() -> None:
    capture = {
        "trace": [
            {"phase": "node-resolution", "status": "ok", "start_ns": 0, "end_ns": 100},
        ],
        "requests": [],
        "reads": [
            {
                "operator": "node_cosine",
                "occurrence": 0,
                "native_start_ns": 20,
                "native_end_ns": 40,
            }
        ],
    }
    dag = build_operator_dag(
        capture,
        cut="CUT-N",
        reusable_read_keys=[("node_cosine", 0)],
    )
    assert dag["status"] == "COMPLETE"
    read_node = next(node for node in dag["nodes"] if node.get("read_keys") == [["node_cosine", 0]])
    assert read_node["reusable"] is True
    assert read_node["cost_ns"] == 20
    assert dag["removable_node_ids"] == [read_node["node_id"]]


def test_partial_exact_request_reuse_is_credited_without_double_charging_failed_work() -> None:
    old_dag = {
        "status": "COMPLETE",
        "baseline_cp_ns": 100,
        "nodes": [
            {"cost_ns": 40, "reusable": True},
            {"cost_ns": 60, "reusable": False},
        ],
    }
    fresh_dag = {
        "status": "COMPLETE",
        "baseline_cp_ns": 100,
        "nodes": [
            {"cost_ns": 50, "reusable": True},
            {"cost_ns": 50, "reusable": False},
        ],
    }
    result = derive_offline_benefit_components(
        comparison_status="UNKNOWN",
        old_dag=old_dag,
        fresh_dag=fresh_dag,
        validation_cost_ns=10,
        seam_tax_ns=0,
        failed_work_lambda=0.5,
    )
    assert result["reuse_hidden_cp_ns"] == 50
    assert result["failed_speculation_work_ns"] == 60
    assert result["offline_benefit_ns"] == pytest.approx(10.0)


def test_whole_cut_validity_credits_full_cp_but_still_charges_c0_validation() -> None:
    old_dag = {"status": "COMPLETE", "baseline_cp_ns": 100, "nodes": []}
    fresh_dag = {"status": "COMPLETE", "baseline_cp_ns": 120, "nodes": []}
    result = derive_offline_benefit_components(
        comparison_status="VALID",
        old_dag=old_dag,
        fresh_dag=fresh_dag,
        validation_cost_ns=20,
        seam_tax_ns=0,
        failed_work_lambda=0.5,
    )
    assert result["reuse_hidden_cp_ns"] == 120
    assert result["failed_speculation_work_ns"] == 0
    assert result["offline_benefit_ns"] == pytest.approx(100.0)


def test_capture_sanitizer_rejects_missing_continuation() -> None:
    with pytest.raises(DvsrCrossSnapshotError):
        sanitize_observer_capture({"phase": "OLD", "reads": [], "requests": []})


def test_development_workload_uses_sealed_history_mapping() -> None:
    episodes = load_development_history_episodes(
        repository_root=__import__("pathlib").Path(__file__).resolve().parents[2],
        history_id="b6019101",
        source_count=2,
    )
    assert len(episodes) == 2
    assert episodes[0].context_id == "b6019101"
    assert [item.source_sequence for item in episodes] == [0, 1]
    assert DEV_COUNTS["b6019101"] == 49


def test_development_workload_rejects_held_out_history() -> None:
    with pytest.raises(DvsrWorkloadError):
        load_development_history_episodes(
            repository_root=__import__("pathlib").Path(__file__).resolve().parents[2],
            history_id="b01defab",
            source_count=2,
        )


def test_single_partition_provenance_bridge_seeds_edge_partition_context() -> None:
    client = SimpleNamespace(
        _membind_entity_partition_sources_by_scope={},
        _membind_entity_partition_hints_by_scope={},
    )
    graphiti = SimpleNamespace(llm_client=client)
    episode = SimpleNamespace(content="[USER] source body")
    _ensure_single_partition_provenance(
        graphiti,
        episode,
        [SimpleNamespace(name="Entity One")],
    )
    assert client._membind_entity_partition_sources_by_scope[(None, None)] == {
        0: "[USER] source body",
        -1: "[USER] source body",
    }
    assert client._membind_entity_partition_hints_by_scope[(None, None)] == {
        "entity one": [0]
    }


def test_single_partition_provenance_bridge_walks_capture_wrapper_chain() -> None:
    leaf = SimpleNamespace(
        _membind_entity_partition_sources_by_scope={},
        _membind_entity_partition_hints_by_scope={},
    )
    wrapped = SimpleNamespace(inner=SimpleNamespace(inner=leaf))
    graphiti = SimpleNamespace(llm_client=wrapped)
    episode = SimpleNamespace(content="[USER] wrapped source")
    _ensure_single_partition_provenance(
        graphiti,
        episode,
        [SimpleNamespace(name="Wrapped Entity")],
    )
    assert leaf._membind_entity_partition_sources_by_scope[(None, None)] == {
        0: "[USER] wrapped source",
        -1: "[USER] wrapped source",
    }
    assert leaf._membind_entity_partition_hints_by_scope[(None, None)]["wrapped entity"] == [0]


@pytest.mark.asyncio
async def test_prepared_cut_n_does_not_reextract_or_write() -> None:
    calls: list[str] = []

    async def resolve_nodes(_graphiti, nodes, _episode, _previous, _kwargs):
        calls.append("resolve_nodes")
        return nodes, {"e1": "n1"}, []

    async def fail_if_called(*_args, **_kwargs):
        calls.append("unexpected_suffix")
        raise AssertionError("CUT-N must stop before edge/summary")

    def continuation_k(**kwargs):
        return kwargs

    bindings = BuildStageBindings(
        now=lambda: "now",
        retrieve_previous=fail_if_called,
        make_episode=lambda *_args: None,
        extract_nodes=fail_if_called,
        resolve_nodes=resolve_nodes,
        extract_resolve_edges=fail_if_called,
        extract_attributes=fail_if_called,
        continuation_k=continuation_k,
    )
    prepared = BuildStageResult(
        episode=SimpleNamespace(source_sequence=1),
        previous_episodes=(),
        extracted_nodes=(SimpleNamespace(uuid="e1", name="x"),),
        nodes=(),
        entity_edges=(),
        node_episode_index_map={},
        continuation_k={"prepared": True},
    )

    result = await resolve_prepared_to_seam_async(
        SimpleNamespace(),
        prepared,
        {"group_id": "g", "source_sequence": 1},
        cut="CUT-N",
        publication_frontier=0,
        backend_epoch="backend-1",
        read_epoch="state-0",
        bindings=bindings,
    )

    assert result.cut == "CUT-N"
    assert result.database_writes == 0
    assert calls == ["resolve_nodes"]


def test_build_stage_preserves_raw_node_resolution_for_cut_n() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import build_to_seam_async

    async def resolve_nodes(_graphiti, extracted, _episode, _previous, _kwargs):
        return list(extracted), {"e1": "n1"}, []

    async def edges(*_args):
        return [], [], []

    async def attributes(_graphiti, nodes, *_args):
        return [SimpleNamespace(uuid="hydrated", name_embedding=[0.1]) for _ in nodes]

    bindings = BuildStageBindings(
        now=lambda: "now",
        retrieve_previous=lambda *_args: (),
        make_episode=lambda *_args: SimpleNamespace(source_sequence=0, content="x"),
        extract_nodes=lambda *_args: ([SimpleNamespace(uuid="raw", name="x")], {}),
        resolve_nodes=resolve_nodes,
        extract_resolve_edges=edges,
        extract_attributes=attributes,
        continuation_k=lambda **kwargs: kwargs,
    )
    import asyncio

    stage = asyncio.run(
        build_to_seam_async(
            SimpleNamespace(),
            {"group_id": "g", "source_sequence": 0},
            publication_frontier=0,
            backend_epoch="backend-1",
            bindings=bindings,
        )
    )
    assert [node.uuid for node in stage.resolved_nodes] == ["raw"]
    assert [node.uuid for node in stage.nodes] == ["hydrated"]


@pytest.mark.asyncio
async def test_fresh_state_refreshes_previous_context_instead_of_reusing_prepared_snapshot() -> None:
    calls: list[str] = []

    async def resolve_nodes(_graphiti, nodes, _episode, previous, _kwargs):
        calls.append(f"resolve:{tuple(previous)}")
        return nodes, {"e1": "n1"}, []

    async def retrieve_previous(_graphiti, _kwargs):
        calls.append("retrieve")
        return ("current-state-previous",)

    bindings = BuildStageBindings(
        now=lambda: "now",
        retrieve_previous=retrieve_previous,
        make_episode=lambda *_args: None,
        extract_nodes=lambda *_args: ([], {}),
        resolve_nodes=resolve_nodes,
        extract_resolve_edges=lambda *_args: ([], [], []),
        extract_attributes=lambda *_args: [],
        continuation_k=lambda **kwargs: kwargs,
    )
    prepared = BuildStageResult(
        episode=SimpleNamespace(source_sequence=1),
        previous_episodes=("old-snapshot-previous",),
        extracted_nodes=(SimpleNamespace(uuid="e1", name="x"),),
        nodes=(),
        entity_edges=(),
        node_episode_index_map={},
        continuation_k={"now": "now", "prepared": True},
    )

    await resolve_prepared_to_seam_async(
        SimpleNamespace(),
        prepared,
        {"group_id": "g", "source_sequence": 1},
        cut="CUT-N",
        publication_frontier=1,
        backend_epoch="backend-1",
        read_epoch="state-1",
        bindings=bindings,
        refresh_previous=True,
    )

    assert calls == ["retrieve", "resolve:('current-state-previous',)"]


@pytest.mark.asyncio
async def test_prepared_cut_d_executes_suffix_and_keeps_zero_writes() -> None:
    calls: list[str] = []

    async def resolve_nodes(_graphiti, nodes, _episode, _previous, _kwargs):
        calls.append("resolve_nodes")
        return nodes, {"e1": "n1"}, []

    async def edges(_graphiti, _episode, _extracted, _previous, _nodes, _uuid_map, _kwargs):
        calls.append("edges")
        return ([SimpleNamespace(uuid="edge", source_node_uuid="n1", target_node_uuid="n1", fact_embedding=[0.1])], [], [])

    async def attributes(_graphiti, nodes, _episode, _previous, _new_edges, _kwargs):
        calls.append("attributes")
        return [SimpleNamespace(uuid="n1", name_embedding=[0.2]) for _ in nodes]

    def continuation_k(**kwargs):
        return {"schema_version": "x", **kwargs}

    bindings = BuildStageBindings(
        now=lambda: "now",
        retrieve_previous=lambda *_args: (),
        make_episode=lambda *_args: None,
        extract_nodes=lambda *_args: ([], {}),
        resolve_nodes=resolve_nodes,
        extract_resolve_edges=edges,
        extract_attributes=attributes,
        continuation_k=continuation_k,
    )
    prepared = BuildStageResult(
        episode=SimpleNamespace(source_sequence=1),
        previous_episodes=(),
        extracted_nodes=(SimpleNamespace(uuid="e1", name="x"),),
        nodes=(),
        entity_edges=(),
        node_episode_index_map={},
        continuation_k={"now": "now", "prepared": True},
    )
    graphiti = SimpleNamespace(
        driver=SimpleNamespace(provider=SimpleNamespace(value="neo4j"), _database="neo4j"),
        store_raw_episode_content=True,
    )

    result = await resolve_prepared_to_seam_async(
        graphiti,
        prepared,
        {"group_id": "g", "source_sequence": 1},
        cut="CUT-D",
        publication_frontier=0,
        backend_epoch="backend-1",
        read_epoch="state-0",
        bindings=bindings,
    )

    assert calls == ["resolve_nodes", "edges", "attributes"]
    assert result.database_writes == 0
    assert result.entity_edges
