from __future__ import annotations

import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PREREG = PROJECT / "v7/dmsv_b1r2_structural_closure/DMSV_B1R2_PREREGISTRATION.json"


def test_b1r2_preregistration_is_frozen_before_evidence() -> None:
    prereg = json.loads(PREREG.read_text(encoding="ascii"))
    assert prereg["status"] == "FROZEN_BEFORE_EVIDENCE_EXTRACTION"
    assert prereg["input_commit"] == "37871aae8193d994a1642605e3a705712dd786e1"
    assert list(prereg["claim_taxonomy"]) == [
        "L1_SENSITIVITY",
        "L2_DIRTY_WITNESS_EXISTS",
        "L3_DIRTY_RATE_ESTIMATED",
        "L4_STRUCTURALLY_ALWAYS_DIRTY",
        "L5_NATIVE_CALL_UNAVOIDABLE",
        "source",
    ]
    assert all(value is False for value in prereg["terminal_authorizations"].values())
