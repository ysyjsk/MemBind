"""Unified, network-isolated Q3 dry-run for Judge qualification.

This module is an offline orchestration helper.  It deliberately delegates all
scientific decisions, durable writes, and verification to the production
qualification modules while forcing every HTTP path through ``MockTransport``.
It never creates or consumes live authorization.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Mapping

import httpx

from evaluation.judge_qualification import (
    JUDGE_QUALIFICATION_ONLY,
    JudgeQualificationArtifactError,
    JudgeQualificationArtifactStore,
    verify_judge_qualification_artifacts,
)
from evaluation.judge_qualification_live import run_formal_judge_qualification


_CONFIG = {
    "base_url": "http://judge-q3.invalid/v1",
    "api_key": "OFFLINE-MOCK-CREDENTIAL",
}


class _SimulatedProcessInterruption(BaseException):
    """Model a process loss after durable intent but before terminal output."""


def _models_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {
                    "id": "qwen3-32b-fp8",
                    "object": "model",
                    "owned_by": "vllm",
                    "root": "qwen3-32b-fp8",
                    "max_model_len": 65536,
                }
            ],
        },
    )


def _completion(label: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "mock-q3-dry-run",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen3-32b-fp8",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": label},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def _labels(freeze: Mapping[str, Any]) -> Iterator[str]:
    return iter(
        "YES" if item["human_label"] else "NO" for item in freeze["items"]
    )


def _event_count(run_dir: Path) -> int:
    raw = run_dir.joinpath("events.jsonl").read_text(encoding="ascii")
    return len(raw.splitlines())


def _offline_fields(run_dir: Path) -> dict[str, object]:
    authorization_paths = (
        run_dir / "live_authorization.json",
        run_dir / "live_authorization_consumption.json",
    )
    return {
        "real_external_requests": 0,
        "live_authorization_created": any(path.exists() for path in authorization_paths),
    }


async def _run_terminal_scenario(
    *,
    validation_root: Path,
    runs_root: Path,
    run_id: str,
    freeze: dict[str, Any],
    deployment_evidence_binding: Mapping[str, object],
    terminal_kind: str,
    terminal_index: int | None,
) -> tuple[dict[str, Any], dict[str, Any], int, int, Path]:
    labels = _labels(freeze)
    models_get_count = 0
    chat_post_count = 0

    def models_handler(request: httpx.Request) -> httpx.Response:
        nonlocal models_get_count
        if (request.method, request.url.path) != ("GET", "/v1/models"):
            raise AssertionError("Q3 models transport received an unexpected request")
        models_get_count += 1
        return _models_response()

    def chat_handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_post_count
        if (request.method, request.url.path) != (
            "POST",
            "/v1/chat/completions",
        ):
            raise AssertionError("Q3 chat transport received an unexpected request")
        index = chat_post_count
        chat_post_count += 1
        if terminal_index is not None and index == terminal_index:
            if terminal_kind == "invalid":
                return _completion("MAYBE")
            if terminal_kind == "service_error":
                return httpx.Response(503, json={"error": {"message": "offline"}})
        return _completion(next(labels))

    result = await run_formal_judge_qualification(
        validation_root=validation_root,
        runs_root=runs_root,
        run_id=run_id,
        freeze=freeze,
        config_mapping=_CONFIG,
        deployment_evidence_binding=deployment_evidence_binding,
        authorization_binding=None,
        models_transport=httpx.MockTransport(models_handler),
        chat_transport=httpx.MockTransport(chat_handler),
    )
    run_dir = runs_root / run_id
    verification = verify_judge_qualification_artifacts(run_dir, freeze)
    return result, verification, models_get_count, chat_post_count, run_dir


async def _run_ambiguous_scenario(
    *,
    validation_root: Path,
    runs_root: Path,
    freeze: dict[str, Any],
    deployment_evidence_binding: Mapping[str, object],
) -> dict[str, object]:
    run_id = "jq-4444444444444444"
    models_get_count = 0
    chat_post_count = 0

    def models_handler(request: httpx.Request) -> httpx.Response:
        nonlocal models_get_count
        if (request.method, request.url.path) != ("GET", "/v1/models"):
            raise AssertionError("Q3 models transport received an unexpected request")
        models_get_count += 1
        return _models_response()

    def chat_handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_post_count
        if (request.method, request.url.path) != (
            "POST",
            "/v1/chat/completions",
        ):
            raise AssertionError("Q3 chat transport received an unexpected request")
        chat_post_count += 1
        raise _SimulatedProcessInterruption()

    try:
        await run_formal_judge_qualification(
            validation_root=validation_root,
            runs_root=runs_root,
            run_id=run_id,
            freeze=freeze,
            config_mapping=_CONFIG,
            deployment_evidence_binding=deployment_evidence_binding,
            authorization_binding=None,
            models_transport=httpx.MockTransport(models_handler),
            chat_transport=httpx.MockTransport(chat_handler),
        )
    except _SimulatedProcessInterruption:
        pass
    else:
        raise AssertionError("Q3 ambiguous scenario did not interrupt in flight")

    run_dir = runs_root / run_id
    before_resume = verify_judge_qualification_artifacts(run_dir, freeze)
    resume_rejected = False
    try:
        JudgeQualificationArtifactStore.resume(run_dir=run_dir, freeze=freeze)
    except JudgeQualificationArtifactError:
        resume_rejected = True
    after_resume = verify_judge_qualification_artifacts(run_dir, freeze)
    return {
        "models_get_count": models_get_count,
        "chat_post_count": chat_post_count,
        "event_count": _event_count(run_dir),
        "verifier_attempt_status": after_resume["attempt_status"],
        "verifier_failure_class": after_resume["failure_class"],
        "pre_resume_failure_class": before_resume["failure_class"],
        "resume_rejected": resume_rejected,
        "suffix_dispatched": chat_post_count > 1,
        **_offline_fields(run_dir),
    }


async def run_judge_q3_dry_run(
    *,
    validation_root: Path,
    runs_root: Path,
    freeze: dict[str, Any],
    deployment_evidence_binding: Mapping[str, object],
) -> dict[str, object]:
    """Run all frozen Q3 branches using only explicit double MockTransport."""

    root = Path(validation_root).resolve(strict=True)
    run_root = Path(runs_root)
    pass_result, pass_verification, pass_models, pass_chat, pass_dir = (
        await _run_terminal_scenario(
            validation_root=root,
            runs_root=run_root,
            run_id="jq-1111111111111111",
            freeze=freeze,
            deployment_evidence_binding=deployment_evidence_binding,
            terminal_kind="pass",
            terminal_index=None,
        )
    )
    invalid_result, invalid_verification, invalid_models, invalid_chat, invalid_dir = (
        await _run_terminal_scenario(
            validation_root=root,
            runs_root=run_root,
            run_id="jq-2222222222222222",
            freeze=freeze,
            deployment_evidence_binding=deployment_evidence_binding,
            terminal_kind="invalid",
            terminal_index=1,
        )
    )
    service_result, service_verification, service_models, service_chat, service_dir = (
        await _run_terminal_scenario(
            validation_root=root,
            runs_root=run_root,
            run_id="jq-3333333333333333",
            freeze=freeze,
            deployment_evidence_binding=deployment_evidence_binding,
            terminal_kind="service_error",
            terminal_index=2,
        )
    )
    ambiguous = await _run_ambiguous_scenario(
        validation_root=root,
        runs_root=run_root,
        freeze=freeze,
        deployment_evidence_binding=deployment_evidence_binding,
    )

    # Corrupt the already-audited PASS event stream only after collecting its
    # scientific and verifier views.  This is a verifier branch, not a run.
    before_tamper = verify_judge_qualification_artifacts(pass_dir, freeze)
    pass_event_count = _event_count(pass_dir)
    with pass_dir.joinpath("events.jsonl").open("ab") as handle:
        handle.write(b"not-canonical\n")
        handle.flush()
    after_tamper = verify_judge_qualification_artifacts(pass_dir, freeze)

    scenarios: dict[str, dict[str, object]] = {
        "full_pass": {
            "planned_item_count": pass_result["planned_item_count"],
            "terminal_item_count": pass_result["terminal_item_count"],
            "eligible_item_count": pass_result["eligible_item_count"],
            "models_get_count": pass_models,
            "chat_post_count": pass_chat,
            "event_count": pass_event_count,
            "qualification_status": pass_result["qualification_status"],
            "verifier_attempt_status": pass_verification["attempt_status"],
            "verifier_mergeable": pass_verification["mergeable"],
            **_offline_fields(pass_dir),
        },
        "invalid_stop": {
            "runner_attempt_status": invalid_result["attempt_status"],
            "failure_class": invalid_result["failure_class"],
            "models_get_count": invalid_models,
            "chat_post_count": invalid_chat,
            "event_count": _event_count(invalid_dir),
            "verifier_invalid_output_count": invalid_verification[
                "invalid_output_count"
            ],
            "verifier_attempt_status": invalid_verification["attempt_status"],
            "suffix_dispatched": invalid_chat > 2,
            **_offline_fields(invalid_dir),
        },
        "service_error_stop": {
            "runner_attempt_status": service_result["attempt_status"],
            "failure_class": service_result["failure_class"],
            "models_get_count": service_models,
            "chat_post_count": service_chat,
            "event_count": _event_count(service_dir),
            "verifier_service_error_count": service_verification[
                "service_error_count"
            ],
            "verifier_attempt_status": service_verification["attempt_status"],
            "suffix_dispatched": service_chat > 3,
            **_offline_fields(service_dir),
        },
        "tamper": {
            "before_tamper_attempt_status": before_tamper["attempt_status"],
            "after_tamper_attempt_status": after_tamper["attempt_status"],
            "after_tamper_failure_class": after_tamper["failure_class"],
            **_offline_fields(pass_dir),
        },
        "ambiguous_inflight": ambiguous,
    }
    return {
        "schema_version": "membind.judge-q3-dry-run.v1",
        "scientific_surface": JUDGE_QUALIFICATION_ONLY,
        "dry_run_only": True,
        "real_external_requests": 0,
        "live_authorization_created": any(
            scenario["live_authorization_created"] for scenario in scenarios.values()
        ),
        "scenarios": scenarios,
    }


__all__ = ["run_judge_q3_dry_run"]
