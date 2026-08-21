"""Regression checks for the sealed real 0..11 readiness characterization."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256


ROOT = (
    Path(__file__).resolve().parents[1]
    / "artifacts/paper_eval/membind_v4/meg_runtime_readiness"
    / "meg-runtime-readiness-20260821-011"
)


def _load(name: str) -> dict[str, object]:
    value = json.loads((ROOT / name).read_text(encoding="utf-8"))
    declared = value.pop("payload_sha256")
    assert declared == payload_sha256(value)
    value["payload_sha256"] = declared
    return value


def test_real_0_11_readiness_is_case_b_and_hash_sealed() -> None:
    capture = _load("MEG_REAL_0_11_READINESS_CAPTURE.json")
    prepared = _load("MEG_PREPARED_BARRIER_ANALYSIS.json")
    frontier = _load("MEG_STATE_FRONTIER_WINDOW_ANALYSIS.json")
    decision = _load("MEG_READINESS_DECISION.json")

    assert capture["status"] == "PASS_REAL_MEG_READINESS_CAPTURE"
    assert capture["runtime_validity"]["request_lineage_coverage"] == 1.0
    assert capture["runtime_validity"]["sources_completed"] is True
    assert prepared["total_semantic_operators"] == 390
    assert prepared["operators_ready_before_prepared"] == 24
    assert prepared["by_classification"]["EVIDENCE_DERIVED"]["ready_before_prepared_count"] == 24
    assert prepared["by_classification"]["STATE_DERIVED"]["ready_before_prepared_count"] == 0
    assert frontier["state_derived_total"] == 231
    assert frontier["ready_before_predecessor_count"] == 0
    assert decision["decision"] == "STOP_VALIDATED_SEMANTIC_CONTINUATION_NO_CROSS_VERSION_WINDOW"
    assert decision["next_action"] == "GO_ANALYZE_WITHIN_VERSION_MEG_OPPORTUNITY"


def test_real_0_11_readview_sanity_does_not_claim_stale_validation() -> None:
    capture = _load("MEG_REAL_0_11_READINESS_CAPTURE.json")
    sanity = capture["readview_sanity"]
    assert sanity["count"] == 231
    assert sanity["stable"] == 231
    assert sanity["unstable"] == 0
    assert sanity["opaque"] == 0
    assert "no stale-state" in sanity["interpretation"]
    assert "no stale-state HIT/MISS" in sanity["interpretation"]
