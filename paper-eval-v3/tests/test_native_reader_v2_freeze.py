"""Freeze contracts for the common Reader-v2 evaluation policy."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.native_reader_v2_freeze import (
    ReaderV2FreezeError,
    build_reader_v2_freeze,
    finalize_reader_v2_freeze,
    verify_reader_v2_freeze,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
RUN = NATIVE / "runs/native-reader-v2-canary-20260814-001"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sources() -> dict[str, str]:
    return {
        "workplan": "1" * 64,
        "reader_source": "2" * 64,
        "contract_file": "3" * 64,
        "qualification_file": "4" * 64,
        "result_file": sha256_file(RUN / "NATIVE_READER_V2_RESULT.json"),
        "postlive_tests": "5" * 64,
    }


def _build(*, qa_accuracy: float | None = None) -> dict:
    contract = _load(NATIVE / "NATIVE_READER_V2_CONTRACT.json")
    qualification = _load(NATIVE / "NATIVE_READER_V2_OFFLINE_QUALIFICATION.json")
    result = _load(RUN / "NATIVE_READER_V2_RESULT.json")
    if qa_accuracy is not None:
        result = copy.deepcopy(result)
        result["payload"]["classification"]["qa_accuracy_diagnostic"] = qa_accuracy
        result["payload"]["result"]["qa_accuracy"] = qa_accuracy
        result["payload"]["result"]["reference_sanity_status"] = (
            "PASS" if qa_accuracy == 1.0 else "REVIEW_REQUIRED"
        )
        result["payload_sha256"] = payload_sha256(result["payload"])
    return build_reader_v2_freeze(
        contract=contract,
        qualification_payload_sha256=qualification["payload_sha256"],
        result=result,
        result_file_sha256=sha256_file(RUN / "NATIVE_READER_V2_RESULT.json"),
        judge_config_sha256=result["payload"]["classification"][
            "judge_config_sha256"
        ],
        source_sha256=_sources(),
        git_commit="deadbeef",
        run_id="native-reader-v2-freeze-test-001",
    )


@pytest.mark.parametrize("qa_accuracy", [0.0, 1.0])
def test_freeze_is_independent_of_canary_qa_and_binds_all_methods(
    qa_accuracy: float,
) -> None:
    freeze = verify_reader_v2_freeze(_build(qa_accuracy=qa_accuracy))
    payload = freeze["payload"]

    assert payload["status"] == "PASS"
    assert payload["qa_accuracy_diagnostic"] == qa_accuracy
    assert payload["quality_gate_used"] is False
    assert payload["qualification_scope"] == "ADAPTER_COMPATIBILITY_ONLY"
    assert payload["native_quality_mergeable"] is False
    assert payload["pilot_or_final_mergeable"] is False
    assert len(set(payload["method_reader_bindings"].values())) == 1
    assert len(set(payload["method_judge_bindings"].values())) == 1
    assert set(payload["method_reader_bindings"]) == {"U0", "A0", "P*", "M*"}
    assert payload["s3_configuration_update_authorized"] is True
    assert payload["pilot_execution_authorized"] is False
    assert payload["s3_authorized"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "compatibility",
        "quality_gate",
        "quality_merge",
        "reader_binding",
        "pilot_authority",
        "s3",
    ],
)
def test_rejects_result_or_common_binding_drift(mutation: str) -> None:
    freeze = copy.deepcopy(_build())
    if mutation == "compatibility":
        freeze["payload"]["compatibility_status"] = "FAIL"
    elif mutation == "quality_gate":
        freeze["payload"]["quality_gate_used"] = True
    elif mutation == "quality_merge":
        freeze["payload"]["native_quality_mergeable"] = True
    elif mutation == "reader_binding":
        freeze["payload"]["method_reader_bindings"]["M*"] = "0" * 64
    elif mutation == "pilot_authority":
        freeze["payload"]["pilot_execution_authorized"] = True
    else:
        freeze["payload"]["s3_authorized"] = True

    with pytest.raises(ReaderV2FreezeError):
        verify_reader_v2_freeze(freeze)


def test_rejects_tampering_and_exclusive_finalization(tmp_path: Path) -> None:
    freeze = _build()
    target = tmp_path / "NATIVE_READER_V2_FREEZE.json"

    finalized = finalize_reader_v2_freeze(path=target, artifact=freeze)

    assert target.is_file()
    assert finalized == verify_reader_v2_freeze(_load(target))
    original = target.read_bytes()
    with pytest.raises(ReaderV2FreezeError, match="already exists"):
        finalize_reader_v2_freeze(path=target, artifact=freeze)
    assert target.read_bytes() == original

    tampered = copy.deepcopy(freeze)
    tampered["payload"]["raw_output"] = "private model output"
    with pytest.raises(ReaderV2FreezeError):
        verify_reader_v2_freeze(tampered)


def test_real_finalized_freeze_remains_valid_and_non_authorizing_for_pilot() -> None:
    freeze = verify_reader_v2_freeze(
        _load(NATIVE / "NATIVE_READER_V2_FREEZE.json")
    )

    assert freeze["payload"]["compatibility_status"] == "PASS"
    assert freeze["payload"]["quality_gate_used"] is False
    assert freeze["payload"]["pilot_execution_authorized"] is False
    assert freeze["payload"]["s3_configuration_update_authorized"] is True
