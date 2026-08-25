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
    schema = _json(root / "schemas/required_observation_fields.json")
    required = schema.get("required_fields")
    if not isinstance(required, list) or len(required) < 20:
        raise ValueError("observer schema is incomplete")
    if schema.get("treatment_allowed") is not False:
        raise ValueError("observer schema authorizes treatment")
    if pin.get("membind_pin_match") is not False:
        raise ValueError("pin seal unexpectedly changed")
    if p7.get("treatment_authorized") is not False or core.get("m1_authorized") is not False:
        raise ValueError("P7/core seal unexpectedly authorizes treatment")
    if r0.get("treatment_calls") != 0 or r0.get("live_provider_calls") != 0:
        raise ValueError("freeze contains live provider calls")
    if terminal.get("state") != "V7_THEORY_OR_SYSTEM_BLOCKED":
        raise ValueError("terminal state must remain fail-closed before the opportunity gate")
    return {
        "status": "PASS_WITH_EXPECTED_BLOCKER",
        "blocker": pin.get("status"),
        "observer_fields": len(required),
        "p7_status": p7.get("status"),
        "core_status": core.get("decision"),
        "terminal_state": terminal.get("state"),
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
