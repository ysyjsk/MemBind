from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.qualification_seal import (
    QualificationSealError,
    verify_qualification_seal,
    write_qualification_seal,
)
from saturated_fixed_work_baseline_v1_2.run_manifest import (
    initialize_run_artifacts,
    verify_run_artifacts,
)


def _initialize(repository_root: Path, run_root: Path) -> None:
    initialize_run_artifacts(
        repository_root=repository_root,
        run_root=run_root,
        run_id="sfwb-v1-2-qualification-test",
        resource_envelope={
            "historical_resource_match": True,
            "live_resource_envelope_verified": True,
            "all_formal_blocks_share_one_resource_envelope": "NOT_EVALUATED",
        },
    )


def _passing_evidence() -> dict[str, object]:
    return {
        "preflight_passed": True,
        "instrumentation_aa_qualified": True,
        "b0_a_valid": True,
        "b0_b_valid": True,
        "b1_valid": True,
        "b0_schedule_contract": True,
        "b1_schedule_contract": True,
        "qa_read_only_passed": True,
        "canonical_diffs_emitted": True,
        "serial_serial_12_scope": "12_EPISODE_QUALIFICATION_ONLY",
        "qualification_root": "qualification/l1-attempt-001",
    }


def test_qualification_seal_advances_without_mutating_initial_manifest(
    repository_root: Path, tmp_path: Path
) -> None:
    _initialize(repository_root, tmp_path)
    protocol_path = tmp_path / "protocol_manifest.json"
    protocol_before = protocol_path.read_bytes()
    inventory_before = verify_run_artifacts(tmp_path)["inventory_payload_sha256"]

    seal = write_qualification_seal(tmp_path, _passing_evidence())

    assert seal["qualification_passed"] is True
    assert protocol_path.read_bytes() == protocol_before
    assert json.loads(protocol_before)["qualification_passed"] is False
    assert verify_run_artifacts(tmp_path)["inventory_payload_sha256"] == inventory_before
    assert verify_qualification_seal(tmp_path)["verified"] is True


def test_qualification_seal_is_append_only_and_detects_tampering(
    repository_root: Path, tmp_path: Path
) -> None:
    _initialize(repository_root, tmp_path)
    write_qualification_seal(tmp_path, _passing_evidence())
    with pytest.raises(QualificationSealError, match="QUALIFICATION_SEAL_ALREADY_EXISTS"):
        write_qualification_seal(tmp_path, _passing_evidence())

    path = tmp_path / "qualification/qualification_seal.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["evidence"]["b1_valid"] = False
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(QualificationSealError, match="QUALIFICATION_SEAL_HASH_INVALID"):
        verify_qualification_seal(tmp_path)


def test_qualification_seal_refuses_an_incomplete_l1(
    repository_root: Path, tmp_path: Path
) -> None:
    _initialize(repository_root, tmp_path)
    evidence = _passing_evidence()
    evidence["qa_read_only_passed"] = False
    with pytest.raises(QualificationSealError, match="QUALIFICATION_GATE_INCOMPLETE"):
        write_qualification_seal(tmp_path, evidence)
    assert not (tmp_path / "qualification/qualification_seal.json").exists()
