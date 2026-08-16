"""Offline TDD for method-scoped, single-use S5 live authority.

The authority is the only object allowed to cross from a read-only preflight
into one live method smoke.  These tests use sealed in-memory fixtures and
temporary files; they never construct Graphiti or contact a service.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s5_live_authority import (
    S5LiveAuthorityError,
    build_s5_live_authority,
    consume_s5_live_authority,
    finalize_s5_live_authority,
    verify_s5_live_authority,
    verify_s5_live_authority_consumption,
)
from paper_eval.s5_live_preflight import verify_s5_live_preflight
from paper_eval.s5_live_preflight import (
    evaluate_s5_live_preflight,
    finalize_s5_live_preflight,
)
from paper_eval.s5_production_runner import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    build_s5_production_identity,
)
from tests.test_s5_live_preflight import (
    QUALIFICATION_FILE_SHA256,
    POINTER_FILE_SHA256,
    _fx0,
    _pointer,
    _qualification,
)


SHA = "a" * 64
SOURCE_SHA256S = tuple(f"{index + 1:064x}" for index in range(49))
SOURCE_MANIFEST_SHA256 = payload_sha256(
    [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(SOURCE_SHA256S)
    ]
)


def test_live_authority_requires_the_exact_preflight_qualification() -> None:
    qualification = _qualification("A0")
    authority = build_s5_live_authority(
        method="A0",
        run=_run("A0"),
        production_identity_qualification=qualification,
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        preflight=_preflight("A0"),
        preflight_file_sha256="3" * 64,
        current_stage_pointer_sha256=POINTER_FILE_SHA256,
        predecessor=None,
        fx0_qualification=None,
        source_sha256={
            "authority": "6" * 64,
            "controller": "7" * 64,
            "result_verifier": "8" * 64,
            "test": "9" * 64,
        },
    )
    assert authority["payload"]["production_identity_qualification"][
        "qualification_payload_sha256"
    ] == qualification["payload_sha256"]


def test_raw_identity_and_qualification_file_drift_cannot_reach_authority() -> None:
    with pytest.raises(
        S5LiveAuthorityError, match="production_identity_qualification_invalid"
    ):
        build_s5_live_authority(
            method="A0",
            run=_run("A0"),
            production_identity_qualification=_identity("A0"),
            production_identity_qualification_file_sha256=(
                QUALIFICATION_FILE_SHA256
            ),
            preflight=_preflight("A0"),
            preflight_file_sha256="3" * 64,
            current_stage_pointer_sha256=POINTER_FILE_SHA256,
            predecessor=None,
            fx0_qualification=None,
            source_sha256={
                "authority": "6" * 64,
                "controller": "7" * 64,
                "result_verifier": "8" * 64,
                "test": "9" * 64,
            },
        )

    with pytest.raises(S5LiveAuthorityError, match="preflight_binding_mismatch"):
        build_s5_live_authority(
            method="A0",
            run=_run("A0"),
            production_identity_qualification=_qualification("A0"),
            production_identity_qualification_file_sha256="0" * 64,
            preflight=_preflight("A0"),
            preflight_file_sha256="3" * 64,
            current_stage_pointer_sha256=POINTER_FILE_SHA256,
            predecessor=None,
            fx0_qualification=None,
            source_sha256={
                "authority": "6" * 64,
                "controller": "7" * 64,
                "result_verifier": "8" * 64,
                "test": "9" * 64,
            },
        )


def _identity(method: str) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "method": method,
        "graphiti_version": GRAPHITI_VERSION,
        "graphiti_commit": GRAPHITI_COMMIT,
        "graphiti_native_source_sha256": "1" * 64,
        "graphiti_semantic_api_sha256": "2" * 64,
        "runtime_factory_entrypoint": (
            "native_characterization_runtime.build_u0_graphiti_from_env"
        ),
        "runtime_factory_source_sha256": "3" * 64,
        "scheduler_source_sha256": "4" * 64,
        "scheduler_test_source_sha256": "5" * 64,
        "durable_store_source_sha256": "6" * 64,
        "durable_store_test_source_sha256": "7" * 64,
        "runtime_config_sha256": "8" * 64,
    }
    if method == "M*":
        kwargs["fx0_parity_artifact_sha256"] = "9" * 64
    return build_s5_production_identity(**kwargs)


def _run(method: str) -> dict[str, object]:
    suffix = {"A0": "a0", "P*": "p-star", "M*": "mstar"}[method]
    return {
        "method": method,
        "run_id": f"s5-{suffix}-20260816-001",
        "namespace": f"pev3-s5-{suffix}-20260816-001",
        "history_id": "07741c45",
        "episode_count": 49,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "configured_concurrency": {"A0": 1, "P*": 2, "M*": 2}[method],
    }


def _preflight(method: str) -> dict[str, object]:
    run = _run(method)
    pointer = _pointer()
    qualification = _qualification(method)
    predecessor = None
    if method != "A0":
        predecessor = {
            "method": "A0" if method == "P*" else "P*",
            "verdict": (
                "PASS" if method == "P*" else "SCIENTIFIC_OUTCOME_COMPLETE"
            ),
            "artifact_sha256": "0" * 64,
        }
    fx0 = None
    if method == "M*":
        fx0 = _fx0()
    evaluation = evaluate_s5_live_preflight(
        method=method,
        run_id=str(run["run_id"]),
        namespace=str(run["namespace"]),
        episode_source_sha256s=SOURCE_SHA256S,
        observations={
            "construction": {
                "served_model_id": "qwen3-32b-fp8",
                "vllm_version": "0.26.0",
                "max_model_len": 65536,
            },
            "embedding": {"served_model_id": "qwen3-embedding-0.6b"},
            "neo4j_connectivity": True,
            "namespace": run["namespace"],
            "namespace_state": {"node_count": 0, "relationship_count": 0},
        },
        production_identity_qualification=qualification,
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        current_stage_pointer=pointer,
        current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
        predecessor=predecessor,
        fx0_qualification=fx0,
    )
    payload = {
        "schema_version": "membind.paper-eval-v3.s5-live-preflight-artifact.v1",
        "stage": "S5_LIVE_PREFLIGHT",
        "verdict": "PASS",
        "method": method,
        "run_id": run["run_id"],
        "namespace": run["namespace"],
        "workload": evaluation["workload"],
        "production_identity_qualification": evaluation[
            "production_identity_qualification"
        ],
        "current_stage_pointer": evaluation["current_stage_pointer"],
        "predecessor": evaluation["predecessor"],
        "fx0_qualification": evaluation["fx0_qualification"],
        "evaluation": evaluation,
        "evaluation_sha256": payload_sha256(evaluation),
        "source_sha256": {
            "contract": "d" * 64,
            "production": "e" * 64,
            "contract_test": "f" * 64,
            "production_test": "a" * 64,
        },
        "authority": evaluation["authority"],
    }
    return verify_s5_live_preflight(
        finalize_envelope(
            payload=payload,
            protocol_version="paper-eval-v3",
            git_commit="deadbeef",
            run_id=str(run["run_id"]),
        )
    )


def _predecessor(method: str) -> dict[str, object] | None:
    if method == "A0":
        return None
    predecessor = "A0" if method == "P*" else "P*"
    return {
        "method": predecessor,
        "result_file_sha256": "0" * 64,
        "result_payload_sha256": "1" * 64,
        "verdict": "PASS" if predecessor == "A0" else "SCIENTIFIC_OUTCOME_COMPLETE",
    }


def _build(method: str) -> dict[str, object]:
    qualification = _qualification(method)
    preflight = _preflight(method)
    preflight_fx0 = preflight["payload"]["fx0_qualification"]
    return build_s5_live_authority(
        method=method,
        run=_run(method),
        production_identity_qualification=qualification,
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        preflight=preflight,
        preflight_file_sha256="3" * 64,
        current_stage_pointer_sha256=POINTER_FILE_SHA256,
        predecessor=_predecessor(method),
        fx0_qualification=(
            {
                "qualification_file_sha256": qualification["payload"][
                    "mstar_fx0"
                ]["qualification_file_sha256"],
                "qualification_payload_sha256": preflight_fx0[
                    "qualification_payload_sha256"
                ],
                "production_parity_payload_sha256": qualification["payload"][
                    "mstar_fx0"
                ]["fx0_artifact_payload_sha256"],
                "verdict": "PRODUCTION_PATH_EXACT_PARITY_PASS",
            }
            if method == "M*"
            else None
        ),
        source_sha256={
            "authority": "6" * 64,
            "controller": "7" * 64,
            "result_verifier": "8" * 64,
            "test": "9" * 64,
        },
    )


@pytest.mark.parametrize("method", ["A0", "P*", "M*"])
def test_authority_is_method_scoped_single_use_and_preformal(method: str) -> None:
    artifact = verify_s5_live_authority(_build(method))
    payload = artifact["payload"]

    assert payload["method"] == method
    assert payload["run"] == _run(method)
    assert payload["authority"] == {
        "single_use": True,
        "model_call_authorized": True,
        "embedding_call_authorized": True,
        "neo4j_read_authorized": True,
        "neo4j_mutation_authorized": True,
        "s5_method_smoke_authorized": True,
        "next_method_authorized": False,
        "current_stage_pointer_update_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
    }
    assert (payload["fx0_qualification"] is not None) is (method == "M*")


@pytest.mark.parametrize(
    ("method", "mutation"),
    [
        ("A0", lambda value: value["run"].update(configured_concurrency=2)),
        ("A0", lambda value: value["run"].update(history_id="different")),
        ("P*", lambda value: value.update(predecessor=None)),
        ("M*", lambda value: value.update(fx0_qualification=None)),
        ("M*", lambda value: value["run"].update(namespace="pev3-s5-a0-wrong")),
    ],
)
def test_authority_rejects_method_order_scope_and_fx0_drift(
    method: str, mutation
) -> None:
    artifact = _build(method)
    mutation(artifact["payload"])
    with pytest.raises(ValueError):
        verify_s5_live_authority(artifact)


def test_authority_rejects_preflight_identity_and_pointer_drift() -> None:
    qualification = _qualification("A0")
    preflight = _preflight("A0")
    preflight["payload"]["production_identity_qualification"][
        "production_identity_sha256"
    ] = "0" * 64
    with pytest.raises(ValueError, match="preflight"):
        build_s5_live_authority(
            method="A0",
            run=_run("A0"),
            production_identity_qualification=qualification,
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            preflight=preflight,
            preflight_file_sha256="3" * 64,
            current_stage_pointer_sha256=POINTER_FILE_SHA256,
            predecessor=None,
            fx0_qualification=None,
            source_sha256={
                "authority": "6" * 64,
                "controller": "7" * 64,
                "result_verifier": "8" * 64,
                "test": "9" * 64,
            },
        )


def test_authority_rejects_source_manifest_drift_from_preflight() -> None:
    qualification = _qualification("A0")
    run = _run("A0")
    run["source_manifest_sha256"] = "c" * 64

    with pytest.raises(ValueError, match="preflight_binding_mismatch"):
        build_s5_live_authority(
            method="A0",
            run=run,
            production_identity_qualification=qualification,
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            preflight=_preflight("A0"),
            preflight_file_sha256="3" * 64,
            current_stage_pointer_sha256=POINTER_FILE_SHA256,
            predecessor=None,
            fx0_qualification=None,
            source_sha256={
                "authority": "6" * 64,
                "controller": "7" * 64,
                "result_verifier": "8" * 64,
                "test": "9" * 64,
            },
        )


def test_authority_consumes_exact_canonical_preflight_finalizer_artifact(
    tmp_path: Path,
) -> None:
    qualification = _qualification("A0")
    pointer = _pointer()
    run = _run("A0")
    evaluation = evaluate_s5_live_preflight(
        method="A0",
        run_id=str(run["run_id"]),
        namespace=str(run["namespace"]),
        episode_source_sha256s=SOURCE_SHA256S,
        observations={
            "construction": {
                "served_model_id": "qwen3-32b-fp8",
                "vllm_version": "0.26.0",
                "max_model_len": 65536,
            },
            "embedding": {"served_model_id": "qwen3-embedding-0.6b"},
            "neo4j_connectivity": True,
            "namespace": run["namespace"],
            "namespace_state": {"node_count": 0, "relationship_count": 0},
        },
        production_identity_qualification=qualification,
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        current_stage_pointer=pointer,
        current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
    )
    preflight_path = tmp_path / "S5_A0_PREFLIGHT.json"
    preflight = finalize_s5_live_preflight(
        output_path=preflight_path,
        evaluation=evaluation,
        source_sha256={
            "contract": "1" * 64,
            "production": "2" * 64,
            "contract_test": "3" * 64,
            "production_test": "4" * 64,
        },
        git_commit="deadbeef",
    )

    authority = build_s5_live_authority(
        method="A0",
        run=run,
        production_identity_qualification=qualification,
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        preflight=preflight,
        preflight_file_sha256=sha256_file(preflight_path),
        current_stage_pointer_sha256=POINTER_FILE_SHA256,
        predecessor=None,
        fx0_qualification=None,
        source_sha256={
            "authority": "6" * 64,
            "controller": "7" * 64,
            "result_verifier": "8" * 64,
            "test": "9" * 64,
        },
    )

    assert authority["payload"]["preflight_payload_sha256"] == preflight[
        "payload_sha256"
    ]


def test_finalization_and_consumption_are_exclusive(tmp_path: Path) -> None:
    authority_path = tmp_path / "S5_A0_AUTHORIZATION.json"
    authority = finalize_s5_live_authority(
        output_path=authority_path,
        authority=_build("A0")["payload"],
        git_commit="deadbeef",
        run_id="s5-a0-20260816-001-authority",
    )
    assert verify_s5_live_authority(authority) == authority
    with pytest.raises(FileExistsError):
        finalize_s5_live_authority(
            output_path=authority_path,
            authority=_build("A0")["payload"],
            git_commit="deadbeef",
            run_id="s5-a0-20260816-001-authority",
        )

    consumption_path = tmp_path / "S5_A0_AUTHORIZATION_CONSUMPTION.json"
    consumption = consume_s5_live_authority(
        authority=authority,
        authority_file_sha256=sha256_file(authority_path),
        output_path=consumption_path,
        git_commit="deadbeef",
        run_id="s5-a0-20260816-001-consumption",
    )
    assert verify_s5_live_authority_consumption(consumption) == consumption
    assert consumption["payload"]["further_live_authority"] is False
    with pytest.raises(FileExistsError):
        consume_s5_live_authority(
            authority=authority,
            authority_file_sha256=sha256_file(authority_path),
            output_path=consumption_path,
            git_commit="deadbeef",
            run_id="s5-a0-20260816-001-consumption",
        )


def test_verifier_rejects_scope_hash_and_private_field_tamper() -> None:
    authority = _build("A0")
    for mutation in (
        lambda value: value["payload"]["authority"].update(
            formal_execution_authorized=True
        ),
        lambda value: value["payload"].update(api_key="secret"),
        lambda value: value.update(payload_sha256=SHA),
    ):
        altered = copy.deepcopy(authority)
        mutation(altered)
        with pytest.raises(ValueError):
            verify_s5_live_authority(altered)
