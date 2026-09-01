from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_SCRIPT = Path(__file__).parents[2] / "scripts" / "finalize_recent_three_arm_campaign.py"
_SPEC = importlib.util.spec_from_file_location("recent_three_arm_finalizer", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
finalizer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(finalizer)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _attempt(root: Path, method: str, attempt_id: str, *, valid: bool = True) -> Path:
    attempt = root / "context-0" / method / attempt_id
    block = attempt / "block"
    _write(
        attempt / "complete.json" if valid else attempt / "failure.json",
        {"attempt_id": attempt_id, "status": "PASS" if valid else "FAILED", "episode_count": 1},
    )
    if valid:
        _write(block / "construction_seal.json", {"status": "CONSTRUCTION_SEALED"})
        _write(block / "lifecycle_validation.json", {"contract_status": "PASS"})
        _write(block / "order_validation.json", {"order_contract_status": "PASS"})
        _write(block / "refinement_validation.json", {"refinement_status": "PASS"})
        _write(block / "metrics.json", {"t_build_ns": 1000})
        _write(block / "work_inventory.json", {"expected_episode_count": 1})
        _write(block / "graph_diagnostics.json", {"status": "PASS", "canonical_graph_hash": "a" * 64})
        (block / "raw_events.jsonl").write_text(
            '{"event":"FORMAL_START","monotonic_ns":1}\n'
            '{"event":"PUBLICATION_DURABLE","source_sequence":0,"monotonic_ns":1001}\n',
            encoding="utf-8",
        )
    return attempt


def _campaign(tmp_path: Path, *, methods: tuple[str, ...] = finalizer.METHODS) -> Path:
    _write(
        tmp_path / "RECENT_THREE_ARM_CAMPAIGN_PREREGISTRATION.json",
        {"campaign_id": "fixture", "histories": [{"history": 0}]},
    )
    for method in methods:
        _attempt(tmp_path, method, f"{method.lower()}-0")
    return tmp_path


def test_multiple_terminals_require_explicit_ledger_selection(tmp_path: Path) -> None:
    root = _campaign(tmp_path)
    _attempt(root, "NATIVE_SERIAL", "serial-1")
    result = finalizer.finalize(root)
    row = next(row for row in result["rows"] if row["method"] == "NATIVE_SERIAL")
    assert row["validity"] == "MISSING"
    assert row["selection_error"] == "multiple_terminal_attempts_require_ledger_selection"
    block = json.loads((root / "history-0" / "HISTORY_BLOCK_RESULT.json").read_text())
    assert block["status"] == "HISTORY_BLOCK_INCOMPLETE"


def test_explicit_selection_and_validity_filtering_gate_speedup(tmp_path: Path) -> None:
    root = _campaign(tmp_path)
    _attempt(root, "NATIVE_SERIAL", "serial-replacement")
    with (root / "campaign_ledger.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event": "ATTEMPT_TERMINAL_SELECTED",
            "context_index": 0,
            "method": "NATIVE_SERIAL",
            "attempt_id": "serial-replacement",
        }) + "\n")
    result = finalizer.finalize(root)
    assert result["status"] == "PASS"
    assert all(row["validity"] == "PASS_VALID" for row in result["rows"])
    assert result["speedup_core"][0]["speedup"] == 1.0
    assert all("Mem" not in str(row.get("attempt_root")) for row in result["rows"])


def test_scientific_failure_is_never_sealed_or_used_for_speedup(tmp_path: Path) -> None:
    root = _campaign(tmp_path)
    serial = root / "context-0" / "NATIVE_SERIAL" / "native_serial-0"
    (serial / "complete.json").unlink()
    _write(
        serial / "failure.json",
        {
            "attempt_id": "native_serial-0",
            "status": "FAILED",
            "failure_class": "OUTPUT_LENGTH_TRUNCATION",
            "error": "JSONDecodeError",
        },
    )
    result = finalizer.finalize(root)
    row = next(row for row in result["rows"] if row["method"] == "NATIVE_SERIAL")
    assert row["validity"] == "SCIENTIFIC_FAILURE"
    assert result["status"] == "PARTIAL"
    assert result["speedup_core"][0]["speedup"] == "MISSING"
    block = json.loads((root / "history-0" / "HISTORY_BLOCK_RESULT.json").read_text())
    assert block["status"] == "HISTORY_BLOCK_INCOMPLETE"
