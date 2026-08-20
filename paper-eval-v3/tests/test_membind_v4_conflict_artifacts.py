"""Offline artifact contracts for the terminal conflict-aware v4 decision."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.membind_v4.conflict_replay import build_conflict_offline_replay


PROJECT = Path(__file__).resolve().parents[1]
ARTIFACTS = PROJECT / "artifacts/paper_eval"
V4_ROOT = ARTIFACTS / "membind_v4"
V31_BLOCK = (
    ARTIFACTS
    / "membind_v31/feasibility/membind-v31-feasibility-20260819-004/block-00"
)
REPLAY_PATH = V4_ROOT / "V4_CONFLICT_OFFLINE_REPLAY.json"


def _rebuild() -> dict[str, object]:
    return build_conflict_offline_replay(
        audit_path=(
            V4_ROOT
            / "protocol_amendment_a1/V4_OPPORTUNITY_AUDIT_A1.json"
        ),
        block_manifest_path=V31_BLOCK / "manifest.json",
        events_path=V31_BLOCK / "events.jsonl",
        prepared_dir=V31_BLOCK / "private/prepared",
        baseline_binding_path=V4_ROOT / "BASELINE_BINDING.json",
        old_c01_candidate_dir=(
            V4_ROOT
            / "autoresearch/membind-v4-ar-20260819-c01-6-live/candidates/c01"
        ),
        source_count=12,
    )


def test_sealed_replay_is_exactly_reproducible_from_frozen_evidence() -> None:
    persisted = json.loads(REPLAY_PATH.read_text(encoding="utf-8"))

    assert persisted == _rebuild()
    assert persisted["payload_sha256"] == payload_sha256(
        {
            key: value
            for key, value in persisted.items()
            if key != "payload_sha256"
        }
    )
    assert persisted["gate"] == {
        "decision": "STOP_CONFLICT_AWARE_NODE_RESOLVE",
        "final_outcome": "STOP_V4_NODE_RESOLVE",
        "live_authorized": False,
        "reason": "low_conflict_opportunities_zero",
    }


def test_decision_documents_bind_replay_and_leave_live_artifacts_absent() -> None:
    offline = (V4_ROOT / "V4_CONFLICT_OFFLINE_DECISION.md").read_text(
        encoding="utf-8"
    )
    final = (V4_ROOT / "V4_FINAL_DECISION.md").read_text(encoding="utf-8")

    assert sha256_file(REPLAY_PATH) in offline
    assert "STOP_CONFLICT_AWARE_NODE_RESOLVE" in offline
    assert "LOW_CONFLICT opportunities == 0" in offline
    assert "STOP_V4_NODE_RESOLVE" in final
    assert "FREEZE_CONFLICT_AWARE_V4` is not authorized" in final
    assert not (V4_ROOT / "V4_C01_CA_RESULT.json").exists()
    assert not (V4_ROOT / "V4_C01_CA_REDUCED.json").exists()

