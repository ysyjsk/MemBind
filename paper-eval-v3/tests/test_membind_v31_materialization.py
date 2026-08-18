"""TDD for read-only APC status and transactional v3.1 plan materialization."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    build_apc_aligned_baseline_plan,
)
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.baseline_acceptance import (
    ACCEPTANCE_SCHEMA,
    EXPECTED_BASELINE_RUN_ID,
)
from paper_eval.membind_v31.materialization import (
    BASELINE_ACCEPTANCE_NAME,
    CONTROL_COMMIT_NAME,
    METHOD_PLAN_NAME,
    MaterializationError,
    inspect_materialized_control,
    materialize_membind_v31_live_plan,
    materialize_membind_v31_control,
)


PROJECT = Path(__file__).resolve().parents[1]
STATUS_SCRIPT = PROJECT / "scripts/status_membind_v31_baseline.py"
MATERIALIZE_SCRIPT = PROJECT / "scripts/materialize_membind_v31_control.py"


def _module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plan() -> dict[str, object]:
    counts = {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}
    cursor = 1
    sources: dict[str, list[str]] = {}
    for history in APC_BASELINE_HISTORIES:
        sources[history] = [f"{cursor + index:064x}" for index in range(counts[history])]
        cursor += counts[history]
    return build_apc_aligned_baseline_plan(
        run_id=EXPECTED_BASELINE_RUN_ID,
        history_source_sha256s=sources,
        interarrival_ns=10,
        execution_envelope_sha256="e" * 64,
        service_reference_ns=12,
        normalized_offered_load=1.2,
    )


def _accepted(plan: dict[str, object]) -> dict[str, object]:
    body = {
        "schema_version": ACCEPTANCE_SCHEMA,
        "status": "PASS",
        "artifact_status": "SEALED_VALID",
        "semantic_verdicts": {
            method: {"direct_violations": 0, "semantic_status": "SAFE"}
            for method in ("U0-aligned", "A0-aligned", "P(C=2)-aligned")
        },
        "run_id": EXPECTED_BASELINE_RUN_ID,
        "completed_block_count": 12,
        "terminal_episode_count_per_method": 188,
        "plan_payload_sha256": plan["payload_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "arrival_trace_sha256": plan["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": plan["shared_execution_envelope_sha256"],
        "global_llm_admission_k": 2,
        "execution_identity_sha256": "d" * 64,
        "block_result_payload_sha256s": [f"{index + 1000:064x}" for index in range(12)],
        "quality_run_id": "qev1-apc-20260817-001",
        "quality_report_payload_sha256": "1" * 64,
        "quality_identity_sha256": "2" * 64,
        "quality_runtime_identity_sha256": "3" * 64,
    }
    return {**body, "payload_sha256": payload_sha256(body)}


def _write_plan(root: Path, plan: dict[str, object]) -> None:
    root.mkdir(parents=True)
    (root / "PLAN.json").write_text(
        json.dumps(plan, sort_keys=True) + "\n", encoding="utf-8"
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_not_terminal_materialization_is_write_free_and_does_not_touch_apc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / EXPECTED_BASELINE_RUN_ID
    _write_plan(baseline, _plan())
    before = _tree_bytes(baseline)
    output = tmp_path / "v31-control"
    monkeypatch.setattr(
        "paper_eval.membind_v31.materialization.verify_apc_baseline_acceptance",
        lambda *_args, **_kwargs: {
            "schema_version": ACCEPTANCE_SCHEMA,
            "status": "NOT_TERMINAL",
            "run_id": EXPECTED_BASELINE_RUN_ID,
            "completed_block_count": 1,
            "reason": "BLOCK_01_PENDING",
        },
    )

    result = materialize_membind_v31_control(
        baseline_root=baseline,
        quality_root=None,
        output_root=output,
        run_id="membind-v31-dev-20260817-001",
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )

    assert result["status"] == "NOT_TERMINAL"
    assert not output.exists()
    assert _tree_bytes(baseline) == before


def test_pass_materialization_commits_a_hash_bound_pair_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / EXPECTED_BASELINE_RUN_ID
    plan = _plan()
    accepted = _accepted(plan)
    _write_plan(baseline, plan)
    before = _tree_bytes(baseline)
    output = tmp_path / "v31-control"
    monkeypatch.setattr(
        "paper_eval.membind_v31.materialization.verify_apc_baseline_acceptance",
        lambda *_args, **_kwargs: accepted,
    )

    first = materialize_membind_v31_control(
        baseline_root=baseline,
        quality_root=tmp_path / "quality",
        output_root=output,
        run_id="membind-v31-dev-20260817-002",
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )
    persisted = inspect_materialized_control(output)
    second = materialize_membind_v31_control(
        baseline_root=baseline,
        quality_root=tmp_path / "quality",
        output_root=output,
        run_id="membind-v31-dev-20260817-002",
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )

    assert first["status"] == "PASS"
    assert first["disposition"] == "MATERIALIZED"
    assert second["disposition"] == "REUSED_IDENTICAL"
    assert set(path.name for path in output.iterdir()) == {
        BASELINE_ACCEPTANCE_NAME,
        METHOD_PLAN_NAME,
        CONTROL_COMMIT_NAME,
    }
    assert persisted["acceptance"] == accepted
    assert persisted["method_plan"]["authorization_scope"] == (
        "LIVE_EXECUTION_AUTHORIZED_BASELINE_MERGE_PENDING"
    )
    assert persisted["commit"]["baseline_acceptance_artifact"] == (
        BASELINE_ACCEPTANCE_NAME
    )
    assert persisted["commit"]["baseline_acceptance_payload_sha256"] == accepted[
        "payload_sha256"
    ]
    assert "reuse_audit_artifact" not in persisted["commit"]
    assert "reuse_audit_payload_sha256" not in persisted["commit"]
    assert persisted["commit"]["method_plan_payload_sha256"] == persisted[
        "method_plan"
    ]["payload_sha256"]
    assert _tree_bytes(baseline) == before


def test_source_bound_live_plan_is_promoted_by_later_acceptance_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / EXPECTED_BASELINE_RUN_ID
    plan = _plan()
    accepted = _accepted(plan)
    _write_plan(baseline, plan)
    output = tmp_path / "v31-control"

    live = materialize_membind_v31_live_plan(
        baseline_root=baseline,
        output_root=output,
        run_id="membind-v31-dev-20260818-001",
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )
    before = (output / METHOD_PLAN_NAME).read_bytes()
    assert set(path.name for path in output.iterdir()) == {METHOD_PLAN_NAME}
    assert live["authorization_scope"] == (
        "LIVE_EXECUTION_AUTHORIZED_BASELINE_MERGE_PENDING"
    )
    monkeypatch.setattr(
        "paper_eval.membind_v31.materialization.verify_apc_baseline_acceptance",
        lambda *_args, **_kwargs: accepted,
    )

    result = materialize_membind_v31_control(
        baseline_root=baseline,
        quality_root=tmp_path / "quality",
        output_root=output,
        run_id="membind-v31-dev-20260818-001",
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )

    assert result["status"] == "PASS"
    assert (output / METHOD_PLAN_NAME).read_bytes() == before
    assert set(path.name for path in output.iterdir()) == {
        BASELINE_ACCEPTANCE_NAME,
        METHOD_PLAN_NAME,
        CONTROL_COMMIT_NAME,
    }
    assert inspect_materialized_control(output)["acceptance"] == accepted


def test_existing_uncommitted_or_conflicting_output_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / EXPECTED_BASELINE_RUN_ID
    plan = _plan()
    _write_plan(baseline, plan)
    monkeypatch.setattr(
        "paper_eval.membind_v31.materialization.verify_apc_baseline_acceptance",
        lambda *_args, **_kwargs: _accepted(plan),
    )
    output = tmp_path / "v31-control"
    output.mkdir()
    (output / BASELINE_ACCEPTANCE_NAME).write_text("{}\n", encoding="utf-8")

    with pytest.raises(MaterializationError, match="incomplete or conflicting"):
        materialize_membind_v31_control(
            baseline_root=baseline,
            quality_root=tmp_path / "quality",
            output_root=output,
            run_id="membind-v31-dev-20260817-003",
            methodology_sha256="a" * 64,
            workplan_sha256="b" * 64,
        )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.__setitem__("artifact_status", "INCOMPLETE"),
        lambda value: value["semantic_verdicts"]["P(C=2)-aligned"].__setitem__(
            "semantic_status", "VIOLATION_OBSERVED"
        ),
    ),
)
def test_materialization_rejects_invalid_artifact_or_semantic_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate,
) -> None:
    baseline = tmp_path / EXPECTED_BASELINE_RUN_ID
    plan = _plan()
    _write_plan(baseline, plan)
    accepted = _accepted(plan)
    mutate(accepted)
    accepted["payload_sha256"] = payload_sha256(
        {key: value for key, value in accepted.items() if key != "payload_sha256"}
    )
    monkeypatch.setattr(
        "paper_eval.membind_v31.materialization.verify_apc_baseline_acceptance",
        lambda *_args, **_kwargs: accepted,
    )

    with pytest.raises(MaterializationError, match="baseline acceptance"):
        materialize_membind_v31_control(
            baseline_root=baseline,
            quality_root=tmp_path / "quality",
            output_root=tmp_path / "v31-control",
            run_id="membind-v31-dev-20260817-006",
            methodology_sha256="a" * 64,
            workplan_sha256="b" * 64,
        )


def test_terminal_materialization_preserves_offline_reuse_audit_filename_and_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / EXPECTED_BASELINE_RUN_ID
    plan = _plan()
    accepted = _accepted(plan)
    _write_plan(baseline, plan)
    monkeypatch.setattr(
        "paper_eval.membind_v31.materialization.verify_apc_baseline_acceptance",
        lambda *_args, **_kwargs: accepted,
    )
    output = tmp_path / "v31-control"
    output.mkdir()
    offline_reuse = b'{"schema_version":"offline-reuse-audit-test","status":"PASS"}\n'
    (output / "V31_REUSE_AUDIT.json").write_bytes(offline_reuse)

    result = materialize_membind_v31_control(
        baseline_root=baseline,
        quality_root=tmp_path / "quality",
        output_root=output,
        run_id="membind-v31-dev-20260817-005",
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )

    assert result["status"] == "PASS"
    assert (output / "V31_REUSE_AUDIT.json").read_bytes() == offline_reuse
    assert (output / BASELINE_ACCEPTANCE_NAME).is_file()
    assert inspect_materialized_control(output)["acceptance"] == accepted


def test_status_only_cli_prints_not_terminal_and_never_materializes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module(STATUS_SCRIPT, "status_membind_v31_baseline_test")
    monkeypatch.setattr(
        module,
        "verify_apc_baseline_acceptance",
        lambda *_args, **_kwargs: {
            "schema_version": ACCEPTANCE_SCHEMA,
            "status": "NOT_TERMINAL",
            "run_id": EXPECTED_BASELINE_RUN_ID,
            "completed_block_count": 1,
            "reason": "BLOCK_01_PENDING",
        },
    )
    output = tmp_path / "must-not-exist"

    assert module.main(["--baseline-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_TERMINAL"
    assert not output.exists()


def test_materialize_cli_delegates_without_fallback_or_hidden_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module(MATERIALIZE_SCRIPT, "materialize_membind_v31_control_test")
    observed: dict[str, object] = {}

    def fake_materialize(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {"status": "PASS", "disposition": "MATERIALIZED"}

    monkeypatch.setattr(module, "verify_apc_baseline_acceptance", lambda *_args, **_kwargs: {"status": "PASS"})
    monkeypatch.setattr(module, "materialize_membind_v31_control", fake_materialize)
    (tmp_path / "methodology.md").write_text("methodology\n", encoding="utf-8")
    (tmp_path / "workplan.md").write_text("workplan\n", encoding="utf-8")
    result = module.main(
        [
            "--baseline-root",
            str(tmp_path / "baseline"),
            "--quality-root",
            str(tmp_path / "quality"),
            "--output-root",
            str(tmp_path / "output"),
            "--run-id",
            "membind-v31-dev-20260817-004",
            "--methodology",
            str(tmp_path / "methodology.md"),
            "--workplan",
            str(tmp_path / "workplan.md"),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    assert observed["run_id"] == "membind-v31-dev-20260817-004"
