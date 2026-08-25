from __future__ import annotations

from dataclasses import replace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.certificates import (
    CertificateStatus,
    Witness,
    certify_bm25,
    certify_exact_topk,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.demand import (
    DemandNode,
    ReplayAdmissibility,
    ReplayStatus,
    check_demand_validity,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.lineage import (
    AlignmentStatus,
    DependencyGraph,
    DependencyKind,
    align_names,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.opportunity import (
    DagNode,
    counterfactual,
    longest_path,
    work_ratio,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.propagation import (
    PropagationNode,
    propagate,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.semantics import (
    NodeKind,
    SemanticTrace,
    SnapshotToken,
    TraceNode,
    alpha_equivalent,
    continuation_equivalent,
    validate_snapshot_soundness,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.state_delta import (
    DeltaChange,
    ObservableSpec,
    StateDelta,
    complete_delta,
)


def test_observer_off_returns_exact_native_result_and_no_observation() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.observer import Observer

    calls: list[str] = []
    observer = Observer(enabled=False)
    result = observer.run("native", lambda: calls.append("called") or {"state": 1})
    assert result == {"state": 1}
    assert calls == ["called"]
    assert observer.records == ()


def test_async_completion_order_is_not_a_semantic_edge() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.observer import semantic_edges

    first = semantic_edges([{"id": "a", "completion": 3}, {"id": "b", "completion": 1}])
    second = semantic_edges([{"id": "a", "completion": 1}, {"id": "b", "completion": 3}])
    assert first == second == ()


def test_observation_schema_requires_environment_and_effect_epochs() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.observer import ObservationError, validate_record

    with pytest.raises(ObservationError, match="required observation field"):
        validate_record({"snapshot": {"version": 1}})


def test_m2_closed_plan_rejects_hidden_apply_read_and_frontier_jump() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.m2_theory import (
        ApplyPlan,
        Frontier,
        validate_apply_plan,
    )

    plan = ApplyPlan(1, 0, preconditions={"frontier": 0}, effects=("write",), idempotency_key="k", hidden_reads=("embedder",))
    with pytest.raises(ValueError, match="hidden read"):
        validate_apply_plan(plan, Frontier(0))
    clean = ApplyPlan(1, 0, preconditions={"frontier": 0}, effects=("write",), idempotency_key="k")
    with pytest.raises(ValueError, match="frontier"):
        validate_apply_plan(clean, Frontier(2))


def test_r3_confusion_matrix_keeps_unknown_separate_and_false_stable_zero() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.analysis import confusion_matrix, false_stable_rate

    matrix = confusion_matrix(
        ["STABLE", "INVALID", "UNKNOWN", "STABLE"],
        ["SAME", "CHANGED", "SAME", "SAME"],
    )
    assert matrix["STABLE/SAME"] == 2
    assert matrix["INVALID/CHANGED"] == 1
    assert matrix["UNKNOWN/SAME"] == 1
    assert false_stable_rate(matrix) == 0.0


def test_csp_unions_overlapping_intervals_and_handles_zero_denominator() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.analysis import certifiable_stable_portion

    assert certifiable_stable_portion([(0, 10), (5, 15)], [(0, 20)]) == 0.75
    assert certifiable_stable_portion([], []) is None


def test_r3_metrics_keep_resource_ratios_independent() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.analysis import semantic_change_amplification

    values = semantic_change_amplification({"wall": (2, 6), "tokens": (10, 40)})
    assert values == {"wall": 3.0, "tokens": 4.0}


def test_observer_enabled_rejects_malformed_replay_contract() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.observer import ObservationError, Observer, REQUIRED_FIELDS

    record = {field: None for field in REQUIRED_FIELDS}
    record["replay_contract"] = {"status": "BROKEN"}
    with pytest.raises(ObservationError, match="replay contract"):
        Observer(enabled=True).run("read", lambda: 1, record=record)


def test_observer_campaign_runs_two_source_without_treatment_or_publication() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.campaign import run_r2_observer

    result = run_r2_observer(seed=17)
    assert result["status"] == "OBSERVER_ONLY"
    assert result["treatment_calls"] == 0
    assert result["publication_calls"] == 0
    assert result["canonical_seam_equal"] is True


def test_r3_block_has_zero_false_stable_or_marks_undefined() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.campaign import run_r3_block

    result = run_r3_block(seed=17, source_count=6)
    assert result["status"] == "OBSERVER_ONLY"
    assert result["false_stable_rate"] in {0.0, None}
    assert result["treatment_calls"] == 0


def test_v7_live_runner_defaults_to_dry_run_and_redacts_api_key(tmp_path, monkeypatch) -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, redact_config

    monkeypatch.setenv("SILICONFLOW_API_KEY", "secret-key-for-test")
    config = V7LiveConfig(output_root=tmp_path / "run", run_id="v7-dry-run")
    assert config.dry_run is True
    redacted = redact_config(config)
    assert "secret-key-for-test" not in repr(redacted)
    assert redacted["api_key_present"] is True


def test_v7_live_runner_rejects_live_without_method_selection_seal(tmp_path) -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, V7LiveRunnerError, validate_live_gate

    config = V7LiveConfig(output_root=tmp_path / "run", run_id="v7-live", dry_run=False, method="M1", gate_path=tmp_path / "missing.json")
    with pytest.raises(V7LiveRunnerError, match="method selection"):
        validate_live_gate(config)


def test_v7_live_runner_dry_run_is_provider_free_and_writes_recoverable_manifest(tmp_path) -> None:
    import asyncio

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, run_v7_live_async

    config = V7LiveConfig(output_root=tmp_path / "run", run_id="v7-dry-run")
    calls: list[str] = []
    result = asyncio.run(run_v7_live_async(config, provider_call=lambda: calls.append("provider")))
    assert result["status"] == "DRY_RUN"
    assert result["provider_calls"] == 0
    assert calls == []
    assert (tmp_path / "run" / "RUN_MANIFEST.json").exists()


def test_v7_live_runner_calls_only_injected_provider_after_gate_and_never_seals_key(tmp_path, monkeypatch) -> None:
    import asyncio
    import json

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, run_v7_live_async

    gate = tmp_path / "METHOD_SELECTION.json"
    gate.write_text(json.dumps({"status": "PASS", "selected_method": "M1", "treatment_authorized": True}), encoding="utf-8")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "secret-key-for-test")
    config = V7LiveConfig(output_root=tmp_path / "live", run_id="v7-live", method="M1", dry_run=False, gate_path=gate)
    calls: list[str] = []
    result = asyncio.run(run_v7_live_async(config, provider_call=lambda: calls.append("provider") or {"ok": True}))
    assert result["status"] == "LIVE_AUTHORIZED"
    assert calls == ["provider"]
    assert "secret-key-for-test" not in (tmp_path / "live" / "RUN_MANIFEST.json").read_text()


def test_v7_live_runner_persists_sanitized_failure_before_propagating_error(tmp_path, monkeypatch) -> None:
    import asyncio
    import json

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, run_v7_live_async

    gate = tmp_path / "METHOD_SELECTION.json"
    gate.write_text(json.dumps({"status": "PASS", "selected_method": "M1", "treatment_authorized": True}), encoding="utf-8")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "secret-key-for-test")
    config = V7LiveConfig(output_root=tmp_path / "live-failure", run_id="v7-live-failure", method="M1", dry_run=False, gate_path=gate)

    def failed_provider():
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(run_v7_live_async(config, provider_call=failed_provider))
    failure = json.loads((tmp_path / "live-failure" / "RUN_FAILURE.json").read_text())
    assert failure["error_type"] == "builtins.RuntimeError"
    assert "secret-key-for-test" not in (tmp_path / "live-failure" / "RUN_FAILURE.json").read_text()


def test_snapshot_soundness_rejects_mixed_versions_and_write_after_seam() -> None:
    v1 = SnapshotToken(version=1, epoch="db-1", writer_fence=7)
    v2 = SnapshotToken(version=2, epoch="db-1", writer_fence=8)
    trace = SemanticTrace(
        nodes=(
            TraceNode("read-a", NodeKind.READ, snapshot=v1),
            TraceNode("read-b", NodeKind.READ, snapshot=v2),
        ),
        edges=(),
        seam_snapshot=v1,
    )
    with pytest.raises(ValueError, match="snapshot"):
        validate_snapshot_soundness(trace)
    dirty = replace(trace, nodes=(replace(trace.nodes[0], writes_state=True),), seam_snapshot=v1)
    with pytest.raises(ValueError, match="write"):
        validate_snapshot_soundness(dirty)


def test_scoped_delta_completeness_rejects_omitted_embedding_but_ignores_other_operator() -> None:
    delta = StateDelta(
        source_version=1,
        target_version=2,
        changes=(DeltaChange("node", "n1", changed_fields=frozenset({"name"})),),
    )
    spec = ObservableSpec("node_cosine", frozenset({"name", "name_embedding", "group_id"}))
    result = complete_delta(delta, spec)
    assert result.status == "UNKNOWN"
    assert "name_embedding" in result.missing_fields
    unrelated = ObservableSpec("edge_bm25", frozenset({"fact"}))
    assert complete_delta(delta, unrelated).status == "COMPLETE"


def test_delta_change_with_exact_after_values_is_complete_for_selected_projection() -> None:
    delta = StateDelta(
        source_version=1,
        target_version=2,
        changes=(
            DeltaChange(
                "node",
                "n1",
                changed_fields=frozenset({"name", "name_embedding"}),
                before={"name": "old", "name_embedding": [1.0, 0.0]},
                after={"name": "new", "name_embedding": [0.0, 1.0]},
            ),
        ),
    )
    spec = ObservableSpec("node_cosine", frozenset({"name", "name_embedding"}))
    assert complete_delta(delta, spec).status == "COMPLETE"


def test_topk_short_result_has_no_fabricated_cutoff() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1",),
        domain=("n1", "n2"),
        k=3,
        cutoff=None,
        ties=(),
        query_epoch="embed-1",
        index_epoch="idx-1",
    )
    delta = StateDelta(
        source_version=1,
        target_version=2,
        changes=(DeltaChange("node", "n2", changed_fields=frozenset({"name_embedding"})),),
    )
    assert certify_exact_topk(witness, delta).status == CertificateStatus.UNKNOWN


def test_topk_boundary_tie_is_unknown_without_consumer_tie_contract() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1", "n2"),
        domain=("n1", "n2", "n3"),
        k=2,
        cutoff=0.8,
        ties=("n3",),
        query_epoch="embed-1",
        index_epoch="idx-1",
    )
    delta = StateDelta(
        source_version=1,
        target_version=2,
        changes=(DeltaChange("node", "n3", changed_fields=frozenset({"name_embedding"})),),
    )
    assert certify_exact_topk(witness, delta).status == CertificateStatus.UNKNOWN


def test_topk_nonmember_with_explicit_post_score_bound_is_stable() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1", "n2"),
        domain=("n1", "n2", "n3"),
        k=2,
        cutoff=0.8,
        ties=(),
        query_epoch="embed-1",
        index_epoch="idx-1",
        proof_data={"post_scores": {"n3": 0.2}, "tie_contract": "consumer-order-frozen"},
    )
    delta = StateDelta(1, 2, (DeltaChange("node", "n3", frozenset({"name_embedding"}), after={"name_embedding": [0.0, 1.0]}),))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.STABLE


def test_bm25_without_index_contract_is_unknown_even_when_delta_is_empty() -> None:
    witness = Witness(
        operator="edge_bm25",
        query="hello",
        result=("e1",),
        domain=("e1",),
        k=1,
        cutoff=1.0,
        ties=(),
        query_epoch="q-1",
        index_epoch=None,
    )
    result = certify_bm25(witness, StateDelta(1, 1, ()))
    assert result.status == CertificateStatus.UNKNOWN


def test_previous_episode_window_is_state_dependency_reaching_demand() -> None:
    graph = DependencyGraph()
    graph.add("previous-episodes", "node-extraction", DependencyKind.DATA)
    graph.add("node-extraction", "node-demand", DependencyKind.DATA)
    assert graph.reaches("previous-episodes", "node-demand")
    assert graph.fingerprint("node-demand")


def test_ambiguous_alignment_fails_closed() -> None:
    result = align_names(
        old=[("source", "node", "n", "parent", 0)],
        new=[
            ("source", "node", "n", "parent", 0),
            ("source", "node", "n", "parent", 0),
        ],
    )
    assert result.status == AlignmentStatus.AMBIGUOUS


def test_demand_validity_requires_existence_binding_predecessor_and_request() -> None:
    demand = DemandNode(
        name="node-demand",
        dependencies={
            "existence": "same",
            "binding": "same",
            "predecessor": "changed",
            "builder": "same",
            "request": "same",
        },
    )
    assert check_demand_validity(demand).status == "INVALID"
    assert "predecessor" in check_demand_validity(demand).reasons


def test_replay_allowed_cannot_be_inferred_from_repeated_agreement() -> None:
    contract = ReplayAdmissibility(
        status=ReplayStatus.UNKNOWN,
        authority=None,
        request_fields=frozenset({"request_digest"}),
        artifact_complete=False,
        hidden_state_fields=frozenset({"session_history"}),
    )
    assert contract.can_replay is False
    with pytest.raises(ValueError, match="contract"):
        contract.require_replay()


def test_replay_contract_requires_exact_epoch_fields_even_when_declared_allowed() -> None:
    contract = ReplayAdmissibility(
        status=ReplayStatus.ALLOWED,
        authority="provider-contract-v1",
        request_fields=frozenset({"request_digest"}),
        artifact_complete=True,
    )
    assert contract.can_replay is False


def test_alpha_equivalence_ignores_runtime_uuid_but_not_order_or_effect_key() -> None:
    left = {"logical_id": "n1", "runtime_uuid": "u-left", "ordered": ["a", "b"], "effect_key": "e1"}
    right = {"logical_id": "n1", "runtime_uuid": "u-right", "ordered": ["a", "b"], "effect_key": "e1"}
    assert alpha_equivalent(left, right)
    assert not alpha_equivalent(left, {**right, "ordered": ["b", "a"]})
    assert not alpha_equivalent(left, {**right, "effect_key": "e2"})


def test_continuation_relation_keeps_ignored_id_visible_when_declared_in_k() -> None:
    left = {"logical_id": "n1", "runtime_uuid": "u-left", "edge_endpoint_id": "u-left"}
    right = {"logical_id": "n1", "runtime_uuid": "u-right", "edge_endpoint_id": "u-right"}
    assert alpha_equivalent(left, right)
    assert not continuation_equivalent(left, right, observable_fields={"edge_endpoint_id"})


def test_propagation_reconverges_when_repaired_output_is_canonical_equal() -> None:
    nodes = {
        "read": PropagationNode("read", output="old", dirty=True),
        "pure": PropagationNode("pure", output="same"),
        "suffix": PropagationNode("suffix", output="stable"),
    }
    edges = (("read", "pure"), ("pure", "suffix"))
    result = propagate(nodes, edges)
    assert result.repaired == {"read"}
    assert result.unaffected == {"pure", "suffix"}


def test_propagation_rejects_unbounded_repair() -> None:
    nodes = {"a": PropagationNode("a", output="a", dirty=True)}
    with pytest.raises(ValueError, match="termination"):
        propagate(nodes, (("a", "a"),), max_repairs=0)


def test_propagation_rejects_semantic_cycle_even_when_outputs_look_equal() -> None:
    nodes = {"a": PropagationNode("a", output="a"), "b": PropagationNode("b", output="b")}
    with pytest.raises(ValueError, match="termination"):
        propagate(nodes, (("a", "b"), ("b", "a")))


def test_dag_longest_path_allows_path_switch_and_counterfactual_costs() -> None:
    nodes = (
        DagNode("a", (), 4.0),
        DagNode("b", ("a",), 4.0),
        DagNode("c", ("a",), 7.0),
        DagNode("d", ("b", "c"), 1.0),
    )
    assert longest_path(nodes).cost == 12.0
    cf = counterfactual(nodes, removed={"c"})
    assert cf.saved_cost == 3.0
    assert cf.path == ("a", "b", "d")


def test_work_ratio_uses_same_resource_denominator_not_critical_path() -> None:
    assert work_ratio(direct=2.0, affected=6.0) == 3.0
    assert work_ratio(direct=0.0, affected=0.0) is None


def test_trace_reference_differential_is_canonical_and_seam_safe() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.reference_model import (
        build_trace,
        maintain_trace,
    )

    old = {"nodes": {"n1": {"name": "old", "embedding": (1.0, 0.0)}}}
    delta = StateDelta(1, 2, (DeltaChange("node", "n1", frozenset({"name"}), before={"name": "old"}, after={"name": "new"}),))
    fresh = build_trace({"nodes": {"n1": {"name": "new", "embedding": (1.0, 0.0)}}}, "episode", SnapshotToken(2, "db", 2))
    maintained = maintain_trace(old, delta, "episode", SnapshotToken(2, "db", 2))
    assert alpha_equivalent(maintained.seam_output, fresh.seam_output)
