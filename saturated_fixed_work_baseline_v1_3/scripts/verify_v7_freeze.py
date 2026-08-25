#!/usr/bin/env python3
"""Verify V7 theory/P7/observer seals without opening a provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def verify(root: Path) -> dict[str, object]:
    pin = _json(root / "PIN_VERIFICATION.json")
    p7 = _json(root / "P7_REFINEMENT_STATUS.json")
    core = _json(root / "CORE_THEORY_FREEZE.json")
    r0 = _json(root / "R0_FREEZE.json")
    terminal = _json(root / "V7_TERMINAL_STATE.json")
    method = _json(root / "METHOD_SELECTION.json")
    schema = _json(root / "schemas/required_observation_fields.json")
    required = schema.get("required_fields")
    if not isinstance(required, list) or len(required) < 20:
        raise ValueError("observer schema is incomplete")
    if schema.get("treatment_allowed") is not False:
        raise ValueError("observer schema authorizes treatment")
    if pin.get("native_subject_match") is not True or pin.get("membind_pin_match") is not True:
        raise ValueError("native subject pin is not verified")
    if pin.get("schema_version") != "membind.v7.pin-verification.v2":
        raise ValueError("pin seal schema is not v2")
    if not pin.get("v7_harness_pin") or not pin.get("observed_harness_head"):
        raise ValueError("harness pin identity is missing")
    if p7.get("operators", {}).get("native_continuation") != "SUPPORTED_WITH_GUARD":
        raise ValueError("guarded native continuation refinement is not sealed")
    if p7.get("treatment_authorized") is not False or core.get("m1_authorized") is not False:
        raise ValueError("P7/core seal unexpectedly authorizes treatment")
    if r0.get("treatment_calls") != 0 or r0.get("live_provider_calls") != 0:
        raise ValueError("freeze contains live provider calls")
    if terminal.get("state") != "V7_THEORY_OR_SYSTEM_BLOCKED":
        raise ValueError("terminal state must remain fail-closed before the opportunity gate")
    if (
        terminal.get("gate_a_e_evaluated") is not False
        or terminal.get("live_treatment_authorized") is not False
        or terminal.get("selected_method") is not None
        or method.get("authorized") is not False
        or method.get("treatment_authorized") is not False
        or method.get("selected_method") is not None
    ):
        raise ValueError("blocked terminal state contains a method or treatment authorization")
    from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import (
        verify_v7_live_artifacts,
    )
    from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (
        verify_observer_manifest,
    )

    terminal_root = root / str(terminal.get("terminal_artifact"))
    terminal_seal = verify_observer_manifest(terminal_root)
    if terminal_seal.get("manifest_sha256") != terminal.get("terminal_manifest_sha256"):
        raise ValueError("terminal blocker manifest digest differs")
    if method.get("terminal_manifest_sha256") != terminal.get("terminal_manifest_sha256"):
        raise ValueError("method and terminal blocker digests differ")
    live_root = root / str(terminal.get("live_runner_dry_run_artifact"))
    live_seal = verify_v7_live_artifacts(live_root)
    if live_seal.get("manifest_sha256") != terminal.get("live_runner_dry_run_manifest_sha256"):
        raise ValueError("live-runner dry-run manifest digest differs")
    return {
        "status": "PASS_WITH_EXPECTED_BLOCKER",
        "blocker": terminal.get("blocker"),
        "observer_fields": len(required),
        "p7_status": p7.get("status"),
        "core_status": core.get("decision"),
        "terminal_state": terminal.get("state"),
        "terminal_manifest_sha256": terminal_seal.get("manifest_sha256"),
        "live_runner_manifest_sha256": live_seal.get("manifest_sha256"),
        "baseline_tests": r0.get("baseline_regression"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("v7"))
    args = parser.parse_args()
    print(json.dumps(verify(args.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
