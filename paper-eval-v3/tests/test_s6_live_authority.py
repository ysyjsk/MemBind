"""Offline RED/GREEN contracts for one S6 calibration-block authority."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s6_calibration_contract import (
    DEVELOPMENT_HISTORIES_PAYLOAD_SHA256,
    build_s6_matrix,
    verify_s6_matrix_freeze,
)
from paper_eval.s6_live_authority import (
    S6LiveAuthorityError,
    build_s6_live_authority,
    consume_s6_live_authority,
    evaluate_s6_live_preflight,
    finalize_s6_live_authority,
    finalize_s6_live_preflight,
    verify_s6_live_authority,
    verify_s6_live_authority_binding,
    verify_s6_live_authority_consumption,
    verify_s6_live_preflight,
)


SHA = "a" * 64
MATRIX_FILE_SHA256 = "b" * 64
SOURCE_SHA256S = ("1" * 64, "2" * 64, "3" * 64)
SOURCE_CLOSURE = {
    "authority": "1" * 64,
    "calibration_contract": "2" * 64,
    "block_controller": "3" * 64,
    "method_runner": "4" * 64,
    "block_postprocess": "5" * 64,
    "authority_test": "6" * 64,
    "production_runtime": "7" * 64,
}


def _matrix() -> dict[str, object]:
    return build_s6_matrix(
        input_bindings={
            "s6_development_histories_payload_sha256": (
                DEVELOPMENT_HISTORIES_PAYLOAD_SHA256
            ),
            "parent_protocol_sha256": "7" * 64,
            "s5_pstar_result_file_sha256": "8" * 64,
            "s5_pstar_result_payload_sha256": "9" * 64,
            "s5_mstar_result_file_sha256": "a" * 64,
            "s5_mstar_result_payload_sha256": "b" * 64,
        }
    )


def _matrix_freeze() -> dict[str, object]:
    matrix = _matrix()
    return verify_s6_matrix_freeze(
        {
            "protocol_version": "paper-eval-v3",
            "git_commit": "d" * 40,
            "run_id": "s6-calibration-matrix-freeze-20260816-001",
            "status": "finalized",
            "payload": matrix,
            "payload_sha256": payload_sha256(matrix),
        }
    )


def _observations(namespace: str) -> dict[str, object]:
    return {
        "construction": {
            "status": "PASS",
            "served_model_id": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
        },
        "embedding": {
            "status": "PASS",
            "served_model_id": "qwen3-embedding-0.6b",
        },
        "neo4j_connectivity": True,
        "namespace": namespace,
        "namespace_state": {"node_count": 0, "relationship_count": 0},
    }


def _evaluation(
    *, cell_index: int = 0, observations: dict[str, object] | None = None
) -> dict[str, object]:
    matrix = _matrix()
    cell = matrix["cells"][cell_index]
    return evaluate_s6_live_preflight(
        matrix_freeze=_matrix_freeze(),
        matrix_file_sha256=MATRIX_FILE_SHA256,
        cell_index=cell_index,
        episode_source_sha256s=SOURCE_SHA256S,
        execution_identity_sha256="c" * 64,
        observations=(
            observations
            if observations is not None
            else _observations(str(cell["namespace"]))
        ),
    )


def _preflight() -> dict[str, object]:
    evaluation = _evaluation()
    return verify_s6_live_preflight(
        {
            "protocol_version": "paper-eval-v3",
            "git_commit": "d" * 40,
            "run_id": "s6-07741c45-pstar-c1-001-preflight",
            "status": "finalized",
            "payload": evaluation,
            "payload_sha256": payload_sha256(evaluation),
        }
    )


def _authority() -> dict[str, object]:
    return build_s6_live_authority(
        matrix_freeze=_matrix_freeze(),
        matrix_file_sha256=MATRIX_FILE_SHA256,
        cell_index=0,
        episode_source_sha256s=SOURCE_SHA256S,
        preflight=_preflight(),
        preflight_file_sha256="d" * 64,
        execution_identity_sha256="c" * 64,
        source_sha256=SOURCE_CLOSURE,
    )


def test_preflight_binds_exact_matrix_cell_source_manifest_and_empty_namespace() -> None:
    evaluation = _evaluation()
    cell = _matrix()["cells"][0]

    assert evaluation["verdict"] == "PASS"
    assert evaluation["cell"] == cell
    assert evaluation["matrix"] == {
        "file_sha256": MATRIX_FILE_SHA256,
        "payload_sha256": _matrix_freeze()["payload_sha256"],
        "matrix_sha256": _matrix()["matrix_sha256"],
    }
    assert evaluation["workload"] == {
        "source_count": 3,
        "source_manifest_sha256": payload_sha256(
            [
                {"source_sequence": index, "source_sha256": digest}
                for index, digest in enumerate(SOURCE_SHA256S)
            ]
        ),
    }
    assert evaluation["failures"] == []
    assert evaluation["authority"]["s6_block_authority_creation_authorized"] is True


@pytest.mark.parametrize(
    "failure",
    [
        "construction_status",
        "construction_model",
        "vllm_version",
        "max_model_len",
        "embedding_model",
        "namespace",
        "nonempty",
    ],
)
def test_preflight_records_failure_without_live_authority(failure: str) -> None:
    matrix = _matrix()
    observations = _observations(str(matrix["cells"][0]["namespace"]))
    if failure == "construction_status":
        observations["construction"]["status"] = "FAIL"
    elif failure == "construction_model":
        observations["construction"]["served_model_id"] = "wrong"
    elif failure == "vllm_version":
        observations["construction"]["vllm_version"] = "0.25.0"
    elif failure == "max_model_len":
        observations["construction"]["max_model_len"] = 40960
    elif failure == "embedding_model":
        observations["embedding"]["served_model_id"] = "wrong"
    elif failure == "namespace":
        observations["namespace"] = "pev3-wrong"
    else:
        observations["namespace_state"]["node_count"] = 1

    evaluation = _evaluation(observations=observations)

    assert evaluation["verdict"] == "FAIL"
    assert evaluation["failures"]
    assert evaluation["authority"]["s6_block_authority_creation_authorized"] is False


def test_authority_is_one_cell_one_namespace_single_use_and_prefinal() -> None:
    authority = verify_s6_live_authority(_authority())
    payload = authority["payload"]

    assert payload["cell"] == _matrix()["cells"][0]
    assert payload["source_sha256"] == SOURCE_CLOSURE
    assert payload["authority"] == {
        "single_use": True,
        "construction_call_authorized": True,
        "embedding_call_authorized": True,
        "neo4j_read_authorized": True,
        "neo4j_mutation_authorized": True,
        "s6_development_calibration_block_authorized": True,
        "namespace_cleanup_authorized": False,
        "next_cell_authorized": False,
        "current_stage_pointer_update_authorized": False,
        "pilot_execution_authorized": False,
        "final_paper_test_execution_authorized": False,
    }
    assert verify_s6_live_authority_binding(
        authority,
        matrix_freeze=_matrix_freeze(),
        matrix_file_sha256=MATRIX_FILE_SHA256,
    ) == authority


def test_authority_rejects_matrix_preflight_and_source_manifest_drift() -> None:
    with pytest.raises(S6LiveAuthorityError, match="matrix_file_binding_invalid"):
        build_s6_live_authority(
            matrix_freeze=_matrix_freeze(),
            matrix_file_sha256="e" * 64,
            cell_index=0,
            episode_source_sha256s=SOURCE_SHA256S,
            preflight=_preflight(),
            preflight_file_sha256="d" * 64,
            execution_identity_sha256="c" * 64,
            source_sha256=SOURCE_CLOSURE,
        )

    with pytest.raises(S6LiveAuthorityError, match="source_manifest_binding_invalid"):
        build_s6_live_authority(
            matrix_freeze=_matrix_freeze(),
            matrix_file_sha256=MATRIX_FILE_SHA256,
            cell_index=0,
            episode_source_sha256s=(*SOURCE_SHA256S, "4" * 64),
            preflight=_preflight(),
            preflight_file_sha256="d" * 64,
            execution_identity_sha256="c" * 64,
            source_sha256=SOURCE_CLOSURE,
        )

    with pytest.raises(S6LiveAuthorityError, match="execution_identity_binding_invalid"):
        build_s6_live_authority(
            matrix_freeze=_matrix_freeze(),
            matrix_file_sha256=MATRIX_FILE_SHA256,
            cell_index=0,
            episode_source_sha256s=SOURCE_SHA256S,
            preflight=_preflight(),
            preflight_file_sha256="d" * 64,
            execution_identity_sha256="e" * 64,
            source_sha256=SOURCE_CLOSURE,
        )

    failed_preflight = _preflight()
    failed_preflight["payload"] = _evaluation(
        observations={
            **_observations(str(_matrix()["cells"][0]["namespace"])),
            "neo4j_connectivity": False,
        }
    )
    failed_preflight["payload_sha256"] = payload_sha256(failed_preflight["payload"])
    with pytest.raises(S6LiveAuthorityError, match="preflight_not_pass"):
        build_s6_live_authority(
            matrix_freeze=_matrix_freeze(),
            matrix_file_sha256=MATRIX_FILE_SHA256,
            cell_index=0,
            episode_source_sha256s=SOURCE_SHA256S,
            preflight=failed_preflight,
            preflight_file_sha256="d" * 64,
            execution_identity_sha256="c" * 64,
            source_sha256=SOURCE_CLOSURE,
        )


def test_authority_rejects_out_of_matrix_cell_and_source_closure_drift() -> None:
    with pytest.raises(S6LiveAuthorityError, match="cell_index_invalid"):
        evaluate_s6_live_preflight(
            matrix_freeze=_matrix_freeze(),
            matrix_file_sha256=MATRIX_FILE_SHA256,
            cell_index=32,
            episode_source_sha256s=SOURCE_SHA256S,
            execution_identity_sha256="c" * 64,
            observations=_observations("unused"),
        )

    closure = dict(SOURCE_CLOSURE)
    closure.pop("authority_test")
    with pytest.raises(S6LiveAuthorityError, match="source_inventory_invalid"):
        build_s6_live_authority(
            matrix_freeze=_matrix_freeze(),
            matrix_file_sha256=MATRIX_FILE_SHA256,
            cell_index=0,
            episode_source_sha256s=SOURCE_SHA256S,
            preflight=_preflight(),
            preflight_file_sha256="d" * 64,
            execution_identity_sha256="c" * 64,
            source_sha256=closure,
        )


def test_preflight_authority_and_consumption_are_exclusive(tmp_path: Path) -> None:
    preflight_path = tmp_path / "preflight.json"
    preflight = finalize_s6_live_preflight(
        output_path=preflight_path,
        evaluation=_evaluation(),
        git_commit="d" * 40,
    )
    assert verify_s6_live_preflight(preflight) == preflight
    with pytest.raises(FileExistsError):
        finalize_s6_live_preflight(
            output_path=preflight_path,
            evaluation=_evaluation(),
            git_commit="d" * 40,
        )

    draft = build_s6_live_authority(
        matrix_freeze=_matrix_freeze(),
        matrix_file_sha256=MATRIX_FILE_SHA256,
        cell_index=0,
        episode_source_sha256s=SOURCE_SHA256S,
        preflight=preflight,
        preflight_file_sha256=sha256_file(preflight_path),
        execution_identity_sha256="c" * 64,
        source_sha256=SOURCE_CLOSURE,
    )
    authority_path = tmp_path / "authority.json"
    authority = finalize_s6_live_authority(
        output_path=authority_path,
        authority=draft["payload"],
        git_commit="d" * 40,
    )
    assert verify_s6_live_authority(authority) == authority
    with pytest.raises(FileExistsError):
        finalize_s6_live_authority(
            output_path=authority_path,
            authority=draft["payload"],
            git_commit="d" * 40,
        )

    consumption_path = tmp_path / "authority_consumption.json"
    consumption = consume_s6_live_authority(
        authority=authority,
        authority_file_sha256=sha256_file(authority_path),
        output_path=consumption_path,
        git_commit="d" * 40,
    )
    assert verify_s6_live_authority_consumption(consumption) == consumption
    consumed = consumption["payload"]
    authorized = authority["payload"]
    assert consumed["matrix"] == authorized["matrix"]
    assert consumed["cell"] == authorized["cell"]
    assert consumed["workload"] == authorized["workload"]
    assert consumed["preflight"] == authorized["preflight"]
    assert consumed["execution_identity_sha256"] == authorized[
        "execution_identity_sha256"
    ]
    assert consumed["source_sha256"] == SOURCE_CLOSURE
    assert consumed["source_closure_sha256"] == payload_sha256(SOURCE_CLOSURE)
    assert consumed["authority_file_sha256"] == sha256_file(authority_path)
    assert consumed["authority_payload_sha256"] == authority["payload_sha256"]
    assert consumed["further_live_authority"] is False
    with pytest.raises(FileExistsError):
        consume_s6_live_authority(
            authority=authority,
            authority_file_sha256=sha256_file(authority_path),
            output_path=consumption_path,
            git_commit="d" * 40,
        )


def test_verifiers_reject_hash_scope_cell_and_private_field_tamper() -> None:
    for mutation in (
        lambda value: value["payload"]["authority"].update(
            next_cell_authorized=True
        ),
        lambda value: value["payload"]["cell"].update(cell_index=1),
        lambda value: value["payload"]["cell"].update(configured_concurrency=2),
        lambda value: value["payload"]["cell"].update(namespace="pev3-wrong"),
        lambda value: value["payload"].update(api_key="secret"),
    ):
        altered = copy.deepcopy(_authority())
        mutation(altered)
        altered["payload_sha256"] = payload_sha256(altered["payload"])
        with pytest.raises(S6LiveAuthorityError):
            verify_s6_live_authority(altered)

    altered = copy.deepcopy(_authority())
    altered["payload_sha256"] = SHA
    with pytest.raises(S6LiveAuthorityError):
        verify_s6_live_authority(altered)
