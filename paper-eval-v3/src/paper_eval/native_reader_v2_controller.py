"""Durable one-shot controller for the Native Reader-v2 canary."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    finalize_envelope,
    payload_sha256,
    sha256_file,
)
from .native_reader_v2_authority import (
    consume_reader_v2_authorization,
    verify_reader_v2_authorization,
    verify_reader_v2_offline_qualification,
)
from .native_reader_v2_qualification import (
    classify_reader_v2_canary,
    verify_reader_v2_contract,
)
from .s2_completion_chain import BoundedCompletionResult


READER_V2_RESULT_SCHEMA = "membind.paper-eval-v3.native-reader-v2-result.v1"
READER_V2_FAILURE_SCHEMA = "membind.paper-eval-v3.native-reader-v2-failure.v1"
READER_V2_EVENT_SCHEMA = "membind.paper-eval-v3.native-reader-v2-event.v1"
READER_V2_CHECKPOINT_SCHEMA = (
    "membind.paper-eval-v3.native-reader-v2-checkpoint.v1"
)


@dataclass(frozen=True)
class ReaderV2LiveExecutor:
    execute: Callable[
        [Callable[[str, dict[str, Any]], None]],
        Awaitable[BoundedCompletionResult] | BoundedCompletionResult,
    ]
    close: Callable[[], Awaitable[Any] | Any]


@dataclass(frozen=True)
class ReaderV2ControllerDependencies:
    build_live: Callable[[], ReaderV2LiveExecutor]


@dataclass(frozen=True)
class ReaderV2ControllerOutcome:
    status: str
    run_id: str
    artifact_path: Path


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"{label} is unreadable") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} is invalid")
    return value


def _safe_error_class(error: BaseException) -> str:
    return type(error).__name__


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _execute_and_close(
    executor: ReaderV2LiveExecutor,
    checkpoint: Callable[[str, dict[str, Any]], None],
) -> BoundedCompletionResult:
    try:
        result = await _await(executor.execute(checkpoint))
        if not isinstance(result, BoundedCompletionResult):
            raise RuntimeError("Reader-v2 executor returned an invalid result")
        return result
    finally:
        await _await(executor.close())


class _DurableReaderV2Attempt:
    def __init__(self, *, run_dir: Path, run_id: str, history_id: str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.history_id = history_id
        self.events_path = self.run_dir / "events.jsonl"
        self.checkpoint_path = self.run_dir / "checkpoint.json"
        self.event_count = 0
        self.completed_stages: list[str] = []

    def event(self, event_type: str, evidence: Mapping[str, Any]) -> None:
        if event_type in {
            "retrieval_complete",
            "reader_complete",
            "judge_complete",
        }:
            expected = [
                "retrieval_complete",
                "reader_complete",
                "judge_complete",
            ][len(self.completed_stages)]
            if event_type != expected:
                raise RuntimeError("Reader-v2 checkpoint order drift")
            self.completed_stages.append(event_type)
        body = {
            "schema_version": READER_V2_EVENT_SCHEMA,
            "run_id": self.run_id,
            "history_id": self.history_id,
            "event_sequence": self.event_count,
            "event_type": event_type,
            "evidence": dict(evidence),
        }
        body["payload_sha256"] = payload_sha256(body)
        append_jsonl_durable(self.events_path, body)
        self.event_count += 1
        self._checkpoint(status="running", error_class=None)

    def _checkpoint(self, *, status: str, error_class: str | None) -> None:
        body = {
            "schema_version": READER_V2_CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "history_id": self.history_id,
            "status": status,
            "completed_stages": list(self.completed_stages),
            "event_count": self.event_count,
            "events_sha256": sha256_file(self.events_path),
            "error_class": error_class,
        }
        body["payload_sha256"] = payload_sha256(body)
        atomic_write_json(self.checkpoint_path, body)

    def terminal(self, *, success: bool, error_class: str | None = None) -> None:
        self.event(
            "terminal_success" if success else "terminal_failure",
            {"success": success, "error_class": error_class},
        )
        self._checkpoint(
            status="completed" if success else "failed_stopped",
            error_class=error_class,
        )


def _finalize_result(
    *,
    path: Path,
    result: BoundedCompletionResult,
    classification: Mapping[str, Any],
    authorization: Mapping[str, Any],
    authorization_file_sha256: str,
    consumption_file_sha256: str,
    qualification_file_sha256: str,
    contract_file_sha256: str,
    attempt: _DurableReaderV2Attempt,
) -> dict[str, Any]:
    payload = {
        "schema_version": READER_V2_RESULT_SCHEMA,
        "stage": "NATIVE-READER-V2-CANARY",
        "method": "U0-C2-CANARY-GRAPH",
        "status": "PASS",
        "compatibility_status": "PASS",
        "qualification_scope": "ADAPTER_COMPATIBILITY_ONLY",
        "quality_gate_used": False,
        "classification": dict(classification),
        "result": result.to_artifact(),
        "authorization_sha256": authorization_file_sha256,
        "authorization_payload_sha256": authorization["payload_sha256"],
        "consumption_sha256": consumption_file_sha256,
        "qualification_file_sha256": qualification_file_sha256,
        "contract_file_sha256": contract_file_sha256,
        "events_sha256": sha256_file(attempt.events_path),
        "checkpoint_sha256": sha256_file(attempt.checkpoint_path),
        "qualification_mergeable": True,
        "native_quality_mergeable": False,
        "pilot_or_final_mergeable": False,
        "s3_authorized": False,
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=str(authorization["git_commit"]),
        run_id=str(authorization["run_id"]),
    )
    if path.exists():
        raise ValueError("Reader-v2 result already exists")
    atomic_write_json(path, artifact)
    return artifact


def _finalize_failure(
    *,
    path: Path,
    error: BaseException,
    authorization: Mapping[str, Any],
    authorization_file_sha256: str,
    consumption_file_sha256: str,
    attempt: _DurableReaderV2Attempt,
) -> dict[str, Any]:
    payload = {
        "schema_version": READER_V2_FAILURE_SCHEMA,
        "stage": "NATIVE-READER-V2-CANARY",
        "status": "FAILED_STOPPED",
        "run_id": authorization["run_id"],
        "history_id": authorization["payload"]["history_id"],
        "error_class": _safe_error_class(error),
        "completed_stages": list(attempt.completed_stages),
        "event_count": attempt.event_count,
        "events_sha256": sha256_file(attempt.events_path),
        "checkpoint_sha256": sha256_file(attempt.checkpoint_path),
        "authorization_sha256": authorization_file_sha256,
        "authorization_payload_sha256": authorization["payload_sha256"],
        "consumption_sha256": consumption_file_sha256,
        "result_mergeable": False,
        "quality_conclusion": "NOT_PRODUCED",
        "automatic_retry": False,
        "s3_authorized": False,
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=str(authorization["git_commit"]),
        run_id=str(authorization["run_id"]),
    )
    if path.exists():
        raise ValueError("Reader-v2 failure already exists")
    atomic_write_json(path, artifact)
    return artifact


def run_reader_v2_controller(
    *,
    authorization_path: Path,
    qualification_path: Path,
    contract_path: Path,
    dependencies: ReaderV2ControllerDependencies,
) -> ReaderV2ControllerOutcome:
    """Validate bindings, consume authority, then issue the bounded live chain."""

    authorization_file = Path(authorization_path)
    authorization = verify_reader_v2_authorization(
        _load_json(authorization_file, label="Reader-v2 authorization")
    )
    auth_payload = authorization["payload"]
    qualification_file = Path(qualification_path)
    qualification = verify_reader_v2_offline_qualification(
        _load_json(qualification_file, label="Reader-v2 qualification")
    )
    contract_file = Path(contract_path)
    try:
        contract = verify_reader_v2_contract(
            _load_json(contract_file, label="Reader-v2 contract")
        )
    except ValueError:
        raise ValueError("Reader-v2 contract validation failed") from None
    if (
        sha256_file(qualification_file)
        != auth_payload["qualification_file_sha256"]
        or qualification["payload_sha256"]
        != auth_payload["qualification_payload_sha256"]
        or sha256_file(contract_file) != auth_payload["contract_file_sha256"]
        or contract["contract_sha256"] != auth_payload["contract_sha256"]
        or qualification["payload"]["contract_file_sha256"]
        != sha256_file(contract_file)
        or qualification["payload"]["contract_sha256"]
        != contract["contract_sha256"]
    ):
        raise ValueError("Reader-v2 contract or qualification hash drift")

    consumption_path = Path(auth_payload["consumption_path"])
    result_path = Path(auth_payload["result_path"])
    failure_path = Path(auth_payload["failure_path"])
    if result_path.exists() or failure_path.exists():
        raise ValueError("Reader-v2 terminal artifact already exists")
    authorization_hash = sha256_file(authorization_file)
    consumption = consume_reader_v2_authorization(
        authorization=authorization,
        authorization_file_sha256=authorization_hash,
        consumption_path=consumption_path,
    )
    consumption_hash = sha256_file(consumption_path)
    attempt = _DurableReaderV2Attempt(
        run_dir=consumption_path.parent,
        run_id=str(auth_payload["run_id"]),
        history_id=str(auth_payload["history_id"]),
    )
    attempt.event(
        "authorization_consumed",
        {
            "authorization_sha256": authorization_hash,
            "consumption_sha256": consumption_hash,
            "live_io_performed_at_consumption": consumption["payload"][
                "live_io_performed_at_consumption"
            ],
        },
    )
    try:
        live = dependencies.build_live()
        if not isinstance(live, ReaderV2LiveExecutor):
            raise RuntimeError("Reader-v2 live executor is invalid")
        result = asyncio.run(_execute_and_close(live, attempt.event))
        classification = classify_reader_v2_canary(result)
        attempt.terminal(success=True)
        _finalize_result(
            path=result_path,
            result=result,
            classification=classification,
            authorization=authorization,
            authorization_file_sha256=authorization_hash,
            consumption_file_sha256=consumption_hash,
            qualification_file_sha256=sha256_file(qualification_file),
            contract_file_sha256=sha256_file(contract_file),
            attempt=attempt,
        )
        return ReaderV2ControllerOutcome(
            status="PASS",
            run_id=str(auth_payload["run_id"]),
            artifact_path=result_path,
        )
    except Exception as error:
        attempt.terminal(success=False, error_class=_safe_error_class(error))
        _finalize_failure(
            path=failure_path,
            error=error,
            authorization=authorization,
            authorization_file_sha256=authorization_hash,
            consumption_file_sha256=consumption_hash,
            attempt=attempt,
        )
        return ReaderV2ControllerOutcome(
            status="FAILED_STOPPED",
            run_id=str(auth_payload["run_id"]),
            artifact_path=failure_path,
        )
