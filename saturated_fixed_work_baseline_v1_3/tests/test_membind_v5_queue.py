from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.queue import QueueContractError, build_queue_manifest, validate_queue_manifest


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

