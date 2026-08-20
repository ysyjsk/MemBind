from __future__ import annotations

import unittest

from analyze import AnalysisError, build_analysis, wilson_interval


def _public(method: str, history_id: str, label: bool) -> dict[str, object]:
    return {
        "method": method,
        "history_id": history_id,
        "failure_category": "SUCCESS" if label else "READER_OR_JUDGE_INCORRECT",
        "context_gold_session_coverage_posthoc": 1.0,
        "reader": {"status": "SUCCESS", "finish_reason": "stop"},
        "judge": {"status": "SUCCESS", "label": label},
        "session_metrics": {
            "recall_at_1": 0.5,
            "recall_at_3": 1.0,
            "recall_at_5": 1.0,
            "recall_at_10": 1.0,
            "mrr": 1.0,
            "ndcg_at_10": 1.0,
        },
    }


def _judge(method: str, history_id: str, label: bool) -> dict[str, object]:
    return {
        "method": method,
        "history_id": history_id,
        "model": "Qwen/Qwen3-32B",
        "finish_reason": "stop",
        "parse_status": "YES" if label else "NO",
        "label": label,
        "original_label": label,
        "agrees_with_original": True,
    }


class AnalysisTests(unittest.TestCase):
    def test_reduces_valid_judges_and_paired_disagreements(self) -> None:
        rows = []
        judges = []
        for method in ("U0", "P(C=2)"):
            for history_id, label in (("h1", True), ("h2", False)):
                rows.append(_public(method, history_id, label))
                judges.append(_judge(method, history_id, label))

        result = build_analysis(rows, judges)

        self.assertEqual(
            result["claim_scope"], "BASELINE_REUSE_4_HISTORY_NOT_MAB_MULTIQA"
        )
        self.assertEqual(result["methods"]["U0"]["accuracy"], 0.5)
        self.assertEqual(result["methods"]["P(C=2)"]["valid_count"], 2)
        self.assertEqual(result["paired"]["agreement_count"], 2)
        self.assertEqual(result["paired"]["discordant_count"], 0)
        self.assertEqual(result["paired"]["accuracy_delta_pc2_minus_u0"], 0.0)
        self.assertEqual(result["judge_validation"]["invalid_count"], 0)
        self.assertEqual(result["judge_validation"]["agreement_rate"], 1.0)

    def test_requires_exact_siliconflow_model(self) -> None:
        rows = [_public("U0", "h1", True), _public("P(C=2)", "h1", True)]
        judges = [_judge("U0", "h1", True), _judge("P(C=2)", "h1", True)]
        judges[0]["model"] = "Qwen/Qwen3-14B"
        with self.assertRaisesRegex(AnalysisError, "JUDGE_MODEL_DRIFT"):
            build_analysis(rows, judges)

    def test_requires_exact_paired_inventory(self) -> None:
        rows = [_public("U0", "h1", True), _public("P(C=2)", "h2", True)]
        judges = [_judge("U0", "h1", True), _judge("P(C=2)", "h2", True)]
        with self.assertRaisesRegex(AnalysisError, "PAIRED_INVENTORY_MISMATCH"):
            build_analysis(rows, judges)

    def test_wilson_interval_exposes_small_sample_uncertainty(self) -> None:
        low, high = wilson_interval(2, 4)
        self.assertAlmostEqual(low, 0.1500, places=4)
        self.assertAlmostEqual(high, 0.8500, places=4)


if __name__ == "__main__":
    unittest.main()
