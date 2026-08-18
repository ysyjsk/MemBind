"""TDD contracts for v3.1 non-preemptive frontier-first LLM admission."""

from __future__ import annotations

import pytest

from paper_eval.membind_v31.admission import (
    AdmissionPolicy,
    MemBindV31AdmissionError,
    RequestAdmissionController,
    RequestKind,
    RequestSpec,
)


def _compile(
    request_id: str,
    sequence: int,
    *,
    score: int = 0,
    recency: int = 0,
    cohort_gain: int = 0,
    signature: str = "sig-none",
) -> RequestSpec:
    return RequestSpec(
        request_id=request_id,
        kind=RequestKind.COMPILE,
        stream_id="history-a",
        source_sequence=sequence,
        affinity_score=score,
        provider_recency=recency,
        cohort_gain=cohort_gain,
        affinity_signature=signature,
    )


def _frontier(request_id: str, sequence: int = 0) -> RequestSpec:
    return RequestSpec(
        request_id=request_id,
        kind=RequestKind.FRONTIER,
        stream_id="history-a",
        source_sequence=sequence,
    )


@pytest.mark.parametrize("policy", [AdmissionPolicy.FIFO, AdmissionPolicy.CACHE_AFFINE])
def test_waiting_frontier_outranks_compile_and_residual_capacity_is_work_conserving(
    policy: AdmissionPolicy,
) -> None:
    admission = RequestAdmissionController(limit=3, policy=policy)
    admission.submit(_compile("compile-0", 0, score=100))
    admission.submit(_compile("compile-1", 1, score=10))
    admission.submit(_frontier("bind-0"))

    selected = admission.admit_available()

    assert selected[0].request_id == "bind-0"
    assert len(selected) == 3
    assert admission.observation()["active_count"] == 3
    assert admission.observation()["observed_max_inflight"] == 3


def test_barrier_policy_leaves_residual_capacity_idle_until_frontier_finishes() -> None:
    admission = RequestAdmissionController(limit=3, policy=AdmissionPolicy.BARRIER)
    admission.submit(_compile("compile-0", 0))
    admission.submit(_compile("compile-1", 1))
    admission.submit(_frontier("bind-0"))

    assert [item.request_id for item in admission.admit_available()] == ["bind-0"]
    assert admission.admit_available() == ()
    admission.finish("bind-0")
    assert [item.request_id for item in admission.admit_available()] == [
        "compile-0",
        "compile-1",
    ]


def test_fifo_and_cache_affine_have_deterministic_but_distinct_compile_order() -> None:
    specs = [
        _compile("compile-2", 2, score=5, signature="safe-b"),
        _compile("compile-0", 0, score=5, signature="safe-a"),
        _compile("compile-1", 1, score=9, signature="safe-c"),
    ]

    fifo = RequestAdmissionController(limit=3, policy=AdmissionPolicy.FIFO)
    cache = RequestAdmissionController(limit=3, policy=AdmissionPolicy.CACHE_AFFINE)
    for spec in specs:
        fifo.submit(spec)
        cache.submit(spec)

    assert [item.request_id for item in fifo.admit_available()] == [
        "compile-2",
        "compile-0",
        "compile-1",
    ]
    assert [item.request_id for item in cache.admit_available()] == [
        "compile-1",
        "compile-0",
        "compile-2",
    ]


def test_cache_affine_lexicographic_order_matches_frozen_methodology() -> None:
    admission = RequestAdmissionController(limit=4, policy=AdmissionPolicy.CACHE_AFFINE)
    specs = [
        _compile("lower-affinity", 0, score=4, recency=99, cohort_gain=999),
        _compile("older-provider", 1, score=8, recency=2, cohort_gain=999),
        _compile("lower-cohort", 2, score=8, recency=3, cohort_gain=4),
        _compile("winner", 3, score=8, recency=3, cohort_gain=12),
    ]
    for spec in specs:
        admission.submit(spec)

    assert [item.request_id for item in admission.admit_available()] == [
        "winner",
        "lower-cohort",
        "older-provider",
        "lower-affinity",
    ]


def test_waiting_affinity_can_be_refreshed_but_active_or_identity_changes_fail_closed() -> None:
    admission = RequestAdmissionController(limit=1, policy=AdmissionPolicy.CACHE_AFFINE)
    admission.submit(_compile("compile-0", 0))
    admission.update_waiting_affinity(
        "compile-0",
        affinity_score=16,
        provider_recency=2,
        cohort_gain=32,
        affinity_signature="safe-updated",
    )
    selected = admission.admit_available()[0]
    assert (
        selected.affinity_score,
        selected.provider_recency,
        selected.cohort_gain,
        selected.affinity_signature,
    ) == (16, 2, 32, "safe-updated")
    with pytest.raises(MemBindV31AdmissionError, match="request_not_waiting"):
        admission.update_waiting_affinity(
            "compile-0",
            affinity_score=0,
            provider_recency=0,
            cohort_gain=0,
            affinity_signature="safe-late",
        )


def test_second_global_frontier_request_fails_closed() -> None:
    admission = RequestAdmissionController(limit=2, policy=AdmissionPolicy.FIFO)
    admission.submit(_frontier("bind-0", 0))
    admission.submit(_frontier("bind-1", 1))
    with pytest.raises(MemBindV31AdmissionError, match="multiple_frontier_requests"):
        admission.admit_available()


def test_active_compile_is_not_preempted_when_frontier_becomes_ready() -> None:
    admission = RequestAdmissionController(limit=1, policy=AdmissionPolicy.FIFO)
    admission.submit(_compile("compile-0", 0))
    assert admission.admit_available()[0].request_id == "compile-0"

    admission.submit(_frontier("bind-0"))
    assert admission.admit_available() == ()
    assert admission.cancel("compile-0") == "CANCELLATION_REQUESTED"
    assert admission.observation()["active_request_ids"] == ["compile-0"]

    admission.finish("compile-0", outcome="cancelled")
    assert admission.admit_available()[0].request_id == "bind-0"


def test_waiting_cancellation_and_active_error_are_deterministic_and_content_safe() -> None:
    admission = RequestAdmissionController(limit=1, policy=AdmissionPolicy.FIFO)
    admission.submit(_compile("compile-active", 0, signature="safe-signature"))
    admission.submit(_compile("compile-waiting", 1, signature="safe-signature"))
    admission.admit_available()

    assert admission.cancel("compile-waiting") == "CANCELLED"
    admission.fail("compile-active", RuntimeError("private prompt text"))
    snapshot = admission.observation()

    assert snapshot["active_count"] == 0
    assert snapshot["cancelled_count"] == 1
    assert snapshot["failed_count"] == 1
    assert "private prompt text" not in repr(snapshot)
    assert admission.public_events[-1]["error_class"] == "builtins.RuntimeError"
    assert all("payload" not in event and "prompt" not in event for event in admission.public_events)


def test_duplicate_or_invalid_requests_fail_closed() -> None:
    admission = RequestAdmissionController(limit=2, policy=AdmissionPolicy.FIFO)
    admission.submit(_compile("compile-0", 0))
    with pytest.raises(MemBindV31AdmissionError, match="request_id_duplicate"):
        admission.submit(_compile("compile-0", 1))
    with pytest.raises(MemBindV31AdmissionError, match="affinity_score_invalid"):
        _compile("compile-bad", 0, score=-1)
