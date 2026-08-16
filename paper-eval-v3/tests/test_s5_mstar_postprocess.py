"""TDD contracts for the independent M* observation/finalization chain."""

from __future__ import annotations

from paper_eval.s5_mstar_postprocess import build_s5_mstar_smoke_summary


def _pipeline_evidence(count: int = 2) -> dict[str, object]:
    events: list[dict[str, object]] = []
    event_sequence = 0
    for source in range(count):
        events.append(
            {
                "event_sequence": event_sequence,
                "event_type": "intent",
                "source_sequence": source,
                "logical_time_ns": 1_735_000_000_000_000_000 + source * 1_000,
                "intent_timestamp_ns": 10 + source,
            }
        )
        event_sequence += 1
    for source in range(count):
        events.extend(
            [
                {
                    "event_sequence": event_sequence,
                    "event_type": "prepare_start",
                    "source_sequence": source,
                    "worker_id": source % 2,
                    "prepare_start_timestamp_ns": 20 + source,
                },
                {
                    "event_sequence": event_sequence + 1,
                    "event_type": "commit_returned",
                    "source_sequence": source,
                    "commit_return_timestamp_ns": 30 + source * 10,
                },
                {
                    "event_sequence": event_sequence + 2,
                    "event_type": "publication",
                    "source_sequence": source,
                    "publication_timestamp_ns": 31 + source * 10,
                },
            ]
        )
        event_sequence += 3
    events.append(
        {"event_sequence": event_sequence, "event_type": "terminal_success"}
    )
    return {"method": "M*", "status": "PASS", "events": events}


def test_smoke_summary_accepts_independent_zero_violation_observation() -> None:
    summary = build_s5_mstar_smoke_summary(
        pipeline_evidence=_pipeline_evidence(),
        expected_source_sequences=[0, 1],
        direct_invariant_violation_count=0,
    )

    assert summary["status"] == "PASS"
    assert summary["direct_invariant_violation_count"] == 0


def test_smoke_summary_preserves_counterexample_without_calling_it_adapter_failure() -> None:
    summary = build_s5_mstar_smoke_summary(
        pipeline_evidence=_pipeline_evidence(),
        expected_source_sequences=[0, 1],
        direct_invariant_violation_count=3,
    )

    assert summary["status"] == "PASS"
    assert summary["direct_invariant_violation_count"] == 3
    assert summary["scientific_outcome_not_adapter_failure"] is False
