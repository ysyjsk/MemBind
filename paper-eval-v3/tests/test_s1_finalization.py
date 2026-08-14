from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256
from paper_eval.s1_live import finalize_u0_smoke


def test_finalized_u0_smoke_has_protocol_hash_and_safe_payload(tmp_path: Path) -> None:
    output = tmp_path / "U0_SMOKE.json"
    artifact = finalize_u0_smoke(
        output_path=output,
        git_commit="deadbeef",
        run_id="s1-test",
        history_id="07741c45",
        namespace="pev3-test",
        completed_sequences=[0, 1],
        expected_episode_count=2,
        retrieval_result_ids=["r1"],
        checkpoint_sha256="a" * 64,
        events_sha256="b" * 64,
    )
    persisted = json.loads(output.read_text())
    assert persisted == artifact
    assert artifact["status"] == "finalized"
    assert artifact["payload_sha256"] == payload_sha256(artifact["payload"])
    assert artifact["payload"]["verdict"] == "PASS"
    assert "prompt" not in output.read_text().lower()
