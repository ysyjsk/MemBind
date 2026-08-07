"""Preflight validation for the frozen formal 64-run experiment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


FORMAL_RUN_COUNT = 64
CORRECTNESS_CAPTURE_COUNT = 8
CORRECTNESS_REPLAY_COUNT = 8
PERFORMANCE_RUN_COUNT = 48


def validate_formal_gate(
    artifacts: str | Path,
    data_path: str | Path,
    plan: list[dict[str, Any]],
    *,
    smoke_attempt: str | None = None,
) -> dict[str, Any]:
    """Validate every prerequisite that must hold before a formal run.

    The validator is deliberately side-effect free. Callers may persist the returned
    report, but a failed report must prevent any call to ``run_experiment``.
    """

    artifacts = Path(artifacts)
    data_path = Path(data_path)
    failures: list[str] = []
    checks: dict[str, Any] = {}

    unresolved_blockers = []
    for blocker_path in sorted((artifacts / "environment").glob("*_blocker.json")):
        blocker = _read_json(blocker_path, failures, "environment blocker")
        if (
            blocker.get("status") == "blocked"
            or blocker.get("formal_gate_allowed") is False
        ):
            unresolved_blockers.append(blocker_path.name)
    checks["unresolved_environment_blockers"] = unresolved_blockers
    if unresolved_blockers:
        failures.append(
            "unresolved environment blocker(s): " + ", ".join(unresolved_blockers)
        )

    integration_status = _read_json(
        artifacts / "environment" / "integration_gate_status.json", failures, "integration gate"
    )
    checks["integration_ok"] = integration_status.get("ok") is True
    if not checks["integration_ok"]:
        failures.append("integration gate is not successful")

    manifest_path = artifacts / "environment" / "manifest.json"
    manifest = (
        _read_json(manifest_path, failures, "environment manifest")
        if manifest_path.exists()
        else {}
    )
    contract = integration_status.get("remote_construction_contract") or manifest.get("model_probe") or {}
    checks["structured_checks"] = contract.get("structured_checks")
    checks["structured_success"] = contract.get("structured_success")
    checks["structured_contract_ok"] = (
        contract.get("structured_checks") == 20
        and contract.get("structured_success") == 20
        and contract.get("models_ok", True) is True
        and contract.get("runtime_contract_ok") is True
        and contract.get("ok", True) is True
    )
    if not checks["structured_contract_ok"]:
        failures.append(
            "construction contract must have structured_success=20/20 and frozen runtime "
            f"(got {contract.get('structured_success')}/{contract.get('structured_checks')})"
        )

    smoke = _load_smoke(artifacts, smoke_attempt, failures)
    m2_comparison = smoke.get("m2_vs_m0", {})
    m2_source_order = smoke.get("source_order", {}).get("M2", {})
    checks["smoke_ok"] = (
        smoke.get("ok") is True
        and m2_comparison.get("canonical_graph_parity") is True
        and smoke.get("unexpected_prompt") is not True
        and m2_source_order.get("exactly_once") is True
        and m2_source_order.get("source_order_violation") is False
    )
    if not checks["smoke_ok"]:
        failures.append("smoke must succeed with M2 canonical parity and no unexpected prompt")

    checks["plan_count"] = len(plan)
    plan_failures = _validate_plan(plan)
    failures.extend(plan_failures)
    checks["plan_ok"] = not plan_failures

    split_result = _validate_split(artifacts, data_path, failures)
    checks.update(split_result)
    checks["split_ok"] = not any(item.startswith("split") for item in failures)

    return {
        "ok": not failures,
        "failures": failures,
        "checks": checks,
    }


def _validate_plan(plan: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    if len(plan) != FORMAL_RUN_COUNT:
        failures.append(f"formal plan must contain exactly 64 runs (got {len(plan)})")

    run_ids = [str(item.get("run_id")) for item in plan]
    if len(set(run_ids)) != len(run_ids):
        failures.append("formal plan contains duplicate run_id values")

    captures = {
        str(item.get("question_id")): item
        for item in plan
        if item.get("lane") == "correctness" and item.get("mode") == "capture" and item.get("method") == "M0"
    }
    replays = [
        item
        for item in plan
        if item.get("lane") == "correctness" and item.get("mode") == "replay" and item.get("method") == "M2"
    ]
    performance = [item for item in plan if item.get("lane") == "performance" and item.get("mode") == "live"]
    if len(captures) != CORRECTNESS_CAPTURE_COUNT:
        failures.append(f"formal plan must contain 8 M0 correctness captures (got {len(captures)})")
    if len(replays) != CORRECTNESS_REPLAY_COUNT:
        failures.append(f"formal plan must contain 8 M2 correctness replays (got {len(replays)})")
    if len(performance) != PERFORMANCE_RUN_COUNT:
        failures.append(f"formal plan must contain 48 performance runs (got {len(performance)})")

    positions = {run_id: index for index, run_id in enumerate(run_ids)}
    for replay in replays:
        qid = str(replay.get("question_id"))
        capture = captures.get(qid)
        dependency = replay.get("depends_on")
        if capture is None or dependency != capture.get("run_id"):
            failures.append(f"replay {replay.get('run_id')} depends_on must name its M0 capture")
            continue
        if positions.get(str(dependency), len(plan)) >= positions.get(str(replay.get("run_id")), -1):
            failures.append(f"replay {replay.get('run_id')} must occur after its capture")
    return failures


def _validate_split(
    artifacts: Path,
    data_path: Path,
    failures: list[str],
) -> dict[str, Any]:
    split = _read_json(artifacts / "dataset" / "frozen_split.json", failures, "frozen split")
    expected = str(split.get("source_sha256") or "")
    source_file = artifacts / "dataset" / "source_sha256.txt"
    recorded = source_file.read_text(encoding="utf-8").strip() if source_file.exists() else ""
    actual = ""
    if data_path.exists():
        actual = hashlib.sha256(data_path.read_bytes()).hexdigest()
    if not expected or expected != actual or expected != recorded:
        failures.append(
            "split/source SHA256 does not match the supplied input "
            f"(frozen={expected or '<missing>'}, recorded={recorded or '<missing>'}, actual={actual or '<missing>'})"
        )
    return {
        "frozen_source_sha256": expected,
        "recorded_source_sha256": recorded,
        "input_source_sha256": actual,
    }


def _load_smoke(artifacts: Path, attempt: str | None, failures: list[str]) -> dict[str, Any]:
    smoke_dir = artifacts / "smoke"
    if attempt is not None:
        path = smoke_dir / f"{attempt}.json"
    else:
        candidates = sorted(smoke_dir.glob("*.json"), key=lambda item: item.stat().st_mtime)
        path = candidates[-1] if candidates else smoke_dir / "<missing>.json"
    return _read_json(path, failures, "smoke artifact")


def _read_json(path: Path, failures: list[str], label: str) -> dict[str, Any]:
    if not path.exists():
        failures.append(f"missing {label}: {path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"invalid {label}: {path} ({exc})")
        return {}
    if not isinstance(value, dict):
        failures.append(f"invalid {label}: expected JSON object at {path}")
        return {}
    return value
