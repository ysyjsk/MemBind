#!/usr/bin/env python3
"""Materialize the bounded V7 R1-R3 observer/reference campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v7.campaign import run_observer_campaign


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("v7/artifacts/v7-observer-20260825-001"))
    parser.add_argument("--seed", type=int, action="append", dest="seeds")
    args = parser.parse_args()
    seeds = tuple(args.seeds or (17, 23))
    if len(seeds) != 2 or seeds[0] == seeds[1]:
        raise SystemExit("exactly two distinct seeds are required for independent R3 blocks")
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    campaign = run_observer_campaign(seeds=seeds, source_count=6)
    _write_json(root / "R1_ASSUMPTION_AUDIT.json", {"schema_version": "membind.v7.r1-assumption-audit.v1", "status": "OBSERVER_ONLY", "treatment_calls": 0, "unknown_is_first_class": True, "assumption_status": "../ASSUMPTION_STATUS.json"})
    _write_json(root / "R2_TWO_SOURCE_CAUSAL_TRACE.json", campaign["r2"])
    _write_json(root / "R3_BLOCKS.json", campaign["r3_blocks"])
    seal_payload = {
        "schema_version": "membind.v7.observer-campaign-seal.v1",
        "status": "OBSERVER_ONLY_PIN_MISMATCH_BLOCKED_FOR_GRAPHITI_CLAIMS",
        "treatment_calls": 0,
        "publication_calls": 0,
        "seeds": list(seeds),
        "source_count_per_block": 6,
        "files": sorted(path.name for path in root.glob("*.json") if path.name != "SEAL.json"),
        "claim_boundary": "synthetic_reference_contract_only; no Graphiti performance or online economics",
    }
    _write_json(root / "SEAL.json", seal_payload)
    manifest = []
    for path in sorted(root.glob("*.json")):
        if path.name == "MANIFEST.json":
            continue
        manifest.append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    _write_json(root / "MANIFEST.json", {"schema_version": "membind.v7.observer-manifest.v1", "files": manifest})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
