from __future__ import annotations

from saturated_fixed_work_baseline_v1_3.membind_v5.qualification.equivalence import (
    ScriptedEpisode,
    run_scripted_serial_equivalence,
)


def test_scripted_native_and_v5_are_canonically_equal_and_conserve_logical_work() -> None:
    result = run_scripted_serial_equivalence(
        [ScriptedEpisode(0, "first"), ScriptedEpisode(1, "second"), ScriptedEpisode(2, "repeat first")]
    )
    assert result.status == "PASS"
    assert result.canonical_equal is True
    assert result.provider_calls_native == 6
    assert result.provider_calls_v5_capture == 6
    assert result.provider_calls_v5_replay == 0
    assert result.logical_captured == result.logical_consumed == 6
    assert result.local_capture_calls == 6

