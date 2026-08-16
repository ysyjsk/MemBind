"""Sanitized, side-effect-free progress projection for one S5 M* attempt."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .s5_durable_attempt_store import inspect_s5_attempt
from .s5_mstar_controller import inspect_s5_mstar_controller_attempt
from .s5_mstar_post_observation import verify_s5_mstar_post_observation
from .s5_mstar_publication_journal import S5MStarPublicationJournal


SCHEMA = "membind.paper-eval-v3.s5-mstar-progress.v1"


@dataclass(frozen=True)
class S5MStarProgressPaths:
    controller_root: Path
    attempt_root: Path
    publication_journal: Path
    post_observation: Path
    final_result: Path


def _error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def _controller(root: Path) -> tuple[dict[str, object], dict[str, object] | None]:
    if not (Path(root) / "checkpoint.json").is_file():
        return {"status": "NOT_STARTED", "last_stage": None, "event_count": 0}, None
    try:
        checked = inspect_s5_mstar_controller_attempt(root)
    except Exception as error:
        return {
            "status": "INVALID_EVIDENCE",
            "last_stage": "inspection_failed",
            "event_count": 0,
        }, {"stage": "controller_inspection", "error_class": _error_class(error)}
    events = checked["events"]
    return {
        "status": str(checked["checkpoint"].get("status", "INVALID_EVIDENCE")),
        "last_stage": str(events[-1].get("event_type")) if events else None,
        "event_count": len(events),
    }, None


def _empty_attempt() -> dict[str, object]:
    return {
        "status": "NOT_STARTED",
        "event_count": 0,
        "intent_count": 0,
        "prepared_count": 0,
        "commit_returned_count": 0,
        "published_count": 0,
        "last_published_source_sequence": None,
    }


def _attempt(root: Path) -> tuple[dict[str, object], dict[str, object] | None]:
    empty = _empty_attempt()
    if not (Path(root) / "manifest.json").is_file():
        return empty, None
    try:
        checked = inspect_s5_attempt(root)
    except Exception as error:
        return {**empty, "status": "INVALID_EVIDENCE"}, {
            "stage": "attempt_inspection",
            "error_class": _error_class(error),
        }
    events = checked["events"]
    counts = Counter(event.get("event_type") for event in events)
    result = checked.get("result")
    status = result.get("status") if isinstance(result, Mapping) else checked["checkpoint"].get("status")
    publications = [event for event in events if event.get("event_type") == "publication"]
    last = publications[-1].get("source_sequence") if publications else None
    return {
        "status": str(status or "INVALID_EVIDENCE"),
        "event_count": len(events),
        "intent_count": counts["intent"],
        "prepared_count": counts["prepared"],
        "commit_returned_count": counts["commit_returned"],
        "published_count": counts["publication"],
        "last_published_source_sequence": (
            int(last) if isinstance(last, int) and not isinstance(last, bool) else None
        ),
    }, None


def _empty_journal() -> dict[str, object]:
    return {
        "status": "NOT_STARTED",
        "intent_count": 0,
        "commit_count": 0,
        "publication_count": 0,
        "recovered_publication_count": 0,
    }


def _journal(path: Path) -> tuple[dict[str, object], dict[str, object] | None]:
    empty = _empty_journal()
    if not Path(path).is_file():
        return empty, None
    try:
        events = S5MStarPublicationJournal.load(path).events
    except Exception as error:
        return {**empty, "status": "INVALID_EVIDENCE"}, {
            "stage": "publication_journal_inspection",
            "error_class": _error_class(error),
        }
    counts = Counter(event.get("event_type") for event in events)
    recovered = sum(
        event.get("event_type") == "publication" and event.get("recovered") is True
        for event in events
    )
    return {
        "status": "complete" if counts["publication"] == 49 else "running",
        "intent_count": counts["intent"],
        "commit_count": counts["commit"],
        "publication_count": counts["publication"],
        "recovered_publication_count": recovered,
    }, None


def _artifact_status(path: Path, verifier: Any, selector: Any) -> tuple[str, dict[str, object] | None]:
    if not Path(path).is_file():
        return "NOT_AVAILABLE", None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        status = selector(verifier(value))
        if not isinstance(status, str) or not status:
            raise ValueError("status unavailable")
        return status, None
    except Exception as error:
        return "INVALID_EVIDENCE", {
            "stage": "artifact_inspection",
            "error_class": _error_class(error),
        }


def _verify_final(value: object) -> object:
    from .s5_mstar_result_finalizer import verify_s5_mstar_result

    return verify_s5_mstar_result(value)  # type: ignore[arg-type]


def inspect_s5_mstar_progress(paths: S5MStarProgressPaths) -> dict[str, object]:
    """Inspect optional evidence surfaces without reading live service state."""

    if not isinstance(paths, S5MStarProgressPaths):
        raise TypeError("paths must be S5MStarProgressPaths")
    controller, controller_failure = _controller(paths.controller_root)
    attempt, attempt_failure = _attempt(paths.attempt_root)
    journal, journal_failure = _journal(paths.publication_journal)
    post, post_failure = _artifact_status(
        paths.post_observation,
        verify_s5_mstar_post_observation,
        lambda value: value.get("status"),
    )
    final, final_failure = _artifact_status(
        paths.final_result,
        _verify_final,
        lambda value: value.get("payload", {}).get("verdict"),
    )
    failure = (
        controller_failure
        or journal_failure
        or attempt_failure
        or (
            {
                "stage": "post_observation_inspection",
                "error_class": post_failure["error_class"],
            }
            if post_failure
            else None
        )
        or (
            {
                "stage": "final_result_inspection",
                "error_class": final_failure["error_class"],
            }
            if final_failure
            else None
        )
        or {"stage": None, "error_class": None}
    )
    return {
        "schema_version": SCHEMA,
        "controller": controller,
        "attempt": attempt,
        "publication_journal": journal,
        "failure": failure,
        "post_observation_status": post,
        "final_result_status": final,
        "resume_authorized": False,
    }


__all__ = ["SCHEMA", "S5MStarProgressPaths", "inspect_s5_mstar_progress"]

