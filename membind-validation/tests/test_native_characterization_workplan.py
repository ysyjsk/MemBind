"""Document-only contracts for the Native Graphiti characterization reset.

These tests deliberately precede the new workplan.  They must stay offline:
reading the protocol documents must never contact a model, database, or host.
"""

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
WORKPLAN = REPO / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md"
PROPOSAL = REPO / "MemBind_basic_validation_experiment.md"
SOLUTION_PLAN = REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md"
EXECUTION_PLAN = ROOT / "EXPERIMENT_PLAN.md"
MEMORY = ROOT / "GLOBAL_MEMORY.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class NativeCharacterizationWorkplanContractTests(TestCase):
    """Freeze a falsifiable problem-first research order before implementation."""

    def _workplan(self) -> str:
        self.assertTrue(
            WORKPLAN.is_file(),
            "the Native Graphiti characterization workplan has not been created",
        )
        return _read(WORKPLAN)

    def test_workplan_is_the_current_research_priority_override(self):
        plan = self._workplan()
        for token in (
            "native-characterization-v1.0",
            "current research-priority override",
            "Native Graphiti only",
            "solution lane frozen",
            "exploratory prototype",
        ):
            self.assertIn(token, plan)

        for document in (PROPOSAL, SOLUTION_PLAN, EXECUTION_PLAN, MEMORY):
            text = _read(document)
            self.assertIn(WORKPLAN.name, text)
            self.assertIn("current research-priority override", text)

    def test_research_hypothesis_does_not_preselect_membind(self):
        plan = _normalized(self._workplan())
        for token in (
            "昂贵但不依赖 latest materialized graph state",
            "dependency-aware execution",
            "由 characterization 数据决定",
            "不自动选择 M2",
            "不支持该研究问题",
        ):
            self.assertIn(token, plan)
        self.assertIn("DB/index optimization", plan)
        self.assertIn("LLM serving/batching", plan)
        self.assertIn("OCC", plan)

    def test_minimal_experiment_sequence_is_native_first(self):
        plan = self._workplan()
        expected_order = (
            "Experiment 1 - Native construction breakdown",
            "Experiment 2 - State-dependency characterization",
            "Experiment 3 - Native Sync vs Async-Serial",
            "Experiment 4 - Naive Whole-Update Parallel",
        )
        positions = [plan.index(section) for section in expected_order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("C0 - Lightweight native-stack viability", plan)
        self.assertIn("C1 - Instrumentation qualification", plan)
        self.assertIn("C6 - Problem verdict and stop", plan)

    def test_screening_wave_has_a_bounded_execution_matrix(self):
        plan = _normalized(self._workplan())
        for token in (
            "E1/E2: 4 calibration histories x 1 pass = 4 shared-trace blocks",
            "E3: 2 methods x 5 frozen loads x 1 screening repetition = 10 blocks",
            "E4: 4 concurrency settings x 1 screening repetition = 4 blocks",
            "no significance claim",
            "new confirmation plan",
            "not reused as held-out formal evaluation",
        ):
            self.assertIn(token, plan)

    def test_dependency_taxonomy_preserves_history_and_unknowns(self):
        plan = _normalized(self._workplan())
        for token in (
            "D0 episode-only",
            "D1 immutable source/history-prefix",
            "D2 latest materialized graph",
            "D3 mutation/publication",
            "unknown",
            "previous episodes",
            "static source audit",
            "dynamic trace",
            "input-ready-at-arrival",
        ):
            self.assertIn(token, plan)
        self.assertIn("不能无条件把 extraction 标为 state-independent", plan)
        self.assertIn("未观察到 read 不能单独证明 independent", plan)

    def test_method_ids_do_not_collide_with_dependency_classes(self):
        plan = self._workplan()
        self.assertIn("U0-S: Project-Stabilized-Graphiti-Serial", plan)
        self.assertIn("historical method ID `D0`", plan)
        self.assertNotIn("lane is named **D0**", plan)

    def test_publication_confounds_are_predeclared(self):
        plan = _normalized(self._workplan())
        for token in (
            "remote vLLM queue/GPU sharing",
            "network jitter",
            "prefix cache",
            "Neo4j page cache/index warmness",
            "graph size and episode content",
            "model nondeterminism",
            "instrumentation overhead",
            "finite transient",
            "history/run is the analysis unit",
        ):
            self.assertIn(token, plan)

    def test_phase_measurement_avoids_nested_double_counting(self):
        plan = _normalized(self._workplan())
        for token in (
            "inclusive duration",
            "exclusive duration",
            "interval union",
            "parent/child span",
            "monotonic clock",
            "不能直接相加",
            "trace-off",
            "trace-on",
            "overhead gate",
        ):
            self.assertIn(token, plan)

    def test_sync_async_load_sweep_is_frozen_before_outcomes(self):
        plan = _normalized(self._workplan())
        for token in (
            "rho_proxy = mean_native_service / interarrival",
            "{0.5, 0.8, 1.0, 1.2, 1.5}",
            "20/10/5/2 seconds",
            "absolute open-loop schedule",
            "durable enqueue ack",
            "arrival-to-visible",
            "post-return stale window",
            "backlog AUC",
            "drain time",
            "finite synthetic workload",
        ):
            self.assertIn(token, plan)
        self.assertIn("看 E3 结果后不得改点", plan)
        self.assertIn("Async-Serial 不提高 construction service capacity", plan)

    def test_whole_update_parallel_has_falsifiable_semantic_outcomes(self):
        plan = _normalized(self._workplan())
        for token in (
            "C={1,2,4,8}",
            "trajectory divergence",
            "outcome instability",
            "direct invariant violation",
            "canonical graph parity",
            "retrieval parity",
            "oracle miss",
            "不能预先声称",
            "更简单的 baseline",
        ):
            self.assertIn(token, plan)

    def test_gates_allow_stop_or_a_different_system_direction(self):
        plan = self._workplan()
        for token in (
            "G1 - Important cost",
            "G2 - Structural opportunity",
            "G3 - Online tension",
            "G4 - Naive-parallel insufficiency",
            "PROBLEM_SUPPORTED",
            "PARTIAL",
            "NOT_SUPPORTED",
            "p_L",
            "p_U",
            "S_C(p)=1/((1-p)+p/C)",
        ):
            self.assertIn(token, plan)
        self.assertIn("G1-G4 不会自动授权 M2", plan)

    def test_tdd_and_artifact_contracts_are_explicit(self):
        plan = _normalized(self._workplan())
        for token in (
            "RED -> minimal GREEN -> focused -> full offline regression -> dry-run -> live canary",
            "每 episode append + flush",
            "每 history checkpoint",
            "vLLM unreachable",
            "immediate stop-and-report",
            "artifacts/native_characterization",
            "CHARACTERIZATION_REPORT.md",
            "DESIGN_DECISION.md",
            "gpt55_temporary/**",
            ".env",
            "Authorization header",
        ):
            self.assertIn(token, plan)


if __name__ == "__main__":
    import unittest

    unittest.main()
