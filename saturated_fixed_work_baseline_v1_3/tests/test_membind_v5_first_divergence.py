from __future__ import annotations

import hashlib
import json
from pathlib import Path

import saturated_fixed_work_baseline_v1_3.membind_v5.first_divergence as first_divergence_module
from saturated_fixed_work_baseline_v1_3.membind_v5.first_divergence import (
    analyze_first_divergence,
    write_first_semantic_divergence_artifacts,
    write_first_divergence_artifacts,
)


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"


def test_all_sources_are_aligned_and_source_evidence_is_equal() -> None:
    result = analyze_first_divergence(SFWB)
    assert len(result["sources"]) == 12
    assert all(row["source_evidence"]["hash_equal"] for row in result["sources"].values())
    assert all(row["publication"]["b0_complete"] and row["publication"]["membind_complete"] for row in result["sources"].values())


def test_extra_resolution_and_timestamp_work_is_reconstructed() -> None:
    result = analyze_first_divergence(SFWB)
    delta = result["aggregate"]["logical_operator_delta"]
    assert delta["EDGE_RESOLUTION"] == 32
    assert delta["TIMESTAMP"] == 30
    assert result["aggregate"]["extra_work_explanation"]["duplicate_consumption_provable"] is False


def test_first_semantic_boundary_is_fail_closed_when_outputs_are_missing() -> None:
    result = analyze_first_divergence(SFWB)
    for row in result["sources"].values():
        first = row["first_provable_divergence"]
        assert first["classification"] == "OBSERVABILITY_INSUFFICIENT"
        assert first["semantic_cause_provable"] is False
        assert row["observability"]["b0_prepared_outputs"] is False
        assert row["observability"]["candidate_identity_parity"] is False
        assert row["observability"]["batch_membership_parity"] is False


def test_known_first_observable_signals_are_not_final_graph_inferences() -> None:
    result = analyze_first_divergence(SFWB)
    # Source 1 has an edge-extraction token-vector mismatch before its extra
    # timestamp fan-out; the report must preserve that as an observation only.
    source_one = result["sources"]["1"]
    assert source_one["first_observable_signal"]["stage"] == "edge_extraction"
    assert source_one["first_observable_signal"]["kind"] == "input_token_vector"
    assert source_one["first_observable_signal"]["semantic_cause_provable"] is False
    # Source 0 has no extraction request mismatch; its first measurable shape
    # difference is candidate-span coverage, whose identity is unavailable.
    source_zero = result["sources"]["0"]
    assert source_zero["first_observable_signal"]["stage"] == "node_candidate_formation"


def test_artifacts_are_fresh_and_sealed_hashes_unchanged(tmp_path: Path) -> None:
    result = analyze_first_divergence(SFWB)
    sealed = [
        SFWB / "artifacts" / root / "seal.json"
        for root in (
            "sfwb-v1-3-simple-20260821-004/qualification/blocks/qualification-b0-a/attempt-001",
            "sfwb-v1-3-membind-ext-20260821-001/qualification/blocks/qualification-membind/attempt-001",
        )
    ]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sealed}
    paths = write_first_divergence_artifacts(result, tmp_path / "first-divergence")
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sealed}
    assert before == after
    assert {path.name for path in paths} == {
        "SFWB_V13_V5_FIRST_DIVERGENCE_ANALYSIS.json",
        "SFWB_V13_V5_FIRST_DIVERGENCE_ANALYSIS.md",
        "SFWB_V13_V5_SOURCE_CAUSAL_CHAIN.json",
        "SFWB_V13_V5_ROOT_CAUSE_DECISION.md",
    }
    payload = json.loads((tmp_path / "first-divergence" / "SFWB_V13_V5_FIRST_DIVERGENCE_ANALYSIS.json").read_text())
    assert payload["decision"]["gate"] == "STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY"


def test_paired_semantic_fingerprint_precedes_request_shape_fallback(monkeypatch, tmp_path: Path) -> None:
    original_loader = first_divergence_module._load_block
    blocks: dict[str, dict] = {}
    for name, spec in first_divergence_module.EXPECTED_BLOCKS.items():
        if name not in {"B0-A", "MemBind-v3.1"}:
            continue
        block = original_loader(SFWB, name, spec)
        attempt = tmp_path / name.replace("-", "_")
        attempt.mkdir()
        block["path"] = attempt
        records = []
        for source in range(12):
            records.append(
                {
                    "source_sequence": source,
                    "boundary": "NODE_EXTRACTION_OUTPUT",
                    "count": 1,
                    "ordered_identity_sha256": ("a" if name == "B0-A" else ("b" if source == 0 else "a")) * 64,
                    "membership_identity_sha256": "c" * 64,
                }
            )
        (attempt / "semantic_fingerprints.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
        )
        blocks[name] = block

    def fake_loader(root: Path, name: str, spec: dict[str, str]) -> dict:
        return blocks.get(name) or original_loader(root, name, spec)

    monkeypatch.setattr(first_divergence_module, "_load_block", fake_loader)
    result = analyze_first_divergence(SFWB)
    source_zero = result["sources"]["0"]
    assert source_zero["first_provable_divergence"]["classification"] == "EXTRACTION_DIVERGENCE"
    assert source_zero["first_provable_divergence"]["semantic_cause_provable"] is True
    assert source_zero["first_provable_divergence"]["semantic_fingerprint_boundary"] == "NODE_EXTRACTION_OUTPUT"
    assert result["decision"]["gate"] == "GO_V5_NATIVE_EQUIVALENT_COMPILE"


def test_single_sided_fingerprint_does_not_prove_divergence(monkeypatch, tmp_path: Path) -> None:
    original_loader = first_divergence_module._load_block
    native = original_loader(SFWB, "B0-A", first_divergence_module.EXPECTED_BLOCKS["B0-A"])
    membind = original_loader(SFWB, "MemBind-v3.1", first_divergence_module.EXPECTED_BLOCKS["MemBind-v3.1"])
    native_attempt = tmp_path / "native"
    native_attempt.mkdir()
    native["path"] = native_attempt
    (native_attempt / "semantic_fingerprints.jsonl").write_text(
        json.dumps({"source_sequence": 0, "boundary": "NODE_CANDIDATE_SET", "count": 1, "ordered_identity_sha256": "a" * 64}) + "\n",
        encoding="utf-8",
    )
    blocks = {"B0-A": native, "MemBind-v3.1": membind}

    def fake_loader(root: Path, name: str, spec: dict[str, str]) -> dict:
        return blocks.get(name) or original_loader(root, name, spec)

    monkeypatch.setattr(first_divergence_module, "_load_block", fake_loader)
    result = analyze_first_divergence(SFWB)
    assert result["sources"]["0"]["first_provable_divergence"]["classification"] == "OBSERVABILITY_INSUFFICIENT"


def test_fingerprint_aware_artifacts_keep_historical_stop_gate(tmp_path: Path) -> None:
    result = analyze_first_divergence(SFWB)
    paths = write_first_semantic_divergence_artifacts(result, tmp_path / "semantic")
    assert {path.name for path in paths} == {
        "SFWB_V13_V5_FIRST_SEMANTIC_DIVERGENCE.json",
        "SFWB_V13_V5_FIRST_SEMANTIC_DIVERGENCE.md",
        "SFWB_V13_V5_MECHANISM_DECISION.md",
    }
    payload = json.loads((tmp_path / "semantic" / "SFWB_V13_V5_FIRST_SEMANTIC_DIVERGENCE.json").read_text())
    assert payload["historical_sealed_decision"] == "STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY"
    assert payload["analysis"]["decision"]["gate"] == "STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY"


def test_fingerprint_aliases_are_compared_by_semantic_identity(monkeypatch, tmp_path: Path) -> None:
    native = {"path": tmp_path / "native", "semantic_fingerprints": [{"source_sequence": 0, "boundary": "NODE_CANDIDATE_SET", "count": 2, "ordered_identity_sha256": "a" * 64, "membership_identity_sha256": "b" * 64}]}
    candidate = {"path": tmp_path / "candidate", "semantic_fingerprints": [{"source_sequence": 0, "boundary": "NODE_CANDIDATE_SET", "output_count": 2, "ordered_semantic_identity_sha256": "a" * 64, "content_identity_sha256": "b" * 64}]}
    assert first_divergence_module._fingerprint_status(
        first_divergence_module._fingerprint_detail(native, 0, "NODE_CANDIDATE_SET"),
        first_divergence_module._fingerprint_detail(candidate, 0, "NODE_CANDIDATE_SET"),
    ) == "EQUAL"
