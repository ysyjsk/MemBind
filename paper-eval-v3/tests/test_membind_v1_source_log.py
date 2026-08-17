"""TDD contract tests for the isolated MemBind-v1 source inventory."""

from __future__ import annotations

import pytest

from paper_eval.membind_v1.source_log import (
    MemBindV1SourceLogError,
    SourceLog,
    SourceRecord,
)


def _record(sequence: int, *, group_id: str = "group-a") -> SourceRecord:
    return SourceRecord.create(
        source_sequence=sequence,
        episode_uuid=f"episode-{sequence}",
        group_id=group_id,
        reference_time_ns=1_000 + sequence,
        source_filter="message",
        episode_projection={"body": f"episode {sequence}", "name": f"E{sequence}"},
    )


def test_source_log_binds_an_exact_contiguous_immutable_inventory() -> None:
    records = [_record(0), _record(1), _record(2)]
    source_log = SourceLog.create(records)

    assert source_log.source_count == 3
    assert source_log.source_sequences == (0, 1, 2)
    assert source_log.record(1).source_sha256 == records[1].source_sha256
    assert source_log.inventory_sha256 == SourceLog.create(records).inventory_sha256

    projection = source_log.record(0).episode_projection
    projection["body"] = "caller mutation"
    assert source_log.record(0).episode_projection["body"] == "episode 0"


@pytest.mark.parametrize(
    "records, code",
    [
        ([_record(0), _record(2)], "source_sequence_not_contiguous"),
        ([_record(0), _record(0)], "source_sequence_not_contiguous"),
    ],
)
def test_source_log_rejects_noncontiguous_or_duplicate_inventory(
    records: list[SourceRecord], code: str
) -> None:
    with pytest.raises(MemBindV1SourceLogError, match=code):
        SourceLog.create(records)


def test_source_record_hash_binds_the_immutable_source_projection() -> None:
    original = _record(0)
    changed = SourceRecord.create(
        source_sequence=0,
        episode_uuid="episode-0",
        group_id="group-a",
        reference_time_ns=1_000,
        source_filter="message",
        episode_projection={"body": "changed", "name": "E0"},
    )

    assert original.source_sha256 != changed.source_sha256
    with pytest.raises(MemBindV1SourceLogError, match="source_hash_mismatch"):
        SourceRecord.create(
            source_sequence=0,
            episode_uuid="episode-0",
            group_id="group-a",
            reference_time_ns=1_000,
            source_filter="message",
            episode_projection={"body": "episode 0", "name": "E0"},
            source_sha256=changed.source_sha256,
        )


def test_source_log_rejects_a_manually_constructed_record_with_bad_hash() -> None:
    forged = SourceRecord(
        source_sequence=0,
        episode_uuid="episode-0",
        group_id="group-a",
        reference_time_ns=1_000,
        source_filter="message",
        _episode_projection_json='{"body":"episode 0","name":"E0"}',
        source_sha256="0" * 64,
    )

    with pytest.raises(MemBindV1SourceLogError, match="source_hash_mismatch"):
        SourceLog.create([forged])
