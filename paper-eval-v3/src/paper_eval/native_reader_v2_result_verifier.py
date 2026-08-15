"""Independent offline verification of the sealed Reader-v2 live chain."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256, sha256_file
from .native_reader_v2_authority import (
    READER_V2_CONSUMPTION_SCHEMA,
    verify_reader_v2_authorization,
    verify_reader_v2_offline_qualification,
)
from .native_reader_v2_controller import (
    READER_V2_CHECKPOINT_SCHEMA,
    READER_V2_EVENT_SCHEMA,
    READER_V2_RESULT_SCHEMA,
)
from .native_reader_v2_qualification import verify_reader_v2_contract


@dataclass(frozen=True)
class ReaderV2ResultPaths:
    contract: Path
    qualification: Path
    authorization: Path
    consumption: Path
    events: Path
    checkpoint: Path
    result: Path
    failure: Path


@dataclass(frozen=True)
class VerifiedReaderV2Result:
    run_id: str
    status: str
    compatibility_status: str
    evidence_recall_at_10: float
    qa_accuracy_diagnostic: float
    gold_ranks: tuple[int, ...]
    reader_prompt_tokens: int
    reader_completion_tokens: int
    reader_truncation_count: int
    judge_parse_status: str
    qualification_mergeable: bool
    native_quality_mergeable: bool
    pilot_or_final_mergeable: bool
    s3_authorized: bool


class ReaderV2ResultVerificationError(ValueError):
    """The Reader-v2 terminal chain is missing, inconsistent, or tampered."""


def _load(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReaderV2ResultVerificationError(f"{label} is unreadable") from None
    if not isinstance(value, dict):
        raise ReaderV2ResultVerificationError(f"{label} is invalid")
    return value


def _envelope(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReaderV2ResultVerificationError(f"{label} envelope is invalid")
    artifact = dict(value)
    payload = artifact.get("payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or not isinstance(payload, Mapping)
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ReaderV2ResultVerificationError(f"{label} envelope seal drift")
    artifact["payload"] = dict(payload)
    return artifact


def _verify_consumption(
    value: object,
    *,
    authorization: Mapping[str, Any],
    authorization_sha256: str,
) -> dict[str, Any]:
    consumption = _envelope(value, label="consumption")
    payload = consumption["payload"]
    expected = {
        "schema_version",
        "stage",
        "status",
        "run_id",
        "history_id",
        "namespace",
        "authorization_sha256",
        "authorization_payload_sha256",
        "live_io_performed_at_consumption",
        "quality_gate_used",
        "s3_authorized",
    }
    auth_payload = authorization["payload"]
    if (
        set(payload) != expected
        or payload.get("schema_version") != READER_V2_CONSUMPTION_SCHEMA
        or payload.get("status") != "CONSUMED_BEFORE_LIVE_IO"
        or payload.get("run_id") != auth_payload.get("run_id")
        or payload.get("history_id") != auth_payload.get("history_id")
        or payload.get("namespace") != auth_payload.get("namespace")
        or payload.get("authorization_sha256") != authorization_sha256
        or payload.get("authorization_payload_sha256")
        != authorization.get("payload_sha256")
        or payload.get("live_io_performed_at_consumption") is not False
        or payload.get("quality_gate_used") is not False
        or payload.get("s3_authorized") is not False
    ):
        raise ReaderV2ResultVerificationError("consumption binding drift")
    return consumption


def _verify_events(path: Path, *, run_id: str, history_id: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines if line]
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReaderV2ResultVerificationError("event log is unreadable") from None
    expected_types = [
        "authorization_consumed",
        "retrieval_complete",
        "reader_complete",
        "judge_complete",
        "terminal_success",
    ]
    if len(events) != len(expected_types):
        raise ReaderV2ResultVerificationError("event count drift")
    for sequence, (event, expected_type) in enumerate(zip(events, expected_types, strict=True)):
        if not isinstance(event, dict):
            raise ReaderV2ResultVerificationError("event shape drift")
        stored = event.get("payload_sha256")
        body = {key: value for key, value in event.items() if key != "payload_sha256"}
        if (
            event.get("schema_version") != READER_V2_EVENT_SCHEMA
            or event.get("run_id") != run_id
            or event.get("history_id") != history_id
            or event.get("event_sequence") != sequence
            or event.get("event_type") != expected_type
            or stored != payload_sha256(body)
        ):
            raise ReaderV2ResultVerificationError("event identity or hash drift")
    return events


def _verify_checkpoint(
    value: object,
    *,
    run_id: str,
    history_id: str,
    events_sha256: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReaderV2ResultVerificationError("checkpoint is invalid")
    checkpoint = dict(value)
    stored = checkpoint.get("payload_sha256")
    body = {key: child for key, child in checkpoint.items() if key != "payload_sha256"}
    if (
        checkpoint.get("schema_version") != READER_V2_CHECKPOINT_SCHEMA
        or checkpoint.get("run_id") != run_id
        or checkpoint.get("history_id") != history_id
        or checkpoint.get("status") != "completed"
        or checkpoint.get("completed_stages")
        != ["retrieval_complete", "reader_complete", "judge_complete"]
        or checkpoint.get("event_count") != 5
        or checkpoint.get("events_sha256") != events_sha256
        or checkpoint.get("error_class") is not None
        or stored != payload_sha256(body)
    ):
        raise ReaderV2ResultVerificationError("checkpoint state or hash drift")
    return checkpoint


def verify_native_reader_v2_result(
    paths: ReaderV2ResultPaths,
) -> VerifiedReaderV2Result:
    """Cross-check every sealed file and return a small typed result view."""

    if paths.result.exists() == paths.failure.exists():
        raise ReaderV2ResultVerificationError(
            "exactly one Reader-v2 terminal artifact is required"
        )
    if paths.failure.exists():
        raise ReaderV2ResultVerificationError("Reader-v2 terminal is a failure")
    try:
        contract = verify_reader_v2_contract(_load(paths.contract, label="contract"))
    except ValueError:
        raise ReaderV2ResultVerificationError("contract validation failed") from None
    try:
        qualification = verify_reader_v2_offline_qualification(
            _load(paths.qualification, label="qualification")
        )
    except ValueError:
        raise ReaderV2ResultVerificationError("qualification validation failed") from None
    try:
        authorization = verify_reader_v2_authorization(
            _load(paths.authorization, label="authorization")
        )
    except ValueError:
        raise ReaderV2ResultVerificationError("authorization validation failed") from None
    auth_payload = authorization["payload"]
    if (
        sha256_file(paths.contract) != auth_payload.get("contract_file_sha256")
        or contract.get("contract_sha256") != auth_payload.get("contract_sha256")
        or sha256_file(paths.qualification)
        != auth_payload.get("qualification_file_sha256")
        or qualification.get("payload_sha256")
        != auth_payload.get("qualification_payload_sha256")
    ):
        raise ReaderV2ResultVerificationError("contract/qualification binding drift")
    consumption = _verify_consumption(
        _load(paths.consumption, label="consumption"),
        authorization=authorization,
        authorization_sha256=sha256_file(paths.authorization),
    )
    result = _envelope(_load(paths.result, label="result"), label="result")
    payload = result["payload"]
    nested = payload.get("result")
    classification = payload.get("classification")
    if not isinstance(nested, Mapping) or not isinstance(classification, Mapping):
        raise ReaderV2ResultVerificationError("result surfaces are incomplete")
    reader = nested.get("reader")
    judge = nested.get("judge")
    if not isinstance(reader, Mapping) or not isinstance(judge, Mapping):
        raise ReaderV2ResultVerificationError("Reader/Judge result is incomplete")
    run_id = str(result.get("run_id", ""))
    history_id = str(nested.get("history_id", ""))
    events_sha = sha256_file(paths.events)
    _verify_events(paths.events, run_id=run_id, history_id=history_id)
    checkpoint = _verify_checkpoint(
        _load(paths.checkpoint, label="checkpoint"),
        run_id=run_id,
        history_id=history_id,
        events_sha256=events_sha,
    )

    qa = nested.get("qa_accuracy")
    recall = nested.get("evidence_recall_at_10")
    expected_parse = "YES" if qa == 1.0 else "NO"
    if (
        payload.get("schema_version") != READER_V2_RESULT_SCHEMA
        or payload.get("status") != "PASS"
        or payload.get("compatibility_status") != "PASS"
        or classification.get("compatibility_status") != "PASS"
        or payload.get("quality_gate_used") is not False
        or classification.get("quality_gate_used") is not False
        or payload.get("native_quality_mergeable") is not False
        or classification.get("native_quality_mergeable") is not False
        or payload.get("pilot_or_final_mergeable") is not False
        or classification.get("pilot_or_final_mergeable") is not False
        or payload.get("s3_authorized") is not False
        or nested.get("s3_authorized") is not False
        or classification.get("s3_authorized") is not False
        or qa not in {0.0, 1.0}
        or classification.get("qa_accuracy_diagnostic") != qa
        or not isinstance(recall, (int, float))
        or classification.get("evidence_recall_at_10_diagnostic") != recall
        or classification.get("reader_config_sha256") != reader.get("config_sha256")
        or classification.get("reader_prompt_sha256") != reader.get("prompt_sha256")
        or classification.get("reader_output_sha256") != reader.get("output_sha256")
        or classification.get("judge_config_sha256") != judge.get("config_sha256")
        or classification.get("judge_output_sha256") != judge.get("output_sha256")
        or judge.get("parse_status") != expected_parse
        or classification.get("counters") != nested.get("counters")
        or payload.get("authorization_sha256") != sha256_file(paths.authorization)
        or payload.get("authorization_payload_sha256")
        != authorization.get("payload_sha256")
        or payload.get("consumption_sha256") != sha256_file(paths.consumption)
        or payload.get("qualification_file_sha256")
        != sha256_file(paths.qualification)
        or payload.get("contract_file_sha256") != sha256_file(paths.contract)
        or payload.get("events_sha256") != events_sha
        or payload.get("checkpoint_sha256") != sha256_file(paths.checkpoint)
        or checkpoint.get("events_sha256") != events_sha
        or consumption.get("payload", {}).get("run_id") != run_id
    ):
        raise ReaderV2ResultVerificationError("result semantic or hash binding drift")

    gold_ranks = nested.get("gold_ranks")
    if not isinstance(gold_ranks, list) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in gold_ranks
    ):
        raise ReaderV2ResultVerificationError("result gold ranks are invalid")
    return VerifiedReaderV2Result(
        run_id=run_id,
        status="PASS",
        compatibility_status="PASS",
        evidence_recall_at_10=float(recall),
        qa_accuracy_diagnostic=float(qa),
        gold_ranks=tuple(gold_ranks),
        reader_prompt_tokens=int(reader.get("prompt_tokens")),
        reader_completion_tokens=int(reader.get("completion_tokens")),
        reader_truncation_count=int(reader.get("truncation_count")),
        judge_parse_status=str(judge.get("parse_status")),
        qualification_mergeable=payload.get("qualification_mergeable") is True,
        native_quality_mergeable=False,
        pilot_or_final_mergeable=False,
        s3_authorized=False,
    )
