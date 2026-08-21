from __future__ import annotations

import hashlib
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v5.offline_analyzer import (
    EXPECTED_BLOCKS,
    analyze_sealed_workload,
    write_analysis_artifacts,
)


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"


def test_sealed_blocks_and_exact_work_counts() -> None:
    result = analyze_sealed_workload(SFWB)
    assert tuple(result["blocks"]) == ("B0-A", "B0-B", "B1", "MemBind-v3.1")
    expected = {
        "B0-A": (255, 256, 717681, 572),
        "B0-B": (255, 256, 717732, 572),
        "B1": (184, 185, 213288, 448),
        "MemBind-v3.1": (316, 316, 729048, 747),
    }
    for name, values in expected.items():
        work = result["blocks"][name]["work"]
        assert (
            work["logical_calls"],
            work["transport_attempts"],
            work["input_tokens"],
            work["embedding_items"],
        ) == values
        assert result["blocks"][name]["metrics"]["complete_publication_coverage"] is True


def test_prompt_and_source_attribution_is_reconstructed() -> None:
    result = analyze_sealed_workload(SFWB)
    b0 = result["blocks"]["B0-A"]
    assert b0["operator_counts"]["NODE_EXTRACTION"]["logical_calls"] == 12
    assert b0["operator_counts"]["EDGE_EXTRACTION"]["logical_calls"] == 12
    assert b0["operator_counts"]["TIMESTAMP"]["logical_calls"] == 107
    assert b0["operator_counts"]["EDGE_RESOLUTION"]["logical_calls"] == 106
    assert b0["operator_counts"]["EMBEDDING"]["embedding_items"] == 572
    assert b0["operator_counts"]["PERSISTENCE"]["span_count"] > 0
    assert b0["by_source"]["11"]["logical_calls"] == 82
    assert sum(row["embedding_items"] for row in b0["by_source"].values()) == 572
    assert result["blocks"]["MemBind-v3.1"]["by_source"]["8"]["logical_calls"] == 138
    b0b = result["work_attribution"]["B0-B"]
    edge_delta = next(row for row in b0b["operator_delta_records"] if row["operator"] == "EDGE_RESOLUTION")
    assert edge_delta["category"] == "UNKNOWN"
    transport = next(row for row in b0b["operator_delta_records"] if row["operator"] == "TRANSPORT")
    assert transport["retry_overhead_delta"] == 0


def test_serial_floor_is_explicit_and_not_zero() -> None:
    result = analyze_sealed_workload(SFWB)
    floor = result["semantic_divergence"]["serial_self_divergence_floor"]
    assert floor["graph"]["exact_match"] is False
    assert floor["graph"]["difference_counts"] == {
        "entity_key": 2,
        "edge_key": 4,
        "attribute": 6,
        "temporal": 6,
        "source_link": 4,
    }
    assert floor["work"]["input_tokens_delta"] == 51
    assert floor["work"]["logical_calls_delta"] == 0


def test_sealed_files_are_read_only_and_no_live_imports() -> None:
    seal_paths = [
        SFWB / "artifacts" / spec["attempt_root"] / "seal.json" for spec in EXPECTED_BLOCKS.values()
    ]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in seal_paths}
    result = analyze_sealed_workload(SFWB)
    assert all(row["sealed"]["status"] == "VALIDATED_SEALED" for row in result["blocks"].values())
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in seal_paths}
    assert before == after
    source = (SFWB / "src/saturated_fixed_work_baseline_v1_3/membind_v5/offline_analyzer.py").read_text()
    assert "import graphiti" not in source.lower()
    assert "neo4j" not in source.lower()


def test_artifact_writer_emits_fresh_independent_root(tmp_path: Path) -> None:
    result = analyze_sealed_workload(SFWB)
    out = tmp_path / "analysis"
    paths = write_analysis_artifacts(result, out)
    expected_names = {
        "SFWB_V13_V5_MIGRATION_AUDIT.md",
        "SFWB_V13_REALIZED_WORK_ATTRIBUTION.json",
        "SFWB_V13_REALIZED_WORK_ATTRIBUTION.md",
        "SFWB_V13_SEMANTIC_DIVERGENCE_ANALYSIS.json",
        "SFWB_V13_SEMANTIC_DIVERGENCE_ANALYSIS.md",
        "SFWB_V13_V5_METHODOLOGY_RETHINK.md",
        "SFWB_V13_V5_DECISION.md",
    }
    assert {p.name for p in paths} == expected_names
    assert json.loads((out / "SFWB_V13_REALIZED_WORK_ATTRIBUTION.json").read_text())["schema_version"]
