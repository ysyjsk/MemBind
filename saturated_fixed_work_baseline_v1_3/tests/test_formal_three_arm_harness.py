from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PLATFORM = {
    "path": "/profiles/platform.current.json",
    "payload_sha256": "platform-current-sha256",
}


def _module():
    import importlib.util

    path = ROOT / "saturated_fixed_work_baseline_v1_3" / "scripts" / "formal_three_arm_harness.py"
    spec = importlib.util.spec_from_file_location("formal_three_arm_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner_module():
    import importlib.util

    path = ROOT / "saturated_fixed_work_baseline_v1_3" / "scripts" / "run_formal_three_arm.py"
    scripts_dir = str(path.parent)
    sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_formal_three_arm_for_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_dir)
    return module


def _sealed_manifest(root: Path) -> dict:
    h = _module()
    manifest = h.build_manifest(
        root,
        implementation_identity={
            "source_bundle_sha256": "s",
            "evaluator_sha256": "e",
            "config_sha256": "c",
        },
        method_frozen={"seal_sha256": "m", "platform_manifest": PLATFORM},
        authority={
            "authority_sha256": "a",
            "context_ids": [f"h{i}" for i in range(5)],
        },
        platform_identity=PLATFORM,
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "FORMAL_CAMPAIGN_MANIFEST_SEAL.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest


def _valid_row(cell: dict) -> dict:
    return {
        **cell,
        "actual_attempt_id": cell["attempt_id"],
        "actual_namespace": cell["namespace"],
        "construction_status": "PASS",
        "construction_complete_status": "PASS",
        "construction_seal_status": "CONSTRUCTION_SEALED",
        "construction_artifacts_complete": True,
        "qa_status": "PASS",
        "qa_seal_status": "QA_SEALED",
        "qa_rows": 60,
        "qa_result_rows": 60,
        "t_build_ns": 10,
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _artifact_cell() -> dict:
    return {
        "attempt_id": "attempt-1",
        "namespace": "namespace-1",
        "arm": "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
        "history_id": "history-1",
        "expected_construction_artifacts": [
            "complete.json",
            "block/construction_seal.json",
            "block/metrics.json",
            "route_seal.json",
        ],
    }


def test_manifest_has_45_unique_cells_and_fixed_native_ours_async_order(tmp_path: Path) -> None:
    h = _module()
    manifest = h.build_manifest(tmp_path, implementation_identity={"source_bundle_sha256": "s", "evaluator_sha256": "e", "config_sha256": "c"}, method_frozen={"seal_sha256": "m", "platform_manifest": PLATFORM}, authority={"authority_sha256": "a", "context_ids": [f"h{i}" for i in range(5)]}, platform_identity=PLATFORM)
    cells = manifest["cells"]
    assert manifest["status"] == "SEALED"
    assert len(cells) == 45
    assert len({cell["cell_id"] for cell in cells}) == 45
    assert len({cell["attempt_id"] for cell in cells}) == 45
    assert len({cell["namespace"] for cell in cells}) == 45
    assert manifest["history_count"] == 15
    for history_unit in range(15):
        observed = [cell["arm"] for cell in cells if cell["history_index"] == history_unit]
        assert observed == list(h.ARMS)
        assert all(cell["base_history_index"] == history_unit // 3 for cell in cells if cell["history_index"] == history_unit)
        assert all(cell["replicate_id"] == 0 for cell in cells if cell["history_index"] == history_unit)
        assert all(cell["base_replicate_id"] == history_unit % 3 for cell in cells if cell["history_index"] == history_unit)
    assert manifest["identity"]["platform"] == PLATFORM["payload_sha256"]
    assert {cell["platform_manifest_sha256"] for cell in cells} == {
        PLATFORM["payload_sha256"]
    }


def test_manifest_rejects_duplicate_identity(tmp_path: Path) -> None:
    h = _module()
    manifest = h.build_manifest(tmp_path, implementation_identity={"source_bundle_sha256": "s", "evaluator_sha256": "e", "config_sha256": "c"}, method_frozen={"seal_sha256": "m", "platform_manifest": PLATFORM}, authority={"authority_sha256": "a", "context_ids": [f"h{i}" for i in range(5)]}, platform_identity=PLATFORM)
    manifest["cells"][1]["attempt_id"] = manifest["cells"][0]["attempt_id"]
    with pytest.raises(ValueError, match="duplicate"):
        h.validate_manifest(manifest)


def test_manifest_rejects_platform_identity_drift(tmp_path: Path) -> None:
    h = _module()
    with pytest.raises(ValueError, match="platform identities do not match"):
        h.build_manifest(
            tmp_path,
            implementation_identity={"source_bundle_sha256": "s"},
            method_frozen={
                "seal_sha256": "m",
                "platform_manifest": PLATFORM,
            },
            authority={
                "authority_sha256": "a",
                "context_ids": [f"h{i}" for i in range(5)],
            },
            platform_identity={**PLATFORM, "payload_sha256": "different"},
        )


def test_reducer_requires_full_construction_and_qa_before_pass(tmp_path: Path) -> None:
    h = _module()
    rows = [
        {
            "cell_id": f"c{i}",
            "history_index": i // 3,
            "base_history_index": (i // 3) // 3,
            "replicate_id": (i // 3) % 3,
            "base_replicate_id": (i // 3) % 3,
            "arm": h.ARMS[i % 3],
            "construction_status": "PASS",
            "construction_complete_status": "PASS",
            "construction_seal_status": "CONSTRUCTION_SEALED",
            "construction_artifacts_complete": True,
            "qa_status": "PASS",
            "qa_seal_status": "QA_SEALED",
            "qa_rows": 60,
            "qa_result_rows": 60,
            "t_build_ns": 10,
        }
        for i in range(45)
    ]
    result = h.reduce_formal(rows)
    assert result["status"] == "PASS"
    assert result["construction_cell_count"] == 45
    assert result["qa_seal_count"] == 45
    rows[0]["qa_status"] = "INVALID"
    blocked = h.reduce_formal(rows)
    assert blocked["status"] == "INCOMPLETE"
    assert blocked["history_effects"] == []


def test_exact_process_identity_rejects_stale_or_cross_cell_argv(tmp_path: Path) -> None:
    module = _runner_module()
    cell = {"attempt_id": "abc123", "namespace": "ns-a"}
    attempt_root = tmp_path / "construction"
    argv = [
        "/env/bin/python",
        str(module.RUNNER),
        "--output-root",
        str(attempt_root),
        "--attempt-id",
        "abc123",
        "--namespace",
        "ns-a",
    ]
    assert module._argv_has_exact_identity(argv, attempt_root=attempt_root, cell=cell)
    assert not module._argv_has_exact_identity(argv[:-1] + ["ns-b"], attempt_root=attempt_root, cell=cell)
    assert not module._argv_has_exact_identity(argv, attempt_root=tmp_path / "other", cell=cell)


def test_deterministic_failure_does_not_replace_and_stops_campaign(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _runner_module()
    root = tmp_path / "formal"
    _sealed_manifest(root)
    calls: list[dict] = []

    def execute(_root, _frozen, cell, **_kwargs):
        calls.append(dict(cell))
        return {
            **cell,
            "construction_status": "INVALID",
            "construction_complete_status": "FAILED",
            "construction_seal_status": "MISSING",
            "construction_artifacts_complete": False,
            "construction_failure": {
                "error_type": (
                    "saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime."
                    "LocalRuntimeConfigurationError"
                ),
                "error": "edge structured-output preflight failed",
            },
            "qa_status": "MISSING",
            "qa_rows": 0,
        }

    monkeypatch.setattr(runner, "_execute_cell", execute)
    result = runner.run(root, tmp_path / "frozen")

    assert len(calls) == 1
    assert result["status"] == "DETERMINISTIC_SYSTEM_FAILURE"
    seal = json.loads((root / "FORMAL_CAMPAIGN_FAILURE.json").read_text())
    assert seal["failure_class"] == "DETERMINISTIC_SYSTEM_FAILURE"
    assert seal["processed_cells"] == 1
    assert seal["valid_construction_cells"] == 0
    assert seal["valid_full_qa_cells"] == 0


def test_infrastructure_failure_receives_at_most_one_fresh_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _runner_module()
    root = tmp_path / "formal"
    _sealed_manifest(root)
    calls: list[dict] = []

    def execute(_root, _frozen, cell, **_kwargs):
        calls.append(dict(cell))
        if len(calls) == 1:
            return {
                **cell,
                "construction_status": "INVALID",
                "construction_complete_status": "FAILED",
                "construction_seal_status": "MISSING",
                "construction_artifacts_complete": False,
                "construction_failure": {
                    "error_type": "openai.APIConnectionError",
                    "error": "Connection error.",
                },
                "qa_status": "MISSING",
                "qa_rows": 0,
            }
        return _valid_row(cell)

    monkeypatch.setattr(runner, "_execute_cell", execute)
    result = runner.run(root, tmp_path / "frozen")

    assert result["status"] == "PASS"
    assert len(calls) == 46
    assert calls[1]["replacement_of"] == calls[0]["attempt_id"]
    assert calls[1]["attempt_id"] != calls[0]["attempt_id"]
    assert calls[1]["namespace"] != calls[0]["namespace"]
    invalid = json.loads((root / "INVALID_ATTEMPT_LEDGER.json").read_text())
    assert len(invalid["entries"]) == 1
    assert invalid["entries"][0]["failure_class"] == "INFRASTRUCTURE_TRANSIENT"


def test_failed_replacement_stops_immediately_even_when_transient(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _runner_module()
    root = tmp_path / "formal"
    _sealed_manifest(root)
    calls: list[dict] = []

    def execute(_root, _frozen, cell, **_kwargs):
        calls.append(dict(cell))
        return {
            **cell,
            "construction_status": "INVALID",
            "construction_complete_status": "FAILED",
            "construction_seal_status": "MISSING",
            "construction_artifacts_complete": False,
            "construction_failure": {
                "error_type": "openai.APITimeoutError",
                "error": "request timed out",
            },
            "qa_status": "MISSING",
            "qa_rows": 0,
        }

    monkeypatch.setattr(runner, "_execute_cell", execute)
    result = runner.run(root, tmp_path / "frozen")

    assert len(calls) == 2
    assert result["status"] == "REPLACEMENT_FAILURE"
    assert not any(cell.get("replacement_of") for cell in calls[2:])


def test_invalid_cell_never_increments_construction_or_qa_valid_counts(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _runner_module()
    root = tmp_path / "formal"
    _sealed_manifest(root)

    monkeypatch.setattr(
        runner,
        "_execute_cell",
        lambda _root, _frozen, cell, **_kwargs: {
            **cell,
            "construction_status": "PASS",
            "construction_complete_status": "PASS",
            "construction_seal_status": "CONSTRUCTION_SEALED",
            "construction_artifacts_complete": True,
            "qa_status": "INVALID",
            "qa_seal_status": "QA_INVALID",
            "qa_rows": 60,
            "qa_result_rows": 60,
            "qa_failure": {"reason": "qa_contract_invalid"},
        },
    )
    result = runner.run(root, tmp_path / "frozen")

    assert result["status"] == "DETERMINISTIC_SYSTEM_FAILURE"
    progress = json.loads((root / "FORMAL_PROGRESS.json").read_text())
    assert progress["processed_cells"] == 1
    assert progress["valid_construction_cells"] == 1
    assert progress["valid_full_qa_cells"] == 0
    assert progress["selected_valid_cells"] == 0


def test_attempt_environment_binds_exact_measured_provenance_run_id() -> None:
    runner = _runner_module()
    cell = {
        "campaign_id": "campaign",
        "cell_id": "h0-r0-A",
        "attempt_id": "attempt",
    }
    bound = runner._attempt_env({"MEMBIND_PROVENANCE_RUN_ID": "stale"}, cell)
    assert bound["MEMBIND_PROVENANCE_RUN_ID"] == "campaign-h0-r0-A-attempt"
    assert bound["MEMBIND_PROVENANCE_RUN_ID"] != "UNBOUND_PROVIDER_FREE"


def test_construction_contract_requires_terminal_identity_and_manifest_members(
    tmp_path: Path,
) -> None:
    runner = _runner_module()
    cell = _artifact_cell()
    _write_json(
        tmp_path / "complete.json",
        {
            "status": "PASS",
            "attempt_id": cell["attempt_id"],
            "namespace": cell["namespace"],
            "method": cell["arm"],
            "build_makespan_ns": 123,
        },
    )
    _write_json(
        tmp_path / "block" / "construction_seal.json",
        {
            "status": "CONSTRUCTION_SEALED",
            "identity": {
                "namespace": cell["namespace"],
                "method": cell["arm"],
                "context_id": cell["history_id"],
            },
        },
    )
    _write_json(tmp_path / "block" / "metrics.json", {"status": "PASS"})
    _write_json(tmp_path / "route_seal.json", {"status": "PASS"})

    assert runner._construction_contract(tmp_path, cell, returncode=0)[
        "construction_status"
    ] == "PASS"

    (tmp_path / "route_seal.json").unlink()
    missing = runner._construction_contract(tmp_path, cell, returncode=0)
    assert missing["construction_status"] == "INVALID"
    assert missing["missing_construction_artifacts"] == ["route_seal.json"]

    _write_json(tmp_path / "route_seal.json", {"status": "PASS"})
    complete = json.loads((tmp_path / "complete.json").read_text())
    complete["attempt_id"] = "cross-attempt"
    _write_json(tmp_path / "complete.json", complete)
    assert runner._construction_contract(tmp_path, cell, returncode=0)[
        "construction_status"
    ] == "INVALID"


def test_qa_contract_requires_exact_cell_and_unique_nonempty_question_identities(
    tmp_path: Path,
) -> None:
    runner = _runner_module()
    cell = _artifact_cell()
    summary = {
        "quality_status": "PASS",
        "expected_count": 60,
        "completed_count": 60,
        "invalid_count": 0,
        "context_id": cell["history_id"],
        "method": cell["arm"],
        "namespace": cell["namespace"],
    }
    parent = {
        "status": "CONSTRUCTION_SEALED",
        "context_id": cell["history_id"],
        "method": cell["arm"],
        "namespace": cell["namespace"],
        "workload_hash": "w" * 64,
    }
    _write_json(tmp_path / "quality_summary.json", summary)
    _write_json(
        tmp_path / "qa_seal.json",
        {"status": "QA_SEALED", "parent_construction_seal": parent, "summary": summary},
    )
    rows = [
        {
            "status": "COMPLETE",
            "judge_valid": True,
            "context_id": cell["history_id"],
            "qa_pair_id": f"pair-{index}",
            "question_id": f"question-{index}",
            "qa_identity_sha256": f"{index:064x}",
        }
        for index in range(60)
    ]
    (tmp_path / "qa_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert runner._qa_contract(tmp_path, returncode=0, cell=cell)["qa_status"] == "PASS"

    rows[-1]["qa_identity_sha256"] = ""
    (tmp_path / "qa_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    assert runner._qa_contract(tmp_path, returncode=0, cell=cell)["qa_status"] == "INVALID"

    rows[-1]["qa_identity_sha256"] = rows[-2]["qa_identity_sha256"]
    (tmp_path / "qa_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    assert runner._qa_contract(tmp_path, returncode=0, cell=cell)["qa_status"] == "INVALID"

    rows[-1]["qa_identity_sha256"] = f"{59:064x}"
    _write_json(tmp_path / "quality_summary.json", {**summary, "namespace": "cross-cell"})
    assert runner._qa_contract(tmp_path, returncode=0, cell=cell)["qa_status"] == "INVALID"
