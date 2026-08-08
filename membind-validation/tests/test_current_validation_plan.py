import json
import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


class CurrentValidationPlanTests(TestCase):
    def test_v1_2_is_the_authoritative_execution_overlay(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")

        self.assertIn("当前阶段唯一允许 Agent 直接执行的计划", current)
        self.assertIn("CURRENT VALIDATION PLAN v1.2", protocol)
        self.assertIn("Current Validation v1.2", execution)
        self.assertIn("V1 Correctness nondeterminism closure", protocol)
        self.assertIn("V1 Correctness nondeterminism closure", execution)

    def test_current_state_advances_to_v3_only_after_persisted_v2_pass(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        diagnostic = json.loads(
            (
                ROOT
                / "artifacts"
                / "diagnostics"
                / "embedding_nondeterminism_source5.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(state["protocol_version"], "current-validation-v1.2")
        self.assertEqual(state["current_stage"], "V3")
        self.assertEqual(state["status"], "ready_for_full_correctness_smoke")
        self.assertEqual(
            diagnostic["v1_gate"]["status"],
            "pass_with_explicit_evidence_limits",
        )
        self.assertEqual(
            state["completed_stages"]["V1"]["artifact_sha256"],
            "58651ad4a343678934ed88225bafe6ad284bce116680d7dac6e04bfa79691b5c",
        )
        self.assertEqual(
            state["stage_progress"]["embedding_oracle_unit_contracts"],
            "pass",
        )
        self.assertEqual(
            state["stage_progress"]["full_unit_regression"],
            "pass",
        )
        self.assertEqual(state["completed_stages"]["V2"]["status"], "pass")
        self.assertEqual(state["stage_progress"]["embedding_identity"], "operator_fingerprint_persisted")
        self.assertEqual(
            state["stage_progress"]["embedding_runtime_config"],
            "pass",
        )
        self.assertEqual(
            state["stage_progress"]["runtime_cross_encoder_audit"],
            "pass_not_invoked",
        )
        self.assertEqual(
            state["evidence"]["embedding_identity_probe_sha256"],
            "c693905ad3db6d95575a191efa38848f3a4b976606eebff5195d6c42b49276ac",
        )
        self.assertEqual(
            state["evidence"]["embedding_model_fingerprint_sha256"],
            "389fb4c9cf87217c333741170c9162cf7353cb05026de510685b27fa336299d0",
        )
        self.assertEqual(
            state["evidence"]["v2_oracle_integration_verification_sha256"],
            "ef9c20578a9ab418630e650cca76d2b7c3c75601f56fa440eb698b947f1a12aa",
        )
        self.assertIn(
            "V3",
            state["next_allowed_action"],
        )
        self.assertEqual(
            state["forbidden_until_pass"],
            ["V4", "V5", "V6", "future_work"],
        )

    def test_v1_closes_from_retained_artifacts_without_live_recapture(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")

        for text in (current, execution):
            self.assertIn("retained-artifact closure", text)
            self.assertIn("not_computable_from_retained_artifacts", text)
            self.assertIn("不得" if text is current else "must not", text)
        self.assertIn("不得启动新的 live embedding recapture", current)
        self.assertNotIn("embedding_nondeterminism_source5.raw.jsonl", current)
        self.assertNotIn("embedding_nondeterminism_source5.raw.jsonl", execution)

    def test_correctness_oracle_covers_llm_embedding_and_both_concurrent_methods(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )

        for text in (current, protocol):
            self.assertIn("LLM response + embedding vector", text)
            self.assertIn("M1 read-only replay", text)
            self.assertIn("M2 read-only replay", text)
            self.assertIn("24 correctness runs", text)
            self.assertIn("72 runs", text)

    def test_m1_oracle_miss_is_path_divergence_not_final_semantic_claim(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")

        for text in (current, protocol, execution):
            self.assertIn("execution_path_divergence", text)
            self.assertIn("completed_with_divergence", text)
        self.assertIn("H3a", protocol)
        self.assertIn("H3b", protocol)
        self.assertIn("不得据此宣称最终 graph semantic divergence", current)
        self.assertIn("M2 oracle miss", current)
        self.assertIn("阻断 performance", current)
        self.assertIn("final_semantic_parity = not_evaluable_due_to_oracle_miss", current)
        self.assertIn("prompt_name + source_sequence + invocation ordinal", current)

    def test_m1_and_m2_replay_only_model_outputs_not_live_graph_state(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "这些必须由 M1/M2 在各自的最新 committed graph 上真实执行",
            current,
        )

    def test_current_baseline_is_transparently_named_without_upstream_live_guardrail(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")

        for text in (current, execution):
            self.assertIn("Deterministic-Graphiti-Serial", text)
            self.assertIn("method ID", text)
        v4 = re.search(r"# V4\.(?P<body>.*?)# V5\.", current, re.DOTALL)
        self.assertIsNotNone(v4)
        self.assertNotIn("upstream-vs-current", v4.group("body"))
        self.assertIn("不运行 upstream semantic guardrail", v4.group("body"))

    def test_minimal_overhead_gate_uses_preregistered_method_specific_five_percent(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        v4 = re.search(r"# V4\.(?P<body>.*?)# V5\.", current, re.DOTALL)

        self.assertIsNotNone(v4)
        body = v4.group("body")
        self.assertIn("method-specific overhead", body)
        self.assertIn(">5%", body)
        self.assertIn("Pilot 预注册 engineering gate", body)
        self.assertNotIn("<=2%", body)

    def test_embedding_identity_uses_reported_revision_or_immutable_fingerprint(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("endpoint-reported revision", current)
        self.assertIn("operator-supplied immutable deployment fingerprint", current)
        self.assertIn("embedding_model_fingerprint.json", current)
        self.assertIn("不能猜测 checkpoint revision", current)
        self.assertNotIn("endpoint-unreported` 写入 manifest/key", current)

    def test_current_network_contract_is_minimal_real_e2e(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )

        for value in ("same LAN route", "NO_PROXY", "endpoint health", "禁止 RTT subtraction"):
            self.assertIn(value, current)
        self.assertIn("不恢复 100-probe", current)

    def test_active_protocol_section_uses_72_run_correctness_first_plan(self):
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        active = re.search(
            r"## 20\. 本次执行拓扑与 TDD 覆盖(?P<body>.*?)## 21\.",
            protocol,
            re.DOTALL,
        )

        self.assertIsNotNone(active)
        body = active.group("body")
        self.assertIn("72-run", body)
        self.assertIn("24 correctness", body)
        self.assertIn("M2 8/8", body)
        self.assertNotIn("64-run", body)

    def test_frozen_topology_matches_remote_models_and_local_non_docker_neo4j(self):
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        database = re.search(r"### 3\.2 图数据库(?P<body>.*?)### 3\.3", protocol, re.DOTALL)
        hardware = re.search(r"### 3\.5 硬件(?P<body>.*?)(?:\n---)", protocol, re.DOTALL)

        self.assertIsNotNone(database)
        self.assertIsNotNone(hardware)
        self.assertIn("本机直接运行", database.group("body"))
        self.assertIn("不使用 Docker", database.group("body"))
        self.assertNotIn("Docker tag", database.group("body"))
        self.assertIn("远端", hardware.group("body"))
        self.assertIn("本机 RTX 3090", hardware.group("body"))
        self.assertNotIn("RTX PRO 6000", hardware.group("body"))

    def test_current_performance_lifecycle_does_not_reactivate_historical_campaign(self):
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        performance = re.search(
            r"### 6\.2 Performance lane(?P<body>.*?)## 7\.", protocol, re.DOTALL
        )

        self.assertIsNotNone(performance)
        self.assertIn("V4 frozen minimal lifecycle", performance.group("body"))
        self.assertNotIn("§21.6", performance.group("body"))

    def test_infrastructure_and_treatment_failures_are_not_conflated(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("MUST rerun the entire block", current)
        self.assertIn("treatment failure 不得归入 infrastructure failure rate", protocol)
        self.assertIn("infrastructure/protocol-invalid", protocol)

    def test_cross_encoder_result_is_measured_not_predeclared(self):
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")

        self.assertIn("expected not invoked; measurement decides", execution)
        self.assertIn("rank_call_count", execution)
        self.assertIn("model_oracle_audit.json", execution)

    def test_embedding_lane_contract_separates_oracle_replay_from_live_performance(self):
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("canonical single-item embedding input", protocol)
        self.assertIn("Embedding cache miss", protocol)
        self.assertIn("不要求跨 run bitwise-identical vectors", protocol)
        self.assertIn("endpoint-reported revision", protocol)
        self.assertIn("operator-supplied immutable deployment fingerprint", protocol)

    def test_v2_pilot_boundary_matches_bounded_integration_harness(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        for text in (current, protocol, execution):
            self.assertIn("M0 capture", text)
            self.assertIn("M0 read-only replay", text)
            self.assertIn("v2-oracle-integration", text)
        self.assertIn("V3", execution)

    def test_m1_completion_order_is_diagnostic_not_semantic_failure(self):
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        go_section = re.search(
            r"## 13\. Go / No-Go 判据(?P<body>.*?)## 14\.",
            protocol,
            re.DOTALL,
        )

        self.assertIsNotNone(go_section)
        body = go_section.group("body")
        self.assertIn("completion/source-order 仅为 diagnostic", body)
        self.assertNotIn("- source-order violation。", body)

    def test_history_and_future_work_are_not_current_execution_inputs(self):
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        history = ROOT / "artifacts" / "history" / "SMOKE_HISTORY.md"
        future = REPO / "FUTURE_WORK.md"

        self.assertTrue(history.exists())
        self.assertTrue(future.exists())
        self.assertIn("current_stage", execution)
        self.assertIn("current_blocker", execution)
        self.assertIn("next_allowed_action", execution)
        self.assertNotIn("smoke01`:", execution)
        self.assertIn("CURRENT VALIDATION AGENT MUST NOT READ OR EXECUTE THIS FILE", future.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import unittest

    unittest.main()
