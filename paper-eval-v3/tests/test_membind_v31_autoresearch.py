"""TDD for the bounded, non-mergeable MemBind v3.1 autoresearch probe."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.apc_aligned_baseline import build_apc_aligned_baseline_plan
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.autoresearch import (
    MAX_CANDIDATES,
    PROBE_HISTORY,
    PROBE_SOURCE_COUNT,
    AutoresearchProbeError,
    append_results_tsv,
    assess_probe_candidate,
    build_autoresearch_probe_plan,
    derive_u0_prefix_reference,
    record_probe_crash,
)
from paper_eval.membind_v31.baseline_acceptance import EXPECTED_BASELINE_RUN_ID
from paper_eval.membind_v31.method_plan import (
    build_membind_v31_live_plan,
    verify_membind_v31_method_plan,
)


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")


def _formal_plan() -> dict[str, object]:
    sources = {
        history: [f"{position * 1000 + sequence + 1:064x}" for sequence in range(15)]
        for position, history in enumerate(HISTORIES)
    }
    baseline = build_apc_aligned_baseline_plan(
        run_id=EXPECTED_BASELINE_RUN_ID,
        history_source_sha256s=sources,
        interarrival_ns=10,
        execution_envelope_sha256="a" * 64,
        service_reference_ns=12,
        normalized_offered_load=1.2,
    )
    return build_membind_v31_live_plan(
        run_id="membind-v31-dev-autoresearch-test",
        verified_baseline_plan=baseline,
        methodology_sha256="b" * 64,
        workplan_sha256="c" * 64,
    )


def _sealed(body: dict[str, object]) -> dict[str, object]:
    result = dict(body)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _u0_result() -> dict[str, object]:
    per_source = [
        {
            "source_sequence": sequence,
            "arrival_timestamp_ns": sequence * 10,
            "publication_timestamp_ns": sequence * 10 + (sequence + 1) * 100,
            "freshness_ns": (sequence + 1) * 100,
        }
        for sequence in range(15)
    ]
    return _sealed(
        {
            "schema_version": "membind.paper-eval-v3.apc-aligned-baseline-block-result.v1",
            "status": "PASS",
            "method": "U0-aligned",
            "history_id": PROBE_HISTORY,
            "performance": {"episode_count": 15, "per_source": per_source},
        }
    )


def _candidate(*, p95: int, makespan: int, violations: int = 0) -> dict[str, object]:
    return _sealed(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-live-block-result.v1",
            "status": "PASS",
            "method": "MemBind",
            "history_id": PROBE_HISTORY,
            "source_count": PROBE_SOURCE_COUNT,
            "direct_violation_count": violations,
            "performance": {
                "p95_freshness_ns": p95,
                "makespan_ns": makespan,
            },
            "request_admission": {"observed_max_inflight": 2},
            "checkpoint": {"complete_coverage": True, "terminal_status": "COMPLETED"},
        }
    )


def test_probe_plan_is_a_verified_fixed_prefix_with_fresh_identity() -> None:
    formal = _formal_plan()

    probe, authorization = build_autoresearch_probe_plan(
        verified_formal_plan=formal,
        probe_run_id="membind-v31-ar-20260818-c00",
        candidate_id="c00",
    )

    assert verify_membind_v31_method_plan(probe) == probe
    assert PROBE_HISTORY == "07741c45"
    assert PROBE_SOURCE_COUNT == 12
    assert MAX_CANDIDATES == 3
    assert probe["blocks"][0]["source_count"] == 12
    assert probe["history_source_sha256s"][PROBE_HISTORY] == formal[
        "history_source_sha256s"
    ][PROBE_HISTORY][:12]
    assert probe["arrival_traces"][PROBE_HISTORY]["arrival_offsets_ns"] == formal[
        "arrival_traces"
    ][PROBE_HISTORY]["arrival_offsets_ns"][:12]
    assert probe["blocks"][0]["namespace"] != formal["blocks"][0]["namespace"]
    assert probe["compile_workers"] == probe["lookahead"] == 2
    assert probe["global_llm_admission_k"] == 2
    assert authorization["parent_formal_plan_payload_sha256"] == formal["payload_sha256"]
    assert authorization["merge_authority"] == "NONE_NON_MERGEABLE_DEVELOPMENT_PROBE"


def test_probe_plan_rejects_out_of_budget_or_noncanonical_candidate_identity() -> None:
    formal = _formal_plan()
    for candidate in ("c03", "candidate-0", "c-1"):
        with pytest.raises(AutoresearchProbeError, match="candidate identity invalid"):
            build_autoresearch_probe_plan(
                verified_formal_plan=formal,
                probe_run_id="membind-v31-ar-20260818-c00",
                candidate_id=candidate,
            )


def test_u0_prefix_reference_is_derived_from_exact_first_twelve_sources() -> None:
    reference = derive_u0_prefix_reference(_u0_result())

    assert reference["source_sequences"] == list(range(12))
    assert reference["p95_freshness_ns"] == 1200
    assert reference["makespan_ns"] == 1310
    assert reference["source_count"] == 12
    assert reference["merge_authority"] == "COMPARATOR_ONLY"


def test_candidate_decision_is_correctness_first_then_two_metric_non_regression() -> None:
    comparator = {
        "p95_freshness_ns": 1000,
        "makespan_ns": 2000,
        "payload_sha256": "d" * 64,
    }

    kept = assess_probe_candidate(
        candidate_id="c00",
        candidate_result=_candidate(p95=900, makespan=2000),
        comparator=comparator,
        code_sha256="e" * 64,
        parent_code_sha256="f" * 64,
        description="unchanged implementation reference",
    )
    discarded = assess_probe_candidate(
        candidate_id="c01",
        candidate_result=_candidate(p95=900, makespan=2200),
        comparator=comparator,
        code_sha256="1" * 64,
        parent_code_sha256="e" * 64,
        description="one controlled change",
    )
    invalid = assess_probe_candidate(
        candidate_id="c02",
        candidate_result=_candidate(p95=800, makespan=1800, violations=1),
        comparator=comparator,
        code_sha256="2" * 64,
        parent_code_sha256="e" * 64,
        description="semantically invalid speedup",
    )

    assert kept["status"] == "keep"
    assert kept["engineering_review_required"] is False
    assert discarded["status"] == "discard"
    assert invalid["status"] == "discard"
    assert invalid["semantic_status"] == "VIOLATION_OBSERVED"


def test_results_tsv_is_append_only_and_rejects_duplicate_or_unsafe_text(tmp_path: Path) -> None:
    comparator = {
        "p95_freshness_ns": 1000,
        "makespan_ns": 2000,
        "payload_sha256": "d" * 64,
    }
    decision = assess_probe_candidate(
        candidate_id="c00",
        candidate_result=_candidate(p95=900, makespan=1900),
        comparator=comparator,
        code_sha256="e" * 64,
        parent_code_sha256="f" * 64,
        description="reference",
    )
    target = tmp_path / "results.tsv"

    append_results_tsv(target, decision)

    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("candidate_id\t")
    assert lines[1].startswith("c00\t")
    with pytest.raises(AutoresearchProbeError, match="candidate already recorded"):
        append_results_tsv(target, decision)
    unsafe = json.loads(json.dumps(decision))
    unsafe["candidate_id"] = "c01"
    unsafe["description"] = "bad\trow"
    unsafe["payload_sha256"] = payload_sha256(
        {key: value for key, value in unsafe.items() if key != "payload_sha256"}
    )
    with pytest.raises(AutoresearchProbeError, match="description invalid"):
        append_results_tsv(target, unsafe)


def test_crash_row_is_content_safe_and_non_mergeable() -> None:
    crash = record_probe_crash(
        candidate_id="c02",
        code_sha256="1" * 64,
        parent_code_sha256="2" * 64,
        error_class="builtins.TimeoutError",
        description="bounded probe timeout",
    )

    assert crash["status"] == "crash"
    assert crash["artifact_status"] == "INCOMPLETE"
    assert crash["merge_authority"] == "NONE_NON_MERGEABLE_DEVELOPMENT_PROBE"
    assert "private" not in json.dumps(crash).casefold()
