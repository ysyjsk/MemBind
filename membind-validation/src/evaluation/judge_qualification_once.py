"""One-shot controller for the sealed Judge qualification authorization.

This module contains no scoring or transport policy. It assembles the already
sealed evidence, creates the singleton authorization, invokes the production
formal runner exactly once, and emits only secret-safe durable progress.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

from evaluation.judge_qualification import (
    JUDGE_QUALIFICATION_ONLY,
    PROTOCOL_ID,
    canonical_json_bytes,
    validate_strict_judge_qualification_freeze,
    verify_judge_qualification_artifacts,
)
from evaluation.judge_qualification_live import (
    JudgeQualificationLiveError,
    load_judge_live_config,
    load_verified_judge_deployment_evidence,
    run_formal_judge_qualification,
    validate_judge_prelive_evidence_manifest,
)


_PENDING = Path("artifacts/protocol/judge_qualification_pending_live_20260813.json")
_FREEZE = Path("artifacts/protocol/judge_qualification_strict_freeze_20260813.json")
_DEPLOYMENT = Path("artifacts/environment/judge_deployment_evidence_20260813.json")
_LIVE_SOURCE = Path("src/evaluation/judge_qualification_live.py")
_REPORT = Path("artifacts/diagnostics/judge_qualification_live_controller_20260813.json")
_RUN_ID_RE = re.compile(r"^jq-[0-9a-f]{16}$")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text("ascii"))
    if not isinstance(value, dict) or path.read_bytes() != canonical_json_bytes(value) + b"\n":
        raise RuntimeError(f"{path.name} is not canonical JSON")
    return value


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(value)
    sealed["payload_sha256"] = _sha256(canonical_json_bytes(sealed))
    return sealed


def _exclusive_bytes(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _write_report(path: Path, value: Mapping[str, Any]) -> None:
    sealed = _seal(value)
    _exclusive_bytes(path, canonical_json_bytes(sealed) + b"\n")


def _public_event_progress(run_dir: Path) -> tuple[int, int]:
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return 0, 0
    intents = 0
    terminals = 0
    payload = events_path.read_bytes()
    # The scientific writer appends and fsyncs each line. A concurrent reader
    # may still observe an in-progress final write, which is not a run failure.
    complete = payload if payload.endswith(b"\n") else payload.rpartition(b"\n")[0]
    for raw in complete.splitlines():
        if not raw:
            continue
        event = json.loads(raw.decode("ascii"))
        event_type = event.get("event_type")
        if event_type == "dispatch_intent_durable":
            intents += 1
        elif event_type in {
            "terminal_success",
            "terminal_invalid",
            "terminal_service_error",
        }:
            terminals += 1
    return intents, terminals


async def _monitor(run_dir: Path, task: asyncio.Task[dict[str, Any]]) -> None:
    previous = (-1, -1)
    while not task.done():
        current = _public_event_progress(run_dir)
        if current != previous:
            print(
                f"PROGRESS intents={current[0]}/14 terminals={current[1]}/14",
                flush=True,
            )
            previous = current
        await asyncio.sleep(0.5)
    current = _public_event_progress(run_dir)
    if current != previous:
        print(
            f"PROGRESS intents={current[0]}/14 terminals={current[1]}/14",
            flush=True,
        )


async def main() -> int:
    root = Path.cwd().resolve()
    pending = _read_json(root / _PENDING)
    run_id = pending.get("run_id")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise RuntimeError("pending live run ID is invalid")
    run_dir = root / str(pending["run_dir"])
    authorization_path = root / str(pending["authorization_path"])
    receipt_path = root / str(pending["receipt_path"])
    prelive_path = root / str(pending["prelive_path"])
    if any(path.exists() for path in (run_dir, authorization_path, receipt_path, root / _REPORT)):
        raise RuntimeError("singleton Judge live target already exists")

    freeze = _read_json(root / _FREEZE)
    validate_strict_judge_qualification_freeze(freeze, root)
    prelive = _read_json(prelive_path)
    validate_judge_prelive_evidence_manifest(prelive, root)
    prelive_raw = prelive_path.read_bytes()
    if (
        prelive.get("authorized_run_id") != run_id
        or _sha256(prelive_raw) != pending.get("prelive_file_sha256")
        or prelive.get("payload_sha256") != pending.get("prelive_payload_sha256")
    ):
        raise RuntimeError("pending pre-live evidence drifted")

    deployment_path = root / _DEPLOYMENT
    deployment_raw = deployment_path.read_bytes()
    deployment = load_verified_judge_deployment_evidence(
        root,
        _DEPLOYMENT,
        _sha256(deployment_raw),
    )
    authorization = _seal(
        {
            "schema_version": "membind.judge-live-authorization.v1",
            "protocol_id": PROTOCOL_ID,
            "scientific_surface": JUDGE_QUALIFICATION_ONLY,
            "authorization_id": f"jqa-{run_id[3:]}",
            "authorized_run_id": run_id,
            "authorization_path": authorization_path.relative_to(root).as_posix(),
            "live_run_limit": 1,
            "freeze_payload_sha256": freeze["payload_sha256"],
            "qualification_live_source_sha256": _sha256((root / _LIVE_SOURCE).read_bytes()),
            "deployment_evidence_payload_sha256": deployment[
                "evidence_payload_sha256"
            ],
            "prelive_evidence_manifest_file_sha256": _sha256(prelive_raw),
            "prelive_evidence_manifest_payload_sha256": prelive["payload_sha256"],
        }
    )
    authorization_raw = canonical_json_bytes(authorization) + b"\n"
    _exclusive_bytes(authorization_path, authorization_raw)
    authorization_binding = {
        "path": authorization_path.relative_to(root).as_posix(),
        "sha256": _sha256(authorization_raw),
    }
    prelive_binding = {
        "path": prelive_path.relative_to(root).as_posix(),
        "sha256": _sha256(prelive_raw),
    }
    deployment_binding = {
        "path": _DEPLOYMENT.as_posix(),
        "sha256": _sha256(deployment_raw),
    }

    env = dotenv_values(root / ".env")
    base_url = env.get("CONSTRUCTION_LLM_BASE_URL")
    api_key = env.get("CONSTRUCTION_LLM_API_KEY") or env.get("VLLM_API_KEY")
    if not isinstance(base_url, str) or not base_url:
        raise RuntimeError("Judge base URL is unavailable")
    if not isinstance(api_key, str) or not api_key:
        # vLLM can be keyless, but the adapter requires a non-empty value and
        # never persists it. This value is local to the request process.
        api_key = "EMPTY-VLLM-PLACEHOLDER"
    config = load_judge_live_config({"base_url": base_url, "api_key": api_key})

    print(f"LIVE_START run_id={run_id} planned=14", flush=True)
    task = asyncio.create_task(
        run_formal_judge_qualification(
            validation_root=root,
            runs_root=root / "artifacts/judge_qualification/runs",
            run_id=run_id,
            freeze=freeze,
            config_mapping={"base_url": config.base_url, "api_key": config.api_key},
            deployment_evidence_binding=deployment_binding,
            authorization_binding=authorization_binding,
            prelive_evidence_binding=prelive_binding,
            models_transport=None,
            chat_transport=None,
        )
    )
    await _monitor(run_dir, task)
    try:
        result = await task
        error_class = None
    except BaseException as error:
        result = None
        error_class = f"{type(error).__module__}.{type(error).__name__}"

    verification = (
        verify_judge_qualification_artifacts(run_dir, freeze)
        if run_dir.exists()
        else {
            "attempt_status": "incomplete_invalid_non_mergeable",
            "failure_class": "run_directory_not_created",
            "completed_item_count": 0,
            "invalid_output_count": 0,
            "service_error_count": 0,
        }
    )
    report: dict[str, Any] = {
        "schema_version": "membind.judge-live-controller-report.v1",
        "scientific_surface": JUDGE_QUALIFICATION_ONLY,
        "run_id": run_id,
        "authorization_consumed": receipt_path.is_file(),
        "run_directory_created": run_dir.is_dir(),
        "controller_error_class": error_class,
        "attempt_status": verification.get("attempt_status"),
        "failure_class": verification.get("failure_class"),
        "failed_item_id": verification.get("failed_item_id"),
        "completed_item_count": verification.get("completed_item_count", 0),
        "invalid_output_count": verification.get("invalid_output_count", 0),
        "service_error_count": verification.get("service_error_count", 0),
        "qualification_status": verification.get("qualification_status"),
        "mergeable": verification.get("mergeable", False),
        "result_payload_sha256": (
            result.get("payload_sha256") if isinstance(result, dict) else None
        ),
    }
    _write_report(root / _REPORT, report)
    print(
        "LIVE_STOP "
        f"run_id={run_id} attempt_status={report['attempt_status']} "
        f"completed={report['completed_item_count']}/14 "
        f"qualification_status={report['qualification_status']} "
        f"failure_class={report['failure_class']} "
        f"controller_error_class={report['controller_error_class']}",
        flush=True,
    )
    return 0 if report["qualification_status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except JudgeQualificationLiveError as error:
        print(
            f"LIVE_SETUP_STOP error_class={type(error).__module__}.{type(error).__name__}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)
