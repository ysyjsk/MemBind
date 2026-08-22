from __future__ import annotations

import pytest

pytest.importorskip("graphiti_core")

from saturated_fixed_work_baseline_v1_3.membind_v5.qualification.graphiti_native import (
    NativeGraphitiEpisode,
    run_real_graphiti_serial_equivalence,
)


def test_real_pinned_graphiti_add_episode_path_is_serial_equivalent() -> None:
    result = run_real_graphiti_serial_equivalence(
        [NativeGraphitiEpisode(0, "Alice knows Bob"), NativeGraphitiEpisode(1, "Alice knows Bob")]
    )
    assert result["status"] == "PASS"
    assert result["native_graph"] == result["v5_graph"]
    assert result["provider_calls_native"] == 4
    assert result["provider_calls_v5_capture"] == 4
    assert result["provider_calls_v5_replay"] == 0
    assert result["logical_work"]["logical_captured"] == 4
    assert result["logical_work"]["logical_consumed"] == 4
    assert all(row.get("admitted") is True for row in result["admission"] if row["mode"] == "capture")
    assert all(row.get("admitted") is False for row in result["admission"] if row["mode"] == "replay")
    capture_classes = {row.get("admission_class") for row in result["admission"] if row["mode"] == "capture"}
    assert capture_classes == {"FRONTIER_PREPARE", "FUTURE_PREPARE"}
