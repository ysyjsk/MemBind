from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256, sha256_file


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/generate_membind_v4_mseg_oracle.py"
SEALED_HASHES = {
    "V4_CONFLICT_OFFLINE_REPLAY.json": (
        "d003baeca9858cbe91ec11b0d0216741aa2cc32529536bb5616ebe0d412c0834"
    ),
    "V4_READY_TASK_OPPORTUNITY_PROFILE.json": (
        "5f33ca8c7e15627684e62b2f4e79b8a32ccb7d07c07a93e24c4da810b5081d65"
    ),
    "V4_PARALLELISM_FUNNEL.json": (
        "9735680b445e2aa93c623fce2314e3ea1ccf75254a2a772f0b9ef5c9ca0045a7"
    ),
    "V4_FINAL_DECISION.md": (
        "7e971c32a278578ceff1ddbc0ca13486048e428c87d79c4a5d7fb4bcdf97b941"
    ),
}
JSON_ARTIFACTS = (
    "MSEG_GRAPH.json",
    "MSEG_DEPENDENCY_SUMMARY.json",
    "MSEG_LATE_BOUND_ANALYSIS.json",
    "MSEG_PUBLICATION_CRITICAL_PATH.json",
    "MSEG_CONFLICT_ORACLE.json",
    "MSEG_VALIDATED_EXECUTION_ORACLE.json",
    "MSEG_ORACLE_COMPARISON.json",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    digest = value.pop("payload_sha256")
    assert digest == payload_sha256(value)
    value["payload_sha256"] = digest
    return value


def test_generator_emits_fail_closed_mseg_oracle_bundle(tmp_path: Path) -> None:
    output = tmp_path / "mseg"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--output-root", str(output)],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )

    expected = {
        *JSON_ARTIFACTS,
        "MSEG_OPERATOR_AUDIT.md",
        "MSEG_FINE_GRAINED_TRACE.jsonl",
        "MSEG_NOVELTY_AUDIT.md",
        "MSEG_FINAL_DECISION.md",
    }
    assert {path.name for path in output.iterdir()} == expected

    graph = _read_json(output / "MSEG_GRAPH.json")
    assert graph["status"] == "NOT_RECOVERABLE_FROM_SEALED_TRACE"
    assert graph["mseg_recovered"] is False
    assert graph["node_count"] == "NOT_OBSERVABLE"
    assert graph["edge_count_by_type"] == {
        "DATA_DEP": "NOT_OBSERVABLE",
        "EFFECT_CONFLICT_DEP": "NOT_OBSERVABLE",
        "PUBLICATION_DEP": "NOT_OBSERVABLE",
        "VERSION_DEP": "NOT_OBSERVABLE",
    }

    comparison = _read_json(output / "MSEG_ORACLE_COMPARISON.json")
    observability = comparison["trace_observability"]
    assert observability["request_count"] == 279
    assert observability["request_kind_counts"] == {"COMPILE": 24, "FRONTIER": 255}
    assert observability["complete_client_lifecycle_count"] == 279
    assert observability["field_coverage"]["operator_role"]["status"] == (
        "NOT_OBSERVABLE"
    )
    assert comparison["mseg_recovered"] is False
    assert comparison["oracles"]["O0_CURRENT"]["makespan_ns"] == 698_777_570_889
    for oracle in (
        "O1_CERTIFIED_EARLY",
        "O2_CONFLICT_ORDERED",
        "O3_VALIDATED_EXECUTION",
        "O4_PUBLICATION_CRITICAL",
    ):
        assert comparison["oracles"][oracle]["status"] == "NOT_OBSERVABLE"
    assert comparison["decision"] == {
        "dominant_gain_source": "NONE_ORACLE_NOT_RECOVERABLE",
        "live_authorized": False,
        "mseg_recovered": False,
        "next_mechanism": "STOP_V4_FINE_GRAINED",
        "root_cause": "FINE_GRAINED_CAUSAL_IDENTITY_NOT_OBSERVABLE",
    }

    trace_lines = (output / "MSEG_FINE_GRAINED_TRACE.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(trace_lines) == 1
    trace_record = json.loads(trace_lines[0])
    assert trace_record["record_type"] == "OBSERVABILITY_BARRIER"
    assert trace_record["operator_instance_count"] == 0
    assert trace_record["operator_rows_fabricated"] is False


def test_generated_audits_state_code_evidence_and_claim_boundaries(tmp_path: Path) -> None:
    output = tmp_path / "mseg"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--output-root", str(output)],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )

    operator_audit = (output / "MSEG_OPERATOR_AUDIT.md").read_text(encoding="utf-8")
    for required in (
        "Graphiti 0.29.3",
        "Code-Proven Operator Surface",
        "Trace-Observed Instances",
        "dedupe_nodes.nodes",
        "dedupe_edges.resolve_edge",
        "extract_edges.extract_timestamps",
        "add_nodes_and_edges_bulk",
        "NOT_OBSERVABLE",
        "request order",
        "does not mean the",
        "publication critical path",
    ):
        assert required in operator_audit

    novelty = (output / "MSEG_NOVELTY_AUDIT.md").read_text(encoding="utf-8")
    for related_work in ("Parrot", "Agentix", "ROCOCO", "Sarathi-Serve"):
        assert related_work in novelty
    assert "No novelty claim is authorized" in novelty

    decision = (output / "MSEG_FINAL_DECISION.md").read_text(encoding="utf-8")
    for required in (
        "MSEG_RECOVERED: no",
        "H1_OVER_SERIALIZATION: rejected",
        "H2_LATE_BOUND_DEPENDENCY: rejected",
        "H3_CRITICALITY_HETEROGENEITY: rejected",
        "H4_SEMANTIC_ADMISSION_OPPORTUNITY: rejected",
        "MAX_LEGAL_READY_WIDTH: NOT_OBSERVABLE",
        "O0_CURRENT: 698777570889 ns",
        "O1_CERTIFIED_EARLY: NOT_OBSERVABLE",
        "NEXT_MECHANISM: STOP_V4_FINE_GRAINED",
        "LIVE_AUTHORIZED: no",
        "SEALED_ARTIFACTS_UNCHANGED: yes",
    ):
        assert required in decision


def test_generator_does_not_modify_prior_sealed_artifacts(tmp_path: Path) -> None:
    v4_root = PROJECT / "artifacts/paper_eval/membind_v4"
    before = {name: sha256_file(v4_root / name) for name in SEALED_HASHES}
    assert before == SEALED_HASHES

    subprocess.run(
        [sys.executable, str(SCRIPT), "--output-root", str(tmp_path / "mseg")],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )

    after = {name: sha256_file(v4_root / name) for name in SEALED_HASHES}
    assert after == before


def test_registered_bundle_is_byte_identical_to_fresh_generation(tmp_path: Path) -> None:
    registered = PROJECT / "artifacts/paper_eval/membind_v4/mseg"
    regenerated = tmp_path / "mseg"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--output-root", str(regenerated)],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert {path.name for path in registered.iterdir()} == {
        path.name for path in regenerated.iterdir()
    }
    for registered_path in registered.iterdir():
        regenerated_path = regenerated / registered_path.name
        assert registered_path.read_bytes() == regenerated_path.read_bytes()
