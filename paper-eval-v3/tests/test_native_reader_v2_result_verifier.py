"""Offline verification tests for the sealed Reader-v2 terminal chain."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.native_reader_v2_result_verifier import (
    ReaderV2ResultPaths,
    ReaderV2ResultVerificationError,
    verify_native_reader_v2_result,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
RUN_ID = "native-reader-v2-canary-20260814-001"


def _paths(root: Path = PROJECT) -> ReaderV2ResultPaths:
    native = root / "artifacts/paper_eval/native"
    run = native / "runs" / RUN_ID
    return ReaderV2ResultPaths(
        contract=native / "NATIVE_READER_V2_CONTRACT.json",
        qualification=native / "NATIVE_READER_V2_OFFLINE_QUALIFICATION.json",
        authorization=native / "NATIVE_READER_V2_AUTHORIZATION.json",
        consumption=run / "NATIVE_READER_V2_AUTHORIZATION_CONSUMPTION.json",
        events=run / "events.jsonl",
        checkpoint=run / "checkpoint.json",
        result=run / "NATIVE_READER_V2_RESULT.json",
        failure=run / "NATIVE_READER_V2_FAILURE.json",
    )


def _copy_chain(tmp_path: Path) -> ReaderV2ResultPaths:
    source = _paths()
    root = tmp_path / "paper-eval-v3"
    target = _paths(root)
    for name in (
        "contract",
        "qualification",
        "authorization",
        "consumption",
        "events",
        "checkpoint",
        "result",
    ):
        source_path = getattr(source, name)
        target_path = getattr(target, name)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
    return target


def _reseal_envelope(path: Path, mutate) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value["payload"])
    value["payload_sha256"] = payload_sha256(value["payload"])
    atomic_write_json(path, value)


def test_verifies_real_reader_v2_terminal_chain() -> None:
    verified = verify_native_reader_v2_result(_paths())

    assert verified.run_id == RUN_ID
    assert verified.status == "PASS"
    assert verified.compatibility_status == "PASS"
    assert verified.evidence_recall_at_10 == 1.0
    assert verified.qa_accuracy_diagnostic == 1.0
    assert verified.gold_ranks == (1, 2)
    assert verified.reader_prompt_tokens == 26205
    assert verified.reader_completion_tokens == 131
    assert verified.reader_truncation_count == 0
    assert verified.judge_parse_status == "YES"
    assert verified.qualification_mergeable is True
    assert verified.native_quality_mergeable is False
    assert verified.pilot_or_final_mergeable is False
    assert verified.s3_authorized is False


@pytest.mark.parametrize(
    ("surface", "field", "value"),
    [
        ("classification", "quality_gate_used", True),
        ("classification", "qa_accuracy_diagnostic", 0.0),
        ("classification", "reader_prompt_sha256", "0" * 64),
        ("classification", "judge_output_sha256", "0" * 64),
        ("result", "evidence_recall_at_10", 0.0),
        ("result", "qa_accuracy", 0.0),
        ("result", "s3_authorized", True),
        ("root", "native_quality_mergeable", True),
    ],
)
def test_rejects_resealed_result_semantic_drift(
    tmp_path: Path, surface: str, field: str, value: object
) -> None:
    paths = _copy_chain(tmp_path)

    def mutate(payload):
        target = payload if surface == "root" else payload[surface]
        target[field] = value

    _reseal_envelope(paths.result, mutate)

    with pytest.raises(ReaderV2ResultVerificationError):
        verify_native_reader_v2_result(paths)


def test_rejects_event_or_checkpoint_tamper(tmp_path: Path) -> None:
    paths = _copy_chain(tmp_path)
    lines = paths.events.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[2])
    event["event_type"] = "judge_complete"
    event["payload_sha256"] = payload_sha256(
        {key: value for key, value in event.items() if key != "payload_sha256"}
    )
    lines[2] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    paths.events.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ReaderV2ResultVerificationError, match="event"):
        verify_native_reader_v2_result(paths)

    paths = _copy_chain(tmp_path / "checkpoint")
    checkpoint = json.loads(paths.checkpoint.read_text(encoding="utf-8"))
    checkpoint["event_count"] = 4
    checkpoint["payload_sha256"] = payload_sha256(
        {key: value for key, value in checkpoint.items() if key != "payload_sha256"}
    )
    atomic_write_json(paths.checkpoint, checkpoint)
    with pytest.raises(ReaderV2ResultVerificationError, match="checkpoint"):
        verify_native_reader_v2_result(paths)


def test_requires_exactly_one_terminal_artifact(tmp_path: Path) -> None:
    paths = _copy_chain(tmp_path)
    failure = copy.deepcopy(json.loads(paths.result.read_text(encoding="utf-8")))
    atomic_write_json(paths.failure, failure)

    with pytest.raises(ReaderV2ResultVerificationError, match="terminal"):
        verify_native_reader_v2_result(paths)


def test_rejects_bound_contract_or_authorization_drift(tmp_path: Path) -> None:
    paths = _copy_chain(tmp_path)
    contract = json.loads(paths.contract.read_text(encoding="utf-8"))
    contract["reader"]["max_tokens"] = 500
    atomic_write_json(paths.contract, contract)

    with pytest.raises(ReaderV2ResultVerificationError, match="contract"):
        verify_native_reader_v2_result(paths)

    paths = _copy_chain(tmp_path / "authorization")
    authorization = json.loads(paths.authorization.read_text(encoding="utf-8"))
    authorization["payload"]["limits"]["reader_requests"] = 11
    authorization["payload_sha256"] = payload_sha256(authorization["payload"])
    atomic_write_json(paths.authorization, authorization)
    with pytest.raises(ReaderV2ResultVerificationError, match="authorization"):
        verify_native_reader_v2_result(paths)
