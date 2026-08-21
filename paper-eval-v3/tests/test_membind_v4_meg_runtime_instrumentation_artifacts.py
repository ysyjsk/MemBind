"""Offline artifact gates for the real Graphiti 0.29.3 MEG runtime seam."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file


PROJECT = Path(__file__).resolve().parents[1]
GRAPHITI = (
    PROJECT.parent
    / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
)
EXPECTED = {
    "GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.json",
    "GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.md",
    "GRAPHITI_WRITE_PATH_COVERAGE.json",
    "MEG_RUNTIME_PASSIVE_EQUIVALENCE.json",
    "MEG_RUNTIME_INSTRUMENTATION_QUALIFICATION.json",
    "MEG_RUNTIME_INSTRUMENTATION_DECISION.md",
}


def _read_verified(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    stored = value.pop("payload_sha256")
    assert stored == payload_sha256(value)
    value["payload_sha256"] = stored
    return value


def test_offline_runtime_qualification_bundle_is_complete_and_fail_closed(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(PROJECT / "scripts"))
    from run_meg_runtime_instrumentation_offline import main

    output = tmp_path / "qualification"
    boundary_doc = tmp_path / "GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.md"
    assert (
        main(
            [
                "--graphiti-root",
                str(GRAPHITI),
                "--output-root",
                str(output),
                "--boundary-audit-doc",
                str(boundary_doc),
            ]
        )
        == 0
    )
    assert {path.name for path in output.iterdir()} == EXPECTED
    assert boundary_doc.read_bytes() == (
        output / "GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.md"
    ).read_bytes()

    audit = _read_verified(output / "GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.json")
    coverage = _read_verified(output / "GRAPHITI_WRITE_PATH_COVERAGE.json")
    passive = _read_verified(output / "MEG_RUNTIME_PASSIVE_EQUIVALENCE.json")
    qualification = _read_verified(
        output / "MEG_RUNTIME_INSTRUMENTATION_QUALIFICATION.json"
    )

    assert audit["graphiti_version"] == "0.29.3"
    assert audit["status"] == "PASS"
    assert coverage["covered_write_paths"] == coverage["relevant_write_paths"]
    assert coverage["coverage_ratio"] == 1.0
    assert any(
        row["relevance"] == "CONFIG_GUARDED_OUT_OF_SCOPE"
        and "saga" in row["file"]
        for row in coverage["write_path_inventory"]
    )

    assert passive["mode"] == "OBSERVE_ONLY"
    assert passive["status"] == "PASS"
    assert passive["violations"] == []
    assert passive["baseline"] == passive["instrumented"]
    assert passive["instrumentation_source_hashes"]["graphiti_0293_runtime"] == (
        sha256_file(
            PROJECT / "src/paper_eval/membind_v4/mseg/graphiti_0293_runtime.py"
        )
    )
    assert passive["zero_shadow_behavior"] is True
    assert passive["network_calls"] == passive["services_started"] == 0

    assert all(qualification["gates"].values())
    assert qualification["instrumentation_source_hashes"] == passive[
        "instrumentation_source_hashes"
    ]
    assert qualification["scope"] == {
        "live_database_connections": 0,
        "live_model_calls": 0,
        "network_calls": 0,
        "persistent_writes": 0,
        "sealed_artifacts_modified": False,
        "services_started": 0,
    }
    assert qualification["metrics"]["request_lineage_coverage"] == 1.0
    assert qualification["metrics"]["final_mutation_epoch"] == 1
    assert qualification["writer_domain"]["status"] == (
        "CERTIFIED_SINGLE_WRITER_DOMAIN"
    )
    assert qualification["decision"] == {
        "authorized_history_id": "07741c45",
        "authorized_mode": "OBSERVE_ONLY",
        "authorized_source_sequences": [0, 1, 2],
        "bounded_real_capture_authorized": True,
        "bounded_real_capture_started": False,
        "next_gate": "REAL_OBSERVE_ONLY_CAPTURE_0_2",
        "qualification": "QUALIFIED_REAL_MEG_RUNTIME_INSTRUMENTATION",
        "scheduler_authorized": False,
        "semantic_change_authorized": False,
        "shadow_read_authorized": False,
        "status": "PASS_OFFLINE_MEG_RUNTIME_INSTRUMENTATION",
    }

    decision = (output / "MEG_RUNTIME_INSTRUMENTATION_DECISION.md").read_text(
        encoding="utf-8"
    )
    assert "STATUS: PASS_OFFLINE_MEG_RUNTIME_INSTRUMENTATION" in decision
    assert "NEXT_GATE: REAL_OBSERVE_ONLY_CAPTURE_0_2" in decision
    assert "bounded real capture started by this qualification: `False`" in decision
    assert "scheduler authorized: `False`" in decision
