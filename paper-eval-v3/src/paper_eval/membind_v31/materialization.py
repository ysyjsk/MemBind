"""Transactional control-artifact materialization for the v3.1 live lane.

The terminal APC acceptance and method plan are written independently with
durable atomic replacement.  A sealed commit marker is written last and is the
only logical publication point, so consumers never treat a partially written
pair as an authorized live plan.  The earlier ``V31_REUSE_AUDIT.json`` belongs
exclusively to the offline freezer and is neither read nor overwritten here.
The APC producer tree is strictly read-only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from paper_eval.apc_aligned_baseline import APC_BASELINE_METHODS
from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v31.baseline_acceptance import (
    ACCEPTANCE_SCHEMA,
    EXPECTED_BASELINE_RUN_ID,
    verify_apc_baseline_acceptance,
)
from paper_eval.membind_v31.method_plan import (
    build_membind_v31_live_plan,
    build_membind_v31_method_plan,
    verify_membind_v31_method_plan,
)


BASELINE_ACCEPTANCE_NAME = "V31_BASELINE_ACCEPTANCE.json"
METHOD_PLAN_NAME = "V31_METHOD_PLAN.json"
CONTROL_COMMIT_NAME = "V31_CONTROL_COMMIT.json"
COMMIT_SCHEMA = "membind.paper-eval-v3.membind-v31-control-commit.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MaterializationError(ValueError):
    """The v3.1 control transaction is incomplete or conflicts with input."""


def _fail(code: str) -> MaterializationError:
    return MaterializationError(code)


def _read_sealed(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    stored = value.get("payload_sha256")
    body = {key: child for key, child in value.items() if key != "payload_sha256"}
    if (
        not isinstance(stored, str)
        or _SHA256.fullmatch(stored) is None
        or stored != payload_sha256(body)
    ):
        raise _fail(f"{code} hash invalid")
    return value


def _read_plan(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("baseline plan unreadable") from None
    if not isinstance(value, dict):
        raise _fail("baseline plan invalid")
    return value


def _validate_acceptance_status(value: Mapping[str, Any]) -> None:
    if (
        value.get("schema_version") != ACCEPTANCE_SCHEMA
        or value.get("status") != "PASS"
        or value.get("artifact_status") != "SEALED_VALID"
    ):
        raise _fail("baseline acceptance artifact status invalid")
    verdicts = value.get("semantic_verdicts")
    if not isinstance(verdicts, Mapping) or set(verdicts) != set(
        APC_BASELINE_METHODS
    ):
        raise _fail("baseline acceptance semantic inventory invalid")
    for method in APC_BASELINE_METHODS:
        verdict = verdicts.get(method)
        if not isinstance(verdict, Mapping):
            raise _fail("baseline acceptance semantic status invalid")
        violations = verdict.get("direct_violations")
        if isinstance(violations, bool) or not isinstance(violations, int) or violations < 0:
            raise _fail("baseline acceptance semantic status invalid")
        expected = "SAFE" if violations == 0 else "VIOLATION_OBSERVED"
        if verdict.get("semantic_status") != expected:
            raise _fail("baseline acceptance semantic status invalid")


def _commit(*, acceptance: Mapping[str, Any], method_plan: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA,
        "status": "COMMITTED",
        "run_id": method_plan["run_id"],
        "baseline_run_id": EXPECTED_BASELINE_RUN_ID,
        "baseline_acceptance_artifact": BASELINE_ACCEPTANCE_NAME,
        "baseline_acceptance_payload_sha256": acceptance["payload_sha256"],
        "method_plan_artifact": METHOD_PLAN_NAME,
        "method_plan_payload_sha256": method_plan["payload_sha256"],
        "methodology_sha256": method_plan["methodology_sha256"],
        "workplan_sha256": method_plan["workplan_sha256"],
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def inspect_materialized_control(output_root: Path) -> dict[str, dict[str, Any]]:
    """Validate the logical transaction marker and both committed documents."""

    root = Path(output_root)
    acceptance = _read_sealed(
        root / BASELINE_ACCEPTANCE_NAME, "baseline acceptance invalid"
    )
    _validate_acceptance_status(acceptance)
    method_plan_raw = _read_sealed(root / METHOD_PLAN_NAME, "method plan invalid")
    commit = _read_sealed(root / CONTROL_COMMIT_NAME, "control commit invalid")
    try:
        method_plan = verify_membind_v31_method_plan(method_plan_raw)
    except ValueError as error:
        raise _fail(f"method plan verification failed: {error}") from None
    expected_commit = _commit(acceptance=acceptance, method_plan=method_plan)
    if commit != expected_commit:
        raise _fail("control commit binding invalid")
    if (
        method_plan.get("baseline_plan_payload_sha256")
        != acceptance.get("plan_payload_sha256")
        or method_plan.get("source_manifest_sha256")
        != acceptance.get("source_manifest_sha256")
        or method_plan.get("arrival_trace_sha256")
        != acceptance.get("arrival_trace_sha256")
        or method_plan.get("shared_execution_envelope_sha256")
        != acceptance.get("shared_execution_envelope_sha256")
        or method_plan.get("global_llm_admission_k")
        != acceptance.get("global_llm_admission_k")
    ):
        raise _fail("control artifact cross-binding invalid")
    return {"acceptance": acceptance, "method_plan": method_plan, "commit": commit}


def materialize_membind_v31_live_plan(
    *,
    baseline_root: Path,
    output_root: Path,
    run_id: str,
    methodology_sha256: str,
    workplan_sha256: str,
) -> dict[str, Any]:
    """Publish only the source-bound live plan; this grants no merge authority."""

    plan = build_membind_v31_live_plan(
        run_id=run_id,
        verified_baseline_plan=_read_plan(Path(baseline_root) / "PLAN.json"),
        methodology_sha256=methodology_sha256,
        workplan_sha256=workplan_sha256,
    )
    root = Path(output_root)
    target = root / METHOD_PLAN_NAME
    if target.exists():
        existing = _read_sealed(target, "method plan invalid")
        try:
            verified = verify_membind_v31_method_plan(existing)
        except ValueError as error:
            raise _fail(f"method plan verification failed: {error}") from None
        if verified != plan:
            raise _fail("materialized live plan conflicts with requested identity")
        return verified
    if (root / BASELINE_ACCEPTANCE_NAME).exists() or (root / CONTROL_COMMIT_NAME).exists():
        raise _fail("materialized control is incomplete or conflicting")
    atomic_write_json(target, plan)
    return plan


def materialize_membind_v31_control(
    *,
    baseline_root: Path,
    quality_root: Path | None,
    output_root: Path,
    run_id: str,
    methodology_sha256: str,
    workplan_sha256: str,
) -> dict[str, Any]:
    """Publish the plan pair only after the external APC lane is accepted."""

    baseline = Path(baseline_root)
    acceptance = verify_apc_baseline_acceptance(
        baseline, quality_root=None if quality_root is None else Path(quality_root)
    )
    if acceptance.get("status") != "PASS":
        return dict(acceptance)
    _validate_acceptance_status(acceptance)
    try:
        method_plan = build_membind_v31_method_plan(
            run_id=run_id,
            verified_baseline_plan=_read_plan(baseline / "PLAN.json"),
            verified_baseline_acceptance=acceptance,
            methodology_sha256=methodology_sha256,
            workplan_sha256=workplan_sha256,
        )
    except ValueError as error:
        raise _fail(f"method plan materialization failed: {error}") from None
    root = Path(output_root)
    targets = tuple(
        root / name
        for name in (
            BASELINE_ACCEPTANCE_NAME,
            METHOD_PLAN_NAME,
            CONTROL_COMMIT_NAME,
        )
    )
    present = tuple(path.is_file() for path in targets)
    if all(present):
        existing = inspect_materialized_control(root)
        if existing["acceptance"] != acceptance or existing["method_plan"] != method_plan:
            raise _fail("materialized control is incomplete or conflicting")
        return {
            "status": "PASS",
            "disposition": "REUSED_IDENTICAL",
            "run_id": run_id,
            "baseline_acceptance_payload_sha256": acceptance["payload_sha256"],
            "method_plan_payload_sha256": method_plan["payload_sha256"],
            "control_commit_payload_sha256": existing["commit"]["payload_sha256"],
        }
    live_plan_already_present = present == (False, True, False)
    if live_plan_already_present:
        existing_plan = _read_sealed(root / METHOD_PLAN_NAME, "method plan invalid")
        try:
            existing_plan = verify_membind_v31_method_plan(existing_plan)
        except ValueError as error:
            raise _fail(f"method plan verification failed: {error}") from None
        if existing_plan != method_plan:
            raise _fail("materialized control is incomplete or conflicting")
    elif any(present):
        raise _fail("materialized control is incomplete or conflicting")
    commit = _commit(acceptance=acceptance, method_plan=method_plan)
    atomic_write_json(root / BASELINE_ACCEPTANCE_NAME, acceptance)
    if not live_plan_already_present:
        atomic_write_json(root / METHOD_PLAN_NAME, method_plan)
    # Logical publication point.  Consumers must require this marker.
    atomic_write_json(root / CONTROL_COMMIT_NAME, commit)
    verified = inspect_materialized_control(root)
    return {
        "status": "PASS",
        "disposition": "MATERIALIZED",
        "run_id": run_id,
        "baseline_acceptance_payload_sha256": verified["acceptance"][
            "payload_sha256"
        ],
        "method_plan_payload_sha256": verified["method_plan"]["payload_sha256"],
        "control_commit_payload_sha256": verified["commit"]["payload_sha256"],
    }


__all__ = [
    "BASELINE_ACCEPTANCE_NAME",
    "CONTROL_COMMIT_NAME",
    "COMMIT_SCHEMA",
    "METHOD_PLAN_NAME",
    "MaterializationError",
    "inspect_materialized_control",
    "materialize_membind_v31_live_plan",
    "materialize_membind_v31_control",
]
