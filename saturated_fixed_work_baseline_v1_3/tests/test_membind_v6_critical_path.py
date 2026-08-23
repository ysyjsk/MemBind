from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6.critical_path import (
    CriticalPathError,
    reduce_history_artifact,
)


ROOT = Path(__file__).resolve().parents[2]
HISTORY_ROOT = (
    ROOT
    / "saturated_fixed_work_baseline_v1_3"
    / "artifacts"
    / "sfwb-v1-3-v5-queue-20260822-032328"
    / "p9-history-6071bd76-gpu0-20260822"
    / "histories"
    / "6071bd76"
)


def test_reducer_reconstructs_sealed_v5_makespan_and_native_decomposition() -> None:
    result = reduce_history_artifact(HISTORY_ROOT)

    assert result["schema_version"] == "membind.v6.critical-path.v1"
    assert result["history_id"] == "6071bd76"
    assert result["source_count"] == 46
    assert result["durable_frontier"] == 45
    assert result["timer"]["build_makespan_ns"] == 1_522_517_673_483
    assert result["timer"]["reconstructed_from_events"] is True
    assert result["critical_path"]["source0_prepare_to_first_native_ns"] == 206_530_169_066
    assert result["critical_path"]["native_chain_ns"] == 1_315_798_013_061
    assert result["critical_path"]["inter_native_gap_ns"] == 187_354_224
    assert result["critical_path"]["decomposition_residual_ns"] == 0


def test_reducer_reports_phase_attribution_without_summing_overlapping_spans() -> None:
    result = reduce_history_artifact(HISTORY_ROOT)

    phases = result["phase_attribution"]
    assert phases["attributes-summary"]["span_count"] == 46
    assert phases["node-resolution"]["span_count"] == 46
    assert phases["attributes-summary"]["overlap_safe"] is False
    assert phases["attributes-summary"]["total_duration_ns"] > 0
    assert result["phase_attribution_method"] == "span_totals_for_attribution_only"


def test_reducer_rejects_frontier_jump(tmp_path: Path) -> None:
    for name in ("raw_events.jsonl", "native_trace.jsonl"):
        (tmp_path / name).write_text((HISTORY_ROOT / name).read_text(), encoding="utf-8")
    events = [json.loads(line) for line in (tmp_path / "raw_events.jsonl").read_text().splitlines()]
    durable = [row for row in events if row.get("event") == "PUBLICATION_DURABLE"]
    durable[1]["source_sequence"] = 3
    (tmp_path / "raw_events.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in events),
        encoding="utf-8",
    )
    with pytest.raises(CriticalPathError, match="durable frontier"):
        reduce_history_artifact(tmp_path)


def test_reducer_rejects_missing_native_interval(tmp_path: Path) -> None:
    for name in ("raw_events.jsonl", "native_trace.jsonl"):
        (tmp_path / name).write_text((HISTORY_ROOT / name).read_text(), encoding="utf-8")
    events = [json.loads(line) for line in (tmp_path / "raw_events.jsonl").read_text().splitlines()]
    events = [row for row in events if not (row.get("event") == "NATIVE_INTERVAL" and row.get("source_sequence") == 45)]
    (tmp_path / "raw_events.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in events),
        encoding="utf-8",
    )
    with pytest.raises(CriticalPathError, match="native interval coverage"):
        reduce_history_artifact(tmp_path)
