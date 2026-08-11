import hashlib
import json
import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
CURRENT_H0_STATUS = "h0_q1_b_live_only"
CURRENT_H0_ACTION_SCOPE = "h0_q1_b_live_only"
CURRENT_H0_NEXT_ACTION = "run_q1_h0-b-post-workload-replacement"
CURRENT_CHARACTERIZATION_STAGE = "NATIVE_CHARACTERIZATION"
CURRENT_CHARACTERIZATION_STATUS = "native_characterization_c2_live_only"
CURRENT_CHARACTERIZATION_BLOCKER = None
CURRENT_CHARACTERIZATION_BLOCKER_TEXT = "none"
CURRENT_CHARACTERIZATION_SCOPE = "native_characterization_c2_live_only"
CURRENT_CHARACTERIZATION_NEXT_ACTION = "run_native_characterization_c2"
CURRENT_INTERRUPTED_C2_RUN_ID = "c2-2fe3711c62933407"
HISTORICAL_V3_BLOCKER = "v3_smoke_002_m0_structured_output_failure"


class CurrentValidationPlanTests(TestCase):
    def test_v3_failure_report_uses_the_actual_llm_failure_artifact_hash(self):
        report = (
            ROOT
            / "artifacts"
            / "diagnostics"
            / "v3_smoke_002_failure_report_20260809.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "718e09c45f10744f2a1a7a7027a37df23566bf21716ace1e7b3b8f0827a4cd53",
            report,
        )
        self.assertNotIn(
            "718e09c45f107544a1a7a7027a37df23566bf21716ace1e7b3b8f0827a4cd53",
            report,
        )

    def test_v3_structured_output_failure_evidence_remains_persisted(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))

        self.assertEqual(state["current_stage"], CURRENT_CHARACTERIZATION_STAGE)
        self.assertEqual(
            state["historical_blocker"],
            HISTORICAL_V3_BLOCKER,
        )
        report = state["evidence"]["v3_smoke_002_failure_report"]
        self.assertEqual(
            report,
            "artifacts/diagnostics/v3_smoke_002_failure_report_20260809.md",
        )
        self.assertEqual(
            state["evidence"]["v3_smoke_002_failure_report_sha256"],
            "060e59eeb5e68015f8b0a022b5e266e19be15dd16dcac7fe240e7c20e8a5b09e",
        )
        self.assertEqual(
            state["evidence"]["v3_remote_service_preflight"],
            "artifacts/environment/v3_remote_service_preflight_20260809_initfix.json",
        )
        self.assertEqual(
            state["evidence"]["v3_remote_service_preflight_sha256"],
            "f8ae2eeb28e5d38f6aae438e656def9eec9ecb0000fd9439705e30cd8aaf14a0",
        )
        self.assertEqual(
            state["evidence"]["v3_blocker_status_update"],
            "artifacts/diagnostics/v3_blocker_status_update_20260809.md",
        )
        self.assertEqual(
            state["evidence"]["v3_blocker_status_update_sha256"],
            "4967cebf0dba8c96fd15da0c7e88063e01bfa8ec09b762b089e411a9a37ef4bc",
        )
        failure_report_path = (
            ROOT
            / "artifacts"
            / "diagnostics"
            / "v3_smoke_002_failure_report_20260809.md"
        )
        self.assertEqual(
            hashlib.sha256(failure_report_path.read_bytes()).hexdigest(),
            state["evidence"]["v3_smoke_002_failure_report_sha256"],
        )
        self.assertEqual(
            state["evidence"]["v3_smoke_002_structured_failure_diagnostic"],
            "artifacts/diagnostics/v3_smoke_002_structured_failure_diagnostic_20260809.json",
        )
        self.assertEqual(
            state["evidence"]["v3_vllm_metadata_probe_attempt02"],
            "artifacts/environment/v3_vllm_metadata_probe_20260809_attempt02.json",
        )
        self.assertEqual(
            state["evidence"]["v3_offline_diagnosis_status_update"],
            "artifacts/diagnostics/v3_offline_diagnosis_status_update_20260809.md",
        )
        self.assertEqual(
            state["evidence"]["v3_offline_diagnosis_status_update_sha256"],
            "f2daaf679a71275a46c608552a3ba243228e14b624ef077081d8c29840b82d83",
        )
        self.assertEqual(
            state["evidence"]["v3_offline_diagnosis_full_regression_log"],
            "artifacts/tdd/v3_offline_diagnosis_final_full_regression_green_094.log",
        )
        self.assertEqual(
            state["evidence"]["v3_offline_diagnosis_full_regression_test_count"],
            234,
        )
        self.assertEqual(state["evidence"]["v3_blocker_full_regression_test_count"], 222)
        self.assertEqual(
            state["evidence"]["v3_blocker_full_regression_sha256"],
            "6be85ceb90f0436accaf75de967c60ba88784578714ab6d19aae73c3cac547b8",
        )
        self.assertEqual(state["current_blocker"], CURRENT_CHARACTERIZATION_BLOCKER)
        interruption = state["native_characterization_c2_interruption"]
        self.assertEqual(interruption["run_id"], CURRENT_INTERRUPTED_C2_RUN_ID)
        self.assertEqual(interruption["error_code"], "openai.APIConnectionError")
        self.assertFalse(interruption["attempt_valid"])
        self.assertFalse(interruption["attempt_mergeable"])
        self.assertFalse(interruption["resume_allowed"])
        self.assertTrue(interruption["cleanup_authorized"])

    def test_historical_v3_blocker_survives_characterization_transition(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))

        self.assertEqual(state["current_stage"], CURRENT_CHARACTERIZATION_STAGE)
        self.assertEqual(state["status"], CURRENT_CHARACTERIZATION_STATUS)
        self.assertEqual(
            state["historical_blocker"],
            HISTORICAL_V3_BLOCKER,
        )
        self.assertEqual(state["current_blocker"], CURRENT_CHARACTERIZATION_BLOCKER)
        self.assertEqual(
            state["evidence"]["v3_actual_schema_probe_corrected"],
            "artifacts/environment/v3_actual_schema_compatibility_probe_20260809_004_reclassified.json",
        )
        self.assertEqual(
            state["evidence"]["v3_actual_schema_probe_corrected_sha256"],
            "d3caf163af7639f2dcbc5322d4f1e3e5a3d23067f2638bb4398d15c4c2b9bcfb",
        )
        invalid = state["invalidated_diagnostics"][
            "v3_construction_runtime_identity_drift_after_smoke_002"
        ]
        self.assertIn("public generate_response wrapper", invalid["reason"])
        self.assertEqual(
            state["next_allowed_action"], CURRENT_CHARACTERIZATION_NEXT_ACTION
        )
        self.assertTrue(state["v3_smoke_003_retired"])
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertIn("historical_h0_live_authorization", state)

    def test_v3_public_path_correction_report_is_persisted_and_hashed(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        evidence = state["evidence"]
        report_relative = evidence["v3_public_path_correction_report"]
        report_path = ROOT / report_relative
        report = report_path.read_text(encoding="utf-8")

        self.assertEqual(
            report_relative,
            "artifacts/diagnostics/v3_public_path_correction_report_20260809.md",
        )
        self.assertEqual(
            hashlib.sha256(report_path.read_bytes()).hexdigest(),
            evidence["v3_public_path_correction_report_sha256"],
        )
        for expected in (
            "private `_generate_response`",
            "public `generate_response`",
            "metadata attempt02",
            "metadata attempt03",
            "004_reclassified",
            "5795",
            "d9340f0bc347bbb5d7049aa55bee2739485755444f28e216e523c4bd3a5b0a16",
            "94fb64c3921b3e1e7bfecee99e6faa00e620fea7f949cc0d839d8b185035aef0",
            "M2 did not start",
            "M1 remained forbidden",
            "v3_smoke_002_m0_structured_output_failure",
        ):
            self.assertIn(expected, report)

        focused_log = ROOT / evidence["v3_public_probe_state_correction_focused_log"]
        self.assertEqual(
            hashlib.sha256(focused_log.read_bytes()).hexdigest(),
            evidence["v3_public_probe_state_correction_focused_sha256"],
        )
        self.assertEqual(
            evidence["v3_public_probe_state_correction_focused_test_count"],
            24,
        )
        invalidated = state["invalidated_diagnostics"]
        for key in (
            "v3_metadata_probe_attempt02_proxy_route",
            "v3_private_method_schema_probes_001_002",
            "v3_public_path_probe_003_derived_classification",
        ):
            self.assertIn(key, invalidated)

    def test_historical_v3_action_is_preserved_while_characterization_is_offline(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        historical = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        memory = (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8")

        self.assertEqual(
            state["current_action_scope"], CURRENT_CHARACTERIZATION_SCOPE
        )
        self.assertEqual(
            state["next_allowed_action"], CURRENT_CHARACTERIZATION_NEXT_ACTION
        )
        for text in (current, execution, memory):
            self.assertIn(CURRENT_H0_ACTION_SCOPE, text)
            self.assertIn("live_h0_candidate_authorized=true", text)
            self.assertIn("v3_smoke_003_retired=true", text)
        self.assertIn("blocked_waiting_for_explicit_protocol_deviation", historical)
        self.assertIn("sanitized", historical)
        self.assertNotIn(
            "next_allowed_action: obtain structured-output backend evidence and a frozen-path service correction",
            execution,
        )

    def test_fresh_runtime_probe_remains_historical_and_current_h0_stays_offline(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        evidence = state["evidence"]
        probe_relative = evidence["v3_fresh_runtime_compatibility_probe"]
        probe_path = ROOT / probe_relative
        probe = json.loads(probe_path.read_text(encoding="utf-8"))

        self.assertEqual(
            probe_relative,
            "artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json",
        )
        self.assertEqual(
            evidence["v3_fresh_runtime_compatibility_probe_sha256"],
            "fd1b23026689008ce9a5976581b519c2a7d62fc5c2ea05eb0964f5387e10a041",
        )
        self.assertEqual(
            hashlib.sha256(probe_path.read_bytes()).hexdigest(),
            evidence["v3_fresh_runtime_compatibility_probe_sha256"],
        )
        self.assertFalse(probe["ok"])
        self.assertEqual(
            probe["classification"],
            "exact_historical_truncation_reproduced",
        )
        self.assertEqual(probe["error_type"], "JSONDecodeError")
        self.assertEqual(probe["observed_prompt_token_counts"], [5795])
        self.assertTrue(probe["prompt_token_count_matches_history"])
        self.assertEqual(probe["llm_call_count"], 8)
        self.assertEqual(probe["high_level_attempt_count"], 4)
        self.assertEqual(probe["outer_retry_count"], 3)
        self.assertFalse(probe["database_called"])
        self.assertFalse(probe["embedding_called"])
        self.assertFalse(probe["response_bodies_persisted"])
        self.assertFalse(probe["secrets_persisted"])
        expected_pairs = [
            (
                2048,
                2048,
                "d9340f0bc347bbb5d7049aa55bee2739485755444f28e216e523c4bd3a5b0a16",
            ),
            (
                8192,
                8192,
                "94fb64c3921b3e1e7bfecee99e6faa00e620fea7f949cc0d839d8b185035aef0",
            ),
        ] * 4
        self.assertEqual(
            [
                (
                    event["max_tokens"],
                    event["completion_tokens"],
                    event["body_sha256"],
                )
                for event in probe["observed_events"]
            ],
            expected_pairs,
        )
        self.assertTrue(
            all(event["finish_reason"] == "length" for event in probe["observed_events"])
        )

        runtime_relative = evidence["v3_post_compatibility_runtime_evidence"]
        runtime_path = ROOT / runtime_relative
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
            evidence["v3_post_compatibility_runtime_evidence_sha256"],
        )
        self.assertEqual(
            runtime["remote_log_sha256"],
            "d71df1614b6da7d4d9549d739d1f2c0d67351916aa88ef5be0cc7aa2c818a761",
        )
        self.assertEqual(runtime["chat_completion_post_count"], 8)
        self.assertEqual(runtime["chat_completion_200_count"], 8)
        self.assertEqual(runtime["error_marker_count"], 0)
        self.assertFalse(runtime["writes_performed"])
        self.assertFalse(runtime["raw_remote_log_persisted"])

        report_relative = evidence["v3_fresh_runtime_compatibility_failure_report"]
        report_path = ROOT / report_relative
        report = report_path.read_text(encoding="utf-8")
        self.assertEqual(
            hashlib.sha256(report_path.read_bytes()).hexdigest(),
            evidence["v3_fresh_runtime_compatibility_failure_report_sha256"],
        )
        for expected in (
            "exact_historical_truncation_reproduced",
            "fd1b23026689008ce9a5976581b519c2a7d62fc5c2ea05eb0964f5387e10a041",
            "d71df1614b6da7d4d9549d739d1f2c0d67351916aa88ef5be0cc7aa2c818a761",
            "8/8",
            "v3_smoke_003 remains forbidden",
            "no database or embedding call",
        ):
            self.assertIn(expected, report)

        tdd_contracts = (
            (
                "v3_fresh_runtime_failure_contract_red_log",
                "v3_fresh_runtime_failure_contract_red_sha256",
                "v3_fresh_runtime_failure_contract_red_test_count",
                "artifacts/tdd/v3_fresh_runtime_failure_contract_red_146.log",
                2,
            ),
            (
                "v3_fresh_runtime_failure_contract_green_log",
                "v3_fresh_runtime_failure_contract_green_sha256",
                "v3_fresh_runtime_failure_contract_green_test_count",
                "artifacts/tdd/v3_fresh_runtime_failure_contract_green_148.log",
                2,
            ),
            (
                "v3_fresh_runtime_failure_focused_green_log",
                "v3_fresh_runtime_failure_focused_green_sha256",
                "v3_fresh_runtime_failure_focused_green_test_count",
                "artifacts/tdd/v3_fresh_runtime_failure_focused_green_151.log",
                40,
            ),
            (
                "v3_fresh_runtime_failure_full_regression_log",
                "v3_fresh_runtime_failure_full_regression_sha256",
                "v3_fresh_runtime_failure_full_regression_test_count",
                "artifacts/tdd/v3_fresh_runtime_failure_full_regression_green_154.log",
                258,
            ),
        )
        for path_key, hash_key, count_key, expected_path, expected_count in tdd_contracts:
            relative = evidence[path_key]
            path = ROOT / relative
            self.assertEqual(relative, expected_path)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                evidence[hash_key],
            )
            self.assertEqual(evidence[count_key], expected_count)

        self.assertEqual(
            state["current_action_scope"], CURRENT_CHARACTERIZATION_SCOPE
        )
        self.assertEqual(
            state["stage_progress"]["v3_fresh_runtime_compatibility_probe"],
            "fail_exact_historical_truncation_reproduced",
        )
        self.assertFalse(state["v3_smoke_003_authorized"])
        self.assertTrue(state["v3_smoke_003_retired"])
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertEqual(
            state["next_allowed_action"], CURRENT_CHARACTERIZATION_NEXT_ACTION
        )
        historical = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("blocked_waiting_for_explicit_protocol_deviation", historical)
        self.assertIn("005_fresh_restart", historical)
        self.assertIn("v3_smoke_003 remains forbidden", historical)
        for text in (
            (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md").read_text(
                encoding="utf-8"
            ),
            (REPO / "MemBind_basic_validation_experiment.md").read_text(
                encoding="utf-8"
            ),
            (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8"),
            (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8"),
        ):
            self.assertIn("005_fresh_restart", text)
            self.assertIn("historical_negative_host_qualification_evidence", text)
            self.assertIn("v3_smoke_003_retired=true", text)

    def test_historical_v3_heading_and_current_h0_stage_fence_are_explicit(self):
        historical = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        memory = (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8")
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))

        self.assertIn(
            "### V3 截断记录与当前 structured-output blocker",
            historical,
        )
        self.assertNotIn("### V3 截断记录与当前 identity blocker", historical)
        self.assertIn("forbidden_until_pass: V4/V5/V6/future_work", historical)
        self.assertEqual(
            state["forbidden_until_pass"],
            [
                "live_H0",
                "V2-R",
                "V3-R",
                "V4",
                "V5",
                "V6",
                "V7",
                "P1",
                "P2",
                "P3",
                "P4",
                "future_work",
            ],
        )
        self.assertIn(
            "forbidden_until_pass: live-H0/V2-R/V3-R/V4/V5/V6/V7/P1/P2/P3/P4/future_work",
            execution,
        )
        self.assertIn("live_h0_candidate_authorized=true", current)
        self.assertIn("Live H0, V2-R, V3-R, V4-V7, P1-P4", memory)

    def test_restored_vllm_health_does_not_bypass_backend_evidence_gate(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        evidence = state["evidence"]
        metadata_relative = evidence["v3_vllm_metadata_probe_attempt04_restored"]
        metadata_path = ROOT / metadata_relative
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(
            metadata_relative,
            "artifacts/environment/v3_vllm_metadata_probe_20260809_attempt04_restored.json",
        )
        self.assertEqual(
            hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            evidence["v3_vllm_metadata_probe_attempt04_restored_sha256"],
        )
        self.assertTrue(metadata["ok"])
        self.assertTrue(metadata["route_contract_ok"])
        self.assertEqual(metadata["version"], "0.26.0")
        self.assertEqual(metadata["models"][0]["id"], "qwen3-32b-fp8")
        self.assertEqual(metadata["models"][0]["max_model_len"], 40960)
        self.assertFalse(metadata["server_config_available"])
        self.assertIsNone(metadata["server_config"])
        self.assertFalse(metadata["generation_endpoint_called"])
        self.assertFalse(metadata["secrets_persisted"])
        statuses = {item["path"]: item["status"] for item in metadata["endpoint_results"]}
        self.assertEqual(statuses["/server_info?config_format=json"], 404)

        report_relative = evidence["v3_restored_service_status_report"]
        report_path = ROOT / report_relative
        report = report_path.read_text(encoding="utf-8")
        self.assertEqual(
            report_relative,
            "artifacts/diagnostics/v3_restored_service_status_report_20260809.md",
        )
        self.assertEqual(
            hashlib.sha256(report_path.read_bytes()).hexdigest(),
            evidence["v3_restored_service_status_report_sha256"],
        )
        for expected in (
            "service_restored_backend_config_unavailable",
            "`/server_info` remains 404",
            "generation endpoint was not called",
            "evidence_collection_only",
            "v3_smoke_003 remains forbidden",
        ):
            self.assertIn(expected, report)

        self.assertEqual(
            state["historical_blocker"],
            HISTORICAL_V3_BLOCKER,
        )
        self.assertEqual(state["current_blocker"], CURRENT_CHARACTERIZATION_BLOCKER)

    def test_restored_service_observation_is_synchronized_across_plan_memory(self):
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        memory = (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8")

        for text in (current, execution, memory):
            self.assertIn(
                "v3_vllm_metadata_probe_20260809_attempt04_restored.json",
                text,
            )
            self.assertIn("service_restored_backend_config_unavailable", text)

    def test_restricted_model_host_access_contract_is_persisted(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        memory = (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8")

        contract = state["remote_access_contract"]
        self.assertEqual(contract["ssh_alias"], "zju-liuyi")
        self.assertEqual(contract["remote_scope"], "/home/lhx/liuyi/**")
        self.assertEqual(
            contract["allowed_forced_commands"],
            ["status", "list", "read", "tail", "follow"],
        )
        self.assertFalse(contract["ordinary_shell_allowed"])
        self.assertFalse(contract["writes_allowed"])
        self.assertTrue(contract["write_extension_requires_explicit_report"])
        for text in (current, execution, memory):
            self.assertIn("ssh zju-liuyi '<forced-command>'", text)
            self.assertIn("remote_scope: /home/lhx/liuyi/**", text)
            self.assertIn(
                "allowed_forced_commands: status/list/read/tail/follow",
                text,
            )
            self.assertIn("ordinary shell", text)
            self.assertIn("write permission", text)

    def test_restricted_ssh_runtime_evidence_remains_historical_under_h0_gate(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        evidence = state["evidence"]
        relative = evidence["v3_construction_runtime_evidence"]
        path = ROOT / relative
        payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            relative,
            "artifacts/environment/v3_construction_runtime_evidence_20260809.json",
        )
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            evidence["v3_construction_runtime_evidence_sha256"],
        )
        self.assertEqual(
            payload["classification"],
            "configured_backend_auto_fresh_service_no_generation_observed",
        )
        self.assertEqual(payload["ssh_alias"], "zju-liuyi")
        self.assertEqual(payload["remote_scope"], "/home/lhx/liuyi/**")
        self.assertEqual(
            payload["commands_used"],
            ["status", "list logs", "tail logs/qwen3-32b-fp8-server.log", "read logs/qwen3-32b-fp8-server.log"],
        )
        self.assertTrue(payload["remote_contacted"])
        self.assertTrue(payload["forced_command_enforced"])
        self.assertFalse(payload["writes_performed"])
        self.assertFalse(payload["fallback_transport_attempted"])
        runtime = payload["runtime"]
        self.assertEqual(runtime["vllm_version"], "0.26.0")
        self.assertEqual(runtime["served_model_name"], "qwen3-32b-fp8")
        self.assertEqual(runtime["max_model_len"], 40960)
        self.assertEqual(runtime["structured_outputs_config"]["backend"], "auto")
        self.assertFalse(runtime["structured_outputs_config"]["disable_any_whitespace"])
        self.assertFalse(
            runtime["structured_outputs_config"]["disable_additional_properties"]
        )
        self.assertFalse(payload["generation_request_observed"])
        self.assertEqual(
            payload["remote_log_sha256"],
            "59633742b4a260682f08bc8f1838a9fcf6631d6ab582393a1686050c16e6eaac",
        )
        self.assertEqual(
            state["remote_access_status"],
            "pass_forced_command_read_only",
        )
        self.assertEqual(state["status"], CURRENT_CHARACTERIZATION_STATUS)
        self.assertEqual(
            state["current_action_scope"], CURRENT_CHARACTERIZATION_SCOPE
        )
        self.assertEqual(
            state["historical_blocker"],
            HISTORICAL_V3_BLOCKER,
        )
        self.assertTrue(state["v3_smoke_003_retired"])
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertEqual(
            state["next_allowed_action"], CURRENT_CHARACTERIZATION_NEXT_ACTION
        )

    def test_current_headers_match_characterization_while_h0_remains_history(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        historical = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")

        self.assertEqual(state["current_stage"], CURRENT_CHARACTERIZATION_STAGE)
        self.assertEqual(state["status"], CURRENT_CHARACTERIZATION_STATUS)
        self.assertIn("current_stage=NATIVE_CHARACTERIZATION", current[:2000])
        self.assertIn(
            f"status={CURRENT_CHARACTERIZATION_STATUS}", current[:2000]
        )
        self.assertIn(
            f"current_blocker={CURRENT_CHARACTERIZATION_BLOCKER_TEXT}",
            current[:2000],
        )
        self.assertIn(
            f"current_action_scope={CURRENT_CHARACTERIZATION_SCOPE}",
            current[:2000],
        )
        self.assertIn(
            f"interrupted_c2_attempt={CURRENT_INTERRUPTED_C2_RUN_ID}",
            current[:2000],
        )
        self.assertIn(
            "interruption_error_code=openai.APIConnectionError",
            current[:2000],
        )
        self.assertIn("current_stage=NATIVE_CHARACTERIZATION", execution[:2000])
        self.assertIn(
            f"status={CURRENT_CHARACTERIZATION_STATUS}", execution[:2000]
        )
        self.assertIn(
            f"current_action_scope={CURRENT_CHARACTERIZATION_SCOPE}",
            execution[:2000],
        )
        self.assertIn("HISTORICAL_SOLUTION_LANE_BELOW=true", current)
        self.assertIn("HISTORICAL_SOLUTION_LANE_BELOW=true", execution)
        self.assertIn("当前阶段**: `H0 - Host Stack Qualification`", current)
        self.assertIn("current_stage: H0", execution)
        self.assertIn("structured-output backend", execution)
        self.assertNotIn("current_stage: V1", execution[:1000])
        self.assertIn("当前阶段**：`V3 — Full Correctness Smoke`", historical[:1400])
        self.assertIn(
            "当前阻塞**：`v3_smoke_002_m0_structured_output_failure`",
            historical[:1600],
        )

    def test_mainline_memory_quarantines_all_gpt55_temporary_code(self):
        memory = (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8")
        normalized = " ".join(memory.split())

        self.assertIn("Mainline exclusion fence", memory)
        self.assertIn("`gpt55_temporary/**`", memory)
        self.assertIn("MUST NOT import, execute, test, cite, or copy", normalized)
        self.assertIn("not V3/V4/V5/V6/V7 evidence", memory)
        self.assertIn("Qwen3-32B-FP8 through vLLM 0.26.0", memory)
        self.assertIn("v3_smoke_002_m0_structured_output_failure", memory)
        self.assertIn(
            "v3_construction_runtime_identity_drift_after_smoke_002",
            memory,
        )
        self.assertIn("invalidated diagnostic", normalized)

    def test_v1_3_history_is_preserved_while_characterization_controls_execution(self):
        historical = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.2.md").read_text(
            encoding="utf-8"
        )
        current = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md").read_text(
            encoding="utf-8"
        )
        protocol = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")

        self.assertIn("frozen solution-validation lane 的历史 overlay", current)
        self.assertIn("MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md", current)
        self.assertIn("current_stage=NATIVE_CHARACTERIZATION", current)
        self.assertIn("v1.2 保留为历史协议", current)
        self.assertIn("本文件已由 `MemBind_CURRENT_VALIDATION_PLAN_v1.3.md` supersede", historical)
        self.assertIn("CURRENT VALIDATION PLAN v1.3", protocol)
        self.assertIn("Current Validation v1.3", execution)
        self.assertIn("V1 Correctness nondeterminism closure", protocol)
        self.assertIn("V1 Correctness nondeterminism closure", execution)

    def test_current_state_enters_characterization_while_v1_v2_evidence_remains(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
        diagnostic = json.loads(
            (
                ROOT
                / "artifacts"
                / "diagnostics"
                / "embedding_nondeterminism_source5.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(state["protocol_version"], "current-validation-v1.3")
        self.assertEqual(state["current_stage"], CURRENT_CHARACTERIZATION_STAGE)
        self.assertEqual(state["status"], CURRENT_CHARACTERIZATION_STATUS)
        self.assertEqual(
            state["historical_blocker"],
            HISTORICAL_V3_BLOCKER,
        )
        self.assertEqual(state["current_blocker"], CURRENT_CHARACTERIZATION_BLOCKER)
        interruption = state["native_characterization_c2_interruption"]
        self.assertEqual(interruption["run_id"], CURRENT_INTERRUPTED_C2_RUN_ID)
        self.assertFalse(interruption["semantic_attempt_consumed"])
        self.assertEqual(interruption["semantic_attempts_remaining"], 1)
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
        self.assertEqual(
            state["next_allowed_action"], CURRENT_CHARACTERIZATION_NEXT_ACTION
        )
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertTrue(state["v3_smoke_003_retired"])
        self.assertEqual(
            state["forbidden_until_pass"],
            [
                "live_H0",
                "V2-R",
                "V3-R",
                "V4",
                "V5",
                "V6",
                "V7",
                "P1",
                "P2",
                "P3",
                "P4",
                "future_work",
            ],
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
        self.assertIn(
            "修复当前 code 中 stale 64-run/global-shuffle implementation",
            body,
        )

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
