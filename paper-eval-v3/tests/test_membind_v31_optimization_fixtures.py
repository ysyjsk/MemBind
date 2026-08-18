"""Offline evidence fixtures for the isolated v3.1 optimization lane.

These tests do not authorize a new live configuration.  They make the
lookahead and admission observations reproducible before any W/cohort pilot.
"""

from __future__ import annotations

import pytest

from paper_eval.membind_v31.admission import (
    AdmissionPolicy,
    RequestAdmissionController,
    RequestKind,
    RequestSpec,
)
from paper_eval.membind_v31.scheduler import MemBindV31SchedulerError, PreparedROB


def _compile(request_id: str, source_sequence: int, *, cohort_gain: int = 0) -> RequestSpec:
    return RequestSpec(
        request_id=request_id,
        kind=RequestKind.COMPILE,
        stream_id="history-a",
        source_sequence=source_sequence,
        cohort_gain=cohort_gain,
        affinity_signature=f"cohort-{cohort_gain}",
    )


def _arrived_rob(*, lookahead: int) -> PreparedROB:
    rob = PreparedROB(compile_workers=4, lookahead=lookahead)
    for source_sequence in range(5):
        rob.record_arrival("history-a", source_sequence)
    return rob


def test_w2_can_hide_an_arrived_future_source_while_w4_exposes_it() -> None:
    w2 = _arrived_rob(lookahead=2)
    for source_sequence in (0, 1, 2):
        w2.start_compile("history-a", source_sequence)
        w2.complete_compile(
            "history-a",
            source_sequence,
            artifact={"source_sequence": source_sequence, "artifact_id": f"a-{source_sequence}"},
        )
    with pytest.raises(MemBindV31SchedulerError, match="outside_lookahead"):
        w2.start_compile("history-a", 3)

    w4 = _arrived_rob(lookahead=4)
    for source_sequence in (0, 1, 2, 3):
        w4.start_compile("history-a", source_sequence)
        w4.complete_compile(
            "history-a",
            source_sequence,
            artifact={"source_sequence": source_sequence, "artifact_id": f"a-{source_sequence}"},
        )

    assert w2.observation()["prepared_count"] == 3
    assert w4.observation()["prepared_count"] == 4
    assert w4.start_bind("history-a", 0) == {"source_sequence": 0, "artifact_id": "a-0"}
    w4.publish("history-a", 0)
    assert w4.start_bind("history-a", 1) == {"source_sequence": 1, "artifact_id": "a-1"}


def test_admission_is_work_conserving_when_a_second_compile_is_waiting() -> None:
    admission = RequestAdmissionController(limit=2, policy=AdmissionPolicy.FIFO)
    admission.submit(_compile("compile-0", 0))
    admission.submit(_compile("compile-1", 1))
    assert [item.request_id for item in admission.admit_available()] == [
        "compile-0",
        "compile-1",
    ]

    admission.finish("compile-0")
    admission.submit(_compile("compile-2", 2))
    assert [item.request_id for item in admission.admit_available()] == ["compile-2"]
    assert admission.observation()["observed_max_inflight"] == 2


def test_cohort_ordering_changes_only_admission_order_and_not_request_identity() -> None:
    admission = RequestAdmissionController(limit=3, policy=AdmissionPolicy.CACHE_AFFINE)
    for spec in (
        _compile("compile-0", 0, cohort_gain=1),
        _compile("compile-1", 1, cohort_gain=9),
        _compile("compile-2", 2, cohort_gain=4),
    ):
        admission.submit(spec)

    selected = admission.admit_available()

    assert [item.request_id for item in selected] == ["compile-1", "compile-2", "compile-0"]
    assert [(item.request_id, item.source_sequence) for item in selected] == [
        ("compile-1", 1),
        ("compile-2", 2),
        ("compile-0", 0),
    ]
    assert {item.request_id for item in selected} == {"compile-0", "compile-1", "compile-2"}
