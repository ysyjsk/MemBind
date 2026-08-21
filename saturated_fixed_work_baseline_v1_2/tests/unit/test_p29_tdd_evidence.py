from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.tdd_evidence import (
    TddEvidenceError,
    append_observation_order_amendment,
    verify_tdd_evidence,
)


SCHEMA = "membind.saturated-fixed-work.tdd-evidence.v1"


def _row(stage: str, event: str, observed_at: str, exit_code: int) -> dict[str, object]:
    return {
        "schema_version": SCHEMA,
        "stage": stage,
        "event": event,
        "command": "pytest -q tests/unit/test_capability.py",
        "exit_code": exit_code,
        "observed_at": observed_at,
        "output_summary": "observed result",
    }


def _write(path: Path, rows: list[dict[str, object]]) -> list[str]:
    lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def test_verifier_requires_observed_red_before_green(tmp_path: Path) -> None:
    journal = tmp_path / "tdd_evidence.jsonl"
    _write(
        journal,
        [
            _row("P29", "RED", "2026-08-21T04:30:00+08:00", 1),
            _row("P29", "GREEN", "2026-08-21T04:31:00+08:00", 0),
        ],
    )

    result = verify_tdd_evidence(journal, required_red_green_stages=("P29",))

    assert result["verified"] is True
    assert result["verified_red_green_stages"] == ["P29"]
    assert result["amendment_count"] == 0
    assert result["journal_sha256"] == hashlib.sha256(journal.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("red_exit", "green_exit", "error"),
    [(0, 0, "RED_EXIT_CODE_INVALID"), (1, 1, "GREEN_EXIT_CODE_INVALID")],
)
def test_verifier_rejects_exit_code_semantic_drift(
    tmp_path: Path, red_exit: int, green_exit: int, error: str
) -> None:
    journal = tmp_path / "tdd_evidence.jsonl"
    _write(
        journal,
        [
            _row("P29", "RED", "2026-08-21T04:30:00+08:00", red_exit),
            _row("P29", "GREEN", "2026-08-21T04:31:00+08:00", green_exit),
        ],
    )
    with pytest.raises(TddEvidenceError, match=error):
        verify_tdd_evidence(journal, required_red_green_stages=("P29",))


def test_reversed_timestamps_require_hash_bound_append_only_amendment(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "tdd_evidence.jsonl"
    lines = _write(
        journal,
        [
            _row("P25", "RED", "2026-08-21T04:24:00+08:00", 1),
            _row("P25", "GREEN", "2026-08-21T04:23:20+08:00", 0),
        ],
    )
    with pytest.raises(TddEvidenceError, match="OBSERVATION_ORDER_INVALID"):
        verify_tdd_evidence(journal, required_red_green_stages=("P25",))

    before = journal.read_bytes()
    amendment = append_observation_order_amendment(
        journal,
        target_line_sha256=hashlib.sha256(lines[0].encode("utf-8")).hexdigest(),
        related_line_sha256=hashlib.sha256(lines[1].encode("utf-8")).hexdigest(),
        amended_at="2026-08-21T04:40:00+08:00",
        reason="The RED timestamp was transcribed after the command; journal order is authoritative.",
    )

    after = journal.read_bytes()
    assert after.startswith(before)
    assert amendment["corrected_relation"] == "BEFORE"
    result = verify_tdd_evidence(journal, required_red_green_stages=("P25",))
    assert result["verified"] is True
    assert result["amendment_count"] == 1
    assert result["amended_stage_pairs"] == ["P25"]


def test_amendment_cannot_target_changed_or_future_rows(tmp_path: Path) -> None:
    journal = tmp_path / "tdd_evidence.jsonl"
    lines = _write(
        journal,
        [
            _row("P25", "RED", "2026-08-21T04:24:00+08:00", 1),
            _row("P25", "GREEN", "2026-08-21T04:23:20+08:00", 0),
        ],
    )
    append_observation_order_amendment(
        journal,
        target_line_sha256=hashlib.sha256(lines[0].encode("utf-8")).hexdigest(),
        related_line_sha256="f" * 64,
        amended_at="2026-08-21T04:40:00+08:00",
        reason="Invalid related row used to exercise fail-closed verification.",
    )
    with pytest.raises(TddEvidenceError, match="AMENDMENT_RELATED_ROW_UNKNOWN"):
        verify_tdd_evidence(journal, required_red_green_stages=("P25",))


def test_required_stage_must_have_both_red_and_green(tmp_path: Path) -> None:
    journal = tmp_path / "tdd_evidence.jsonl"
    _write(
        journal,
        [_row("P29", "GREEN", "2026-08-21T04:31:00+08:00", 0)],
    )
    with pytest.raises(TddEvidenceError, match="REQUIRED_RED_GREEN_STAGE_INCOMPLETE"):
        verify_tdd_evidence(journal, required_red_green_stages=("P29",))
