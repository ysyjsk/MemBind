from __future__ import annotations

import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v5.autoresearch_ledger import append_entry


def test_autoresearch_entry_is_complete_and_append_only(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    entry = append_entry(
        path,
        symptom="fixture mismatch",
        observed_evidence="RED",
        hypothesis="identity omitted field",
        relevant_prior_implementation="pinned client signature",
        minimal_reproduction="one request",
        root_cause="missing seed",
        change="include seed",
        validation="GREEN",
        what_was_learned="transport config is semantic",
        next_action="rerun qualification",
    )
    assert entry["schema_version"].startswith("membind.v5.autoresearch")
    assert json.loads(path.read_text().splitlines()[0])["root_cause"] == "missing seed"
