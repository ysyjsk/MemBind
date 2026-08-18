"""CLI wiring test for the offline-only v3.1 development reducer."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reduce_membind_v31_development.py"


def _module():
    spec = importlib.util.spec_from_file_location("reduce_membind_v31_development", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_wires_only_sealed_input_paths_to_reducer_and_writer(tmp_path, monkeypatch) -> None:
    module = _module()
    loaded = {
        "baseline_acceptance": {},
        "baseline_results": [],
        "method_plan": {},
        "method_artifacts": [],
        "quality_report": {},
        "quality_rows": [],
        "workload_complexity": {},
    }
    table = {
        "status": "PASS",
        "table_run_id": "main-table-v31-dev-001",
        "payload_sha256": "a" * 64,
    }
    outputs = {"DEVELOPMENT_MAIN_TABLE.json": table}
    calls = {}

    def fake_load(**kwargs):
        calls["load"] = kwargs
        return loaded

    def fake_reduce(**kwargs):
        calls["reduce"] = kwargs
        return outputs

    def fake_write(root, value):
        calls["write"] = (root, value)

    monkeypatch.setattr(module, "load_development_inputs", fake_load)
    monkeypatch.setattr(module, "reduce_development_results", fake_reduce)
    monkeypatch.setattr(module, "write_development_outputs", fake_write)
    output = tmp_path / "output"

    assert module.main(
        [
            "--table-run-id",
            "main-table-v31-dev-001",
            "--baseline-acceptance",
            str(tmp_path / "acceptance.json"),
            "--baseline-run-root",
            str(tmp_path / "baseline"),
            "--method-plan",
            str(tmp_path / "PLAN.json"),
            "--method-run-root",
            str(tmp_path / "method"),
            "--quality-root",
            str(tmp_path / "quality"),
            "--workload-complexity",
            str(tmp_path / "V31_WORKLOAD_COMPLEXITY.json"),
            "--output-root",
            str(output),
        ]
    ) == 0

    assert calls["reduce"] == {"table_run_id": "main-table-v31-dev-001", **loaded}
    assert calls["write"] == (output, outputs)
    assert set(calls["load"]) == {
        "baseline_acceptance_path",
        "baseline_run_root",
        "method_plan_path",
        "method_run_root",
        "quality_root",
        "workload_complexity_path",
    }
