"""Provider-free C0/C1 read validity, fallback, and cost accounting."""

from __future__ import annotations

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_read_accounting import (
    evaluate_c0_c1_read_accounting,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.state_delta import DeltaChange, StateDelta


def _read(*, result: tuple[str, ...] = ("n1", "n2"), complete: bool = True, start: int = 10, end: int = 30) -> dict:
    return {
        "operator": "node_cosine",
        "occurrence": 0,
        "query": [1.0, 0.0],
        "filter_fingerprint": "filter-v1",
        "group_ids": ["g"],
        "limit": 2,
        "min_score": 0.6,
        "actual_result": list(result),
        "complete_domain": [
            {"uuid": "n1", "embedding": [1.0, 0.0], "score": 1.0},
            {"uuid": "n2", "embedding": [0.8, 0.6], "score": 0.8},
            {"uuid": "n3", "embedding": [0.0, 1.0], "score": 0.0},
        ],
        "cutoff": 0.8,
        "boundary_ties": [],
        "query_epoch": "query-v1",
        "index_epoch": "index-v1",
        "config_epoch": "config-v1",
        "completeness_status": "COMPLETE" if complete else "INCOMPLETE",
        "native_start_ns": start,
        "native_end_ns": end,
    }


def _capture(read: dict) -> dict:
    return {"reads": [read]}


def _node(*, summary: str, embedding: tuple[float, float]) -> dict:
    return {
        "name": summary,
        "group_id": "g",
        "labels": ["Entity"],
        "summary": summary,
        "attributes": {"summary": summary},
        "name_embedding": list(embedding),
    }


def _nodes(*, n1: str = "one", n2: str = "two", n3: str = "three") -> dict:
    return {
        "n1": _node(summary=n1, embedding=(1.0, 0.0)),
        "n2": _node(summary=n2, embedding=(0.8, 0.6)),
        "n3": _node(summary=n3, embedding=(0.0, 1.0)),
    }


class _Clock:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


def test_c1_low_score_nonmember_valid_avoids_c0_read_and_is_subset() -> None:
    old_nodes = _nodes()
    fresh_nodes = _nodes(n3="changed but excluded")
    delta = StateDelta(
        0,
        1,
        (
            DeltaChange(
                "node",
                "n3",
                frozenset({"summary", "attributes"}),
                before=old_nodes["n3"],
                after=fresh_nodes["n3"],
            ),
        ),
    )
    result = evaluate_c0_c1_read_accounting(
        old_capture=_capture(_read()),
        fresh_capture=_capture(_read()),
        delta=delta,
        old_nodes=old_nodes,
        fresh_nodes=fresh_nodes,
        clock_ns=_Clock(100, 107),
    )

    assert result["c0_valid_keys"] == [["node_cosine", 0]]
    assert result["c1_valid_keys"] == [["node_cosine", 0]]
    assert result["reusable_read_keys"] == [["node_cosine", 0]]
    assert result["c1_valid_subset_of_c0"] is True
    assert result["c0_fresh_requery_cost_ns"] == 20
    assert result["c0_fallback_cost_ns"] == 0
    assert result["c1_certificate_cost_ns"] == 7
    assert result["selected_validation_cost_ns"] == 7


def test_member_payload_change_is_invalid_and_falls_back_to_fresh() -> None:
    old_nodes = _nodes()
    fresh_nodes = _nodes(n1="changed member")
    delta = StateDelta(
        0,
        1,
        (
            DeltaChange(
                "node",
                "n1",
                frozenset({"name", "summary", "attributes"}),
                before=old_nodes["n1"],
                after=fresh_nodes["n1"],
            ),
        ),
    )
    result = evaluate_c0_c1_read_accounting(
        old_capture=_capture(_read()),
        fresh_capture=_capture(_read()),
        delta=delta,
        old_nodes=old_nodes,
        fresh_nodes=fresh_nodes,
        clock_ns=_Clock(200, 205),
    )

    assert result["rows"][0]["c0_status"] == "INVALID_CHANGED"
    assert result["rows"][0]["c1_status"] == "INVALID_CHANGED"
    assert result["reusable_read_keys"] == []
    assert result["c0_fallback_cost_ns"] == 20
    assert result["selected_validation_cost_ns"] == 25


def test_c1_false_valid_is_reported_and_never_credited() -> None:
    result = evaluate_c0_c1_read_accounting(
        old_capture=_capture(_read()),
        fresh_capture=_capture(_read(result=("n1", "n3"))),
        delta=StateDelta(0, 1, ()),
        old_nodes=_nodes(),
        fresh_nodes=_nodes(),
        clock_ns=_Clock(300, 302),
    )

    assert result["status"] == "UNSOUND_FALSE_VALID"
    assert result["false_valid_count"] == 1
    assert result["c1_valid_subset_of_c0"] is False
    assert result["reusable_read_keys"] == []


def test_incomplete_c0_evidence_remains_unknown_and_uncredited() -> None:
    result = evaluate_c0_c1_read_accounting(
        old_capture=_capture(_read(complete=False)),
        fresh_capture=_capture(_read()),
        delta=StateDelta(0, 1, ()),
        old_nodes=_nodes(),
        fresh_nodes=_nodes(),
        clock_ns=_Clock(400, 401),
    )

    assert result["rows"][0]["c0_status"] == "UNKNOWN_INCOMPLETE_EVIDENCE"
    assert result["rows"][0]["c1_status"] == "UNKNOWN_INCOMPLETE_EVIDENCE"
    assert result["unknown_count"] == 1
    assert result["reusable_read_keys"] == []


def test_c1_unknown_falls_back_to_c0_and_overlapping_reads_use_union_cost() -> None:
    old = _read(start=10, end=30)
    fresh = _read(start=10, end=30)
    old_second = {**_read(start=20, end=40), "occurrence": 1}
    fresh_second = {**_read(start=20, end=40), "occurrence": 1}
    result = evaluate_c0_c1_read_accounting(
        old_capture={"reads": [old, old_second]},
        fresh_capture={"reads": [fresh, fresh_second]},
        delta=StateDelta(0, 1, (), environment_changes=frozenset({"index_epoch"})),
        old_nodes=_nodes(),
        fresh_nodes=_nodes(),
        clock_ns=_Clock(500, 502, 503, 506),
    )

    assert result["c1_valid_keys"] == []
    assert result["c0_valid_keys"] == [["node_cosine", 0], ["node_cosine", 1]]
    assert result["c0_fresh_requery_cost_ns"] == 30
    assert result["c0_fallback_cost_ns"] == 30
    assert result["c1_certificate_cost_ns"] == 5
    assert result["selected_validation_cost_ns"] == 35
