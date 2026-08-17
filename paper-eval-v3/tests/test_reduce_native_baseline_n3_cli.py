from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.native_baseline_runner import DEVELOPMENT_HISTORIES


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reduce_native_baseline_n3.py"
SPEC = importlib.util.spec_from_file_location("reduce_native_baseline_n3_cli", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
reduce_native_baseline_n3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reduce_native_baseline_n3)


RUN_ID = "nb-cli-fixture-001"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="ascii",
    )


def _complete_report() -> dict[str, object]:
    per_history = []
    for index, history_id in enumerate(DEVELOPMENT_HISTORIES):
        per_history.append(
            {
                "history_id": history_id,
                "episode_count": 40 + index,
                "headline_metrics": {
                    "qa_accuracy": 0.5,
                    "evidence_recall_at_10": 0.75,
                    "direct_violations": 0,
                    "p95_freshness_ns": 1000 + index,
                    "successful_goodput": 0.1,
                    "makespan_ns": 400_000_000_000,
                },
                "secondary_metrics": {
                    "p99_freshness_ns": 1200 + index,
                    "max_backlog": None,
                    "max_backlog_status": "NOT_APPLICABLE_SERIAL_BASELINE",
                },
                "quality_status": "SUCCESS",
                "work_volume": {"llm_logical_calls": 10},
                "graph_work": {"nodes_added": 5},
                "graph_work_total": 5.0,
                "system_work_total": 10.0,
            }
        )
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.native-baseline-n3.v1",
        "run_id": RUN_ID,
        "method": "U0",
        "repeat_id": 0,
        "aggregation_unit": "history_macro_equal_weight",
        "target_histories": list(DEVELOPMENT_HISTORIES),
        "eligibility": True,
        "ineligibility_reasons": [],
        "decision": "HEALTHY_FOR_NEXT_BASELINE",
        "decision_reasons": [],
        "successful_goodput_unit": "episodes_per_second",
        "per_history": per_history,
        "macro_descriptive": {
            "qa_accuracy": {
                "history_count": 4,
                "mean": 0.5,
                "median": 0.5,
                "min": 0.5,
                "max": 0.5,
            }
        },
        "secondary_metrics": {
            "p99_freshness_ns": {
                "history_count": 4,
                "mean": 1201.5,
                "median": 1201.5,
                "min": 1200,
                "max": 1203,
            },
            "max_backlog": None,
            "max_backlog_status": "NOT_APPLICABLE_SERIAL_BASELINE",
        },
        "scientific_scope": "DESCRIPTIVE_DEVELOPMENT_SCREEN_ONLY",
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _write_complete_shape(run_root: Path) -> None:
    for index, history_id in enumerate(DEVELOPMENT_HISTORIES):
        root = run_root / RUN_ID / history_id
        _write_json(
            root / "checkpoint.json",
            {
                "history_id": history_id,
                "marker": f"checkpoint-{index}",
                "status": "completed",
            },
        )
        _write_json(
            root / "history_result.json",
            {"history_id": history_id, "marker": f"result-{index}"},
        )
        for filename in reduce_native_baseline_n3.LEVEL_ZERO_FILES.values():
            _write_jsonl(root / filename, [{"history_id": history_id}])


def test_incomplete_filesystem_writes_screen_and_report_but_never_decision(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "runs"
    output_dir = tmp_path / "out"

    outcome = reduce_native_baseline_n3.generate_outputs(
        run_id=RUN_ID,
        run_root=run_root,
        output_dir=output_dir,
    )

    screen = json.loads((output_dir / "NATIVE_BASELINE_SCREEN.json").read_text())
    assert outcome == screen
    assert screen["eligibility"] is False
    assert screen["decision"] is None
    assert not (output_dir / "NATIVE_BASELINE_DECISION.json").exists()
    report = (output_dir / "NATIVE_BASELINE_REPORT.md").read_text()
    assert "INCOMPLETE" in report
    assert "DESCRIPTIVE_DEVELOPMENT_SCREEN_ONLY" in report


def test_complete_filesystem_is_loaded_in_fixed_order_and_writes_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "runs"
    output_dir = tmp_path / "out"
    _write_complete_shape(run_root)
    expected_report = _complete_report()
    observed: dict[str, object] = {}

    def fake_reducer(*, run_id: str, history_evidence: list[dict[str, object]]) -> dict:
        observed["run_id"] = run_id
        observed["history_evidence"] = history_evidence
        return expected_report

    monkeypatch.setattr(
        reduce_native_baseline_n3,
        "reduce_native_baseline_n3",
        fake_reducer,
    )

    reduce_native_baseline_n3.generate_outputs(
        run_id=RUN_ID,
        run_root=run_root,
        output_dir=output_dir,
    )

    evidence = observed["history_evidence"]
    assert isinstance(evidence, list)
    assert [row["checkpoint"]["history_id"] for row in evidence] == list(
        DEVELOPMENT_HISTORIES
    )
    assert evidence[0]["raw_rows"]["per_episode"] == [
        {"history_id": DEVELOPMENT_HISTORIES[0]}
    ]
    decision = json.loads(
        (output_dir / "NATIVE_BASELINE_DECISION.json").read_text()
    )
    assert decision["decision"] == "HEALTHY_FOR_NEXT_BASELINE"
    assert decision["source_screen_payload_sha256"] == expected_report["payload_sha256"]
    assert decision["scientific_scope"] == "DESCRIPTIVE_DEVELOPMENT_SCREEN_ONLY"
    assert decision["payload_sha256"] == payload_sha256(
        {name: value for name, value in decision.items() if name != "payload_sha256"}
    )
    markdown = (output_dir / "NATIVE_BASELINE_REPORT.md").read_text()
    assert "07741c45" in markdown
    assert "history_macro_equal_weight" in markdown
    assert "does not establish a MemBind benefit claim" in markdown


def test_completed_marker_with_missing_level_zero_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "runs"
    output_dir = tmp_path / "out"
    _write_complete_shape(run_root)
    missing = (
        run_root
        / RUN_ID
        / DEVELOPMENT_HISTORIES[0]
        / reduce_native_baseline_n3.LEVEL_ZERO_FILES["spans"]
    )
    missing.unlink()

    with pytest.raises(RuntimeError, match="level_zero_file_missing:spans"):
        reduce_native_baseline_n3.generate_outputs(
            run_id=RUN_ID,
            run_root=run_root,
            output_dir=output_dir,
        )

    assert not (output_dir / "NATIVE_BASELINE_SCREEN.json").exists()
    assert not (output_dir / "NATIVE_BASELINE_DECISION.json").exists()
