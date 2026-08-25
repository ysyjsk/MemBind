from __future__ import annotations

import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.artifact_materializer import materialize_construction_block


def test_materializer_writes_plan_schema_and_non_v6_refinement_na(tmp_path: Path) -> None:
    result = {
        "method": "B0",
        "namespace": "ns-b0",
        "events": [
            {"event": "FORMAL_START", "event_index": 0, "monotonic_ns": 1},
            {"event": "SUBMIT", "source_sequence": 0, "event_index": 1, "monotonic_ns": 2},
            {"event": "NATIVE_ENTER", "source_sequence": 0, "event_index": 2, "monotonic_ns": 3},
            {"event": "PUBLICATION_DURABLE", "source_sequence": 0, "event_index": 3, "monotonic_ns": 4},
        ],
        "lifecycle_validation": {"contract_status": "PASS", "t_build_ns": 3},
        "order_validation": {"order_contract_status": "PASS"},
        "refinement_validation": {"refinement_status": "N/A"},
        "expected_episode_count": 1,
        "submitted_count": 1,
        "completed_count": 1,
        "t_build_ns": 3,
    }
    authority = {"authority_sha256": "a" * 64}
    config = {"config_sha256": "c" * 64}
    manifest = {"manifest_sha256": "w" * 64, "jsonl": "{\"source_sequence\":0}\n"}
    seal = materialize_construction_block(
        tmp_path / "block",
        authority=authority,
        workload_manifest=manifest,
        frozen_config=config,
        result=result,
        identity={"method": "B0", "context_id": "ctx", "repeat": 0, "attempt": "a1"},
    )
    assert seal["status"] == "CONSTRUCTION_SEALED"
    assert json.loads((tmp_path / "block" / "refinement_validation.json").read_text())["refinement_status"] == "N/A"
    assert (tmp_path / "block" / "raw_events.jsonl").is_file()
    assert (tmp_path / "block" / "construction_seal.json").is_file()
