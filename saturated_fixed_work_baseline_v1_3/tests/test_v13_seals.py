from __future__ import annotations

from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.artifact_seals import (
    SealError,
    seal_construction_block,
    verify_seal,
)


def test_construction_seal_hashes_only_referenced_members_and_detects_mutation(tmp_path: Path) -> None:
    (tmp_path / "raw_events.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "metrics.json").write_text("{}\n", encoding="utf-8")
    seal = seal_construction_block(
        tmp_path,
        identity={"method": "B0", "context_id": "ctx0", "repeat": 0, "workload_hash": "w" * 64},
        required_members=("raw_events.jsonl", "metrics.json"),
    )
    assert seal["status"] == "CONSTRUCTION_SEALED"
    assert verify_seal(tmp_path, seal)["status"] == "PASS"
    (tmp_path / "scratch.tmp").write_text("ignored", encoding="utf-8")
    assert verify_seal(tmp_path, seal)["status"] == "PASS"
    (tmp_path / "metrics.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(SealError, match="hash"):
        verify_seal(tmp_path, seal)


def test_seal_rejects_missing_required_member(tmp_path: Path) -> None:
    with pytest.raises(SealError, match="missing"):
        seal_construction_block(tmp_path, identity={"method": "B0"}, required_members=("raw_events.jsonl",))
