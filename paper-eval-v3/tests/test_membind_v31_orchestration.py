"""TDD for the v3.1 smoke-then-six-block offline orchestration state machine."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.orchestration import (
    OrchestrationError,
    OrchestrationHooks,
    run_v31_orchestration,
)


def _seal(body: dict[str, object], field: str = "payload_sha256") -> dict[str, object]:
    result = deepcopy(body)
    result[field] = payload_sha256(result)
    return result


def _control() -> dict[str, object]:
    histories = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
    identities = (
        ("MemBind", histories[0], "FRONTIER_FIRST_CACHE_AFFINITY", 49),
        ("MemBind", histories[1], "FRONTIER_FIRST_CACHE_AFFINITY", 49),
        ("MemBind", histories[2], "FRONTIER_FIRST_CACHE_AFFINITY", 46),
        ("MemBind", histories[3], "FRONTIER_FIRST_CACHE_AFFINITY", 44),
        ("MemBind-Barrier", histories[0], "FRONTIER_BARRIER", 49),
        ("MemBind-FIFO", histories[0], "FRONTIER_FIRST_FIFO", 49),
    )
    blocks = [
        {
            "block_index": index,
            "method": method,
            "history_id": history,
            "policy": policy,
            "source_count": count,
            "namespace": f"v31-{index}-{history}",
            "global_llm_admission_k": 2,
            "cache_salt_sha256": f"{100 + index:064x}",
        }
        for index, (method, history, policy, count) in enumerate(identities)
    ]
    plan = _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-method-plan.v2",
            "run_id": "membind-v31-dev-20260817-001",
            "authorization_scope": (
                "LIVE_EXECUTION_AUTHORIZED_BASELINE_MERGE_PENDING"
            ),
            "representative_history_id": "07741c45",
            "global_llm_admission_k": 2,
            "methodology_sha256": "a" * 64,
            "workplan_sha256": "b" * 64,
            "blocks": blocks,
        }
    )
    acceptance = _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-baseline-acceptance.v1",
            "status": "PASS",
            "run_id": "apc-baseline-dev-20260817-001",
        }
    )
    commit = _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-control-commit.v1",
            "status": "COMMITTED",
            "run_id": plan["run_id"],
            "baseline_acceptance_payload_sha256": acceptance["payload_sha256"],
            "method_plan_payload_sha256": plan["payload_sha256"],
        }
    )
    return {"acceptance": acceptance, "method_plan": plan, "commit": commit}


def _smoke_result(spec) -> dict[str, object]:
    return _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-smoke-result.v1",
            "status": "PASS",
            "attempt_id": spec.attempt_id,
            "plan_payload_sha256": spec.plan_payload_sha256,
            "method": "MemBind",
            "history_id": "07741c45",
            "namespace": spec.namespace,
            "source_sequences": [0, 1, 2],
            "source_count": 3,
            "global_llm_admission_k": 2,
            "observed_max_inflight": 2,
            "verified_prepared_artifact_count": 3,
            "publication_source_sequences": [0, 1, 2],
            "visibility_confirmed_count": 3,
            "direct_violation_count": 0,
        }
    )


def _block_result(plan, index: int) -> dict[str, object]:
    block = plan["blocks"][index]
    checkpoint = _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-block-checkpoint.v1",
            "terminal_status": "COMPLETED",
            "complete_coverage": True,
            "completed_source_prefix": block["source_count"] - 1,
        },
        "checkpoint_sha256",
    )
    return _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-live-block-result.v1",
            "status": "PASS",
            "run_id": plan["run_id"],
            "block_index": index,
            "method": block["method"],
            "policy": block["policy"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "source_count": block["source_count"],
            "plan_payload_sha256": plan["payload_sha256"],
            "global_llm_admission_k": 2,
            "direct_violation_count": 0,
            "checkpoint": checkpoint,
        }
    )


def _hooks(control: dict[str, object], calls: list[object]) -> OrchestrationHooks:
    def smoke(spec, root):
        calls.append(("smoke", spec.source_sequences, root.name))
        return _smoke_result(spec)

    def block(plan, index, root):
        calls.append(("block", index, root.name))
        return _block_result(plan, index)

    return OrchestrationHooks(
        executor_identity_sha256="e" * 64,
        run_smoke=smoke,
        run_block=block,
    )


def test_orchestrator_runs_exact_smoke_then_strict_six_blocks_and_seals_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _control()
    monkeypatch.setattr(
        "paper_eval.membind_v31.orchestration.inspect_materialized_control",
        lambda _root: control,
    )
    calls: list[object] = []
    root = tmp_path / "attempt"

    result = run_v31_orchestration(
        control_root=tmp_path / "control",
        attempt_root=root,
        attempt_id="v31-attempt-dev-001",
        hooks=_hooks(control, calls),
    )

    assert calls == [("smoke", (0, 1, 2), "smoke"), *[("block", i, f"block-{i:02d}") for i in range(6)]]
    assert result["status"] == "PASS"
    assert result["completed_block_indices"] == list(range(6))
    assert json.loads((root / "ORCHESTRATION_CHECKPOINT.json").read_text())["status"] == "COMPLETED"
    assert json.loads((root / "SMOKE_GATE.json").read_text())["status"] == "PASS"
    assert (root / "ORCHESTRATION_RESULT.json").is_file()
    assert not (root / "FAILURE.json").exists()


def test_smoke_failure_seals_non_reusable_attempt_before_any_formal_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _control()
    monkeypatch.setattr(
        "paper_eval.membind_v31.orchestration.inspect_materialized_control",
        lambda _root: control,
    )

    def fail_smoke(_spec, _root):
        raise RuntimeError("private episode text")

    hooks = OrchestrationHooks(
        executor_identity_sha256="e" * 64,
        run_smoke=fail_smoke,
        run_block=lambda *_args: pytest.fail("formal block must not run"),
    )
    root = tmp_path / "attempt"
    with pytest.raises(OrchestrationError, match="smoke execution failed"):
        run_v31_orchestration(
            control_root=tmp_path / "control",
            attempt_root=root,
            attempt_id="v31-attempt-dev-002",
            hooks=hooks,
        )

    failure = json.loads((root / "FAILURE.json").read_text())
    checkpoint = json.loads((root / "ORCHESTRATION_CHECKPOINT.json").read_text())
    assert failure["failure_stage"] == "SMOKE"
    assert failure["error_class"] == "builtins.RuntimeError"
    assert "private episode text" not in repr(failure)
    assert checkpoint["status"] == "FAILED_NON_REUSABLE"
    assert not (root / "blocks").exists()


def test_block_failure_preserves_completed_prefix_and_forbids_in_place_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _control()
    monkeypatch.setattr(
        "paper_eval.membind_v31.orchestration.inspect_materialized_control",
        lambda _root: control,
    )
    calls: list[int] = []

    def block(plan, index, _root):
        calls.append(index)
        if index == 2:
            raise ValueError("provider disconnected with private response")
        return _block_result(plan, index)

    hooks = OrchestrationHooks("e" * 64, lambda spec, _root: _smoke_result(spec), block)
    root = tmp_path / "attempt"
    with pytest.raises(OrchestrationError, match="block execution failed"):
        run_v31_orchestration(
            control_root=tmp_path / "control",
            attempt_root=root,
            attempt_id="v31-attempt-dev-003",
            hooks=hooks,
        )

    checkpoint = json.loads((root / "ORCHESTRATION_CHECKPOINT.json").read_text())
    assert calls == [0, 1, 2]
    assert checkpoint["completed_block_indices"] == [0, 1]
    assert checkpoint["next_block_index"] == 2
    assert checkpoint["status"] == "FAILED_NON_REUSABLE"
    assert "private response" not in (root / "FAILURE.json").read_text()
    with pytest.raises(OrchestrationError, match="attempt terminal"):
        run_v31_orchestration(
            control_root=tmp_path / "control",
            attempt_root=root,
            attempt_id="v31-attempt-dev-003",
            hooks=hooks,
        )


def test_interruption_between_blocks_resumes_only_from_sealed_completed_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _control()
    monkeypatch.setattr(
        "paper_eval.membind_v31.orchestration.inspect_materialized_control",
        lambda _root: control,
    )
    interrupted = True
    calls: list[int] = []

    def block(plan, index, _root):
        nonlocal interrupted
        calls.append(index)
        if index == 2 and interrupted:
            interrupted = False
            raise KeyboardInterrupt
        return _block_result(plan, index)

    hooks = OrchestrationHooks("e" * 64, lambda spec, _root: _smoke_result(spec), block)
    root = tmp_path / "attempt"
    with pytest.raises(KeyboardInterrupt):
        run_v31_orchestration(
            control_root=tmp_path / "control",
            attempt_root=root,
            attempt_id="v31-attempt-dev-004",
            hooks=hooks,
        )

    checkpoint = json.loads((root / "ORCHESTRATION_CHECKPOINT.json").read_text())
    assert checkpoint["completed_block_indices"] == [0, 1]
    assert checkpoint["status"] == "BLOCKS_RUNNING"
    result = run_v31_orchestration(
        control_root=tmp_path / "control",
        attempt_root=root,
        attempt_id="v31-attempt-dev-004",
        hooks=hooks,
    )
    assert result["status"] == "PASS"
    assert calls == [0, 1, 2, 2, 3, 4, 5]


def test_live_plan_only_stops_after_four_main_blocks_then_full_control_resumes_ablation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _control()
    control_root = tmp_path / "control"
    control_root.mkdir()
    (control_root / "V31_METHOD_PLAN.json").write_text(
        json.dumps(control["method_plan"]), encoding="utf-8"
    )
    monkeypatch.setattr(
        "paper_eval.membind_v31.orchestration.verify_membind_v31_method_plan",
        lambda value: value,
    )
    calls: list[object] = []
    root = tmp_path / "attempt"

    first = run_v31_orchestration(
        control_root=control_root,
        attempt_root=root,
        attempt_id="v31-attempt-dev-005",
        hooks=_hooks(control, calls),
        formal_block_limit=4,
    )

    assert first["status"] == "MAIN_METHOD_PASS_BASELINE_RESUME_REQUIRED"
    assert first["completed_block_indices"] == [0, 1, 2, 3]
    assert calls == [
        ("smoke", (0, 1, 2), "smoke"),
        *[("block", i, f"block-{i:02d}") for i in range(4)],
    ]
    assert not (root / "ORCHESTRATION_RESULT.json").exists()
    checkpoint = json.loads((root / "ORCHESTRATION_CHECKPOINT.json").read_text())
    assert checkpoint["status"] == "MAIN_METHOD_COMPLETED_BASELINE_RESUME_REQUIRED"

    monkeypatch.setattr(
        "paper_eval.membind_v31.orchestration.inspect_materialized_control",
        lambda _root: control,
    )
    second = run_v31_orchestration(
        control_root=control_root,
        attempt_root=root,
        attempt_id="v31-attempt-dev-005",
        hooks=_hooks(control, calls),
        formal_block_limit=6,
    )
    assert second["status"] == "PASS"
    assert calls[-2:] == [("block", 4, "block-04"), ("block", 5, "block-05")]


def test_smoke_only_checkpoint_stops_for_probe_then_resumes_same_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _control()
    control_root = tmp_path / "control"
    control_root.mkdir()
    (control_root / "V31_METHOD_PLAN.json").write_text(
        json.dumps(control["method_plan"]), encoding="utf-8"
    )
    monkeypatch.setattr(
        "paper_eval.membind_v31.orchestration.verify_membind_v31_method_plan",
        lambda value: value,
    )
    calls: list[object] = []
    root = tmp_path / "attempt"

    smoke_only = run_v31_orchestration(
        control_root=control_root,
        attempt_root=root,
        attempt_id="v31-attempt-dev-smoke-only",
        hooks=_hooks(control, calls),
        formal_block_limit=0,
    )

    assert smoke_only["status"] == "SMOKE_PASS_PROBE_REQUIRED"
    assert smoke_only["completed_block_indices"] == []
    assert calls == [("smoke", (0, 1, 2), "smoke")]
    checkpoint = json.loads((root / "ORCHESTRATION_CHECKPOINT.json").read_text())
    assert checkpoint["status"] == "SMOKE_COMPLETED_PROBE_REQUIRED"
    assert checkpoint["next_block_index"] == 0
    assert (root / "SMOKE_ONLY_RESULT.json").is_file()
    assert not (root / "blocks").exists()

    resumed = run_v31_orchestration(
        control_root=control_root,
        attempt_root=root,
        attempt_id="v31-attempt-dev-smoke-only",
        hooks=_hooks(control, calls),
        formal_block_limit=4,
    )

    assert resumed["status"] == "MAIN_METHOD_PASS_BASELINE_RESUME_REQUIRED"
    assert calls == [
        ("smoke", (0, 1, 2), "smoke"),
        *[("block", index, f"block-{index:02d}") for index in range(4)],
    ]
