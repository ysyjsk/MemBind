from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.dmsv_phase2b import (
    build_dominant_request_delta_matrix,
)


ROOT = Path(__file__).resolve().parents[1]
PHASE2B = ROOT / "v7" / "dmsv_phase2b"
PREREG = PHASE2B / "DMSV_B1_CLOSURE_REPAIR_PREREGISTRATION.json"
OBSERVER = Path(
    "/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/"
    "v7_dvsr_phase3/phase3-b6019101-6s-20260831T054247Z-23c37c/"
    "DVSR_CROSS_SNAPSHOT_OBSERVER.jsonl"
)


def _rows() -> list[dict]:
    if not OBSERVER.exists():
        pytest.skip("development observer artifact is not present")
    return [json.loads(line) for line in OBSERVER.read_text().splitlines() if line.strip()]


def _request(row: dict, phase: str) -> dict | None:
    capture = row[f"{phase.lower()}_capture"]
    requests = capture.get("requests", [])
    return next((item for item in requests if item.get("prompt_name") == "dedupe_nodes.nodes"), None)


def test_preregistration_is_frozen_before_r3_and_fail_closed() -> None:
    prereg = json.loads(PREREG.read_text())
    assert prereg["status"] == "FROZEN_BEFORE_R3"
    assert prereg["graphiti_version"] == "0.29.3"
    assert prereg["fixed_terminal_constraints"] == {
        "MAIN_TRACK_CANDIDATE": False,
        "B2_AUTHORIZED": False,
        "B3_AUTHORIZED": False,
        "PHASE3A_AUTHORIZED": False,
        "LIVE_AUTHORIZED": False,
    }
    required = set(prereg["r3_scope"]["required_real_pair_fields"])
    assert {"request_binding_digest_before", "decoding_contract"} <= required


def test_real_adjacent_pair_is_not_promoted_when_binding_fields_are_missing() -> None:
    pair = next((row for row in _rows() if row.get("source_sequence") == 4), None)
    assert pair is not None
    old_previous = pair["old_capture"]["previous_episode"]["order"]
    fresh_previous = pair["fresh_capture"]["previous_episode"]["order"]
    old_request = _request(pair, "OLD")
    fresh_request = _request(pair, "FRESH")
    assert old_previous != fresh_previous
    assert old_request and fresh_request
    assert old_request["request_identity"] != fresh_request["request_identity"]

    # The observer intentionally records only digest-level binding evidence.
    # Missing fields prohibit an inevitability/unavoidability claim.
    required = {
        "reference_time",
        "source_or_group",
        "last_n",
        "request_binding_digest",
        "schema_epoch",
        "index_epoch",
        "decoding_contract",
    }
    available = set(old_request) | set(fresh_request)
    available |= set(pair["fresh_capture"]["previous_episode"])
    assert required - available


def test_graphiti_retrieval_chain_exposes_ordered_previous_episode_contract() -> None:
    from graphiti_core.graphiti import retrieve_episodes

    source = inspect.getsource(retrieve_episodes)
    assert "ORDER BY e.valid_at DESC" in source
    assert "LIMIT $num_episodes" in source
    assert "num_episodes=last_n" in source
    assert "e.valid_at <= $reference_time" in source


def test_synthetic_matrix_is_sensitivity_only_without_real_pair() -> None:
    base = {
        "extracted_nodes": [{"id": 0, "name": "Alice"}],
        "existing_nodes": [{"candidate_id": 0, "name": "Alice", "summary": "old"}],
        "episode_content": "Alice met Bob.",
        "previous_episodes": [{"content": "prior", "timestamp": "2023-01-01"}],
    }
    rows = build_dominant_request_delta_matrix(base)
    # The prompt template may canonicalize one mutation (for example, a
    # candidate reorder); that is still only a sensitivity probe, not a real
    # adjacent-state proof. At least one prompt-visible mutation must change.
    assert rows and any(row["request_equal"] is False for row in rows)
    assert not any(row.get("real_adjacent_state") for row in rows)


def test_causal_witness_artifact_is_explicitly_blocked_if_missing_fields() -> None:
    witness_path = PHASE2B / "DMSV_DOMINANT_REQUEST_CAUSAL_WITNESSES.jsonl"
    if not witness_path.exists():
        pytest.skip("causal witness artifact is generated after R3")
    rows = [json.loads(line) for line in witness_path.read_text().splitlines() if line.strip()]
    assert rows
    for row in rows:
        assert row["claim_level"] in {"SENSITIVITY", "INEVITABILITY", "UNAVOIDABILITY"}
        if row["status"] == "REAL_PAIR_WITNESS_MISSING_FIELD":
            assert row["final_state"] == "BLOCKED_DOMINANT_REQUEST_INEVITABILITY_UNPROVEN"
