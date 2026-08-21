from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256


def test_bind_vertical_slice_qualification_artifacts_are_hash_sealed_and_fail_closed() -> None:
    root = (
        Path(__file__).resolve().parents[1]
        / "artifacts/paper_eval/membind_v4/meg_runtime_instrumentation/meg-runtime-offline-20260821-008"
    )
    qualification = json.loads((root / "MEG_BIND_VERTICAL_SLICE_QUALIFICATION.json").read_text())
    failure = json.loads((root / "MEG_FAILURE_CAUSALITY_QUALIFICATION.json").read_text())
    passive = json.loads((root / "MEG_BIND_PASSIVE_EQUIVALENCE.json").read_text())
    for payload in (qualification, failure, passive):
        digest = payload.pop("payload_sha256")
        assert digest == payload_sha256(payload)
    assert qualification["status"] == "PASS_OFFLINE_MEG_BIND_VERTICAL_SLICE"
    assert all(qualification["gates"].values())
    assert qualification["decision"] == {
        "live_retry_authorized": False,
        "next_gate": "NONE",
        "sealed_states_unchanged": True,
        "status": "STOP_REAL_RUNTIME_SEMANTIC_LINEAGE",
    }
    assert failure["status"] == "PASS"
    assert len(failure["cases"]) == 4
    assert all(case["root_exception_type"] != "bind_failed" for case in failure["cases"])
    assert failure["historical_capture_policy"]["retroactive_inference"] is False
    assert passive["status"] == "PASS"
    assert passive["violations"] == []
