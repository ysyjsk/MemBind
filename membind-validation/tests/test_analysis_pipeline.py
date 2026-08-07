import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis_pipeline import analyze_artifacts  # noqa: E402
from statistics import decide_go_no_go  # noqa: E402


class AnalysisPipelineTests(TestCase):
    def test_analysis_uses_only_formal_plan_and_writes_all_required_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            plan = []
            for qid in ("q0", "q1"):
                plan.extend(
                    [
                        _spec(f"capture-{qid}", qid, "correctness", "M0", "capture", 0),
                        _spec(f"replay-{qid}", qid, "correctness", "M2", "replay", 0),
                    ]
                )
                for method, latency in (("M0", 100.0), ("M1", 60.0), ("M2", 50.0)):
                    for repeat in (0, 1):
                        run_id = f"perf-{qid}-{method}-{repeat}"
                        plan.append(_spec(run_id, qid, "performance", method, "live", repeat))
                        _write_trace(artifacts, run_id, qid, method, repeat, latency)

            final = artifacts / "final"
            final.mkdir(parents=True)
            (final / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n",
                encoding="utf-8",
            )
            for item in plan:
                _write_status(artifacts, item)
                trace_path = artifacts / "traces" / f"{item['run_id']}.jsonl"
                if not trace_path.exists():
                    _write_trace(
                        artifacts,
                        item["run_id"],
                        item["question_id"],
                        item["method"],
                        item["repeat"],
                        10.0,
                    )
                graph = _graph(item["question_id"])
                if item["method"] == "M1" and item["question_id"] == "q0":
                    graph = _graph("divergent")
                _write_json(artifacts / "graphs" / f"{item['run_id']}.canonical.json", graph)
                retrieved = ["gold"]
                if item["method"] == "M1" and item["question_id"] == "q0":
                    retrieved = ["wrong"]
                _write_json(
                    artifacts / "retrieval" / f"{item['run_id']}.json",
                    {
                        "question_id": item["question_id"],
                        "gold_episode_ids": ["gold"],
                        "retrieved_episode_ids": retrieved,
                        "metrics": {"evidence_recall_at_10": float(retrieved == ["gold"])},
                    },
                )

            result = analyze_artifacts(artifacts, bootstrap_samples=100)

            self.assertEqual(result["m2_canonical_graph_parity_count"], 2)
            self.assertEqual(result["m1_divergence_count"], 1)
            self.assertEqual(result["m1_canonical_graph_parity_count"], 2)
            self.assertEqual(result["m1_graph_comparison_count"], 4)
            self.assertEqual(result["m1_canonical_graph_comparison_count"], 4)
            self.assertEqual(result["m1_canonical_graph_divergence_question_count"], 1)
            self.assertGreater(result["m2_m0_makespan_geomean_speedup"], 1.0)
            comparisons = result["performance_comparisons"]
            for metric in (
                "p95_arrival_to_publish_ms",
                "makespan_ms",
                "drain_ms",
            ):
                self.assertEqual(
                    set(comparisons[metric]),
                    {"M2_vs_M0", "M1_vs_M0", "M2_vs_M1"},
                )
                for comparison in comparisons[metric].values():
                    self.assertEqual(comparison["pair_count"], 2)
                    self.assertGreater(comparison["geometric_mean_speedup"], 1.0)
                    self.assertGreater(comparison["median_speedup"], 1.0)
                    self.assertLessEqual(
                        comparison["bootstrap_ci"]["lower"],
                        comparison["bootstrap_ci"]["upper"],
                    )
            p95 = comparisons["p95_arrival_to_publish_ms"]
            self.assertEqual(p95["M2_vs_M0"]["baseline_method"], "M0")
            self.assertEqual(p95["M2_vs_M0"]["candidate_method"], "M2")
            self.assertAlmostEqual(p95["M2_vs_M0"]["geometric_mean_speedup"], 2.0)
            self.assertAlmostEqual(p95["M1_vs_M0"]["geometric_mean_speedup"], 100 / 60)
            self.assertAlmostEqual(p95["M2_vs_M1"]["geometric_mean_speedup"], 60 / 50)
            self.assertEqual(result["bootstrap_resampling_unit"], "question_id")
            self.assertEqual(result["bootstrap_samples"], 100)
            self.assertEqual(result["bootstrap_seed"], 20260806)
            self.assertEqual(result["bootstrap_confidence_level"], 0.95)
            self.assertTrue(result["m2_correctness_exactly_once"])
            self.assertEqual(result["m2_correctness_exactly_once_count"], 2)
            self.assertEqual(result["m2_correctness_expected_run_count"], 2)
            self.assertEqual(result["m2_correctness_source_order_violation_count"], 0)
            self.assertEqual(result["m2_correctness_unexpected_prompt_run_count"], 0)
            self.assertEqual(result["m2_correctness_unexpected_prompt_run_ids"], [])
            retrieval = result["retrieval_parity"]["correctness_M2_vs_M0"]
            self.assertEqual(retrieval["instance_count"], 2)
            self.assertEqual(retrieval["mean_m0_evidence_recall_at_5"], 1.0)
            self.assertEqual(retrieval["mean_candidate_evidence_recall_at_5"], 1.0)
            self.assertEqual(retrieval["mean_episode_set_overlap_with_m0"], 1.0)
            self.assertEqual(retrieval["mean_rank_biased_overlap_with_m0"], 1.0)
            required = [
                "run_manifest.parquet",
                "episode_metrics.parquet",
                "instance_metrics.parquet",
                "graph_parity.csv",
                "retrieval_metrics.csv",
                "statistical_summary.json",
                "figure_p95_latency.pdf",
                "figure_makespan.pdf",
                "figure_parity.pdf",
                "VALIDATION_REPORT.md",
            ]
            for name in required:
                path = final / name
                self.assertTrue(path.exists(), name)
                self.assertGreater(path.stat().st_size, 0, name)
            report = (final / "VALIDATION_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("1. 是否保持原生语义？", report)
            self.assertIn("4. 是否值得继续？", report)
            self.assertIn("M2 vs M0", report)
            self.assertIn("M1 vs M0", report)
            self.assertIn("M2 vs M1", report)
            self.assertIn("drain", report)
            self.assertIn("exactly-once", report)
            self.assertIn("episode-set overlap", report)
            self.assertIn("rank-biased overlap", report)
            self.assertIn("M1 canonical parity 为 2/4", report)
            self.assertIn("P95 CI lower > 1.0", report)
            self.assertIn("Recall@10 drop <= 1 pp", report)
            self.assertIn("LLM token growth <= 5%", report)

    def test_analysis_marks_pending_formal_runs_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            plan = [_spec("pending-0", "q0", "performance", "M0", "live", 0)]
            (final / "run_plan.jsonl").write_text(
                json.dumps(plan[0]) + "\n", encoding="utf-8"
            )

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            self.assertEqual(result["pending_run_count"], 1)
            self.assertEqual(result["decision"], "INCONCLUSIVE")
            report = (final / "VALIDATION_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("INCONCLUSIVE", report)
            self.assertIn("Late Binding 的必要性未被证明", report)

    def test_correctness_exactly_once_is_independent_from_source_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            capture = _spec("capture-q0", "q0", "correctness", "M0", "capture", 0)
            replay = _spec("replay-q0", "q0", "correctness", "M2", "replay", 0)
            plan = [capture, replay]
            (final / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n",
                encoding="utf-8",
            )
            for item in plan:
                _write_status(artifacts, item, episode_count=2)
                _write_trace_rows(
                    artifacts,
                    item,
                    latencies=[50.0, 50.0],
                    publish_order=[1, 0] if item["method"] == "M2" else [0, 1],
                )
                _write_outputs(artifacts, item, _graph("q0", episode_count=2))

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            self.assertTrue(result["m2_correctness_exactly_once"])
            self.assertEqual(result["m2_correctness_exactly_once_count"], 1)
            self.assertEqual(result["m2_correctness_source_order_violation_count"], 1)
            self.assertTrue(result["m2_source_order_violation"])

    def test_structured_success_rate_uses_logical_requests_not_internal_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            item = _spec("logical-retry", "q0", "performance", "M0", "live", 0)
            (final / "run_plan.jsonl").write_text(
                json.dumps(item) + "\n", encoding="utf-8"
            )
            _write_json(
                artifacts / "runs" / "logical-retry.json",
                {
                    **item,
                    "status": "success",
                    "episode_count": 1,
                    "post_run_node_count": 0,
                    "llm_metrics": {
                        "llm_call_count": 3,
                        "structured_parse_failures": 2,
                        "structured_request_count": 1,
                        "structured_response_failures": 0,
                    },
                },
            )

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            self.assertEqual(result["structured_output_parse_success_rate"], 1.0)

    def test_analysis_does_not_report_speedup_when_formal_run_is_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            plan = [
                _spec("m0", "q0", "performance", "M0", "live", 0),
                _spec("m2", "q0", "performance", "M2", "live", 0),
            ]
            (final / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n", encoding="utf-8"
            )
            _write_status(artifacts, plan[0])
            _write_trace(artifacts, "m0", "q0", "M0", 0, 100.0)
            _write_trace(artifacts, "m2", "q0", "M2", 0, 50.0)

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            self.assertEqual(result["pending_run_count"], 1)
            self.assertEqual(result["decision"], "INCONCLUSIVE")
            self.assertNotEqual(result["m2_m0_p95_geomean_speedup"], result["m2_m0_p95_geomean_speedup"])
            self.assertNotEqual(result["m2_m0_makespan_geomean_speedup"], result["m2_m0_makespan_geomean_speedup"])

    def test_failed_and_partial_questions_are_excluded_from_every_performance_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            plan = []
            for qid in ("complete", "failed", "partial"):
                for method, latency in (("M0", 100.0), ("M1", 75.0), ("M2", 50.0)):
                    for repeat in (0, 1):
                        run_id = f"perf-{qid}-{method}-{repeat}"
                        item = _spec(run_id, qid, "performance", method, "live", repeat)
                        plan.append(item)
                        _write_status(artifacts, item)
                        _write_trace(artifacts, run_id, qid, method, repeat, latency)
                        _write_outputs(artifacts, item, _graph(qid))

            failed = next(
                item
                for item in plan
                if item["question_id"] == "failed"
                and item["method"] == "M1"
                and item["repeat"] == 1
            )
            _write_status(
                artifacts,
                failed,
                status="failed",
                error="RuntimeError('construction failed after partial output')",
            )
            partial = next(
                item
                for item in plan
                if item["question_id"] == "partial"
                and item["method"] == "M2"
                and item["repeat"] == 1
            )
            _write_status(artifacts, partial, episode_count=2)
            (final / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n",
                encoding="utf-8",
            )

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            episodes = pd.read_parquet(final / "episode_metrics.parquet")
            instances = pd.read_parquet(final / "instance_metrics.parquet")
            self.assertEqual(set(episodes["question_id"]), {"complete"})
            self.assertEqual(set(instances["question_id"]), {"complete"})
            graph_csv = (final / "graph_parity.csv").read_text(encoding="utf-8")
            retrieval_csv = (final / "retrieval_metrics.csv").read_text(encoding="utf-8")
            self.assertIn("complete", graph_csv)
            self.assertNotIn("failed", graph_csv)
            self.assertNotIn("partial", graph_csv)
            self.assertIn("complete", retrieval_csv)
            self.assertNotIn("failed", retrieval_csv)
            self.assertNotIn("partial", retrieval_csv)
            self.assertEqual(result["performance_analysis_instance_count"], 1)
            self.assertEqual(result["m0_performance_llm_tokens"], 200)
            self.assertEqual(result["m2_performance_llm_tokens"], 200)

    def test_per_method_failure_rate_and_hard_anomalies_force_inconclusive(self):
        otherwise_go = {
            "pending_run_count": 0,
            "completed_live_instance_count": 7,
            "structured_output_parse_success_rate": 1.0,
            "failed_run_rate": 1 / 64,
            "failed_run_rates_by_method": {"M0": 0.0, "M1": 1 / 16, "M2": 0.0},
            "hard_inconclusive_reasons": [],
            "m2_canonical_graph_parity_count": 8,
            "m2_m0_makespan_geomean_speedup": 1.6,
            "m2_m0_p95_latency_reduction": 0.40,
            "m2_m0_p95_speedup_ci_lower": 1.1,
            "m2_recall10_drop_pp": 0.0,
            "m2_llm_token_growth": 0.0,
            "m2_source_order_violation": False,
            "m1_divergence_count": 1,
        }

        self.assertEqual(decide_go_no_go(otherwise_go), "INCONCLUSIVE")
        otherwise_go["failed_run_rates_by_method"] = {"M0": 0.0, "M1": 0.0, "M2": 0.0}
        otherwise_go["hard_inconclusive_reasons"] = ["database_isolation_failure:run-1"]
        self.assertEqual(decide_go_no_go(otherwise_go), "INCONCLUSIVE")

    def test_go_requires_m2_correctness_exactly_once(self):
        otherwise_go = {
            "pending_run_count": 0,
            "completed_live_instance_count": 8,
            "structured_output_parse_success_rate": 1.0,
            "failed_run_rate": 0.0,
            "failed_run_rates_by_method": {"M0": 0.0, "M1": 0.0, "M2": 0.0},
            "hard_inconclusive_reasons": [],
            "m2_canonical_graph_parity_count": 8,
            "m2_correctness_expected_run_count": 8,
            "m2_correctness_exactly_once": False,
            "m2_m0_makespan_geomean_speedup": 1.6,
            "m2_m0_p95_latency_reduction": 0.40,
            "m2_m0_p95_speedup_ci_lower": 1.1,
            "m2_recall10_drop_pp": 0.0,
            "m2_llm_token_growth": 0.0,
            "m2_source_order_violation": False,
            "m1_divergence_count": 1,
        }
        self.assertEqual(decide_go_no_go(otherwise_go), "NO-GO")

    def test_analysis_classifies_database_cache_and_oom_failures_as_hard_anomalies(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            cases = (
                ("db", "M0", "database isolation failure: 2 nodes remain"),
                ("cache", "M1", "cache contains conflicting responses for hash"),
                ("oom", "M2", "CUDA out of memory"),
            )
            plan = []
            for qid, method, error in cases:
                item = _spec(f"run-{qid}", qid, "performance", method, "live", 0)
                plan.append(item)
                _write_status(artifacts, item, status="failed", error=error)
            (final / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n",
                encoding="utf-8",
            )

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            self.assertEqual(result["decision"], "INCONCLUSIVE")
            reasons = "\n".join(result["hard_inconclusive_reasons"])
            self.assertIn("database_isolation_failure", reasons)
            self.assertIn("response_cache_conflict", reasons)
            self.assertIn("gpu_oom", reasons)
            report = (final / "VALIDATION_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("gpu_oom", report)

    def test_malformed_trace_is_excluded_instead_of_crashing_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            plan = []
            for method in ("M0", "M1", "M2"):
                for repeat in (0, 1):
                    run_id = f"perf-q0-{method}-{repeat}"
                    item = _spec(run_id, "q0", "performance", method, "live", repeat)
                    plan.append(item)
                    _write_status(artifacts, item)
                    _write_trace(artifacts, run_id, "q0", method, repeat, 50.0)
                    _write_outputs(artifacts, item, _graph("q0"))
            malformed = artifacts / "traces" / "perf-q0-M2-0.jsonl"
            row = json.loads(malformed.read_text(encoding="utf-8"))
            row["repeat"] = None
            row["publish_time"] = "not-a-timestamp"
            malformed.write_text(json.dumps(row) + "\n", encoding="utf-8")
            (final / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n",
                encoding="utf-8",
            )

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            self.assertEqual(result["performance_analysis_instance_count"], 0)
            self.assertIn("perf-q0-M2-0", result["incomplete_successful_run_ids"])

    def test_missing_m2_publish_is_exactly_once_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            plan = []
            for method in ("M0", "M1", "M2"):
                for repeat in (0, 1):
                    run_id = f"perf-q0-{method}-{repeat}"
                    item = _spec(run_id, "q0", "performance", method, "live", repeat)
                    plan.append(item)
                    _write_status(artifacts, item)
                    _write_trace(
                        artifacts,
                        run_id,
                        "q0",
                        method,
                        repeat,
                        50.0,
                        missing_publish=method == "M2" and repeat == 0,
                    )
                    _write_outputs(artifacts, item, _graph("q0"))
            (final / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n",
                encoding="utf-8",
            )

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            self.assertTrue(result["m2_source_order_violation"])
            self.assertEqual(result["performance_analysis_instance_count"], 0)

    def test_m1_source_order_violation_counts_as_divergence(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            final = artifacts / "final"
            final.mkdir(parents=True)
            plan = []
            for method in ("M0", "M1", "M2"):
                for repeat in (0, 1):
                    run_id = f"perf-q0-{method}-{repeat}"
                    item = _spec(run_id, "q0", "performance", method, "live", repeat)
                    plan.append(item)
                    _write_status(artifacts, item, episode_count=2)
                    publish_order = [1, 0] if method == "M1" and repeat == 0 else [0, 1]
                    _write_trace_rows(
                        artifacts,
                        item,
                        latencies=[50.0, 50.0],
                        publish_order=publish_order,
                    )
                    _write_outputs(artifacts, item, _graph("q0", episode_count=2))
            (final / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n",
                encoding="utf-8",
            )

            result = analyze_artifacts(artifacts, bootstrap_samples=10)

            self.assertEqual(result["performance_analysis_instance_count"], 1)
            self.assertEqual(result["m1_source_order_violation_count"], 1)
            self.assertEqual(result["m1_divergence_count"], 1)
            report = (final / "VALIDATION_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("M1 source-order violation instance 数为 1", report)


def _spec(run_id, qid, lane, method, mode, repeat):
    return {
        "run_id": run_id,
        "question_id": qid,
        "lane": lane,
        "method": method,
        "mode": mode,
        "repeat": repeat,
    }


def _write_trace(
    artifacts,
    run_id,
    qid,
    method,
    repeat,
    latency,
    *,
    missing_publish=False,
):
    item = _spec(run_id, qid, "performance", method, "live", repeat)
    _write_trace_rows(
        artifacts,
        item,
        latencies=[latency],
        publish_order=[] if missing_publish else [0],
    )


def _write_trace_rows(artifacts, item, *, latencies, publish_order):
    path = artifacts / "traces" / f"{item['run_id']}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    publish_rank = {sequence: rank for rank, sequence in enumerate(publish_order)}
    rows = []
    for sequence, latency in enumerate(latencies):
        arrival = 1000.0 + sequence
        rank = publish_rank.get(sequence)
        publish_ms = 1000.0 + float(latency) + rank if rank is not None else None
        rows.append(
            {
                "run_id": item["run_id"],
                "question_id": item["question_id"],
                "method": item["method"],
                "repeat": item["repeat"],
                "source_sequence": sequence,
                "arrival_time_ms": arrival,
                "publish_time_ms": publish_ms,
                "arrival_to_publish_ms": publish_ms - arrival if publish_ms is not None else None,
                "publish_time": int(publish_ms * 1_000_000) if publish_ms is not None else None,
                "error": None,
            }
        )
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_status(
    artifacts,
    item,
    *,
    status="success",
    episode_count=1,
    error=None,
):
    value = {
        **item,
        "status": status,
        "episode_count": episode_count,
        "post_run_node_count": 0,
        "llm_metrics": {
            "llm_call_count": 1,
            "llm_total_tokens": 100,
            "structured_parse_failures": 0,
            "unexpected_prompt": False,
        },
    }
    if error is not None:
        value["error"] = error
    _write_json(
        artifacts / "runs" / f"{item['run_id']}.json",
        value,
    )


def _graph(name, *, episode_count=1):
    return {
        "entities": [{"group_id": "g", "name": name, "labels": [], "summary": "", "attributes": {}}],
        "edges": [],
        "episodes": [
            {"source_sequence": sequence, "source_hash": f"h{sequence}", "session_id": "gold"}
            for sequence in range(episode_count)
        ],
    }


def _write_outputs(artifacts, item, graph):
    _write_json(artifacts / "graphs" / f"{item['run_id']}.canonical.json", graph)
    _write_json(
        artifacts / "retrieval" / f"{item['run_id']}.json",
        {
            "question_id": item["question_id"],
            "gold_episode_ids": ["gold"],
            "retrieved_episode_ids": ["gold"],
            "metrics": {"evidence_recall_at_10": 1.0},
        },
    )


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    import unittest

    unittest.main()
