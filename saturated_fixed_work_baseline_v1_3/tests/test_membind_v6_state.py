from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6.autoresearch import (
    V6LedgerError,
    append_ledger_entry,
    create_campaign_root,
    update_run_state,
)


def test_campaign_root_has_recoverable_state_and_first_ledger_entry(tmp_path: Path) -> None:
    root = create_campaign_root(tmp_path / "v6")
    state = json.loads((root / "RUN_STATE.json").read_text())
    assert state["schema_version"] == "membind.v6.run-state.v1"
    assert state["status"] == "INITIALIZED"
    assert (root / "V6_AUTORESEARCH_LEDGER.jsonl").exists()
    assert (root / "environment").is_dir()
    entry = json.loads((root / "V6_AUTORESEARCH_LEDGER.jsonl").read_text().splitlines()[0])
    assert entry["iteration"] == "R00"
    assert entry["test_written_before_code"] == "bootstrap"


def test_ledger_requires_all_machine_readable_fields(tmp_path: Path) -> None:
    root = create_campaign_root(tmp_path / "v6")
    with pytest.raises(V6LedgerError, match="required ledger field"):
        append_ledger_entry(root, {"iteration": "R01"})


def test_state_update_is_fail_closed_and_records_next_action(tmp_path: Path) -> None:
    root = create_campaign_root(tmp_path / "v6")
    update_run_state(root, status="L0_GREEN", active_hypothesis="native request stability", next_action="write Probe A RED")
    state = json.loads((root / "RUN_STATE.json").read_text())
    assert state["status"] == "L0_GREEN"
    assert state["active_hypothesis"] == "native request stability"
    assert state["next_action"] == "write Probe A RED"
    with pytest.raises(V6LedgerError, match="known run-state field"):
        update_run_state(root, unknown="x")


def test_campaign_root_must_be_fresh(tmp_path: Path) -> None:
    target = tmp_path / "v6"
    create_campaign_root(target)
    with pytest.raises(V6LedgerError, match="fresh"):
        create_campaign_root(target)
