"""Pure orchestration contract for the isolated three-baseline suite.

This module performs no I/O. It freezes method order, workload inventory,
namespace identity, and fail-closed block decisions so those rules can be
tested before a live runner constructs Graphiti or contacts any service.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .artifacts import payload_sha256


SCHEMA = "membind.paper-eval-v3.baseline-suite-plan.v1"
BASELINE_METHODS = ("U0", "A0", "P(C=2)")
DEVELOPMENT_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
CANARY_EPISODE_LIMITS = {"U0": 1, "A0": 1, "P(C=2)": 2}

_RUN_ID = re.compile(r"^bs-[a-z0-9][a-z0-9-]{2,63}$")
_NATIVE_RUN_ID = re.compile(r"^nb-[a-z0-9][a-z0-9-]{2,63}$")
_HISTORY_ID = re.compile(r"^[0-9a-f]{8}$")
_METHOD_SLUG = {"U0": "u0", "A0": "a0", "P(C=2)": "pc2"}
_MODES = {"canary", "development"}
_PROGRESS_STATUSES = {
    "planned",
    "running",
    "quality_pending",
    "completed",
    "failed",
    "incomplete",
    "incomplete_non_mergeable",
}


class BaselineSuiteError(ValueError):
    """A stable baseline-suite planning or resume contract failed."""


def canonicalize_baseline_method(value: object) -> str:
    if not isinstance(value, str) or value not in BASELINE_METHODS:
        raise BaselineSuiteError("method is not a registered baseline")
    return value


def _validate_suite_run_id(value: object) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise BaselineSuiteError("suite run id is invalid")
    return value


def _validate_attempt(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 999:
        raise BaselineSuiteError("attempt ordinal is invalid")
    return value


def _validate_reuse_u0_run(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _NATIVE_RUN_ID.fullmatch(value) is None:
        raise BaselineSuiteError("reuse U0 run id is invalid")
    return value


def baseline_block_namespace(
    *,
    suite_run_id: str,
    method: str,
    history_id: str,
    attempt_ordinal: int,
) -> str:
    """Derive a readable namespace bound to every block identity component."""

    run_id = _validate_suite_run_id(suite_run_id)
    selected_method = canonicalize_baseline_method(method)
    if history_id not in DEVELOPMENT_HISTORIES or _HISTORY_ID.fullmatch(history_id) is None:
        raise BaselineSuiteError("history id is outside the development inventory")
    attempt = _validate_attempt(attempt_ordinal)
    return (
        f"pev3-{run_id}-{_METHOD_SLUG[selected_method]}-"
        f"{history_id}-a{attempt:03d}"
    )


def _blocks(
    *,
    suite_run_id: str,
    mode: str,
    attempt_ordinal: int,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if mode == "canary":
        inventory = (
            (method, DEVELOPMENT_HISTORIES[0], CANARY_EPISODE_LIMITS[method])
            for method in BASELINE_METHODS
        )
    else:
        inventory = (
            (method, history_id, None)
            for method in BASELINE_METHODS
            for history_id in DEVELOPMENT_HISTORIES
        )
    for block_index, (method, history_id, episode_limit) in enumerate(inventory):
        blocks.append(
            {
                "block_index": block_index,
                "suite_run_id": suite_run_id,
                "mode": mode,
                "method": method,
                "history_id": history_id,
                "episode_limit": episode_limit,
                "attempt_ordinal": attempt_ordinal,
                "namespace": baseline_block_namespace(
                    suite_run_id=suite_run_id,
                    method=method,
                    history_id=history_id,
                    attempt_ordinal=attempt_ordinal,
                ),
            }
        )
    return blocks


def build_baseline_suite_plan(
    suite_run_id: str,
    *,
    mode: str,
    attempt_ordinal: int = 1,
    reuse_u0_run: str | None = None,
) -> dict[str, Any]:
    """Build the exact canary or method-major development inventory."""

    plan = _build_unverified_plan(
        suite_run_id,
        mode=mode,
        attempt_ordinal=attempt_ordinal,
        reuse_u0_run=reuse_u0_run,
    )
    return verify_baseline_suite_plan(plan)


def verify_baseline_suite_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute the complete inventory rather than trusting stored blocks."""

    if not isinstance(value, Mapping):
        raise BaselineSuiteError("baseline suite plan must be an object")
    candidate = deepcopy(dict(value))
    expected = _build_unverified_plan(
        candidate.get("suite_run_id"),
        mode=candidate.get("mode"),
        attempt_ordinal=candidate.get("attempt_ordinal"),
        reuse_u0_run=candidate.get("reuse_u0_run"),
    )
    if candidate != expected:
        raise BaselineSuiteError("baseline suite plan inventory or hash drift")
    return candidate


def _build_unverified_plan(
    suite_run_id: object,
    *,
    mode: object,
    attempt_ordinal: object,
    reuse_u0_run: object,
) -> dict[str, Any]:
    run_id = _validate_suite_run_id(suite_run_id)
    if mode not in _MODES:
        raise BaselineSuiteError("baseline suite mode is invalid")
    attempt = _validate_attempt(attempt_ordinal)
    reuse = _validate_reuse_u0_run(reuse_u0_run)
    reuse_status = (
        "REFERENCE_ONLY_NOT_APPLIED_TO_CANARY"
        if mode == "canary" and reuse is not None
        else "PENDING_HASH_VERIFICATION"
        if reuse is not None
        else "NOT_REQUESTED"
    )
    plan: dict[str, Any] = {
        "schema_version": SCHEMA,
        "suite_run_id": run_id,
        "mode": mode,
        "methods": list(BASELINE_METHODS),
        "histories": list(DEVELOPMENT_HISTORIES),
        "attempt_ordinal": attempt,
        "reuse_u0_run": reuse,
        "reuse_u0_status": reuse_status,
        "execution_order": "STRICT_METHOD_MAJOR_SERIAL",
        "blocks": _blocks(
            suite_run_id=run_id,
            mode=str(mode),
            attempt_ordinal=attempt,
        ),
    }
    plan["payload_sha256"] = payload_sha256(plan)
    return plan


def verify_baseline_block_progress(
    *,
    method: str,
    expected_sequences: Sequence[int],
    completed_sequences: Sequence[int],
    status: str,
) -> dict[str, Any]:
    """Validate serial-prefix or P(C=2) unordered completion semantics."""

    selected_method = canonicalize_baseline_method(method)
    expected = list(expected_sequences)
    completed = list(completed_sequences)
    if expected != list(range(len(expected))):
        raise BaselineSuiteError("expected source sequence inventory is invalid")
    if status not in _PROGRESS_STATUSES:
        raise BaselineSuiteError("block progress status is invalid")
    if any(
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence not in expected
        for sequence in completed
    ):
        raise BaselineSuiteError("completed source sequence is invalid")
    if len(completed) != len(set(completed)):
        raise BaselineSuiteError("completed source sequence is duplicate")
    if selected_method in {"U0", "A0"} and completed != expected[: len(completed)]:
        raise BaselineSuiteError("serial completed sequence is not a source prefix")
    if status in {"completed", "quality_pending"} and set(completed) != set(expected):
        raise BaselineSuiteError("complete block does not cover every source sequence")
    return {
        "method": selected_method,
        "expected_sequences": expected,
        "completed_sequences": completed,
        "status": status,
    }


def decide_baseline_block_action(
    *,
    block: Mapping[str, Any],
    observed: Mapping[str, Any] | None,
) -> str:
    """Skip only a verified result; every partial namespace fails closed."""

    if not isinstance(block, Mapping):
        raise BaselineSuiteError("block identity is invalid")
    if observed is None:
        return "RUN_FRESH"
    if not isinstance(observed, Mapping):
        raise BaselineSuiteError("observed block state is invalid")
    for field in (
        "suite_run_id",
        "method",
        "history_id",
        "attempt_ordinal",
        "namespace",
    ):
        if observed.get(field) != block.get(field):
            raise BaselineSuiteError("observed block identity mismatch")
    status = observed.get("status")
    if status == "completed" and observed.get("artifacts_verified") is True:
        return "SKIP_VERIFIED_COMPLETED"
    raise BaselineSuiteError(
        "incomplete or non-mergeable block cannot resume; use a new attempt"
    )


__all__ = [
    "BASELINE_METHODS",
    "CANARY_EPISODE_LIMITS",
    "DEVELOPMENT_HISTORIES",
    "BaselineSuiteError",
    "baseline_block_namespace",
    "build_baseline_suite_plan",
    "canonicalize_baseline_method",
    "decide_baseline_block_action",
    "verify_baseline_block_progress",
    "verify_baseline_suite_plan",
]
