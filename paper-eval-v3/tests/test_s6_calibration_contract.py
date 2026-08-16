"""RED/GREEN contracts for the frozen S6 development-only matrix."""

from __future__ import annotations

from copy import deepcopy

import pytest

from paper_eval.s6_calibration_contract import (
    CONCURRENCIES,
    DEVELOPMENT_HISTORIES,
    METHODS,
    S6CalibrationContractError,
    build_s6_matrix,
    compute_s6_block_metrics,
    deterministic_nearest_rank_p95,
    verify_s6_matrix,
)


def _bindings() -> dict[str, str]:
    return {
        "development_exposed_ids_payload_sha256": "a" * 64,
        "parent_protocol_sha256": "b" * 64,
        "s5_pstar_result_file_sha256": "c" * 64,
        "s5_pstar_result_payload_sha256": "d" * 64,
        "s5_mstar_result_file_sha256": "e" * 64,
        "s5_mstar_result_payload_sha256": "f" * 64,
    }


def test_matrix_is_exactly_four_histories_two_methods_and_four_concurrencies() -> None:
    matrix = build_s6_matrix(input_bindings=_bindings())

    assert matrix["status"] == "FROZEN_DEVELOPMENT_CALIBRATION_MATRIX"
    assert matrix["histories"] == list(DEVELOPMENT_HISTORIES)
    assert matrix["methods"] == list(METHODS)
    assert matrix["concurrencies"] == list(CONCURRENCIES)
    assert matrix["cell_count"] == 32
    assert len(matrix["cells"]) == 32
    assert [cell["cell_index"] for cell in matrix["cells"]] == list(range(32))
    assert {
        (cell["history_id"], cell["method"], cell["configured_concurrency"])
        for cell in matrix["cells"]
    } == {
        (history, method, concurrency)
        for history in DEVELOPMENT_HISTORIES
        for concurrency in CONCURRENCIES
        for method in METHODS
    }
    # Methods are adjacent within each history/C pair. This avoids a matrix
    # order that runs every P* block before every M* block.
    assert [cell["method"] for cell in matrix["cells"][:4]] == [
        "P*",
        "M*",
        "P*",
        "M*",
    ]
    assert all(cell["namespace"] == f"pev3-{cell['run_id']}" for cell in matrix["cells"])
    assert all(cell["attempt_ordinal"] == 1 for cell in matrix["cells"])
    assert verify_s6_matrix(matrix) == matrix


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["histories"].append("PILOT-PRIVATE-ID"),
        lambda value: value["cells"].append(deepcopy(value["cells"][0])),
        lambda value: value["cells"][0].update(configured_concurrency=3),
        lambda value: value["cells"][0].update(method="U0"),
        lambda value: value["cells"][0].update(namespace="pev3-other"),
        lambda value: value["cells"][0].update(cell_index=9),
        lambda value: value["input_bindings"].update(parent_protocol_sha256="bad"),
    ],
)
def test_matrix_verifier_recomputes_inventory_and_rejects_drift(mutation: object) -> None:
    matrix = build_s6_matrix(input_bindings=_bindings())
    mutation(matrix)

    with pytest.raises(S6CalibrationContractError):
        verify_s6_matrix(matrix)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([10], 10),
        ([9, 1], 9),
        (list(range(1, 20)), 19),
        (list(range(1, 21)), 19),
        ([30, 10, 20, 40], 40),
    ],
)
def test_p95_uses_frozen_nearest_rank_convention(
    values: list[int], expected: int
) -> None:
    assert deterministic_nearest_rank_p95(values) == expected


def test_block_metrics_use_first_service_and_last_terminal_or_publication() -> None:
    metrics = compute_s6_block_metrics(
        expected_source_count=4,
        source_outcomes=[
            {
                "source_sequence": 0,
                "status": "PUBLISHED",
                "arrival_timestamp_ns": 50,
                "service_start_timestamp_ns": 100,
                "publication_timestamp_ns": 250,
                "terminal_timestamp_ns": 260,
            },
            {
                "source_sequence": 1,
                "status": "FAILED",
                "arrival_timestamp_ns": 70,
                "service_start_timestamp_ns": 110,
                "publication_timestamp_ns": None,
                "terminal_timestamp_ns": 420,
            },
            {
                "source_sequence": 2,
                "status": "PUBLISHED",
                "arrival_timestamp_ns": 80,
                "service_start_timestamp_ns": 120,
                "publication_timestamp_ns": 500,
                "terminal_timestamp_ns": 490,
            },
            {
                "source_sequence": 3,
                "status": "CENSORED",
                "arrival_timestamp_ns": 90,
                "service_start_timestamp_ns": 130,
                "publication_timestamp_ns": None,
                "terminal_timestamp_ns": 450,
            },
        ],
    )

    assert metrics == {
        "expected_source_count": 4,
        "published_source_count": 2,
        "failed_source_count": 1,
        "censored_source_count": 1,
        "makespan_ns": 400,
        "successful_goodput_per_s": 5_000_000.0,
        "freshness_ns": [200, 420],
        "p95_freshness_ns": 420,
        "p95_quantile_convention": "NEAREST_RANK_CEIL_0_95_N",
    }


def test_complete_treatment_failure_keeps_zero_goodput_and_unstarted_censoring() -> None:
    metrics = compute_s6_block_metrics(
        expected_source_count=3,
        source_outcomes=[
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
        ],
    )

    assert metrics == {
        "expected_source_count": 3,
        "published_source_count": 0,
        "failed_source_count": 1,
        "censored_source_count": 2,
        "makespan_ns": 100,
        "successful_goodput_per_s": 0.0,
        "freshness_ns": [],
        "p95_freshness_ns": None,
        "p95_quantile_convention": "NEAREST_RANK_CEIL_0_95_N",
    }


@pytest.mark.parametrize(
    "outcomes",
    [
        [],
        [
            {
                "source_sequence": 0,
                "status": "PUBLISHED",
                "arrival_timestamp_ns": 100,
                "service_start_timestamp_ns": 90,
                "publication_timestamp_ns": 99,
                "terminal_timestamp_ns": 110,
            }
        ],
        [
            {
                "source_sequence": 0,
                "status": "FAILED",
                "arrival_timestamp_ns": 1,
                "service_start_timestamp_ns": 2,
                "publication_timestamp_ns": 3,
                "terminal_timestamp_ns": 4,
            }
        ],
        [
            {
                "source_sequence": 0,
                "status": "PUBLISHED",
                "arrival_timestamp_ns": 1,
                "service_start_timestamp_ns": 2,
                "publication_timestamp_ns": None,
                "terminal_timestamp_ns": 4,
            }
        ],
        [
            {
                "source_sequence": 1,
                "status": "CENSORED",
                "arrival_timestamp_ns": 1,
                "service_start_timestamp_ns": 2,
                "publication_timestamp_ns": None,
                "terminal_timestamp_ns": 4,
            }
        ],
    ],
)
def test_block_metrics_fail_closed_on_incomplete_or_incoherent_terminal_state(
    outcomes: list[dict[str, object]],
) -> None:
    with pytest.raises(S6CalibrationContractError):
        compute_s6_block_metrics(
            expected_source_count=1,
            source_outcomes=outcomes,
        )


def test_p95_rejects_empty_boolean_or_negative_samples() -> None:
    for values in ([], [True], [-1], [1, None]):
        with pytest.raises(S6CalibrationContractError):
            deterministic_nearest_rank_p95(values)
