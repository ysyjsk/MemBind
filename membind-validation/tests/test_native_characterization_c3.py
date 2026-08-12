"""Focused offline contracts for the frozen C3/E2 characterization."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c3 as c3  # noqa: E402


RUN_ID = "c2-17cdaabd562e9673"


def _span(phase: str, start: int, end: int) -> dict[str, object]:
    return {
        "phase": phase,
        "start_ns": start,
        "end_ns": end,
        "duration_ns": end - start,
    }


class NativeCharacterizationC3Tests(TestCase):
    maxDiff = None

    def test_phase_rules_are_complete_and_conservative(self) -> None:
        rules = {item["phase"]: item for item in c3.PHASE_RULES}
        self.assertEqual(
            set(rules),
            {
                "add-episode",
                "previous-context",
                "node-extraction",
                "node-resolution",
                "edge-extraction",
                "edge-resolution",
                "attributes-summary",
                "publication",
            },
        )
        self.assertEqual(rules["add-episode"]["dependency_class"], "unknown")
        self.assertEqual(rules["add-episode"]["accounting_role"], "denominator_root")
        self.assertFalse(rules["add-episode"]["timing_eligible"])
        self.assertEqual(rules["edge-extraction"]["dependency_class"], "D1")
        self.assertFalse(rules["edge-extraction"]["input_ready_at_arrival"])
        self.assertTrue(rules["edge-extraction"]["transitively_source_derivable"])
        self.assertFalse(
            any(
                item["potentially_independent_unknown"]
                for item in rules.values()
                if item["dependency_class"] == "unknown"
            )
        )

    def test_interval_union_handles_overlap_adjacency_and_clipping(self) -> None:
        self.assertEqual(c3.interval_union_ns([(0, 10), (5, 20), (20, 25)]), 25)
        self.assertEqual(c3.interval_union_ns([(8, 12), (1, 3)]), 6)
        self.assertEqual(
            c3.interval_union_ns([(-5, 5), (7, 15)], clip=(0, 10)),
            8,
        )

    def test_synthetic_episode_excludes_d1_not_ready_and_root_residue(self) -> None:
        root = _span("add-episode", 0, 100)
        phases = [
            _span("previous-context", 0, 5),
            _span("node-extraction", 5, 25),
            _span("node-resolution", 25, 45),
            _span("edge-extraction", 45, 75),
            _span("edge-resolution", 75, 85),
            _span("attributes-summary", 85, 95),
            _span("publication", 95, 100),
        ]

        result = c3.analyze_interval_set(root, phases)

        self.assertEqual(result["total_ns"], 100)
        self.assertEqual(result["class_union_ns"], {"D0": 0, "D1": 55, "D2": 40, "D3": 5, "unknown": 0})
        self.assertEqual(result["verified_d0_d1_arrival_ready_union_ns"], 25)
        self.assertEqual(result["verified_d0_d1_non_arrival_ready_union_ns"], 30)
        self.assertEqual(result["root_uncovered_ns"], 0)
        self.assertEqual(result["p_L"], 0.25)
        self.assertEqual(result["p_U"], 0.25)

        with_residue = c3.analyze_interval_set(
            root,
            [dict(item, end_ns=item["end_ns"] - 1, duration_ns=item["duration_ns"] - 1) for item in phases],
        )
        self.assertGreater(with_residue["root_uncovered_ns"], 0)
        self.assertEqual(with_residue["potentially_independent_unknown_union_ns"], 0)
        self.assertLess(with_residue["p_U"], 1.0)

    def test_upper_bound_unions_possible_unknown_only_once(self) -> None:
        result = c3.opportunity_bounds(
            total_ns=100,
            lower_intervals=[(0, 30)],
            possible_unknown_intervals=[(20, 50), (40, 60)],
        )
        self.assertEqual(result["lower_union_ns"], 30)
        self.assertEqual(result["upper_union_ns"], 60)
        self.assertEqual(result["p_L"], 0.3)
        self.assertEqual(result["p_U"], 0.6)

    def test_aggregate_is_denominator_weighted_not_mean_of_ratios(self) -> None:
        summaries = [
            {
                "history_id": "small",
                "total_ns": 100,
                "verified_d0_d1_arrival_ready_union_ns": 20,
                "verified_d0_d1_non_arrival_ready_union_ns": 10,
                "potentially_independent_unknown_union_ns": 0,
                "root_uncovered_ns": 5,
                "class_union_ns": {"D0": 0, "D1": 30, "D2": 60, "D3": 5, "unknown": 5},
                "phase_union_ns": {},
            },
            {
                "history_id": "large",
                "total_ns": 900,
                "verified_d0_d1_arrival_ready_union_ns": 450,
                "verified_d0_d1_non_arrival_ready_union_ns": 90,
                "potentially_independent_unknown_union_ns": 0,
                "root_uncovered_ns": 10,
                "class_union_ns": {"D0": 0, "D1": 540, "D2": 300, "D3": 50, "unknown": 10},
                "phase_union_ns": {},
            },
        ]

        result = c3.aggregate_episode_summaries(summaries)

        self.assertEqual(result["T_total_ns"], 1000)
        self.assertEqual(result["verified_d0_d1_arrival_ready_union_ns"], 470)
        self.assertAlmostEqual(result["p_L"], 0.47)
        self.assertNotAlmostEqual(result["p_L"], (0.2 + 0.5) / 2)

    def test_dependency_map_binds_source_and_dynamic_evidence(self) -> None:
        result = c3.build_dependency_map(ROOT, RUN_ID)
        rules = {item["phase"]: item for item in result["phase_rules"]}

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(len(rules), 8)
        self.assertEqual(
            result["dynamic_corroboration"]["db_query_count_by_phase"],
            {
                "edge-resolution": 3265,
                "node-resolution": 1032,
                "previous-context": 188,
            },
        )
        self.assertEqual(result["dynamic_corroboration"]["db_write_count_by_phase"], {"publication": 752})
        self.assertEqual(result["dynamic_corroboration"]["nonzero_graph_prefix_episode_count"], 184)
        self.assertEqual(result["dynamic_corroboration"]["node_dedup_candidate_count"], 14730)
        self.assertEqual(result["dynamic_corroboration"]["edge_invalidation_candidate_count"], 6133)
        self.assertEqual(
            result["payload_sha256"],
            c3.payload_sha256(result),
        )

    def test_source_hash_drift_fails_closed(self) -> None:
        drifted = dict(c3.EXPECTED_SOURCE_SHA256)
        drifted[".venv/lib/python3.12/site-packages/graphiti_core/graphiti.py"] = "0" * 64
        with patch.object(c3, "EXPECTED_SOURCE_SHA256", drifted):
            with self.assertRaises(c3.NativeCharacterizationC3Error):
                c3.build_dependency_map(ROOT, RUN_ID)

    def test_retained_c2_e2_golden_is_deterministic_and_sanitized(self) -> None:
        dependency_map = c3.build_dependency_map(ROOT, RUN_ID)
        first = c3.analyze_e2(ROOT, RUN_ID, dependency_map)
        second = c3.analyze_e2(ROOT, RUN_ID, deepcopy(dependency_map))
        aggregate = first["aggregate"]

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(len(first["intervals"]), 188 * 8)
        self.assertEqual(aggregate["T_total_ns"], 9_081_843_769_634)
        self.assertEqual(aggregate["class_union_ns"]["D1"], 5_565_760_939_710)
        self.assertEqual(
            aggregate["verified_d0_d1_arrival_ready_union_ns"],
            2_081_530_651_655,
        )
        self.assertEqual(
            aggregate["verified_d0_d1_non_arrival_ready_union_ns"],
            3_484_230_288_055,
        )
        self.assertAlmostEqual(aggregate["p_L"], 0.229196923494, places=12)
        self.assertEqual(aggregate["p_L"], aggregate["p_U"])
        self.assertAlmostEqual(aggregate["speedup_bounds"]["2"]["at_p_L"], 1.129431062400, places=12)
        self.assertAlmostEqual(aggregate["speedup_bounds"]["4"]["at_p_L"], 1.207580260421, places=12)
        self.assertAlmostEqual(aggregate["speedup_bounds"]["8"]["at_p_L"], 1.250855754291, places=12)
        self.assertEqual(first["payload_sha256"], c3.payload_sha256(first))
        encoded = json.dumps(first, ensure_ascii=True, sort_keys=True)
        for forbidden in ("api_key", "raw_prompt", "raw_response", "Jywc2ncr"):
            self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    import unittest

    unittest.main()
