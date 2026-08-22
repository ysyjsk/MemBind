from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.queue import QueueContractError, build_queue_manifest, promote_queue_after_p8, validate_queue_manifest


def test_queue_builder_creates_gated_minimal_manifest_when_p8_is_absent(tmp_path: Path) -> None:
    repo = Path(__file__).parents[2]
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    queue = tmp_path / "queue"
    # P0's repository check is real; the baseline root can be partial because this is a gated queue.
    manifest = build_queue_manifest(repo_root=repo, baseline_root=baseline, queue_root=queue, session_name="v5-gated")
    assert manifest["status"] == "QUEUED_WITH_GATED_MINIMAL"
    assert manifest["p8"]["verified"] is False
    validate_queue_manifest(manifest)
    disk = json.loads((queue / "queue_manifest.json").read_text())
    assert disk["status"] == manifest["status"]


def test_queue_manifest_cannot_claim_gated_mode_with_verified_p8() -> None:
    with pytest.raises(QueueContractError, match="gated-minimal"):
        validate_queue_manifest({"status": "QUEUED_WITH_GATED_MINIMAL", "p8": {"verified": True}, "gates": [{"name": "baseline_formal_seal"}], "full_must_not_start_before": ["minimal_p8_seal"]})


def test_p9_promotion_is_append_only_and_requires_verified_p8(tmp_path: Path) -> None:
    repo = Path(__file__).parents[2]
    baseline = repo / "saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-formal-baseline-20260822-002"
    queue = tmp_path / "queue"
    manifest = build_queue_manifest(repo_root=repo, baseline_root=baseline, queue_root=queue, session_name="v5-p9")
    p8 = queue / "minimal-6"
    p8.mkdir()
    seal = p8 / "seal.json"
    seal.write_text(json.dumps({"status": "P8_LIVE_SEALED", "source_count": 2}))
    (queue / "p8_ready.json").write_text(json.dumps({"status": "P8_SEAL_READY", "seal_path": str(seal.resolve())}))
    promoted = promote_queue_after_p8(queue_root=queue, p8_seal=seal, baseline_root=baseline)
    body = json.loads(promoted.read_text())
    assert body["status"] == "QUEUED_P9_FULL_AFTER_P8"
    assert json.loads((queue / "queue_manifest.json").read_text()) == manifest
    with pytest.raises(QueueContractError, match="already recorded"):
        promote_queue_after_p8(queue_root=queue, p8_seal=seal, baseline_root=baseline)
