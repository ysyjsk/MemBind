"""Sanitized, read-only progress projection for one S5 P*(C=2) attempt.

The inspector validates each optional durable surface through its owning
verifier.  It never reads authority, pointer, environment, or live-service
state and exposes neither execution identity nor private evidence payloads.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .s5_durable_attempt_store import inspect_s5_attempt
from .s5_pstar_controller import inspect_s5_pstar_controller_attempt
from .s5_pstar_post_observation import verify_s5_pstar_post_observation


SCHEMA = "membind.paper-eval-v3.s5-pstar-progress.v1"


@dataclass(frozen=True)
class S5PStarProgressPaths:
    """The four public evidence surfaces needed for offline monitoring."""

    controller_root: Path
    attempt_root: Path
    post_observation: Path
    final_result: Path


def _error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def _controller(
    root: Path,
) -> tuple[dict[str, object], dict[str, str | None] | None]:
    if not Path(root).exists():
        return {
            "status": "NOT_STARTED",
            "last_stage": None,
            "event_count": 0,
        }, None
    try:
        inspected = inspect_s5_pstar_controller_attempt(root)
    except Exception as error:
        return {
            "status": "INVALID_EVIDENCE",
            "last_stage": "inspection_failed",
            "event_count": 0,
        }, {
            "stage": "controller_inspection",
            "error_class": _error_class(error),
        }
    events = inspected["events"]
    checkpoint = inspected["checkpoint"]
    last_stage = events[-1].get("event_type") if events else None
    failure: dict[str, str | None] | None = None
    if isinstance(checkpoint.get("failure_stage"), str):
        error_class = checkpoint.get("error_class")
        failure = {
            "stage": str(checkpoint["failure_stage"]),
            "error_class": str(error_class) if isinstance(error_class, str) else None,
        }
    return {
        "status": str(checkpoint.get("status", "INVALID_EVIDENCE")),
        "last_stage": str(last_stage) if isinstance(last_stage, str) else None,
        "event_count": len(events),
    }, failure


def _empty_attempt() -> dict[str, object]:
    return {
        "status": "NOT_STARTED",
        "event_count": 0,
        "published_count": 0,
        "last_published_source_sequence": None,
        "terminal_source_count": 0,
        "published_terminal_count": 0,
        "treatment_failed_terminal_count": 0,
        "censored_terminal_count": 0,
    }


def _attempt(
    root: Path,
) -> tuple[dict[str, object], dict[str, str | None] | None]:
    empty = _empty_attempt()
    if not Path(root).exists():
        return empty, None
    try:
        inspected = inspect_s5_attempt(root)
    except Exception as error:
        return {**empty, "status": "INVALID_EVIDENCE"}, {
            "stage": "attempt_inspection",
            "error_class": _error_class(error),
        }

    events = inspected["events"]
    checkpoint = inspected["checkpoint"]
    result = inspected.get("result")
    status = (
        result.get("status") if isinstance(result, Mapping) else checkpoint.get("status")
    )
    publications = [
        event for event in events if event.get("event_type") == "publication"
    ]
    terminals = [
        event for event in events if event.get("event_type") == "source_terminal"
    ]
    classifications = Counter(
        event.get("terminal_classification") for event in terminals
    )
    last_source = publications[-1].get("source_sequence") if publications else None
    failed = next(
        (
            event
            for event in terminals
            if event.get("terminal_classification") == "TREATMENT_FAILED"
        ),
        None,
    )
    failure: dict[str, str | None] | None = None
    if failed is not None:
        error_class = failed.get("error_class")
        failure = {
            "stage": "native_execution",
            "error_class": str(error_class) if isinstance(error_class, str) else None,
        }
    return {
        "status": str(status or "INVALID_EVIDENCE"),
        "event_count": len(events),
        "published_count": len(publications),
        "last_published_source_sequence": (
            int(last_source)
            if isinstance(last_source, int) and not isinstance(last_source, bool)
            else None
        ),
        "terminal_source_count": len(terminals),
        "published_terminal_count": classifications["PUBLISHED"],
        "treatment_failed_terminal_count": classifications["TREATMENT_FAILED"],
        "censored_terminal_count": classifications[
            "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE"
        ],
    }, failure


def _artifact_status(
    path: Path,
    *,
    verifier: Any,
    status: Any,
) -> tuple[str, dict[str, str | None] | None]:
    if not Path(path).exists():
        return "NOT_AVAILABLE", None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        verified = verifier(value)
        selected = status(verified)
        if not isinstance(selected, str) or not selected:
            raise ValueError("status unavailable")
        return selected, None
    except Exception as error:
        return "INVALID_EVIDENCE", {
            "stage": "artifact_inspection",
            "error_class": _error_class(error),
        }


def _verify_final(value: object) -> object:
    # The finalizer is imported lazily so an unfinished optional terminal file
    # cannot prevent monitoring controller/attempt evidence already on disk.
    from . import s5_pstar_result_finalizer

    verifier = getattr(s5_pstar_result_finalizer, "verify_s5_pstar_result")
    return verifier(value)


def inspect_s5_pstar_progress(paths: S5PStarProgressPaths) -> dict[str, object]:
    """Return a fixed execution-identity-free projection of durable progress."""

    if not isinstance(paths, S5PStarProgressPaths):
        raise TypeError("paths must be S5PStarProgressPaths")
    controller, controller_failure = _controller(paths.controller_root)
    attempt, attempt_failure = _attempt(paths.attempt_root)
    post_status, post_failure = _artifact_status(
        paths.post_observation,
        verifier=verify_s5_pstar_post_observation,
        status=lambda value: value.get("status"),
    )
    final_status, final_failure = _artifact_status(
        paths.final_result,
        verifier=_verify_final,
        status=lambda value: value.get("payload", {}).get("verdict"),
    )
    failure = (
        controller_failure
        or attempt_failure
        or (
            {
                "stage": "post_observation_inspection",
                "error_class": post_failure["error_class"],
            }
            if post_failure is not None
            else None
        )
        or (
            {
                "stage": "final_result_inspection",
                "error_class": final_failure["error_class"],
            }
            if final_failure is not None
            else None
        )
        or {"stage": None, "error_class": None}
    )
    return {
        "schema_version": SCHEMA,
        "controller": controller,
        "attempt": attempt,
        "failure": failure,
        "post_observation_status": post_status,
        "final_result_status": final_status,
        "resume_authorized": False,
    }


__all__ = [
    "SCHEMA",
    "S5PStarProgressPaths",
    "inspect_s5_pstar_progress",
]
