"""Offline RED/GREEN tests for one finalized S6 block result."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s6_block_result import (
    S6BlockResultError,
    build_s6_block_result,
    finalize_s6_block_result,
    verify_s6_block_result,
    verify_s6_block_result_payload,
)
from paper_eval.s6_calibration_contract import (
    DEVELOPMENT_HISTORIES_PAYLOAD_SHA256,
    build_s6_matrix,
)


def _matrix() -> dict[str, object]:
    return build_s6_matrix(
        input_bindings={
            "s6_development_histories_payload_sha256": (
                DEVELOPMENT_HISTORIES_PAYLOAD_SHA256
            ),
            "parent_protocol_sha256": "1" * 64,
            "s5_pstar_result_file_sha256": "2" * 64,
            "s5_pstar_result_payload_sha256": "3" * 64,
            "s5_mstar_result_file_sha256": "4" * 64,
            "s5_mstar_result_payload_sha256": "5" * 64,
        }
    )


def _matrix_binding() -> dict[str, str]:
    return {
        "file_sha256": "6" * 64,
        "payload_sha256": payload_sha256(_matrix()),
        "matrix_sha256": str(_matrix()["matrix_sha256"]),
    }


def _workload(count: int) -> dict[str, object]:
    return {
        "source_count": count,
        "source_manifest_sha256": "7" * 64,
    }


def _runner(status: str) -> dict[str, str]:
    return {
        "status": status,
        "evidence_payload_sha256": "8" * 64,
        "events_file_sha256": "9" * 64,
    }


def _correctness(
    *, violations: int = 0, gate: str = "PASS", fallbacks: int = 0
) -> dict[str, object]:
    return {
        "direct_hard_violation_count": violations,
        "deterministic_correctness_gate": gate,
        "hidden_fallback_count": fallbacks,
    }


def _work_volume() -> dict[str, int | None]:
    return {
        "llm_call_count": 10,
        "llm_prompt_tokens": 1_000,
        "llm_completion_tokens": 200,
        "embedding_call_count": 5,
        "embedding_input_count": 12,
        "db_query_count": None,
        "db_transaction_count": 3,
        "db_write_count": None,
    }


def _bindings() -> dict[str, str]:
    return {
        "preflight_file_sha256": "a" * 64,
        "preflight_payload_sha256": "b" * 64,
        "authority_file_sha256": "c" * 64,
        "authority_payload_sha256": "d" * 64,
        "consumption_file_sha256": "e" * 64,
        "consumption_payload_sha256": "f" * 64,
        "post_observation_file_sha256": "1" * 64,
        "post_observation_payload_sha256": "2" * 64,
    }


def _published_outcomes(count: int) -> list[dict[str, object]]:
    return [
        {
            "source_sequence": index,
            "status": "PUBLISHED",
            "arrival_timestamp_ns": 10 + index,
            "service_start_timestamp_ns": 100 + index * 10,
            "publication_timestamp_ns": 200 + index * 100,
            "terminal_timestamp_ns": 210 + index * 100,
        }
        for index in range(count)
    ]


def _build(
    *,
    method: str,
    outcomes: list[dict[str, object]],
    runner_status: str = "PASS",
    correctness: dict[str, object] | None = None,
) -> dict[str, object]:
    cell = _matrix()["cells"][0 if method == "P*" else 1]
    return build_s6_block_result(
        cell=cell,
        matrix_binding=_matrix_binding(),
        workload=_workload(len(outcomes)),
        execution_identity_sha256="3" * 64,
        runner=_runner(runner_status),
        source_outcomes=outcomes,
        correctness=correctness or _correctness(),
        work_volume=_work_volume(),
        bindings=_bindings(),
    )


def test_mstar_pass_recomputes_metrics_and_is_qualified() -> None:
    payload = _build(method="M*", outcomes=_published_outcomes(2))

    assert verify_s6_block_result_payload(payload) == payload
    assert payload["metrics"] == {
        "expected_source_count": 2,
        "published_source_count": 2,
        "failed_source_count": 0,
        "censored_source_count": 0,
        "makespan_ns": 210,
        "successful_goodput_per_s": pytest.approx(9_523_809.523809524),
        "freshness_ns": [190, 289],
        "p95_freshness_ns": 289,
        "p95_quantile_convention": "NEAREST_RANK_CEIL_0_95_N",
    }
    assert payload["terminal_accounting"] == {
        "expected": 2,
        "published": 2,
        "failed": 0,
        "censored": 0,
        "lost": 0,
        "duplicate": 0,
    }
    assert payload["selection_eligibility"] == {
        "pstar_performance_eligible": False,
        "mstar_qualified": True,
        "mstar_disqualification_reasons": [],
    }


@pytest.mark.parametrize(
    ("correctness", "reason"),
    [
        (_correctness(violations=1), "DIRECT_HARD_VIOLATION"),
        (_correctness(gate="FAIL"), "CORRECTNESS_GATE_NOT_PASS"),
        (_correctness(fallbacks=1), "HIDDEN_FALLBACK"),
    ],
)
def test_mstar_complete_block_discloses_but_disqualifies_correctness_failure(
    correctness: dict[str, object], reason: str
) -> None:
    payload = _build(
        method="M*",
        outcomes=_published_outcomes(2),
        correctness=correctness,
    )

    assert payload["status"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert payload["selection_eligibility"]["mstar_qualified"] is False
    assert reason in payload["selection_eligibility"][
        "mstar_disqualification_reasons"
    ]


def test_pstar_treatment_failure_is_complete_zero_goodput_and_eligible() -> None:
    outcomes = [
        {
            "source_sequence": 0,
            "status": "FAILED",
            "arrival_timestamp_ns": 10,
            "service_start_timestamp_ns": 20,
            "publication_timestamp_ns": None,
            "terminal_timestamp_ns": 120,
        },
        {
            "source_sequence": 1,
            "status": "CENSORED",
            "arrival_timestamp_ns": 11,
            "service_start_timestamp_ns": None,
            "publication_timestamp_ns": None,
            "terminal_timestamp_ns": None,
        },
        {
            "source_sequence": 2,
            "status": "CENSORED",
            "arrival_timestamp_ns": 12,
            "service_start_timestamp_ns": None,
            "publication_timestamp_ns": None,
            "terminal_timestamp_ns": None,
        },
    ]
    payload = _build(
        method="P*",
        outcomes=outcomes,
        runner_status="SCIENTIFIC_OUTCOME_COMPLETE",
        correctness=_correctness(violations=2, gate="FAIL"),
    )

    assert payload["status"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert payload["metrics"]["successful_goodput_per_s"] == 0.0
    assert payload["metrics"]["p95_freshness_ns"] is None
    assert payload["selection_eligibility"] == {
        "pstar_performance_eligible": True,
        "mstar_qualified": None,
        "mstar_disqualification_reasons": [],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["metrics"].update(makespan_ns=1),
        lambda value: value["terminal_accounting"].update(lost=1),
        lambda value: value["selection_eligibility"].update(mstar_qualified=False),
        lambda value: value["source_outcomes"][0].update(source_sequence=1),
        lambda value: value["work_volume"].update(llm_call_count=-1),
    ],
)
def test_verifier_recomputes_metrics_accounting_and_qualification(mutation) -> None:
    payload = _build(method="M*", outcomes=_published_outcomes(2))
    mutation(payload)
    payload["block_result_sha256"] = payload_sha256(
        {key: item for key, item in payload.items() if key != "block_result_sha256"}
    )

    with pytest.raises(S6BlockResultError):
        verify_s6_block_result_payload(payload)


def test_builder_rejects_nonterminal_runner_or_wrong_method_cell() -> None:
    with pytest.raises(S6BlockResultError, match="runner_status_invalid"):
        _build(
            method="P*",
            outcomes=_published_outcomes(2),
            runner_status="FAIL_CLOSED",
        )

    pstar_cell = copy.deepcopy(_matrix()["cells"][0])
    pstar_cell["method"] = "M*"
    with pytest.raises(S6BlockResultError, match="cell_identity_invalid"):
        build_s6_block_result(
            cell=pstar_cell,
            matrix_binding=_matrix_binding(),
            workload=_workload(2),
            execution_identity_sha256="3" * 64,
            runner=_runner("PASS"),
            source_outcomes=_published_outcomes(2),
            correctness=_correctness(),
            work_volume=_work_volume(),
            bindings=_bindings(),
        )


def test_finalized_result_envelope_is_exclusive_and_round_trip(tmp_path: Path) -> None:
    payload = _build(method="M*", outcomes=_published_outcomes(2))
    output = tmp_path / "S6_BLOCK_RESULT.json"

    artifact = finalize_s6_block_result(
        output_path=output,
        payload=payload,
        git_commit="a" * 40,
    )

    assert verify_s6_block_result(artifact) == artifact
    assert sha256_file(output) != "missing"
    assert artifact["run_id"] == "s6-07741c45-mstar-c1-001-block-result"
    assert artifact["status"] == "finalized"
    with pytest.raises(FileExistsError):
        finalize_s6_block_result(
            output_path=output,
            payload=payload,
            git_commit="a" * 40,
        )
