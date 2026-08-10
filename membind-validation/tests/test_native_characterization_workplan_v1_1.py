"""Offline contracts for the bounded Native characterization plan v1.1.

These tests are intentionally document-only.  They freeze the accepted review
changes before the workplan is edited and must never contact a model, database,
or remote host.
"""

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
WORKPLAN = REPO / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
V1_0 = REPO / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md"
PROPOSAL = REPO / "MemBind_basic_validation_experiment.md"
SOLUTION_PLAN = REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md"
EXECUTION_PLAN = ROOT / "EXPERIMENT_PLAN.md"
MEMORY = ROOT / "GLOBAL_MEMORY.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class NativeCharacterizationWorkplanV11ContractTests(TestCase):
    """Freeze the review-driven scope reduction before implementation."""

    def _workplan(self) -> str:
        self.assertTrue(WORKPLAN.is_file(), "v1.1 workplan has not been created")
        return _read(WORKPLAN)

    def test_v1_1_is_current_and_v1_0_remains_historical(self):
        plan = self._workplan()
        self.assertTrue(V1_0.is_file(), "v1.0 must remain as immutable history")
        for token in (
            "native-characterization-v1.1",
            "current research-priority override",
            "supersedes v1.0 for future characterization actions",
        ):
            self.assertIn(token, plan)

        for document in (PROPOSAL, SOLUTION_PLAN, EXECUTION_PLAN, MEMORY):
            text = _read(document)
            self.assertIn(WORKPLAN.name, text)
            self.assertIn("native-characterization-v1.1", text)
            self.assertIn(V1_0.name, text)
            self.assertIn("WORKPLAN_FREEZE=true", text)
            self.assertIn("protocol_review_status=closed", text)
            self.assertIn("next_allowed_work=C1_instrumentation_implementation", text)
            self.assertLess(text.index(WORKPLAN.name), text.index(V1_0.name))

    def test_c0_is_exactly_one_bounded_native_episode(self):
        plan = _normalized(self._workplan())
        for token in (
            "C0 is one bounded Native Graphiti episode only",
            "same frozen U0 stack",
            "LLM, embedding, and Neo4j",
            "engineering viability only",
            "not a research result",
            "MUST NOT grow into H0",
            "no candidate selection",
            "no structured-output matrix",
            "no additional canary",
            "no qualification workload",
        ):
            self.assertIn(token, plan)

    def test_instrumentation_is_specified_not_claimed_as_qualified(self):
        plan = _normalized(self._workplan())
        for token in (
            "instrumentation_contract_status=specified_not_yet_qualified",
            "Only a measurement-correctness bug in C0-C5",
            "telemetry scope is frozen",
            "semantic parity",
        ):
            self.assertIn(token, plan)

        c1 = plan.split("## 4. C1", 1)[1].split("## 5. C2", 1)[0]
        self.assertNotIn("phase ranking", c1.lower())
        self.assertNotIn("G1/G2", c1)
        self.assertNotIn("instrumentation is already complete", plan.lower())
        self.assertNotIn("instrumentation is already qualified", plan.lower())

    def test_overhead_gate_is_conditional_and_not_a_literature_standard(self):
        plan = _normalized(self._workplan())
        for token in (
            "<=2%: clean pass",
            "2-5%: warning; report overhead and continue",
            ">5%: block and repair",
            "stable across alternating pairs",
            "fully reported",
            "MUST NOT optimize solely to move a result from 2-5% to <=2%",
            "No optimization or re-test is required solely to reduce overhead below 2%",
            "not a universal systems-paper threshold",
            "DistServe's <2% result is simulator accuracy",
        ):
            self.assertIn(token, plan)

    def test_c2_reports_native_occupancy_and_work_volume_only(self):
        plan = _normalized(self._workplan())
        for token in (
            "Native Graphiti construction",
            "Phase Wall-clock occupancy",
            "previous-context",
            "node extraction",
            "candidate search",
            "node resolution",
            "edge extraction",
            "edge resolution",
            "invalidation/update",
            "publication",
            "LLM / embedding / DB work-volume",
            "No comparison method is added in C2",
        ):
            self.assertIn(token, plan)

    def test_dependency_bounds_are_descriptive_without_speedup_gates(self):
        plan = _normalized(self._workplan())
        for token in (
            "D0 episode-only",
            "D1 immutable source/history-prefix",
            "D2 latest materialized graph",
            "D3 mutation/publication",
            "unknown",
            "p_L",
            "p_U",
            "S_2",
            "S_4",
            "S_8",
            "descriptive structural upper bounds",
            "no counterfactual dependency microexperiment",
        ):
            self.assertIn(token, plan)

        self.assertNotIn("S_8(p_U) < 1.2", plan)
        self.assertNotIn("S_8(p_L) >= 1.5", plan)

    def test_c4_uses_one_frozen_normalized_load_sweep(self):
        plan = _normalized(self._workplan())
        for token in (
            "lambda = 1 / interarrival",
            "rho_proxy = lambda * S_ref",
            "normalized_offered_load = rho_proxy",
            "rho_proxy in {0.5, 0.8, 1.0, 1.2, 1.5}",
            "interarrival = S_ref / rho_proxy",
            "C2 trace for the exact frozen E3 history",
            "no additional live calibration run",
            "the only persisted load field",
            "actual seconds are a derived result column",
            "the only E3 load sweep",
            "controlled deterministic open-loop replay",
        ):
            self.assertIn(token, plan)

        for forbidden in ("20/10/5/2", "20 / 10 / 5 / 2", "5s anchor", "Poisson"):
            self.assertNotIn(forbidden, plan)

    def test_staleness_metric_has_the_correct_boundary_and_clamp(self):
        plan = _normalized(self._workplan())
        self.assertIn(
            "post_return_stale_window = max(0, publish_timestamp - caller_return_timestamp)",
            plan,
        )
        self.assertNotIn("publish-to-return stale window", plan)

    def test_e4_has_bounded_evidence_and_no_sufficiency_claim(self):
        plan = _normalized(self._workplan())
        for token in (
            "direct invariant violation",
            "outcome instability / confounded",
            "NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED",
            "one fixed history",
            "one screening pass",
            "existence counterexample",
            "already-required offline deterministic TDD fixture",
            "not an additional E4 treatment, live run, screening repetition, or experimental block",
            "does not establish Whole-Update Parallel safety, sufficiency, repeatability, or generality",
            "No additional E4 repetition is authorized by that outcome",
        ):
            self.assertIn(token, plan)
        self.assertNotIn("WHOLE_UPDATE_PARALLEL_IS_SUFFICIENT", plan)
        self.assertNotIn("repeated-run stability", plan)
        self.assertNotIn("fixture/replay lane", plan)

    def test_c6_records_a_verdict_and_stops_before_design(self):
        plan = _normalized(self._workplan())
        for token in (
            "PROBLEM_SUPPORTED",
            "PARTIAL",
            "NOT_SUPPORTED",
            "C6 immediately stops",
            "supporting observations",
            "unresolved evidence",
            "does not design, select, or authorize a mechanism",
        ):
            self.assertIn(token, plan)

    def test_contract_and_artifact_surface_is_frozen(self):
        plan = _normalized(self._workplan())
        for token in (
            "freeze.json",
            "phase_map.json",
            "dependency_map.json",
            "e1_breakdown.json",
            "e2_dependency_opportunity.json",
            "e3_sync_async.json",
            "e4_whole_parallel.json",
            "CHARACTERIZATION_REPORT.md",
            "DESIGN_DECISION.md",
            "no new authority layer",
            "no candidate registry",
            "no paper-level run planner",
            "no oracle namespace",
            "no formal split system",
            "no future-work artifact",
        ):
            self.assertIn(token, plan)

    def test_workplan_is_finally_frozen_without_expanding_c0_c6(self):
        plan = _normalized(self._workplan())
        for token in (
            "WORKPLAN_FREEZE=true",
            "protocol_review_status=closed",
            "experiment_surface=C0-C6_only",
            "next_allowed_work=C1_instrumentation_implementation",
            "No further protocol review is authorized",
            "no new stage",
            "no new metric",
            "no new experiment",
        ):
            self.assertIn(token, plan)


if __name__ == "__main__":
    import unittest

    unittest.main()
