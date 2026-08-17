"""TDD contract tests for MemBind-v1 immutable EvidenceFence selection."""

from __future__ import annotations

import pytest

from paper_eval.membind_v1.evidence_fence import (
    EvidenceFence,
    MemBindV1EvidenceFenceError,
    build_compile_input,
)
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord


def _record(
    sequence: int,
    *,
    timestamp: int,
    group_id: str = "group-a",
    source_filter: str = "message",
) -> SourceRecord:
    return SourceRecord.create(
        source_sequence=sequence,
        episode_uuid=f"episode-{sequence}",
        group_id=group_id,
        reference_time_ns=timestamp,
        source_filter=source_filter,
        episode_projection={"body": f"body {sequence}"},
    )


def test_evidence_fence_matches_group_filter_time_last_n_and_chronological_order() -> None:
    source_log = SourceLog.create(
        [
            _record(0, timestamp=10),
            _record(1, timestamp=11, group_id="other"),
            _record(2, timestamp=12),
            _record(3, timestamp=13, source_filter="tool"),
            _record(4, timestamp=14),
            _record(5, timestamp=15),
        ]
    )

    fence = EvidenceFence.capture(source_log, target_source_sequence=5, last_n=2)

    assert fence.evidence_source_sequences == (2, 4)
    assert fence.selection_mode == "native_equivalent"
    assert fence.reference_time_ns == 15
    assert fence.evidence_prefix_sha256


def test_evidence_fence_never_includes_current_or_future_sources() -> None:
    source_log = SourceLog.create(
        [_record(0, timestamp=10), _record(1, timestamp=100), _record(2, timestamp=9)]
    )

    fence = EvidenceFence.capture(source_log, target_source_sequence=1, last_n=5)

    assert fence.evidence_source_sequences == (0,)


def test_equal_timestamp_at_last_n_cutoff_fails_closed_without_explicit_capture() -> None:
    source_log = SourceLog.create(
        [
            _record(0, timestamp=10),
            _record(1, timestamp=20),
            _record(2, timestamp=20),
            _record(3, timestamp=30),
        ]
    )

    with pytest.raises(MemBindV1EvidenceFenceError, match="equal_timestamp_cutoff"):
        EvidenceFence.capture(source_log, target_source_sequence=3, last_n=1)

    captured = EvidenceFence.capture(
        source_log,
        target_source_sequence=3,
        last_n=1,
        explicit_capture_source_sequences=(2,),
    )
    assert captured.evidence_source_sequences == (2,)
    assert captured.selection_mode == "explicit_capture"


def test_compile_input_contains_only_immutable_source_and_fence_data() -> None:
    source_log = SourceLog.create([_record(0, timestamp=10), _record(1, timestamp=20)])
    fence = EvidenceFence.capture(source_log, target_source_sequence=1, last_n=5)

    compile_input = build_compile_input(source_log.record(1), fence)

    assert compile_input.source.source_sequence == 1
    assert compile_input.evidence.evidence_source_sequences == (0,)
    assert not hasattr(compile_input, "driver")
    assert not hasattr(compile_input, "retrieve_episodes")
    with pytest.raises((AttributeError, TypeError)):
        compile_input.driver = object()  # type: ignore[attr-defined]
