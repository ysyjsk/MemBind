from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "artifacts/paper_eval/native/S2_RETRIEVAL_CONTRACT_REVIEW.json"
HISTORICAL = {
    "reference_sanity": ROOT / "artifacts/paper_eval/native/U0_REFERENCE_SANITY.json",
    "checkpoint": (
        ROOT
        / "artifacts/paper_eval/native/runs/s2-live-20260814-001/checkpoint.json"
    ),
    "events": (
        ROOT / "artifacts/paper_eval/native/runs/s2-live-20260814-001/events.jsonl"
    ),
    "adapter_identity": (
        ROOT
        / "artifacts/paper_eval/native/runs/s2-live-20260814-001/adapter_identity.json"
    ),
    "near_zero_diagnosis": (
        ROOT / "artifacts/paper_eval/native/S2_NEAR_ZERO_ROOT_CAUSE.json"
    ),
}


def test_s2_retrieval_contract_review_is_sealed_and_binds_historical_evidence() -> None:
    artifact = json.loads(REVIEW.read_text(encoding="utf-8"))
    payload = artifact["payload"]
    assert artifact["payload_sha256"] == payload_sha256(payload)
    assert payload["status"] == "STOPPED_RETRIEVAL_CONTRACT_MISMATCH_IDENTIFIED"
    assert payload["classification"] == (
        "GOLD_EPISODES_HAVE_NO_ENTITYEDGE_PROVENANCE"
    )
    assert payload["historical_run_id"] == "s2-live-20260814-001"
    assert payload["historical_artifact_mutation_count"] == 0
    assert payload["historical_evidence_sha256"] == {
        key: sha256_file(path) for key, path in HISTORICAL.items()
    }
    assert payload["observed_retrieval_contract"]["top_k_unit"] == "edge"
    assert payload["observed_retrieval_contract"][
        "official_longmemeval_session_metric"
    ] is False
    assert payload["metric_interpretation"]["official_session_recall_at_10"] == (
        "NOT_COMPUTED"
    )
    assert payload["decision"]["s3_authorized"] is False
    assert payload["decision"]["additional_live_calls_performed"] == 0
    # This review is immutable historical evidence.  Later source revisions
    # are bound by a new amendment instead of silently changing these hashes.
    assert payload["source_sha256"]["future_retrieval_contract_source"] == (
        "86534581783e1c7017e1344becfae33126b8e8f7ed0984325ff139070a194586"
    )
    assert payload["source_sha256"]["future_episode_probe_source"] == (
        "a9b28251a8e14f86d505c51b774e1f5c04cd160a509d8a6e527c80ef23d94eb9"
    )
    assert payload["source_sha256"]["historical_offline_scaffold_source"] == (
        sha256_file(ROOT / "scripts/run_s2_offline.py")
    )

    serialized = REVIEW.read_text(encoding="utf-8").lower()
    for forbidden in (
        "api_key",
        "base_url",
        "http://",
        "https://",
        '"question"',
        '"answer"',
        '"prompt"',
        "raw_output",
    ):
        assert forbidden not in serialized
