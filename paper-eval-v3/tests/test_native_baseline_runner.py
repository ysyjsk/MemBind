from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.native_baseline_runner import (
    DEVELOPMENT_HISTORIES,
    NativeBaselinePlan,
    build_native_baseline_plan,
    build_n0_artifact,
    decide_history_resume,
    make_checkpoint,
    seal_history_result,
    should_pause_before_quality,
    upgrade_episode_phase_span_counts,
    validate_read_only_quality_graph,
    verify_native_quality_bindings,
    verify_checkpoint,
    verify_history_result,
)


class _CompletedInitTask:
    def done(self) -> bool:
        return True


def test_quality_retrieval_uses_a_separate_read_only_graph_without_mutating_construction() -> None:
    init_task = _CompletedInitTask()
    construction_graph = SimpleNamespace(
        driver=SimpleNamespace(_init_task=init_task),
    )
    retrieval_graph = SimpleNamespace(
        driver=SimpleNamespace(_init_task=None, execute_query=lambda *_a, **_kw: None),
        search_=lambda *_a, **_kw: None,
    )

    assert (
        validate_read_only_quality_graph(
            construction_graph=construction_graph,
            retrieval_graph=retrieval_graph,
        )
        is retrieval_graph
    )
    assert construction_graph.driver._init_task is init_task
    assert retrieval_graph.driver is not construction_graph.driver


def test_plan_is_exact_four_history_serial_and_namespaces_are_fresh() -> None:
    plan = build_native_baseline_plan("nb-20260816-001")
    assert [item.history_id for item in plan.histories] == list(DEVELOPMENT_HISTORIES)
    assert all(item.method == "U0" for item in plan.histories)
    assert len({item.namespace for item in plan.histories}) == 4
    assert all(item.namespace.startswith("nc-e1e2-") for item in plan.histories)
    assert plan.mode == "serial"


def test_plan_rejects_history_substitution_and_path_unsafe_run_id() -> None:
    with pytest.raises(ValueError, match="fixed development histories"):
        build_native_baseline_plan("nb-20260816-001", history_ids=(DEVELOPMENT_HISTORIES[0],))
    with pytest.raises(ValueError, match="run_id"):
        build_native_baseline_plan("../native")


def test_checkpoint_is_hash_sealed_and_rejects_tampering_or_non_prefix() -> None:
    checkpoint = make_checkpoint(
        run_id="nb-20260816-001",
        history_id=DEVELOPMENT_HISTORIES[0],
        namespace="nc-e1e2-0123456789abcdef",
        expected_sequences=[0, 1, 2],
        completed_sequences=[0, 1],
        status="running",
    )
    assert verify_checkpoint(checkpoint)["completed_sequences"] == [0, 1]
    tampered = dict(checkpoint)
    tampered["completed_sequences"] = [0, 2]
    with pytest.raises(ValueError, match="hash"):
        verify_checkpoint(tampered)
    invalid = dict(checkpoint)
    invalid["completed_sequences"] = [1]
    invalid_body = dict(invalid)
    invalid_body.pop("payload_sha256", None)
    invalid["payload_sha256"] = payload_sha256(invalid_body)
    with pytest.raises(ValueError, match="prefix"):
        verify_checkpoint(invalid)


def test_plan_type_is_explicitly_serial() -> None:
    plan = NativeBaselinePlan(
        run_id="nb-20260816-001",
        histories=tuple(),
        mode="serial",
    )
    assert plan.mode == "serial"


def test_n0_artifact_binds_service_models_without_secrets() -> None:
    artifact = build_n0_artifact(
        run_id="nb-20260816-001",
        construction_models=[{"id": "qwen3-32b-fp8", "max_model_len": 65536}],
        embedding_models=[{"id": "qwen3-embedding-0.6b", "max_model_len": 32768}],
        neo4j_ready=True,
        plan=build_native_baseline_plan("nb-20260816-001"),
        overlay_sha256="a" * 64,
    )
    assert artifact["status"] == "PASS"
    assert artifact["construction"]["max_model_len"] == 65536
    assert "api_key" not in str(artifact).lower()
    assert artifact["target_history_count"] == 4


def test_native_quality_binding_requires_the_frozen_reader_v2_and_judge() -> None:
    frozen = {
        "payload": {
            "baseline_id": "native-graphiti-u0-reader-v2",
            "common_evaluation_policy": {
                "reader_config_sha256": "a" * 64,
                "judge_component_config_sha256": "b" * 64,
            },
        }
    }
    assert verify_native_quality_bindings(
        frozen_baseline=frozen,
        reader_config_sha256="a" * 64,
        judge_config_sha256="b" * 64,
    ) == {
        "baseline_id": "native-graphiti-u0-reader-v2",
        "reader_config_sha256": "a" * 64,
        "judge_config_sha256": "b" * 64,
    }

    with pytest.raises(ValueError, match="Reader-v2"):
        verify_native_quality_bindings(
            frozen_baseline=frozen,
            reader_config_sha256="c" * 64,
            judge_config_sha256="b" * 64,
        )


def test_history_resume_distinguishes_construction_completion_from_finalization() -> None:
    base = {
        "run_id": "nb-20260816-001",
        "history_id": DEVELOPMENT_HISTORIES[0],
        "namespace": "nc-e1e2-0123456789abcdef",
        "expected_sequences": [0, 1],
    }
    running = make_checkpoint(
        **base,
        completed_sequences=[0],
        status="running",
    )
    completed = make_checkpoint(
        **base,
        completed_sequences=[0, 1],
        status="completed",
    )
    running_full = make_checkpoint(
        **base,
        completed_sequences=[0, 1],
        status="running",
    )

    assert decide_history_resume(running, result_exists=False) == "CONSTRUCTION_PENDING"
    assert decide_history_resume(running_full, result_exists=False) == "QUALITY_PENDING"
    assert decide_history_resume(running_full, result_exists=True) == "FINALIZATION_PENDING"
    assert decide_history_resume(completed, result_exists=False) == "QUALITY_PENDING"
    assert decide_history_resume(completed, result_exists=True) == "FINALIZED"
    with pytest.raises(ValueError, match="result exists"):
        decide_history_resume(running, result_exists=True)


def test_history_result_is_hash_sealed_and_bound_to_exact_plan_identity() -> None:
    plan = build_native_baseline_plan("nb-20260816-001").histories[0]
    sealed = seal_history_result(
        {
            "schema_version": "membind.paper-eval-v3.native-baseline-history.v1",
            "run_id": plan.run_id,
            "history_id": plan.history_id,
            "namespace": plan.namespace,
            "method": "U0",
            "repeat_id": 0,
            "status": "completed",
            "quality_identity": {"reader_config_sha256": "a" * 64},
            "quality": {"status": "SUCCESS"},
            "aggregate": {"episode_count": 49},
            "final_namespace_observation": {
                "node_count": 2,
                "relationship_count": 1,
                "episode_count": 49,
                "episode_names_match_expected": True,
            },
        }
    )
    assert verify_history_result(sealed, expected_plan=plan)["status"] == "completed"
    tampered = dict(sealed)
    tampered["quality"] = {"status": "FAILED"}
    with pytest.raises(ValueError, match="hash"):
        verify_history_result(tampered, expected_plan=plan)


def test_quality_fence_triggers_only_for_full_unfinalized_prefix() -> None:
    base = {
        "run_id": "nb-20260816-001",
        "history_id": DEVELOPMENT_HISTORIES[0],
        "namespace": "nc-e1e2-0123456789abcdef",
        "expected_sequences": [0, 1],
    }
    partial = make_checkpoint(
        **base,
        completed_sequences=[0],
        status="running",
    )
    full = make_checkpoint(
        **base,
        completed_sequences=[0, 1],
        status="completed",
    )
    assert not should_pause_before_quality(partial, quality_exists=False, result_exists=False)
    assert should_pause_before_quality(full, quality_exists=False, result_exists=False)
    assert not should_pause_before_quality(full, quality_exists=True, result_exists=False)
    assert not should_pause_before_quality(full, quality_exists=False, result_exists=True)


def test_native_tmux_resume_appends_the_existing_attempt_log() -> None:
    launcher = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_native_baseline_tmux.sh"
    ).read_text(encoding="utf-8")
    assert "tee -a '$LOG'" in launcher


def test_level1_upgrade_derives_phase_span_counts_without_changing_other_metrics() -> None:
    episode_rows = [
        {
            "identity": {"source_sequence": 0},
            "phase_metrics": {
                "add-episode": {"duration_ns": 10},
                "llm": {"duration_ns": 5, "input_tokens": 7},
            },
            "latency_ns": {"service": 10},
        }
    ]
    spans = [
        {"source_sequence": 0, "phase": "add-episode"},
        {"source_sequence": 0, "phase": "llm"},
        {"source_sequence": 0, "phase": "llm"},
    ]
    upgraded = upgrade_episode_phase_span_counts(episode_rows, spans)
    assert upgraded[0]["phase_metrics"]["add-episode"]["span_count"] == 1
    assert upgraded[0]["phase_metrics"]["llm"]["span_count"] == 2
    assert upgraded[0]["phase_metrics"]["llm"]["input_tokens"] == 7
    assert "span_count" not in episode_rows[0]["phase_metrics"]["llm"]
