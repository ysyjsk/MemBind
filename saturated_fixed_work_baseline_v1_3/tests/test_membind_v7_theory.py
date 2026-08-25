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
    costed_counterfactual,
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


def test_v7_live_runner_requires_explicit_authorization_and_treatment_authorization(tmp_path) -> None:
    import json

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, V7LiveRunnerError, validate_live_gate

    gate = tmp_path / "METHOD_SELECTION.json"
    gate.write_text(json.dumps({"status": "PASS", "selected_method": "M1"}), encoding="utf-8")
    config = V7LiveConfig(output_root=tmp_path / "run", run_id="v7-live", dry_run=False, method="M1", gate_path=gate)
    with pytest.raises(V7LiveRunnerError, match="authorize"):
        validate_live_gate(config)

    gate.write_text(json.dumps({"status": "PASS", "authorized": True, "selected_method": "M1"}), encoding="utf-8")
    with pytest.raises(V7LiveRunnerError, match="authorize"):
        validate_live_gate(config)

    gate.write_text(json.dumps({"status": "AUTHORIZED", "authorized": True, "selected_method": "M1", "treatment_authorized": True}), encoding="utf-8")
    with pytest.raises(V7LiveRunnerError, match="hash-sealed"):
        validate_live_gate(config)


def _sealed_authorized_v7_gate(root, *, campaign_harness_bound=True):
    import hashlib
    import json

    from saturated_fixed_work_baseline_v1_3.membind_v7.gates import evaluate_opportunity_gates
    from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import write_observer_artifacts

    def encoded(value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False).encode("ascii") + b"\n"

    r1 = {"schema_version": "test.r1.v1", "status": "PASS"}
    evidence = {
        "schema_version": "membind.v7.r3-evidence-manifest.v1",
        "status": "SEALED_INPUTS",
        "files": [{"path": "R1_ASSUMPTION_AUDIT.json", "sha256": hashlib.sha256(encoded(r1)).hexdigest()}],
        "treatment_calls": 0,
    }
    decision = {
        "real_graphiti_evidence": True,
        "independent_block_count": 2,
        "source_count_per_block": 6,
        "core_assumptions_supported": True,
        "observer_harness_bound": True,
        "t6b_status": "SUPPORTED_WITH_GUARD",
        "false_stable_count": 0,
        "false_unaffected_count": 0,
        "stable_prediction_count": 1,
        "early_memory_specific": True,
        "csp": 0.2,
        "csp_preregistered_min": 0.1,
        "sca_within_bound": True,
        "meaningful_reconvergence": True,
        "gross_saved_cp_lb_ns": 1000,
        "certificate_cost_ub_ns": 10,
        "repair_cost_ub_ns": 10,
        "required_online_headroom_ns": 100,
        "m1_sufficient": True,
        "m2_extension_eligible": False,
        "replay_allowed": False,
        "selected_operator": "node_cosine",
        "selected_seam": "graphiti.add_episode.pre_process_episode_data",
        "sealed_manifest_sha256": hashlib.sha256(encoded(evidence)).hexdigest(),
    }
    method = evaluate_opportunity_gates(decision)
    write_observer_artifacts(
        root,
        {
            "EVIDENCE_MANIFEST.json": evidence,
            "METHOD_SELECTION.json": method,
            "R1_ASSUMPTION_AUDIT.json": r1,
            "R3_DECISION_INPUT.json": decision,
        },
        campaign_identity={
            "schema_version": "membind.v7.real-observer-campaign-identity.v1",
            "run_id": "v7-test-gate",
            "provider": {
                "construction_model": "Qwen/Qwen3-32B",
                "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
            },
            "workload": {
                "r1_r2": {"source_count": 2},
                "r3_blocks": [{"source_count": 6}, {"source_count": 6}],
            },
            "selected_characterization_region": {
                "operator": "node_cosine",
                "seam": "graphiti.add_episode.pre_process_episode_data",
            },
            "protocol_sha256": "b" * 64,
            "treatment_calls": 0,
            "response_replay_calls": 0,
            "observer_harness": (
                {
                    "schema_version": "membind.v7.observer-harness-verification.v1",
                    "status": "PASS",
                    "source_sha256": {"observer.py": "c" * 64},
                }
                if campaign_harness_bound
                else {}
            ),
        },
    )
    return root / "METHOD_SELECTION.json"


def test_v7_live_runner_rejects_sealed_gate_without_observer_source_binding(tmp_path) -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import (
        V7LiveConfig,
        V7LiveRunnerError,
        validate_live_gate,
    )

    gate = _sealed_authorized_v7_gate(
        tmp_path / "unbound-gate", campaign_harness_bound=False
    )
    config = V7LiveConfig(
        output_root=tmp_path / "run",
        run_id="v7-live",
        dry_run=False,
        method="M1",
        gate_path=gate,
    )
    with pytest.raises(V7LiveRunnerError, match="hash-sealed"):
        validate_live_gate(config)


def test_v7_live_runner_dry_run_is_provider_free_and_writes_recoverable_manifest(tmp_path) -> None:
    import asyncio

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, run_v7_live_async, verify_v7_live_artifacts

    config = V7LiveConfig(output_root=tmp_path / "run", run_id="v7-dry-run")
    calls: list[str] = []
    result = asyncio.run(run_v7_live_async(config, provider_call=lambda: calls.append("provider")))
    assert result["status"] == "DRY_RUN"
    assert result["provider_calls"] == 0
    assert calls == []
    assert (tmp_path / "run" / "RUN_MANIFEST.json").exists()
    saved = __import__("json").loads((tmp_path / "run" / "RUN_MANIFEST.json").read_text())
    assert len(saved["runner_source_sha256"]) == 64
    assert verify_v7_live_artifacts(tmp_path / "run")["status"] == "PASS"


def _valid_v7_live_adapter_result(run_id="v7-live", method="M1"):
    return {
        "schema_version": "membind.v7.live-adapter-result.v1",
        "status": "COMPLETED",
        "run_id": run_id,
        "method": method,
        "source_count": 2,
        "provider_calls": 7,
        "treatment_calls": 1,
        "native_publication_calls": 2,
        "publication_source_sequences": [0, 1],
        "canonical_equivalent": True,
        "false_reuse_count": 0,
        "artifact_manifest_sha256": "a" * 64,
    }


def test_v7_live_runner_calls_only_injected_provider_after_gate_and_never_seals_key(tmp_path, monkeypatch) -> None:
    import asyncio
    import json

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, run_v7_live_async

    gate = _sealed_authorized_v7_gate(tmp_path / "gate")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "secret-key-for-test")
    config = V7LiveConfig(output_root=tmp_path / "live", run_id="v7-live", method="M1", dry_run=False, gate_path=gate)
    calls: list[str] = []
    result = asyncio.run(
        run_v7_live_async(
            config,
            provider_call=lambda: calls.append("provider") or _valid_v7_live_adapter_result(),
        )
    )
    assert result["status"] == "LIVE_AUTHORIZED"
    assert calls == ["provider"]
    assert result["provider_calls"] == 7
    assert result["native_publication_calls"] == 2
    assert "secret-key-for-test" not in (tmp_path / "live" / "RUN_MANIFEST.json").read_text()


def test_v7_live_runner_persists_sanitized_failure_before_propagating_error(tmp_path, monkeypatch) -> None:
    import asyncio
    import json

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, run_v7_live_async

    gate = _sealed_authorized_v7_gate(tmp_path / "gate")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "secret-key-for-test")
    config = V7LiveConfig(output_root=tmp_path / "live-failure", run_id="v7-live-failure", method="M1", dry_run=False, gate_path=gate)

    def failed_provider():
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(run_v7_live_async(config, provider_call=failed_provider))
    failure = json.loads((tmp_path / "live-failure" / "RUN_FAILURE.json").read_text())
    assert failure["error_type"] == "builtins.RuntimeError"
    assert failure["adapter_invocations"] == 1
    assert "secret-key-for-test" not in (tmp_path / "live-failure" / "RUN_FAILURE.json").read_text()


def test_v7_live_runner_rejects_invalid_adapter_result_after_persisting_failure(tmp_path, monkeypatch) -> None:
    import asyncio
    import json

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, V7LiveRunnerError, run_v7_live_async

    gate = _sealed_authorized_v7_gate(tmp_path / "gate")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "secret-key-for-test")
    config = V7LiveConfig(output_root=tmp_path / "invalid-live", run_id="v7-live", method="M1", dry_run=False, gate_path=gate)
    with pytest.raises(V7LiveRunnerError, match="adapter result"):
        asyncio.run(run_v7_live_async(config, provider_call=lambda: {"status": "COMPLETED"}))
    failure = json.loads((tmp_path / "invalid-live" / "RUN_FAILURE.json").read_text())
    assert failure["status"] == "FAILED_CLOSED"
    assert failure["adapter_invocations"] == 1


def test_v7_live_config_pins_two_source_siliconflow_models() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, V7LiveRunnerError

    config = V7LiveConfig(output_root=__import__("pathlib").Path("unused"), run_id="v7-live", method="M1", dry_run=False)
    assert config.construction_model == "Qwen/Qwen3-32B"
    assert config.embedding_model == "Qwen/Qwen3-Embedding-0.6B"
    with pytest.raises(V7LiveRunnerError, match="execution envelope"):
        V7LiveConfig(
            output_root=__import__("pathlib").Path("unused"),
            run_id="v7-live",
            method="M1",
            dry_run=False,
            construction_model="other",
        )
    with pytest.raises(V7LiveRunnerError, match="source_count"):
        V7LiveConfig(
            output_root=__import__("pathlib").Path("unused"),
            run_id="v7-dry-run",
            source_count=True,
        )


def test_v7_live_missing_key_fails_before_creating_output_root(tmp_path, monkeypatch) -> None:
    import asyncio

    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import V7LiveConfig, V7LiveRunnerError, run_v7_live_async

    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    gate = _sealed_authorized_v7_gate(tmp_path / "gate")
    output = tmp_path / "must-remain-absent"
    config = V7LiveConfig(output_root=output, run_id="v7-live", method="M1", dry_run=False, gate_path=gate)
    with pytest.raises(V7LiveRunnerError, match="required"):
        asyncio.run(run_v7_live_async(config, provider_call=lambda: _valid_v7_live_adapter_result()))
    assert not output.exists()


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


def test_scoped_delta_completeness_rejects_required_environment_epoch_change() -> None:
    delta = StateDelta(
        source_version=1,
        target_version=2,
        changes=(),
        environment_changes=frozenset({"index_epoch"}),
    )
    spec = ObservableSpec("node_cosine", frozenset(), required_epochs=frozenset({"query_epoch", "index_epoch"}))
    result = complete_delta(delta, spec)
    assert result.status == "UNKNOWN"
    assert result.missing_epochs == {"index_epoch"}


def test_post_pin_harness_changes_do_not_self_block_native_subject() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.pins import verify_membind_pin

    result = verify_membind_pin("/data/predator/ly/MemBind")
    assert result["native_subject_pin"] == "2832d94b56db72fcf993154bde47e16b31ade724"
    assert result["harness_pin"] == "bddc1c5627a2ed49d8503a8cbab2d457f022f543"
    assert result["native_subject_match"] is True


def test_native_subject_source_change_fails_pin_verification(tmp_path) -> None:
    import subprocess

    from saturated_fixed_work_baseline_v1_3.membind_v7.pins import verify_membind_pin

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "v7@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "V7 Test"], check=True)
    subject = tmp_path / "native.py"
    subject.write_text("NATIVE = 1\n", encoding="ascii")
    subprocess.run(["git", "-C", str(tmp_path), "add", "native.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "subject"], check=True)
    pin = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    harness = tmp_path / "harness.py"
    harness.write_text("HARNESS = 1\n", encoding="ascii")
    subprocess.run(["git", "-C", str(tmp_path), "add", "harness.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "harness"], check=True)
    assert verify_membind_pin(tmp_path, expected=pin, native_paths=("native.py",), native_trees=())["native_subject_match"] is True
    subject.write_text("NATIVE = 2\n", encoding="ascii")
    assert verify_membind_pin(tmp_path, expected=pin, native_paths=("native.py",), native_trees=())["native_subject_match"] is False


def test_scoped_delta_completeness_ignores_unrelated_environment_epoch() -> None:
    delta = StateDelta(1, 2, environment_changes=frozenset({"cache_epoch"}))
    spec = ObservableSpec("node_cosine", frozenset(), required_epochs=frozenset({"index_epoch"}))
    assert complete_delta(delta, spec).status == "COMPLETE"


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


def test_topk_short_result_explicitly_excludes_below_threshold_insertion() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1",),
        domain=("n1",),
        k=3,
        cutoff=None,
        ties=(),
        query_epoch="embed-1",
        index_epoch="idx-1",
        proof_data={
            "post_scores": {"n2": 0.2},
            "no_new_eligible": True,
            "min_score": 0.6,
            "tie_contract": "strict-threshold-separation",
        },
    )
    delta = StateDelta(
        1,
        2,
        (DeltaChange("node", "n2", frozenset({"name_embedding"}), after={"name_embedding": [0.0, 1.0]}, operation="insert"),),
    )
    assert certify_exact_topk(witness, delta).status == CertificateStatus.STABLE


def test_topk_short_result_member_change_cannot_hide_behind_exclusion_proof() -> None:
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
        proof_data={
            "post_scores": {},
            "no_new_eligible": True,
            "min_score": 0.6,
            "tie_contract": "strict-threshold-separation",
        },
    )
    delta = StateDelta(1, 2, (DeltaChange("node", "n1", frozenset({"name_embedding"})),))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.INVALID


def test_topk_deletion_of_short_result_nonmember_is_stable() -> None:
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
        proof_data={
            "no_new_eligible": True,
            "min_score": 0.6,
            "tie_contract": "strict-threshold-separation",
        },
    )
    delta = StateDelta(1, 2, (DeltaChange("node", "n2", operation="delete"),))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.STABLE


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


def test_topk_phantom_insertion_outside_old_domain_is_not_stable() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1",),
        domain=("n1",),
        k=1,
        cutoff=0.8,
        ties=(),
        query_epoch="embed-1",
        index_epoch="idx-1",
    )
    delta = StateDelta(1, 2, (DeltaChange("node", "n2", frozenset({"name_embedding"}), operation="insert"),))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.UNKNOWN


def test_topk_phantom_insertion_with_bound_below_cutoff_is_stable() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1",),
        domain=("n1",),
        k=1,
        cutoff=0.8,
        ties=(),
        query_epoch="embed-1",
        index_epoch="idx-1",
        proof_data={"post_scores": {"n2": 0.2}, "tie_contract": "consumer-order-frozen"},
    )
    delta = StateDelta(1, 2, (DeltaChange("node", "n2", frozenset({"name_embedding"}), operation="insert"),))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.STABLE


def test_topk_phantom_insertion_above_cutoff_invalidates_result() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1",),
        domain=("n1",),
        k=1,
        cutoff=0.8,
        ties=(),
        query_epoch="embed-1",
        index_epoch="idx-1",
        proof_data={"post_scores": {"n2": 0.99}, "tie_contract": "consumer-order-frozen"},
    )
    delta = StateDelta(1, 2, (DeltaChange("node", "n2", frozenset({"name_embedding"}), operation="insert"),))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.INVALID


def test_topk_deletion_of_kth_member_is_invalid() -> None:
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
    )
    delta = StateDelta(1, 2, (DeltaChange("node", "n2", operation="delete"),))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.INVALID


def test_topk_unrelated_operator_change_does_not_poison_scoped_region() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1",),
        domain=("n1",),
        k=1,
        cutoff=0.8,
        ties=(),
        query_epoch="embed-1",
        index_epoch="idx-1",
    )
    delta = StateDelta(1, 2, (DeltaChange("edge", "e2", operation="insert"),))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.STABLE


def test_topk_required_environment_epoch_change_is_unknown() -> None:
    witness = Witness(
        operator="node_cosine",
        query=(1.0, 0.0),
        result=("n1",),
        domain=("n1",),
        k=1,
        cutoff=0.8,
        ties=(),
        query_epoch="embed-1",
        index_epoch="idx-1",
    )
    delta = StateDelta(1, 2, environment_changes=frozenset({"index_epoch"}))
    assert certify_exact_topk(witness, delta).status == CertificateStatus.UNKNOWN


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


def _guarded_continuation_k() -> dict:
    return {
        "schema_version": "membind.v7.graphiti-continuation-k.v1",
        "seam": "graphiti.add_episode.pre_process_episode_data",
        "episodes": (
            {
                "uuid": "episode-1",
                "name": "episode",
                "group_id": "g",
                "source": "message",
                "source_description": "source",
                "content": "body",
                "entity_edges": (),
                "created_at": "2026-08-25T00:00:00+00:00",
                "valid_at": "2026-08-25T00:00:00+00:00",
            },
        ),
        "nodes": (
            {
                "uuid": "node-1",
                "name": "name",
                "group_id": "g",
                "summary": "summary",
                "created_at": "2026-08-25T00:00:00+00:00",
                "name_embedding": (1.0, 0.0),
                "labels": ("Entity",),
                "attributes": {},
            },
        ),
        "entity_edges": (
            {
                "uuid": "edge-1",
                "source_node_uuid": "node-1",
                "target_node_uuid": "node-1",
                "name": "related",
                "fact": "fact",
                "fact_embedding": (1.0, 0.0),
                "group_id": "g",
                "episodes": ("episode-1",),
                "created_at": "2026-08-25T00:00:00+00:00",
                "attributes": {},
            },
        ),
        "node_episode_index_map": {"node-1": (0,)},
        "now": "2026-08-25T00:00:00+00:00",
        "group_id": "g",
        "store_raw_episode_content": True,
        "driver_provider": "neo4j",
        "driver_database": "g",
        "backend_epoch": "neo4j-schema-1",
        "publication_frontier": 0,
        "saga": None,
        "saga_previous_episode_uuid": None,
        "update_communities": False,
    }


def test_graphiti_continuation_k_closes_guarded_native_tail() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.continuation import ContinuationStatus, validate_continuation_k

    assert validate_continuation_k(_guarded_continuation_k()).status == ContinuationStatus.SUPPORTED_WITH_GUARD


@pytest.mark.parametrize("guard", ["missing_node_embedding", "missing_edge_embedding", "saga", "communities"])
def test_graphiti_continuation_k_rejects_hidden_tail_work(guard: str) -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.continuation import ContinuationStatus, validate_continuation_k

    value = _guarded_continuation_k()
    if guard == "missing_node_embedding":
        value["nodes"][0]["name_embedding"] = None
    elif guard == "missing_edge_embedding":
        value["entity_edges"][0]["fact_embedding"] = None
    elif guard == "saga":
        value["saga"] = "saga-name"
    else:
        value["update_communities"] = True
    assert validate_continuation_k(value).status == ContinuationStatus.UNKNOWN


def test_graphiti_continuation_k_observes_endpoint_uuid_and_frontier() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v7.continuation import continuation_k_equivalent

    left = _guarded_continuation_k()
    endpoint_changed = _guarded_continuation_k()
    endpoint_changed["entity_edges"][0]["target_node_uuid"] = "node-2"
    assert continuation_k_equivalent(left, endpoint_changed) is False
    frontier_changed = _guarded_continuation_k()
    frontier_changed["publication_frontier"] = 1
    assert continuation_k_equivalent(left, frontier_changed) is False


def test_graphiti_continuation_source_audit_is_bound_to_pinned_files() -> None:
    from pathlib import Path

    from saturated_fixed_work_baseline_v1_3.membind_v7.continuation import ContinuationStatus, audit_continuation_source

    root = Path("membind-validation/.venv/lib/python3.12/site-packages/graphiti_core")
    result = audit_continuation_source(root)
    assert result.status == ContinuationStatus.SUPPORTED_WITH_GUARD
    assert result.failed_guards == ()


def test_propagation_missing_repair_cannot_reconverge() -> None:
    nodes = {
        "read": PropagationNode("read", output="old", dirty=True),
        "pure": PropagationNode("pure", output="same"),
        "suffix": PropagationNode("suffix", output="stable"),
    }
    edges = (("read", "pure"), ("pure", "suffix"))
    result = propagate(nodes, edges)
    assert result.repaired == set()
    assert result.affected == {"read", "pure", "suffix"}
    assert result.unaffected == set()


def test_propagation_reconverges_when_repaired_output_is_canonical_equal() -> None:
    nodes = {
        "read": PropagationNode("read", output={"value": 1, "runtime_uuid": "old"}, dirty=True, repaired_output={"value": 1, "runtime_uuid": "new"}),
        "pure": PropagationNode("pure", output="same"),
        "suffix": PropagationNode("suffix", output="stable"),
    }
    result = propagate(nodes, (("read", "pure"), ("pure", "suffix")))
    assert result.repaired == {"read"}
    assert result.unaffected == {"pure", "suffix"}


def test_propagation_explicit_none_repair_is_not_missing() -> None:
    nodes = {"read": PropagationNode("read", output="old", dirty=True, repaired_output=None)}
    result = propagate(nodes, ())
    assert result.repaired == {"read"}
    assert result.unknown == set()


def test_propagation_explicit_changed_repair_reaches_successor() -> None:
    nodes = {
        "read": PropagationNode("read", output="old", dirty=True, repaired_output="new"),
        "suffix": PropagationNode("suffix", output="stable"),
    }
    result = propagate(nodes, (("read", "suffix"),))
    assert result.repaired == {"read"}
    assert result.affected == {"read", "suffix"}
    assert result.unknown == {"suffix"}


def test_propagation_unknown_root_reaches_successor() -> None:
    nodes = {
        "read": PropagationNode("read", output="old", unknown=True),
        "suffix": PropagationNode("suffix", output="stable"),
    }
    result = propagate(nodes, (("read", "suffix"),))
    assert result.repaired == set()
    assert result.unknown == {"read", "suffix"}


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

    costed = costed_counterfactual(
        nodes,
        removed={"c"},
        added=(DagNode("certificate", ("b", "c"), 2.0),),
        gates={"d": ("certificate",)},
    )
    assert costed.baseline.cost == 12.0
    assert costed.candidate.cost == 11.0
    assert costed.saved_cost == 1.0
    assert costed.path == ("a", "b", "certificate", "d")


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
