"""Offline RED/GREEN tests for correctness-first S6 method selection."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s6_block_result import build_s6_block_result
from paper_eval.s6_calibration_contract import (
    DEVELOPMENT_HISTORIES_PAYLOAD_SHA256,
    build_s6_matrix,
)
from paper_eval.s6_selection import (
    S6SelectionError,
    build_s6_method_selection,
    finalize_s6_method_selection,
    verify_s6_method_selection,
    verify_s6_method_selection_payload,
)


MATRIX_FILE_SHA256 = "a" * 64


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


def _freeze() -> dict[str, object]:
    matrix = _matrix()
    return {
        "protocol_version": "paper-eval-v3",
        "git_commit": "a" * 40,
        "run_id": "s6-calibration-matrix-freeze-20260816-001",
        "status": "finalized",
        "payload": matrix,
        "payload_sha256": payload_sha256(matrix),
    }


def _outcome(duration_ns: int) -> list[dict[str, object]]:
    return [
        {
            "source_sequence": 0,
            "status": "PUBLISHED",
            "arrival_timestamp_ns": 0,
            "service_start_timestamp_ns": 10,
            "publication_timestamp_ns": 10 + duration_ns,
            "terminal_timestamp_ns": 10 + duration_ns,
        }
    ]


def _block_payload(
    cell: dict[str, object],
    *,
    duration_ns: int,
    disqualified: bool,
) -> dict[str, object]:
    method = str(cell["method"])
    concurrency = int(cell["configured_concurrency"])
    history_id = str(cell["history_id"])
    return build_s6_block_result(
        cell=cell,
        matrix_binding={
            "file_sha256": MATRIX_FILE_SHA256,
            "payload_sha256": payload_sha256(_matrix()),
            "matrix_sha256": str(_matrix()["matrix_sha256"]),
        },
        workload={
            "source_count": 1,
            "source_manifest_sha256": payload_sha256(
                {"history_id": history_id, "sources": 1}
            ),
        },
        execution_identity_sha256=payload_sha256(
            {"method": method, "concurrency": concurrency}
        ),
        runner={
            "status": "PASS",
            "evidence_payload_sha256": payload_sha256(
                {"run_id": cell["run_id"], "kind": "runner"}
            ),
            "events_file_sha256": payload_sha256(
                {"run_id": cell["run_id"], "kind": "events"}
            ),
        },
        source_outcomes=_outcome(duration_ns),
        correctness={
            "direct_hard_violation_count": int(disqualified),
            "deterministic_correctness_gate": "PASS",
            "hidden_fallback_count": 0,
        },
        work_volume={
            "llm_call_count": 1,
            "llm_prompt_tokens": 100,
            "llm_completion_tokens": 10,
            "embedding_call_count": 1,
            "embedding_input_count": 1,
            "db_query_count": None,
            "db_transaction_count": 1,
            "db_write_count": None,
        },
        bindings={
            "preflight_file_sha256": "1" * 64,
            "preflight_payload_sha256": "2" * 64,
            "authority_file_sha256": "3" * 64,
            "authority_payload_sha256": "4" * 64,
            "consumption_file_sha256": "5" * 64,
            "consumption_payload_sha256": "6" * 64,
            "post_observation_file_sha256": "7" * 64,
            "post_observation_payload_sha256": "8" * 64,
        },
    )


def _blocks(
    *,
    disqualify_mstar: set[int] | None = None,
    durations: dict[int, int] | None = None,
) -> tuple[list[dict[str, object]], dict[str, str]]:
    selected_durations = durations or {1: 100, 2: 50, 4: 25, 8: 40}
    disqualified = disqualify_mstar if disqualify_mstar is not None else {4}
    artifacts: list[dict[str, object]] = []
    file_hashes: dict[str, str] = {}
    for cell in _matrix()["cells"]:
        payload = _block_payload(
            cell,
            duration_ns=selected_durations[int(cell["configured_concurrency"])],
            disqualified=(
                cell["method"] == "M*"
                and int(cell["configured_concurrency"]) in disqualified
            ),
        )
        run_id = f"{cell['run_id']}-block-result"
        artifact = {
            "protocol_version": "paper-eval-v3",
            "git_commit": "b" * 40,
            "run_id": run_id,
            "status": "finalized",
            "payload": payload,
            "payload_sha256": payload_sha256(payload),
        }
        artifacts.append(artifact)
        file_hashes[str(cell["run_id"])] = payload_sha256(
            {"run_id": run_id, "kind": "file"}
        )
    return artifacts, file_hashes


def _build(
    *,
    disqualify_mstar: set[int] | None = None,
    durations: dict[int, int] | None = None,
) -> dict[str, object]:
    blocks, hashes = _blocks(
        disqualify_mstar=disqualify_mstar,
        durations=durations,
    )
    return build_s6_method_selection(
        matrix_freeze=_freeze(),
        matrix_file_sha256=MATRIX_FILE_SHA256,
        block_results=blocks,
        block_file_sha256s=hashes,
    )


def test_selection_is_goodput_median_and_correctness_first() -> None:
    payload = _build()

    assert verify_s6_method_selection_payload(payload) == payload
    assert payload["verdict"] == "PASS"
    assert payload["selected_concurrency"] == {"P*": 4, "M*": 8}
    pstar = payload["method_results"]["P*"]
    mstar = payload["method_results"]["M*"]
    assert [row["concurrency"] for row in pstar["candidates"]] == [1, 2, 4, 8]
    assert next(row for row in pstar["candidates"] if row["concurrency"] == 4)[
        "qualified"
    ] is True
    mstar_c4 = next(row for row in mstar["candidates"] if row["concurrency"] == 4)
    assert mstar_c4["qualified"] is False
    assert mstar_c4["total_direct_hard_violations"] == 4
    assert mstar["qualified_concurrencies"] == [1, 2, 8]
    assert payload["authority"] == {
        "method_selection_frozen": True,
        "next_stage_authorized": False,
        "pilot_execution_authorized": False,
        "final_paper_test_execution_authorized": False,
    }


def test_exact_goodput_tie_selects_smaller_concurrency() -> None:
    payload = _build(
        disqualify_mstar=set(),
        durations={1: 100, 2: 25, 4: 25, 8: 50},
    )

    assert payload["selected_concurrency"] == {"P*": 2, "M*": 2}


def test_empty_mstar_qualified_set_freezes_stop_verdict() -> None:
    payload = _build(disqualify_mstar={1, 2, 4, 8})

    assert payload["verdict"] == "STOP_MSTAR_QUALIFIED_SET_EMPTY"
    assert payload["selected_concurrency"] == {"P*": 4, "M*": None}
    assert payload["stop_reason"] == "MSTAR_QUALIFIED_SET_EMPTY"
    assert payload["authority"]["next_stage_authorized"] is False


@pytest.mark.parametrize("failure", ["missing", "duplicate", "mixed_identity"])
def test_selection_rejects_partial_duplicate_or_mixed_identity(failure: str) -> None:
    blocks, hashes = _blocks()
    if failure == "missing":
        removed = blocks.pop()
        hashes.pop(str(removed["payload"]["cell"]["run_id"]))
    elif failure == "duplicate":
        blocks.append(copy.deepcopy(blocks[0]))
    else:
        target = blocks[2]
        target["payload"]["execution_identity_sha256"] = "f" * 64
        target["payload"]["block_result_sha256"] = payload_sha256(
            {
                key: item
                for key, item in target["payload"].items()
                if key != "block_result_sha256"
            }
        )
        target["payload_sha256"] = payload_sha256(target["payload"])

    with pytest.raises(S6SelectionError):
        build_s6_method_selection(
            matrix_freeze=_freeze(),
            matrix_file_sha256=MATRIX_FILE_SHA256,
            block_results=blocks,
            block_file_sha256s=hashes,
        )


def test_selection_rejects_workload_or_matrix_binding_drift() -> None:
    blocks, hashes = _blocks()
    target = blocks[1]
    target["payload"]["workload"]["source_manifest_sha256"] = "f" * 64
    target["payload"]["block_result_sha256"] = payload_sha256(
        {
            key: item
            for key, item in target["payload"].items()
            if key != "block_result_sha256"
        }
    )
    target["payload_sha256"] = payload_sha256(target["payload"])

    with pytest.raises(S6SelectionError, match="history_workload_identity_mixed"):
        build_s6_method_selection(
            matrix_freeze=_freeze(),
            matrix_file_sha256=MATRIX_FILE_SHA256,
            block_results=blocks,
            block_file_sha256s=hashes,
        )


def test_selection_verifier_recomputes_winner_and_seal() -> None:
    payload = _build()
    for mutation in (
        lambda value: value["selected_concurrency"].update(**{"P*": 8}),
        lambda value: value["method_results"]["M*"]["candidates"][0].update(
            qualified=False
        ),
        lambda value: value["authority"].update(pilot_execution_authorized=True),
    ):
        altered = copy.deepcopy(payload)
        mutation(altered)
        altered["selection_sha256"] = payload_sha256(
            {key: item for key, item in altered.items() if key != "selection_sha256"}
        )
        with pytest.raises(S6SelectionError):
            verify_s6_method_selection_payload(altered)


def test_finalized_method_selection_is_exclusive(tmp_path: Path) -> None:
    payload = _build()
    output = tmp_path / "METHOD_SELECTION_FREEZE.json"

    artifact = finalize_s6_method_selection(
        output_path=output,
        payload=payload,
        git_commit="c" * 40,
    )

    assert verify_s6_method_selection(artifact) == artifact
    assert artifact["run_id"] == "s6-method-selection-freeze-20260816-001"
    assert artifact["status"] == "finalized"
    with pytest.raises(FileExistsError):
        finalize_s6_method_selection(
            output_path=output,
            payload=payload,
            git_commit="c" * 40,
        )

