"""TDD contracts for the sealed, offline-only MemBind v4 final reducer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v4.freeze import FORMAL_HISTORY_IDS
from paper_eval.membind_v4.full_run import FULL_RESULT_SCHEMA
from paper_eval.membind_v4.reducer import (
    V4_FINAL_OUTPUT_FILES,
    V4ReducerError,
    reduce_v4_final,
    write_v4_final_outputs,
)


def _seal(body: dict[str, object]) -> dict[str, object]:
    value = dict(body)
    value["payload_sha256"] = payload_sha256(value)
    return value


def _write(path: Path, value: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, value)
    return path


def _performance(scale: int, count: int) -> dict[str, object]:
    freshness = [scale * (index + 1) for index in range(count)]
    return {
        "makespan_ns": scale * count * 10,
        "freshness_ns": freshness,
        "published_episode_count": count,
        "goodput_episodes_per_second": count * 1_000_000_000 / (scale * count * 10),
    }


def _fixture(tmp_path: Path, *, runner_mode: str = "live", envelope: str = "FORMAL_LIVE_ENVELOPE_MATCH") -> dict[str, Path]:
    evidence = tmp_path / "evidence"
    baseline_results: list[dict[str, object]] = []
    for index, method in enumerate(("U0-aligned", "A0-aligned", "P(C=2)-aligned"), start=1):
        result = _seal(
            {
                "schema_version": "test.baseline.v1",
                "status": "PASS",
                "method": method,
                "source_count": 188,
                "direct_violation_count": 0,
                "performance": _performance(index * 100, 188),
            }
        )
        path = _write(evidence / f"baseline-{index}.json", result)
        baseline_results.append(
            {
                "role": "baseline_result",
                "method": method,
                "absolute_path": str(path.resolve()),
                "sha256": sha256_file(path),
                "status": "PASS",
            }
        )
    baseline = _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v4-baseline-binding.v1",
            "status": "PASS",
            "identity_consistency": {"status": envelope},
            "artifacts": {"baseline": baseline_results},
        }
    )
    baseline_path = _write(evidence / "BASELINE_BINDING.json", baseline)

    prefix = _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v4-prefix-reference.v1",
            "status": "PASS",
            "history_id": FORMAL_HISTORY_IDS[0],
            "prefixes": {},
        }
    )
    prefix_path = _write(evidence / "PREFIX_REFERENCE.json", prefix)

    v31 = _seal(
        {
            "schema_version": "test.v31.formal.v1",
            "status": "PASS",
            "method": "MemBind v3.1",
            "source_count": 188,
            "direct_violation_count": 0,
            "formal_main_table_eligible": True,
            "performance": _performance(80, 188),
        }
    )
    v31_path = _write(evidence / "V31_RESULT.json", v31)

    quality = _seal(
        {
            "schema_version": "test.quality.v1",
            "status": "PASS",
            "summary": {
                "by_method": {
                    method: {
                        "recall_at_1_macro": 0.5,
                        "recall_at_3_macro": 0.75,
                        "recall_at_5_macro": 0.8,
                        "recall_at_10_macro": 0.9,
                        "mrr_macro": 0.7,
                        "ndcg_at_10_macro": 0.8,
                        "qa_accuracy": 0.5,
                    }
                    for method in ("U0", "A0", "P(C=2)", "MemBind v3.1", "MemBind v4")
                }
            },
        }
    )
    quality_path = _write(evidence / "QUALITY.json", quality)

    frozen = _seal(
        {
            "schema_version": "membind.paper-eval-v4.frozen-method.v1",
            "status": "FROZEN",
            "candidate_id": "c01",
            "policy": "resource-gated-node-resolve-speculation",
            "thresholds": {"global_k": 2},
            "formal_history_ids": list(FORMAL_HISTORY_IDS),
            "evidence": {
                "baseline_binding": {
                    "role": "baseline_binding",
                    "absolute_path": str(baseline_path.resolve()),
                    "sha256": sha256_file(baseline_path),
                },
                "prefix_reference": {
                    "role": "prefix_reference",
                    "absolute_path": str(prefix_path.resolve()),
                    "sha256": sha256_file(prefix_path),
                },
            },
        }
    )
    frozen_path = _write(evidence / "V4_FROZEN_METHOD.json", frozen)

    counts = (49, 46, 44, 49)
    histories: list[dict[str, object]] = []
    for history_id, count in zip(FORMAL_HISTORY_IDS, counts, strict=True):
        histories.append(
            {
                "history_id": history_id,
                "run_id": f"v4-{history_id}",
                "namespace": f"ns-{history_id}",
                "source_count": count,
                "result_payload_sha256": str(count).zfill(64),
                "result": {
                    "performance": _performance(50, count),
                    "telemetry": {
                        "qualified_node_resolve_count": count,
                        "speculation_launch_count": 2,
                        "semantic_hit_count": 1,
                        "semantic_miss_count": 1,
                        "hidden_critical_time_ns": 100,
                        "miss_waste_tokens": 10,
                        "validation_overhead_ns": 5,
                        "frontier_interference_count": 0,
                        "persistent_write_count": 0,
                        "active_two_useful_ns": 30,
                        "active_two_total_ns": 40,
                    },
                    "work_volume": {
                        "llm_logical_calls": count * 2,
                        "llm_transport_attempts": count * 2 + 2,
                        "prompt_tokens": count * 100,
                        "completion_tokens": count * 10,
                        "embedding_calls": count,
                        "db_operations": count * 3,
                        "speculative_wasted_calls": 1,
                        "speculative_wasted_tokens": 10,
                    },
                    "final_graph": {
                        "node_count": count * 3,
                        "edge_count": count * 2,
                        "episode_count": count,
                    },
                },
            }
        )
    full = _seal(
        {
            "schema_version": FULL_RESULT_SCHEMA,
            "status": "PASS",
            "run_id": "v4-full-test",
            "runner_mode": runner_mode,
            "formal_main_table_eligible": runner_mode == "live",
            "manifest_payload_sha256": "a" * 64,
            "frozen_method_payload_sha256": frozen["payload_sha256"],
            "history_ids": list(FORMAL_HISTORY_IDS),
            "history_count": 4,
            "source_count": 188,
            "direct_violation_count": 0,
            "histories": histories,
        }
    )
    full_path = _write(evidence / "FULL_RUN_RESULT.json", full)
    return {
        "frozen": frozen_path,
        "full": full_path,
        "baseline": baseline_path,
        "prefix": prefix_path,
        "v31": v31_path,
        "quality": quality_path,
    }


def _reduce(paths: dict[str, Path]) -> dict[str, object]:
    return reduce_v4_final(
        frozen_method_path=paths["frozen"],
        full_run_result_path=paths["full"],
        baseline_binding_path=paths["baseline"],
        prefix_reference_path=paths["prefix"],
        v31_result_path=paths.get("v31"),
        quality_overlay_path=paths.get("quality"),
    )


def test_final_reducer_fails_closed_when_live_result_is_missing_or_tampered(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    paths["full"] = tmp_path / "missing.json"
    with pytest.raises(V4ReducerError, match="full_run_result_unreadable"):
        _reduce(paths)

    paths = _fixture(tmp_path / "tampered")
    raw = json.loads(paths["full"].read_text(encoding="utf-8"))
    raw["source_count"] = 187
    atomic_write_json(paths["full"], raw)
    with pytest.raises(V4ReducerError, match="full_run_result_payload_hash_mismatch"):
        _reduce(paths)


def test_fixture_and_mixed_envelope_are_never_formal_main_table_eligible(tmp_path: Path) -> None:
    paths = _fixture(
        tmp_path,
        runner_mode="fixture",
        envelope="MIXED_ENVELOPES_NOT_FORMAL_COMPARISON",
    )
    outputs = _reduce(paths)
    result = outputs["V4_FULL_RESULT.json"]
    assert result["status"] == "PASS_NON_FORMAL"
    assert result["formal_main_table_eligible"] is False
    assert set(result["eligibility_reasons"]) == {
        "FULL_RUN_MODE_NOT_LIVE",
        "FULL_RUN_DECLARED_INELIGIBLE",
        "MIXED_OR_UNQUALIFIED_COMPARISON_ENVELOPE",
    }
    assert outputs["V4_MAIN_TABLE.json"]["formal_main_table_eligible"] is False


def test_blocked_run_reduces_to_non_formal_not_available_tables(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    failure = _seal(
        {
            "schema_version": "membind.paper-eval-v4.full-run-failure.v1",
            "status": "FAILED_NON_MERGEABLE",
            "formal_main_table_eligible": False,
            "manifest_payload_sha256": "a" * 64,
            "classification": "EXECUTION_SANDBOX_NETWORK_ISOLATION",
            "history_id": None,
            "error_class": None,
            "error_code": "EXECUTION_SANDBOX_NETWORK_ISOLATION",
            "completed_history_result_payload_sha256s": [],
        }
    )
    _write(paths["full"], failure)
    outputs = _reduce(paths)
    assert outputs["V4_FULL_RESULT.json"]["status"] == "BLOCKED_NON_FORMAL"
    assert outputs["V4_FULL_RESULT.json"]["eligibility_reasons"] == ["FULL_RUN_BLOCKED"]
    assert outputs["V4_MECHANISM_TABLE.json"]["status"] == "NOT_AVAILABLE"
    v4 = outputs["V4_MAIN_TABLE.json"]["rows"][-1]
    assert v4["makespan_ns"] == "NOT_AVAILABLE"
    assert v4["speedup_vs_u0"] == "NOT_AVAILABLE"
    assert v4["speedup_vs_v31"] == "NOT_AVAILABLE"


def test_final_reducer_aggregates_available_performance_mechanism_and_work(tmp_path: Path) -> None:
    outputs = _reduce(_fixture(tmp_path))
    table = outputs["V4_MAIN_TABLE.json"]
    rows = {row["method"]: row for row in table["rows"]}
    v4 = rows["MemBind v4"]
    assert v4["episode_count"] == 188
    assert v4["makespan_ns"] == 94_000
    assert v4["goodput_episodes_per_second"] == pytest.approx(2_000_000.0)
    assert v4["p50_freshness_ns"] == 1_200
    assert v4["p95_freshness_ns"] == 2_250
    assert v4["p99_freshness_ns"] == 2_450
    assert v4["speedup_vs_u0"] == pytest.approx(2.0)
    assert v4["speedup_vs_v31"] == pytest.approx(1.6)
    assert v4["direct_violations"] == 0

    mechanism = outputs["V4_MECHANISM_TABLE.json"]
    v4_mechanism = mechanism["by_method"]["MemBind v4"]
    assert v4_mechanism["node_resolve_qualified"] == 188
    assert v4_mechanism["speculation_launched"] == 8
    assert v4_mechanism["hit_count"] == 4
    assert v4_mechanism["miss_count"] == 4
    assert v4_mechanism["active_two_useful_fraction"] == pytest.approx(0.75)
    assert v4_mechanism["work_volume"]["llm_logical_calls"] == 376
    assert v4_mechanism["work_volume"]["speculative_wasted_calls"] == 4

    quality = outputs["V4_QUALITY_OVERLAY.json"]
    assert quality["status"] == "AVAILABLE"
    assert quality["by_method"]["MemBind v4"]["recall_at_10"] == 0.9


def test_writer_emits_only_six_deterministic_sealed_outputs(tmp_path: Path) -> None:
    outputs = _reduce(_fixture(tmp_path / "inputs"))
    root = tmp_path / "outputs"
    write_v4_final_outputs(root, outputs)
    write_v4_final_outputs(root, outputs)

    assert tuple(sorted(path.name for path in root.iterdir())) == tuple(sorted(V4_FINAL_OUTPUT_FILES))
    for name in V4_FINAL_OUTPUT_FILES:
        path = root / name
        if path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            digest = value.pop("payload_sha256")
            assert digest == payload_sha256(value)
    report = (root / "V4_FINAL_REPORT.md").read_text(encoding="ascii")
    assert "MemBind v4 Final Report" in report
    assert "FORMAL_MAIN_TABLE_ELIGIBLE" in report

    changed = dict(outputs)
    changed["V4_FINAL_REPORT.md"] = str(outputs["V4_FINAL_REPORT.md"]) + "drift\n"
    with pytest.raises(V4ReducerError, match="existing_output_drift"):
        write_v4_final_outputs(root, changed)
