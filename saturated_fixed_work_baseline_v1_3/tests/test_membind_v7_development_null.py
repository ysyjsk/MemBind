from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.development_null import (
    DevelopmentNullError,
    build_development_null_terminal,
    write_development_null_terminal,
)


PROJECT = Path(__file__).resolve().parents[1]
CAMPAIGN = PROJECT / "v7/artifacts/v7-development-strict-20260826-002"
ATTEMPT = PROJECT / "v7/artifacts/.v7-development-strict-20260826-002.attempt.jsonl"
METHOD = PROJECT / "v7/METHOD_SELECTION.json"


def test_development_null_terminal_binds_successful_campaign_and_formal_root() -> None:
    terminal = build_development_null_terminal(
        campaign_root=CAMPAIGN,
        attempt_journal_path=ATTEMPT,
        scientific_method_selection_path=METHOD,
    )

    assert terminal["status"] == "V7_DEVELOPMENT_NULL_NO_MEMORY_SPECIFIC_METHOD"
    assert terminal["selected_method"] == "NULL"
    assert terminal["method_implementation_authorized"] is False
    assert terminal["live_treatment_authorized"] is False
    assert terminal["formal_r1_r3_eligible"] is False
    assert terminal["provider_swap_requires_new_formal_campaign"] is True
    assert terminal["gate_result"] == {
        "A": True,
        "B": False,
        "C": False,
        "D": False,
        "E": False,
    }
    assert terminal["metrics"]["csp"] == 0.0
    assert terminal["metrics"]["gross_saved_cp_lb_ns"] == 0
    assert terminal["metrics"]["false_stable_count"] == 0
    assert terminal["metrics"]["false_unaffected_count"] == 0
    assert terminal["campaign_manifest_sha256"] == (
        "9e67ff19fac1dcdf3586754064452ebbabd5338edbc1fced2209eba0cd873c45"
    )
    assert terminal["scientific_method_selection_sha256"] == (
        "0a2958aeeedc2b7b8762247d5d6cf15252e2b6b737b5807a51a127461a632c53"
    )


def test_development_null_terminal_is_exclusive_private_and_content_free(
    tmp_path: Path,
) -> None:
    output = tmp_path / "DEVELOPMENT_NULL_TERMINAL.json"
    result = write_development_null_terminal(
        output,
        campaign_root=CAMPAIGN,
        attempt_journal_path=ATTEMPT,
        scientific_method_selection_path=METHOD,
    )

    assert result["status"] == "V7_DEVELOPMENT_NULL_NO_MEMORY_SPECIFIC_METHOD"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    persisted = output.read_text(encoding="ascii")
    assert "api_key" not in persisted.casefold()
    assert "prompt" not in persisted.casefold()
    with pytest.raises(DevelopmentNullError, match="already exists"):
        write_development_null_terminal(
            output,
            campaign_root=CAMPAIGN,
            attempt_journal_path=ATTEMPT,
            scientific_method_selection_path=METHOD,
        )


def test_development_null_builder_rejects_non_null_selection(tmp_path: Path) -> None:
    copied = tmp_path / "campaign"
    copied.mkdir()
    for path in CAMPAIGN.glob("*.json"):
        (copied / path.name).write_bytes(path.read_bytes())
    selected = json.loads(
        (copied / "DEVELOPMENT_METHOD_SELECTION.json").read_text(encoding="ascii")
    )
    selected["status"] = "DEVELOPMENT_SELECTED"
    selected["selected_method"] = "M1"
    selected["implementation_authorized"] = True
    (copied / "DEVELOPMENT_METHOD_SELECTION.json").write_text(
        json.dumps(selected),
        encoding="ascii",
    )

    with pytest.raises(DevelopmentNullError):
        build_development_null_terminal(
            campaign_root=copied,
            attempt_journal_path=ATTEMPT,
            scientific_method_selection_path=METHOD,
        )
