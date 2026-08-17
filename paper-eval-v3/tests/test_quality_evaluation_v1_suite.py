"""TDD contracts for U0 freeze and common-method Quality v1 aggregation."""

from __future__ import annotations

import json

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.quality_evaluation_v1_suite import (
    decide_u0_freeze,
    load_or_restore_quality_v1_bundle,
    persist_quality_v1_bundle,
    summarize_quality_v1,
)


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")


def _row(method: str, history: str, qa: float | None) -> dict:
    valid = qa is not None
    return {
        "method": method,
        "history_id": history,
        "qa_accuracy": qa,
        "judge_valid_denominator": 1 if valid else 0,
        "failure_category": "SUCCESS" if qa == 1.0 else (
            "READER_INVALID" if qa is None else "READER_OR_JUDGE_INCORRECT"
        ),
        "session_metrics": {
            "recall_at_1": 0.5,
            "recall_at_3": 1.0,
            "recall_at_5": 1.0,
            "recall_at_10": 1.0,
            "mrr": 0.75,
            "ndcg_at_10": 0.8,
            "gold_ranks": [1, 2],
            "relevance_unit": "LONGMEMEVAL_GOLD_SESSION",
        },
        "edge_provenance_metrics": {
            "metric_scope": "PROVENANCE_PROXY_NOT_GOLD_FACT_RECALL",
            "edge_gold_source_precision_at_10": 0.2,
            "gold_session_edge_coverage_at_10": 0.5,
        },
        "temporal_diagnostics": {
            "stale_fact_count": 1,
            "active_fact_count": 3,
            "future_fact_count": 0,
            "conflicting_relation_group_count": 1,
            "stale_ranked_before_latest_valid_count": 1,
        },
        "context": {"reader_prompt_tokens": 1000},
        "payload_sha256": "a" * 64,
    }


def test_u0_freezes_at_two_or_more_without_invalid() -> None:
    rows = [_row("U0", history, qa) for history, qa in zip(
        HISTORIES, (1.0, 1.0, 0.0, 0.0), strict=True
    )]
    assert decide_u0_freeze(rows)["decision"] == "FREEZE_QUALITY_EVALUATION_V1"


def test_u0_invalid_blocks_freeze_without_becoming_wrong_answer() -> None:
    rows = [_row("U0", history, qa) for history, qa in zip(
        HISTORIES, (1.0, 1.0, 0.0, None), strict=True
    )]
    decision = decide_u0_freeze(rows)
    assert decision["decision"] == "BLOCK_PIPELINE_INVALID"
    assert decision["valid_denominator"] == 3


def test_summary_requires_common_order_and_reports_each_method() -> None:
    rows = [
        _row(method, history, 1.0 if history in HISTORIES[:2] else 0.0)
        for method in ("U0", "A0", "P(C=2)")
        for history in HISTORIES
    ]
    summary = summarize_quality_v1(rows, methods=("U0", "A0", "P(C=2)"))

    assert summary["by_method"]["U0"]["qa_accuracy"] == 0.5
    assert summary["by_method"]["A0"]["recall_at_1_macro"] == 0.5
    assert summary["by_method"]["P(C=2)"]["mrr_macro"] == 0.75
    assert summary["fact_gold_labels_available"] is False


def test_summary_rejects_reordered_or_missing_units() -> None:
    rows = [_row("U0", history, 1.0) for history in HISTORIES]
    rows.reverse()
    with pytest.raises(ValueError, match="inventory"):
        summarize_quality_v1(rows, methods=("U0",))


def test_private_first_bundle_restores_public_without_resampling(tmp_path) -> None:
    private = {
        "schema_version": "membind.paper-eval-v3.quality-v1-private.v1",
        "predicted_answer": "five ounces",
    }
    private["payload_sha256"] = payload_sha256(private)
    public = {
        "schema_version": "membind.paper-eval-v3.quality-v1-public.v1",
        "method": "U0",
        "history_id": HISTORIES[0],
        "private_payload_sha256": private["payload_sha256"],
    }
    public["payload_sha256"] = payload_sha256(public)
    root = tmp_path / "attempt-001"

    persisted = persist_quality_v1_bundle(
        root,
        public_artifact=public,
        private_artifact=private,
    )
    assert persisted == public
    (root / "public.json").unlink()

    restored = load_or_restore_quality_v1_bundle(root)
    assert restored == public
    assert json.loads((root / "public.json").read_text()) == public
    assert "five ounces" not in (root / "public.json").read_text()


def test_existing_sealed_bundle_rejects_answer_resampling(tmp_path) -> None:
    private = {"predicted_answer": "five ounces"}
    private["payload_sha256"] = payload_sha256(private)
    public = {"private_payload_sha256": private["payload_sha256"]}
    public["payload_sha256"] = payload_sha256(public)
    root = tmp_path / "attempt-001"
    persist_quality_v1_bundle(
        root,
        public_artifact=public,
        private_artifact=private,
    )
    changed = dict(private)
    changed["predicted_answer"] = "six ounces"
    changed["payload_sha256"] = payload_sha256(
        {key: value for key, value in changed.items() if key != "payload_sha256"}
    )
    changed_public = dict(public)
    changed_public["private_payload_sha256"] = changed["payload_sha256"]
    changed_public["payload_sha256"] = payload_sha256(
        {
            key: value
            for key, value in changed_public.items()
            if key != "payload_sha256"
        }
    )

    with pytest.raises(ValueError, match="drift"):
        persist_quality_v1_bundle(
            root,
            public_artifact=changed_public,
            private_artifact=changed,
        )
