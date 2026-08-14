"""Durable one-shot controller for the bounded S2 completion chain."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
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
from .s2_completion_authority import (
    consume_completion_authorization,
    verify_completion_authorization,
    verify_completion_offline_qualification,
    verify_completion_policy_freeze,
)
from .s2_completion_chain import BoundedCompletionResult


RESULT_SCHEMA = "membind.paper-eval-v3.s2-completion-result.v1"
FAILURE_SCHEMA = "membind.paper-eval-v3.s2-completion-failure.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.s2-completion-event.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s2-completion-checkpoint.v1"


@dataclass(frozen=True)
class CompletionLiveExecutor:
    execute: Callable[
        [Callable[[str, dict[str, Any]], None]],
        Awaitable[BoundedCompletionResult] | BoundedCompletionResult,
    ]
    close: Callable[[], Awaitable[Any] | Any]


@dataclass(frozen=True)
class CompletionControllerDependencies:
    build_live: Callable[[], CompletionLiveExecutor]


@dataclass(frozen=True)
class CompletionControllerOutcome:
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


def _verify_identity(path: Path, *, file_hash: str, identity_hash: str) -> dict[str, Any]:
    if sha256_file(path) != file_hash:
        raise ValueError("adapter identity file hash drift")
    value = _load_json(path, label="adapter identity")
    stored = value.pop("identity_sha256", None)
    if stored != identity_hash or stored != payload_sha256(value):
        raise ValueError("adapter identity payload hash drift")
    return {**value, "identity_sha256": stored}


def _safe_error_class(error: BaseException) -> str:
    return type(error).__name__


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _execute_and_close(
    executor: CompletionLiveExecutor,
    checkpoint: Callable[[str, dict[str, Any]], None],
) -> BoundedCompletionResult:
    try:
        result = await _await(executor.execute(checkpoint))
        if not isinstance(result, BoundedCompletionResult):
            raise RuntimeError("completion executor returned an invalid result")
        return result
    finally:
        await _await(executor.close())


class _DurableAttempt:
    def __init__(self, *, run_dir: Path, run_id: str, history_id: str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.history_id = history_id
        self.events_path = self.run_dir / "events.jsonl"
        self.checkpoint_path = self.run_dir / "checkpoint.json"
        self.event_count = 0
        self.completed_stages: list[str] = []

    def event(self, event_type: str, evidence: Mapping[str, Any]) -> None:
        body = {
            "schema_version": EVENT_SCHEMA,
            "run_id": self.run_id,
            "history_id": self.history_id,
            "event_sequence": self.event_count,
            "event_type": event_type,
            "evidence": dict(evidence),
        }
        body["payload_sha256"] = payload_sha256(body)
        append_jsonl_durable(self.events_path, body)
        self.event_count += 1
        if event_type in {
            "retrieval_complete",
            "reader_complete",
            "judge_complete",
        }:
            if event_type in self.completed_stages:
                raise RuntimeError("completion stage checkpoint duplicated")
            self.completed_stages.append(event_type)
        self._checkpoint(status="running", error_class=None)

    def _checkpoint(self, *, status: str, error_class: str | None) -> None:
        body = {
            "schema_version": CHECKPOINT_SCHEMA,
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
        event_type = "terminal_success" if success else "terminal_failure"
        self.event(event_type, {"error_class": error_class, "success": success})
        self._checkpoint(
            status="completed" if success else "failed_stopped",
            error_class=error_class,
        )


def _finalize_result(
    *,
    path: Path,
    result: BoundedCompletionResult,
    authorization: Mapping[str, Any],
    authorization_file_sha256: str,
    consumption_file_sha256: str,
    policy_file_sha256: str,
    identity_file_sha256: str,
    attempt: _DurableAttempt,
) -> dict[str, Any]:
    result_status = (
        "PASS" if result.reference_sanity_status == "PASS" else "REVIEW_REQUIRED"
    )
    payload = {
        "schema_version": RESULT_SCHEMA,
        "stage": "S2",
        "method": "U0",
        "status": result_status,
        "completion_scope": "BOUNDED_ONE_HISTORY",
        "diagnostic_only": False,
        "retrieval_policy_selected": True,
        "reader_judge_executed": True,
        "reference_alignment_status": "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED",
        "reference_sanity_status": result.reference_sanity_status,
        "result": result.to_artifact(),
        "authorization_sha256": authorization_file_sha256,
        "authorization_payload_sha256": authorization["payload_sha256"],
        "consumption_sha256": consumption_file_sha256,
        "policy_freeze_file_sha256": policy_file_sha256,
        "adapter_identity_file_sha256": identity_file_sha256,
        "events_sha256": sha256_file(attempt.events_path),
        "checkpoint_sha256": sha256_file(attempt.checkpoint_path),
        "result_mergeable": result_status == "PASS",
        "s3_ready": False,
        "s3_authorized": False,
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=str(authorization["git_commit"]),
        run_id=str(authorization["run_id"]),
    )
    if path.exists():
        raise ValueError("completion result already exists")
    atomic_write_json(path, artifact)
    return artifact


def _finalize_failure(
    *,
    path: Path,
    error: BaseException,
    authorization: Mapping[str, Any],
    authorization_file_sha256: str,
    consumption_file_sha256: str,
    attempt: _DurableAttempt,
) -> dict[str, Any]:
    payload = {
        "schema_version": FAILURE_SCHEMA,
        "stage": "S2-COMPLETION",
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
        "s3_authorized": False,
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=str(authorization["git_commit"]),
        run_id=str(authorization["run_id"]),
    )
    if path.exists():
        raise ValueError("completion failure already exists")
    atomic_write_json(path, artifact)
    return artifact


def run_s2_completion_controller(
    *,
    authorization_path: Path,
    qualification_path: Path,
    policy_freeze_path: Path,
    adapter_identity_path: Path,
    dependencies: CompletionControllerDependencies,
) -> CompletionControllerOutcome:
    """Validate all bindings, consume authority, then run exactly one live chain."""

    authorization_file = Path(authorization_path)
    authorization = verify_completion_authorization(
        _load_json(authorization_file, label="authorization")
    )
    auth_payload = authorization["payload"]
    qualification_file = Path(qualification_path)
    policy_file = Path(policy_freeze_path)
    identity_file = Path(adapter_identity_path)
    qualification = verify_completion_offline_qualification(
        _load_json(qualification_file, label="qualification")
    )
    policy = verify_completion_policy_freeze(
        _load_json(policy_file, label="policy freeze")
    )
    if (
        sha256_file(qualification_file)
        != auth_payload["qualification_file_sha256"]
        or qualification["payload_sha256"]
        != auth_payload["qualification_payload_sha256"]
        or sha256_file(policy_file) != auth_payload["policy_freeze_file_sha256"]
        or qualification["payload"]["policy_freeze_file_sha256"]
        != sha256_file(policy_file)
        or qualification["payload"]["policy_freeze_payload_sha256"]
        != policy["payload_sha256"]
    ):
        raise ValueError("qualification or policy hash drift")
    _verify_identity(
        identity_file,
        file_hash=auth_payload["adapter_identity_file_sha256"],
        identity_hash=auth_payload["adapter_identity_sha256"],
    )
    if (
        policy["payload"]["adapter_identity_file_sha256"]
        != sha256_file(identity_file)
        or policy["payload"]["adapter_identity_sha256"]
        != auth_payload["adapter_identity_sha256"]
        or qualification["payload"]["adapter_identity_sha256"]
        != auth_payload["adapter_identity_sha256"]
    ):
        raise ValueError("adapter identity binding drift")

    consumption_path = Path(auth_payload["consumption_path"])
    result_path = Path(auth_payload["result_path"])
    failure_path = Path(auth_payload["failure_path"])
    if result_path.exists() or failure_path.exists():
        raise ValueError("completion terminal artifact already exists")
    authorization_hash = sha256_file(authorization_file)
    consumption = consume_completion_authorization(
        authorization=authorization,
        authorization_file_sha256=authorization_hash,
        consumption_path=consumption_path,
    )
    consumption_hash = sha256_file(consumption_path)
    attempt = _DurableAttempt(
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
        if not isinstance(live, CompletionLiveExecutor):
            raise RuntimeError("live executor is invalid")
        result = asyncio.run(_execute_and_close(live, attempt.event))
        attempt.terminal(success=True)
        _finalize_result(
            path=result_path,
            result=result,
            authorization=authorization,
            authorization_file_sha256=authorization_hash,
            consumption_file_sha256=consumption_hash,
            policy_file_sha256=sha256_file(policy_file),
            identity_file_sha256=sha256_file(identity_file),
            attempt=attempt,
        )
        return CompletionControllerOutcome(
            status=(
                "PASS"
                if result.reference_sanity_status == "PASS"
                else "REVIEW_REQUIRED"
            ),
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
        return CompletionControllerOutcome(
            status="FAILED_STOPPED",
            run_id=str(auth_payload["run_id"]),
            artifact_path=failure_path,
        )
