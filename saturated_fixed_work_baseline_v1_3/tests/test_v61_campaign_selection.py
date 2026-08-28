from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "saturated_fixed_work_baseline_v1_3/scripts"


def _load(name: str):
    return runpy.run_path(str(SCRIPTS / name), run_name=f"test_{name}")


def test_timing_invalidated_completion_is_rejected_by_all_baseline_loaders(
    tmp_path: Path,
) -> None:
    attempt = tmp_path / "context-0/B0/attempt-id"
    block = attempt / "block"
    block.mkdir(parents=True)
    (attempt / "timing_invalidation.json").write_text("{}\n", encoding="utf-8")
    row = {
        "event": "ATTEMPT_COMPLETE",
        "status": "PASS",
        "episode_count": 30,
        "method": "B0",
        "run_id": "contaminated",
        "attempt_id": "attempt-id",
        "construction_seal": str(block / "construction_seal.json"),
    }
    ledger = tmp_path / "campaign_ledger.jsonl"
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")

    pipeline = _load("run_v61_pipeline_local.py")
    autoresearch = _load("run_v61_autoresearch_local.py")
    summarizer = _load("summarize_v61_local_campaign.py")

    assert pipeline["_baseline_methods"](ledger) == set()
    with pytest.raises(RuntimeError, match="incomplete"):
        autoresearch["_load_baselines"](tmp_path)
    assert summarizer["_baseline_rows"](tmp_path) == {}


def test_promotion_requires_full_scale_and_improvement_over_both_baselines() -> None:
    autoresearch = _load("run_v61_autoresearch_local.py")
    baselines = {"B0": {"makespan_s": 100.0}, "V6_0": {"makespan_s": 80.0}}
    fast = {
        "status": "PASS",
        "correctness": "PASS",
        "scale": 30,
        "makespan_s": 70.0,
        "expected_episode_count": 30,
        "durable_frontier": 29,
    }
    assert autoresearch["_improvement_gate"](fast, baselines, final_scale=30)[0]
    assert not autoresearch["_improvement_gate"](
        {**fast, "scale": 16, "expected_episode_count": 16, "durable_frontier": 15},
        baselines,
        final_scale=30,
    )[0]
    assert not autoresearch["_improvement_gate"](
        {**fast, "makespan_s": 90.0}, baselines, final_scale=30
    )[0]


def test_failure_directed_mutation_is_bounded_and_deduplicated() -> None:
    autoresearch = _load("run_v61_autoresearch_local.py")
    policy = {"lookahead": 2, "future_cap": 2, "native_future_quota": 1}
    timeout_ids = [
        autoresearch["_policy_id"](candidate)
        for candidate in autoresearch["_mutate_policy"](policy, "PROVIDER_TIMEOUT")
    ]
    correctness_ids = [
        autoresearch["_policy_id"](candidate)
        for candidate in autoresearch["_mutate_policy"](policy, "CORRECTNESS_EVIDENCE")
    ]
    assert len(timeout_ids) == len(set(timeout_ids))
    assert len(correctness_ids) == len(set(correctness_ids))
    assert all("f3" not in policy_id for policy_id in timeout_ids)
    assert all("q0" in policy_id or "f1" in policy_id for policy_id in correctness_ids)


def test_live_observer_reports_frontier_and_no_progress(tmp_path: Path) -> None:
    import subprocess
    import time

    autoresearch = _load("run_v61_autoresearch_local.py")
    attempt = tmp_path / "runs/context-0/V6_1/a1"
    attempt.mkdir(parents=True)
    (attempt / "attempt.json").write_text(json.dumps({"run_id": "r1"}), encoding="utf-8")
    (attempt / ".block.live_raw_events.jsonl").write_text(
        json.dumps({"event": "PUBLICATION_DURABLE", "source_sequence": 3}) + "\n",
        encoding="utf-8",
    )
    log = tmp_path / "logs/r1.log"
    log.parent.mkdir()
    log.write_text("progress", encoding="utf-8")
    child = subprocess.Popen(["sleep", "1"])
    first = autoresearch["_observe_candidate"](
        root=tmp_path,
        context_index=0,
        run_id="r1",
        process=child,
        started_at=time.time(),
    )
    time.sleep(0.02)
    second = autoresearch["_observe_candidate"](
        root=tmp_path,
        context_index=0,
        run_id="r1",
        process=child,
        started_at=time.time(),
        previous=first,
    )
    child.wait()
    assert second["durable_frontier"] == 3
    assert second["log_bytes"] == len("progress")
    assert second["no_progress_s"] > 0


def test_campaign_checkpoint_prevents_run_id_reuse(tmp_path: Path) -> None:
    autoresearch = _load("run_v61_autoresearch_local.py")
    ledger = tmp_path / "autoresearch.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "event": "CANDIDATE_START",
                    "candidate_index": 4,
                    "scale": 8,
                    "policy_id": "w1-f1-q0",
                },
                {
                    "event": "CANDIDATE_FINISH",
                    "scale": 8,
                    "policy_id": "w2-f1-q0",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    tried, next_index = autoresearch["_load_campaign_checkpoint"](ledger)
    assert tried == {(8, "w1-f1-q0"), (8, "w2-f1-q0")}
    assert next_index == 5


def test_three_v60_timeouts_form_a_right_censored_baseline(tmp_path: Path) -> None:
    autoresearch = _load("run_v61_autoresearch_local.py")
    pipeline = _load("run_v61_pipeline_local.py")
    b0 = tmp_path / "context-0/B0/a/block"
    b0.mkdir(parents=True)
    (b0 / "metrics.json").write_text(json.dumps({"t_build_ns": 100_000_000_000}), encoding="utf-8")
    (b0 / "work_inventory.json").write_text("{}\n", encoding="utf-8")
    rows = [
        {
            "event": "ATTEMPT_COMPLETE",
            "status": "PASS",
            "episode_count": 30,
            "method": "B0",
            "attempt_id": "b0",
            "construction_seal": str(b0 / "construction_seal.json"),
        }
    ]
    for index, elapsed in enumerate((120.0, 110.0, 130.0)):
        rows.append(
            {
                "event": "ATTEMPT_FAILURE",
                "status": "FAILED",
                "episode_count": 30,
                "method": "V6_0",
                "attempt_id": f"v{index}",
                "error_type": "openai.APITimeoutError",
                "started_at_unix": 1000.0,
                "ended_at_unix": 1000.0 + elapsed,
            }
        )
    ledger = tmp_path / "campaign_ledger.jsonl"
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert pipeline["_baseline_methods"](ledger) == {"B0", "V6_0"}
    baselines = autoresearch["_load_baselines"](tmp_path)
    assert baselines["V6_0"]["status"] == "RIGHT_CENSORED_TIMEOUT"
    assert baselines["V6_0"]["makespan_s"] == 110.0


def test_scale_promotion_keeps_best_and_runner_up() -> None:
    autoresearch = _load("run_v61_autoresearch_local.py")
    ranked = [
        {"policy": {"lookahead": 2, "future_cap": 1, "native_future_quota": 0}},
        {"policy": {"lookahead": 1, "future_cap": 1, "native_future_quota": 0}},
        {"policy": {"lookahead": 1, "future_cap": 2, "native_future_quota": 0}},
    ]
    promoted = autoresearch["_promote_candidates"](ranked, set(), 16, 4)
    promoted_ids = [autoresearch["_policy_id"](row) for row in promoted]
    assert promoted_ids[:2] == ["w2-f1-q0", "w1-f1-q0"]
    assert len(promoted_ids) == len(set(promoted_ids)) == 4
