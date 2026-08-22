from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.campaign import (
    CampaignContractError,
    reduce_extension,
    validate_block_timer_and_traces,
    validate_v5_rows,
    verify_baseline_reference,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.live_block import V5LiveBlock
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import CapacityAuthority


def _v5_row(history: str, episodes: int = 2) -> dict:
    return {
        "history_id": history,
        "method": "V5_VERSIONED_ORACLE_HOIST",
        "canonical_exact_match": True,
        "timer_start_ns": 100,
        "timer_stop_ns": 200,
        "final_publication_ns": 190,
        "semantic_work_after_final_publication": False,
        "trace_envelope_count": episodes,
        "episode_count": episodes,
    }


def test_v5_rows_require_all_histories_and_timer_boundary() -> None:
    rows = [_v5_row(history) for history in ("07741c45", "b6019101", "6071bd76", "a2f3aa27")]
    assert validate_v5_rows(rows)["status"] == "PASS"
    bad = list(rows)
    bad[0] = {**bad[0], "final_publication_ns": 250}
    with pytest.raises(CampaignContractError, match="timer"):
        validate_v5_rows(bad)


def test_trace_timer_gate_requires_one_envelope_per_source_and_both_regions() -> None:
    envelopes = [
        {"source_sequence": 0, "spans": [{"phase": "PREPARE", "start_ns": 11, "end_ns": 12}, {"phase": "NATIVE", "start_ns": 13, "end_ns": 14}]},
        {"source_sequence": 1, "spans": [{"phase": "PREPARE", "start_ns": 15, "end_ns": 16}, {"phase": "NATIVE", "start_ns": 17, "end_ns": 18}]},
    ]
    result = validate_block_timer_and_traces(timer_start_ns=10, timer_stop_ns=30, final_publication_ns=29, source_trace_envelopes=envelopes, episode_count=2)
    assert result["build_makespan_ns"] == 20
    with pytest.raises(CampaignContractError, match="one complete"):
        validate_block_timer_and_traces(timer_start_ns=10, timer_stop_ns=30, final_publication_ns=29, source_trace_envelopes=envelopes[:1], episode_count=2)


def test_partial_baseline_is_rejected_as_reference(tmp_path: Path) -> None:
    root = tmp_path / "partial"
    (root / "qualification").mkdir(parents=True)
    (root / "formal_run_seal.json").write_text("{}")
    (root / "qualification" / "baseline_results.json").write_text(json.dumps({"rows": []}))
    with pytest.raises(CampaignContractError, match="seal|coverage"):
        verify_baseline_reference(root)


def test_baseline_with_failed_qa_contract_is_rejected_as_reference(tmp_path: Path) -> None:
    root = tmp_path / "qa-failed"
    (root / "qualification").mkdir(parents=True)
    (root / "formal_run_seal.json").write_text(
        json.dumps({"status": "FORMAL_RUN_SEALED"})
    )
    rows = [
        {"history_id": history, "method": method}
        for history in ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
        for method in ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")
    ]
    (root / "qualification" / "baseline_results.json").write_text(
        json.dumps(
            {
                "status": "FAIL_QA_CONTRACT",
                "blocks": rows,
                "qa_history_decisions": [
                    {"history_id": "07741c45", "contract_status": "FAIL"}
                ],
            }
        )
    )
    with pytest.raises(CampaignContractError, match="qualification status|QA"):
        verify_baseline_reference(root)


@pytest.mark.asyncio
async def test_live_block_writes_append_only_seal_after_durable_timer_and_trace_gate(tmp_path: Path) -> None:
    block = V5LiveBlock(tmp_path / "attempt", "fresh-ns", 2, CapacityAuthority.from_runtime(2, 2))

    async def prepare(sequence: int) -> dict[str, int]:
        return {"sequence": sequence}

    async def publish(sequence: int, value: dict[str, int]) -> None:
        assert value["sequence"] == sequence

    body = await block.run(prepare, publish)
    assert body["method"] == "V5_VERSIONED_ORACLE_HOIST"
    assert body["timer_start_ns"] <= body["final_publication_ns"] <= body["timer_stop_ns"]
    assert (tmp_path / "attempt" / "seal.json").is_file()
    with pytest.raises(Exception):
        await block.run(prepare, publish)
