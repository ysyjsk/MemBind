from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6.main_comparison import V6ComparisonError, reduce_main_campaign


def _attempt(root: Path, *, policy: str, timer_ns: int, captured: int = 0, consumed: int = 0, future_max: int = 7) -> None:
    (root / "histories/6071bd76").mkdir(parents=True)
    result = {
        "status": "PASS", "history_id": "6071bd76", "source_count": 46,
        "durable_frontier": 45, "policy": policy, "method": "V6_TEST",
        "claim_status": "QUALIFICATION_ONLY", "provider_call_count": 10,
        "request_observation_count": 10, "lifecycle": {"build_makespan_ns": timer_ns},
        "logical_work_summary": {"logical_captured": captured, "logical_consumed": consumed, "duplicates": 0, "unconsumed": captured - consumed},
        "transport_evidence": {"attempt_count": 10, "usage_observed_count": 10, "finish_reason_observed_count": 10, "finish_reasons": ["stop"], "transport_error_count": 0},
        "overlap_evidence": {"future_prepare_overlapped_native": True, "overlap_pairs": [{"native_source_sequence": 0, "prepare_source_sequence": 1}]},
    }
    proof = {"frontier": {"status": "PASS", "durable_frontier": 45}, "provider": {"status": "PASS", "capacity": 8, "max_outstanding": 8, "max_future_outstanding": future_max}, "replay": {"status": "PASS" if policy == "v6" else "NOT_APPLICABLE"}, "request": {"status": "PASS", "match_count": captured, "miss_count": 0}}
    manifest = {"endpoint_identity": {"construction": "http://10.87.5.247:8000/v1/", "embedding": "http://10.87.5.247:8001/v1"}}
    seal = {"status": "V6_PROBE_SEALED"}
    for rel, value in [("histories/6071bd76/history_result.json", result), ("proof.json", proof), ("manifest.json", manifest), ("seal.json", seal)]:
        (root / rel).write_text(json.dumps(value), encoding="utf-8")


def test_reduce_main_campaign_requires_exact_candidate_replay(tmp_path: Path) -> None:
    c1, v1, v2, c2 = [tmp_path / name for name in ("c1", "v1", "v2", "c2")]
    _attempt(c1, policy="matched-control", timer_ns=200)
    _attempt(v1, policy="v6", timer_ns=100, captured=2, consumed=2)
    _attempt(v2, policy="v6", timer_ns=120, captured=2, consumed=2)
    _attempt(c2, policy="matched-control", timer_ns=220)
    out = reduce_main_campaign(control_first_control=c1, control_first_candidate=v1, candidate_first_candidate=v2, candidate_first_control=c2)
    assert out["status"] == "PASS"
    assert out["claim_status"] == "QUALIFICATION_ONLY"
    assert out["mechanism_evidence"]["candidate_replay_exact_single_consume"] is True
    assert out["paired_timer_summary"]["deltas_s_control_minus_candidate"] == pytest.approx([1e-7, 1e-7])


def test_reduce_main_campaign_rejects_incomplete_frontier(tmp_path: Path) -> None:
    c1, v1, v2, c2 = [tmp_path / name for name in ("c1", "v1", "v2", "c2")]
    _attempt(c1, policy="matched-control", timer_ns=200)
    _attempt(v1, policy="v6", timer_ns=100, captured=1, consumed=0)
    _attempt(v2, policy="v6", timer_ns=100, captured=1, consumed=1)
    _attempt(c2, policy="matched-control", timer_ns=200)
    with pytest.raises(V6ComparisonError):
        reduce_main_campaign(control_first_control=c1, control_first_candidate=v1, candidate_first_candidate=v2, candidate_first_control=c2)


def test_reduce_main_campaign_rejects_future_cap_violation(tmp_path: Path) -> None:
    c1, v1, v2, c2 = [tmp_path / name for name in ("c1", "v1", "v2", "c2")]
    _attempt(c1, policy="matched-control", timer_ns=200)
    _attempt(v1, policy="v6", timer_ns=100, captured=1, consumed=1, future_max=8)
    _attempt(v2, policy="v6", timer_ns=100, captured=1, consumed=1)
    _attempt(c2, policy="matched-control", timer_ns=200)
    with pytest.raises(V6ComparisonError, match="provider proof"):
        reduce_main_campaign(control_first_control=c1, control_first_candidate=v1, candidate_first_candidate=v2, candidate_first_control=c2)
