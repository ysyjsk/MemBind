"""Seal the one-history S2 U0 authorization from persisted offline evidence.

This script is intentionally small and deterministic.  It never opens a model
or database connection and persists only public source/config fingerprints.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT.parent / "membind-validation"
sys.path.insert(0, str(ROOT / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.s2_qualification import finalize_u0_qualification


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    native = ROOT / "artifacts/paper_eval/native"
    s0_path = ROOT / "artifacts/paper_eval/S0_CURRENT_STATE.json"
    runtime_identity = _load(s0_path)["payload"]["runtime_identities"]

    # The contract describes the exact upstream call boundary used by S1:
    # Graphiti.add_episode receives a namespace through group_id.  The source
    # hash is public; no code or episode body is copied into this artifact.
    source_path = LEGACY / "src/graphiti_native.py"
    source_hash = __import__("hashlib").sha256(source_path.read_bytes()).hexdigest()
    contract_fields = {
        "source": "membind-validation/src/graphiti_native.py:graphiti_episode_kwargs",
        "source_sha256": source_hash,
        "operation": "graphiti.add_episode",
        "namespace_field": "group_id",
    }
    contract = dict(contract_fields)
    contract["contract_sha256"] = payload_sha256(contract_fields)
    contract_path = native / "U0_DIRECT_ADD_EPISODE_CONTRACT.json"
    atomic_write_json(contract_path, contract)

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True
    ).strip()
    artifact = finalize_u0_qualification(
        output_path=native / "U0_QUALIFICATION.json",
        s0_path=s0_path,
        preflight_path=ROOT / "artifacts/paper_eval/S1_PREFLIGHT.json",
        u0_smoke_path=native / "U0_SMOKE.json",
        run_dir=native / "runs/s1-20260814-001",
        dataset_parity_path=native / "DATASET_PARITY.json",
        evaluator_parity_path=native / "EVALUATOR_PARITY.json",
        git_commit=commit,
        run_id="s2-qual-20260814-001",
        direct_u0_contract_path=contract_path,
        current_runtime_identity=runtime_identity,
    )
    print(
        json.dumps(
            {
                "verdict": artifact["payload"]["verdict"],
                "authorization": artifact["payload"]["authorization"],
                "failure_reasons": artifact["payload"]["failure_reasons"],
                "artifact": str(native / "U0_QUALIFICATION.json"),
                "contract": str(contract_path),
            },
            sort_keys=True,
        )
    )
    return 0 if artifact["payload"]["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
