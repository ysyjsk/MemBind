"""TDD contracts for v3.1 arrival eligibility and the bounded Prepared ROB."""

from __future__ import annotations

import pytest

from paper_eval.membind_v31.scheduler import (
    ArrivalGate,
    MemBindV31SchedulerError,
    PreparedROB,
    SourceEnvelope,
)


def test_unarrived_source_payload_cannot_be_observed_or_claimed() -> None:
    gate = ArrivalGate(
        [
            SourceEnvelope(
                stream_id="history-a",
                source_sequence=0,
                arrival_time=10.0,
                payload={"private_episode": "future evidence"},
            )
        ]
    )

    assert gate.release_eligible(now=9.999) == ()
    assert gate.observation(now=9.999) == {
        "eligible_count": 0,
        "pending_count": 1,
        "released_count": 0,
    }
    assert "future evidence" not in repr(gate.observation(now=9.999))
    with pytest.raises(MemBindV31SchedulerError, match="source_not_arrived"):
        gate.claim(stream_id="history-a", source_sequence=0, now=9.999)

    released = gate.release_eligible(now=10.0)
    assert len(released) == 1
    assert released[0].payload == {"private_episode": "future evidence"}
    assert gate.claim(stream_id="history-a", source_sequence=0, now=10.0) is released[0]


def test_arrival_release_order_is_deterministic_without_leaking_payloads() -> None:
    gate = ArrivalGate(
        [
            SourceEnvelope("stream-b", 0, 2.0, "payload-b"),
            SourceEnvelope("stream-a", 1, 2.0, "payload-a1"),
            SourceEnvelope("stream-a", 0, 1.0, "payload-a0"),
        ]
    )

    assert [
        (item.stream_id, item.source_sequence)
        for item in gate.release_eligible(now=2.0)
    ] == [("stream-a", 0), ("stream-a", 1), ("stream-b", 0)]
    public = gate.public_events
    assert [event["event_type"] for event in public] == ["source_arrived"] * 3
    assert all("payload" not in event for event in public)
    assert "payload-a0" not in repr(public)


def test_rob_enforces_compile_worker_and_per_stream_lookahead_bounds() -> None:
    rob = PreparedROB(compile_workers=2, lookahead=1)
    for sequence in range(3):
        rob.record_arrival("stream-a", sequence)
    rob.record_arrival("stream-b", 0)

    rob.start_compile("stream-a", 1)
    rob.start_compile("stream-b", 0)

    with pytest.raises(MemBindV31SchedulerError, match="compile_worker_limit"):
        rob.start_compile("stream-a", 0)
    with pytest.raises(MemBindV31SchedulerError, match="outside_lookahead"):
        rob.start_compile("stream-a", 2)

    rob.complete_compile("stream-b", 0, artifact={"prepared": "b0"})
    rob.start_compile("stream-a", 0)
    rob.complete_compile("stream-a", 1, artifact={"prepared": "a1"})
    rob.complete_compile("stream-a", 0, artifact={"prepared": "a0"})

    assert rob.observation()["observed_max_active_compiles"] == 2
    assert rob.observation()["prepared_count"] == 3
    assert "a0" not in repr(rob.observation())
    assert "b0" not in repr(rob.observation())


def test_only_frontier_can_bind_and_only_one_bind_can_be_active_globally() -> None:
    rob = PreparedROB(compile_workers=2, lookahead=2)
    for stream in ("stream-a", "stream-b"):
        for sequence in range(2):
            rob.record_arrival(stream, sequence)
            rob.start_compile(stream, sequence)
            rob.complete_compile(stream, sequence, artifact=f"{stream}:{sequence}")

    with pytest.raises(MemBindV31SchedulerError, match="bind_not_at_frontier"):
        rob.start_bind("stream-a", 1)

    assert rob.start_bind("stream-b", 0) == "stream-b:0"
    with pytest.raises(MemBindV31SchedulerError, match="bind_worker_busy"):
        rob.start_bind("stream-a", 0)

    rob.publish("stream-b", 0)
    assert rob.frontier("stream-b") == 1
    assert rob.start_bind("stream-a", 0) == "stream-a:0"
    rob.publish("stream-a", 0)
    assert rob.start_bind("stream-a", 1) == "stream-a:1"


def test_compile_failure_and_bind_cancellation_are_fail_closed_and_content_safe() -> None:
    rob = PreparedROB(compile_workers=1, lookahead=1)
    rob.record_arrival("stream-a", 0)
    rob.start_compile("stream-a", 0)
    rob.fail_compile("stream-a", 0, RuntimeError("secret source fragment"))

    with pytest.raises(MemBindV31SchedulerError, match="stream_failed"):
        rob.start_compile("stream-a", 0)
    assert "secret source fragment" not in repr(rob.public_events)
    assert rob.public_events[-1]["error_class"] == "builtins.RuntimeError"

    other = PreparedROB(compile_workers=1, lookahead=1)
    other.record_arrival("stream-b", 0)
    other.start_compile("stream-b", 0)
    other.complete_compile("stream-b", 0, artifact="private prepared artifact")
    other.start_bind("stream-b", 0)
    other.cancel_bind("stream-b", 0)

    assert other.observation()["terminal_streams"] == ["stream-b"]
    assert "private prepared artifact" not in repr(other.observation())
    with pytest.raises(MemBindV31SchedulerError, match="stream_failed"):
        other.publish("stream-b", 0)


def test_stream_failure_releases_other_same_stream_workers_deterministically() -> None:
    rob = PreparedROB(compile_workers=3, lookahead=2)
    for sequence in range(3):
        rob.record_arrival("stream-a", sequence)
        rob.start_compile("stream-a", sequence)

    rob.fail_compile("stream-a", 1, RuntimeError("private evidence"))

    snapshot = rob.observation()
    assert snapshot["active_compile_count"] == 0
    assert snapshot["state_counts"] == {"CANCELLED": 2, "FAILED": 1}
    assert snapshot["terminal_streams"] == ["stream-a"]
    assert [event["event_type"] for event in rob.public_events[-3:]] == [
        "compile_cancelled_after_stream_failure",
        "compile_cancelled_after_stream_failure",
        "compile_failed",
    ]
    assert "private evidence" not in repr(rob.public_events)
