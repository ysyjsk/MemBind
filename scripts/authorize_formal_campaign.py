#!/usr/bin/env python3
"""Promote authenticated canary + sealed manifest to formal authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(path)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = root / "saturated_fixed_work_baseline_v1_3/structured_output_recovery"
    frozen = _read(evidence / "FINAL_METHOD_FROZEN.json")
    manifest = _read(args.manifest.resolve())
    if frozen.get("status") != "FINAL_METHOD_FROZEN" or manifest.get("status") != "SEALED" or manifest.get("construction_cell_count") != 45:
        raise RuntimeError("FORMAL_AUTHORIZATION_PREREQUISITES_MISSING")
    state_path = evidence / "CURRENT_STATE.json"
    decision_path = evidence / "FINAL_DECISION.json"
    state = _read(state_path)
    state.update({
        "state": "CODE_READY_FOR_THREE_ARM_EXPERIMENT",
        "engineering_canary_executed": True,
        "three_arm_experiment_created": True,
        "formal_history_executed": False,
        "formal_three_arm_authorized": True,
        "final_method_frozen_sha256": hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "formal_manifest_sha256": manifest.get("manifest_sha256"),
        "formal_manifest_path": str(args.manifest.resolve()),
        "formal_authorized_at": datetime.now(timezone.utc).isoformat(),
    })
    state_path.write_text(json.dumps(state, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    decision = _read(decision_path)
    decision.update({"decision": "CODE_READY_FOR_THREE_ARM_EXPERIMENT", "status": "CODE_READY_FOR_THREE_ARM_EXPERIMENT", "canary_authorized": True, "formal_three_arm_authorized": True, "formal_manifest_sha256": manifest.get("manifest_sha256"), "final_method_frozen_sha256": state["final_method_frozen_sha256"]})
    decision_path.write_text(json.dumps(decision, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": state["state"], "formal_three_arm_authorized": True, "manifest_sha256": manifest.get("manifest_sha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
