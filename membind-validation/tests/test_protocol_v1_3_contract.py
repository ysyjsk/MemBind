"""RED contracts for the reviewed Protocol v1.3 document and state transition.

These tests intentionally describe the post-review target before any plan, proposal,
memory, or state file is changed.  They are document-only checks: importing this
module must never contact a model endpoint, database, or remote host.
"""

import hashlib
import json
import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
AUTHORITATIVE_PLAN = REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md"
PROPOSAL = REPO / "MemBind_basic_validation_experiment.md"
REVISION = REPO / "MemBind_Protocol_Revision_v1.3_TopTier_Fairness.md"
EXECUTION_PLAN = ROOT / "EXPERIMENT_PLAN.md"
MEMORY = ROOT / "GLOBAL_MEMORY.md"
STATE = ROOT / "CURRENT_STATE.json"
REVIEW = (
    ROOT
    / "artifacts"
    / "diagnostics"
    / "protocol_v1_3_top_tier_fairness_review_20260809.md"
)

HISTORICAL_BLOCKER = "v3_smoke_002_m0_structured_output_failure"
HISTORICAL_ARTIFACT = (
    "artifacts/environment/"
    "v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json"
)
HISTORICAL_ARTIFACT_SHA256 = (
    "fd1b23026689008ce9a5976581b519c2a7d62fc5c2ea05eb0964f5387e10a041"
)
CANARY_ID = "c6853660"


def _read(path: Path) -> str:
    """Read a UTF-8 contract document without evaluating any project code."""

    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    """Normalize prose whitespace while retaining punctuation and field names."""

    return re.sub(r"\s+", " ", text).strip()


def _section(text: str, start: str, end: str | None = None) -> str:
    """Return a named contract block delimited by stable HTML comments."""

    start_marker = f"<!-- {start} -->"
    end_marker = f"<!-- {end or start.replace('_START', '_END')} -->"
    start_index = text.index(start_marker) + len(start_marker)
    end_index = text.index(end_marker, start_index)
    return text[start_index:end_index]


class ProtocolV13DocumentContractTests(TestCase):
    """Specify the reviewed plan/proposal contract before implementation begins."""

    @classmethod
    def setUpClass(cls):
        cls.proposal = _read(PROPOSAL)
        cls.revision = _read(REVISION)
        cls.execution = _read(EXECUTION_PLAN)
        cls.memory = _read(MEMORY)
        cls.state = json.loads(_read(STATE))

    def _plan(self) -> str:
        """Fail as a RED assertion, rather than error, while the plan is absent."""

        self.assertTrue(
            AUTHORITATIVE_PLAN.is_file(),
            "the reviewed protocol needs MemBind_CURRENT_VALIDATION_PLAN_v1.3.md",
        )
        return _read(AUTHORITATIVE_PLAN)

    def test_authoritative_v1_3_plan_exists_and_core_state_is_synchronized(self):
        self.assertTrue(
            AUTHORITATIVE_PLAN.is_file(),
            "the reviewed protocol needs an authoritative v1.3 execution plan",
        )
        plan = self._plan()
        synchronized = (plan, self.proposal, self.execution, self.memory)
        for document in synchronized:
            for token in (
                "current-validation-v1.3",
                "h0_protocol_accepted_harness_not_implemented",
                "late_discovered_pre_freeze_host_compatibility_failure",
                "h0_offline_tdd_and_harness_only",
                "live_h0_candidate_authorized=false",
            ):
                self.assertIn(token, document)

    def test_state_enters_h0_offline_only_without_authorizing_a_live_candidate(self):
        state = self.state
        self.assertIn("protocol_version", state)
        self.assertEqual(state["protocol_version"], "current-validation-v1.3")
        self.assertEqual(state["current_stage"], "H0")
        self.assertEqual(
            state["status"], "h0_protocol_accepted_harness_not_implemented"
        )
        self.assertEqual(
            state["current_blocker"],
            "late_discovered_pre_freeze_host_compatibility_failure",
        )
        self.assertEqual(
            state["current_action_scope"], "h0_offline_tdd_and_harness_only"
        )
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertFalse(state["v3_smoke_003_authorized"])
        self.assertIn("offline", state["next_allowed_action"])
        self.assertIn("separate explicit gate", state["next_allowed_action"])

    def test_historical_blocker_identity_and_artifact_survive_reclassification(self):
        state = self.state
        self.assertIn("historical_blocker", state)
        self.assertEqual(state["historical_blocker"], HISTORICAL_BLOCKER)
        self.assertEqual(
            state["evidence"]["v3_fresh_runtime_compatibility_probe"],
            HISTORICAL_ARTIFACT,
        )
        self.assertEqual(
            state["evidence"]["v3_fresh_runtime_compatibility_probe_sha256"],
            HISTORICAL_ARTIFACT_SHA256,
        )
        artifact = ROOT / HISTORICAL_ARTIFACT
        self.assertEqual(
            hashlib.sha256(artifact.read_bytes()).hexdigest(),
            HISTORICAL_ARTIFACT_SHA256,
        )
        for document in (
            self._plan(),
            self.proposal,
            self.execution,
            self.memory,
        ):
            self.assertIn(HISTORICAL_BLOCKER, document)
            self.assertIn(HISTORICAL_ARTIFACT, document)
            self.assertIn("historical_negative_host_qualification_evidence", document)

    def test_v3_smoke_003_is_retired_instead_of_becoming_the_next_action(self):
        state = self.state
        self.assertFalse(state["v3_smoke_003_authorized"])
        self.assertIn("v3_smoke_003_retired", state)
        self.assertTrue(state["v3_smoke_003_retired"])
        self.assertNotIn("v3_smoke_003", state["next_allowed_action"])
        for document in (
            self._plan(),
            self.proposal,
            self.execution,
            self.memory,
        ):
            self.assertIn("v3_smoke_003_retired=true", document)

    def test_known_canary_is_quarantined_and_h0_is_calibration_only(self):
        plan = self._plan()
        normalized = _normalized(plan)
        self.assertIn(CANARY_ID, plan)
        self.assertRegex(
            normalized,
            rf"{CANARY_ID}.*quarantined_regression_canary.*"
            r"never.*candidate.*selection",
        )
        for document in (plan, self.proposal):
            self.assertIn("h0_data_scope=calibration_only", document)
            self.assertIn("evaluation_split_access=false", document)
            self.assertIn("canary_selection_eligible=false", document)
            self.assertIn("candidate_selection_metric=first_passing_only", document)

    def test_h0_candidates_have_an_immutable_content_addressed_first_pass_order(self):
        plan = self._plan()
        block = _section(plan, "H0_CANDIDATE_REGISTRY_START")
        self.assertIn("candidate_order: [Q1, Q2, Q3]", block)
        self.assertIn("registry_immutable: true", block)
        self.assertIn("content_address_algorithm: sha256", block)
        self.assertIn("selection_rule: first_passing", block)
        self.assertIn("performance_observed_for_selection: false", block)
        self.assertIn("later_candidates_after_pass: forbidden", block)
        self.assertIn("candidate_artifact_kind: delta_spec_not_runnable_manifest", block)
        self.assertRegex(
            block, r"shared_host_base_spec_sha256: [0-9a-f]{64}"
        )
        self.assertIn("resolved_candidate_manifest_required_before_live: true", block)
        for candidate in ("Q1", "Q2", "Q3"):
            self.assertRegex(
                block, rf"{candidate}_delta_spec_sha256: [0-9a-f]{{64}}"
            )

    def test_candidate_diffs_are_exact_and_effective_payload_is_observed(self):
        plan = self._plan()
        block = _section(plan, "H0_CANDIDATE_REGISTRY_START")
        required = (
            "Q1_diff_from_Q0: completion_budget_policy_only",
            "q0_to_q1_causal_ab_claim: forbidden",
            "Q2_diff_from_Q1: temperature_top_p_top_k_min_p_only",
            "Q3_diff_from_Q2: structured_output_mode_only",
            "Q1_requested_max_tokens: 16384",
            "Q2_temperature: 0.7",
            "Q2_top_p: 0.8",
            "Q2_top_k: 20",
            "Q2_min_p: 0",
            "Q3_structured_output_mode: json_object",
            "requested_request_payload_sha256",
            "observed_request_payload_sha256",
            "top_k_observed_in_payload",
            "min_p_observed_in_payload",
            "not_sent_by_client_contract",
        )
        for token in required:
            self.assertIn(token, block)

    def test_q3_is_explicit_not_automatic_and_uses_effective_shim_schema_injection(self):
        plan = self._plan()
        block = _section(plan, "H0_CANDIDATE_REGISTRY_START")
        for token in (
            "Q3_activation: explicit_after_recorded_Q2_failure",
            "Q3_automatic_fallback: forbidden",
            "json_object_schema_injection_source: effective_shim_schema",
            "schema_upstream_sha256",
            "schema_effective_sha256",
            "schema_injected_sha256",
            "schema_injected_sha256_must_equal_schema_effective_sha256: true",
        ):
            self.assertIn(token, block)

    def test_completion_budget_is_context_safe_and_logs_requested_and_effective_values(self):
        plan = self._plan()
        block = _section(plan, "H0_BUDGET_RETRY_CONTRACT_START")
        compact = re.sub(r"\s+", "", block)
        self.assertIn(
            "effective_max_tokens=max(0,min(requested_max_tokens,"
            "context_limit-prompt_tokens-safety_margin_tokens))",
            compact,
        )
        for token in (
            "requested_max_tokens",
            "effective_max_tokens",
            "context_limit",
            "prompt_tokens",
            "safety_margin_tokens",
            "context_budget_insufficient",
        ):
            self.assertIn(token, block)
        self.assertNotIn("effective_max_tokens_always_16384", block)

    def test_trials_http_attempts_retries_and_seed_policy_are_not_conflated(self):
        plan = self._plan()
        block = _section(plan, "H0_BUDGET_RETRY_CONTRACT_START")
        for token in (
            "logical_trial_id",
            "http_attempt_id",
            "retry_index",
            "logical_trial_seed",
            "trial_seed_policy: fixed_20260806",
            "logical_trials_statistically_independent: false",
            "server_observed_seed",
            "retry_same_logical_trial: true",
            "retry_is_not_independent_trial: true",
            "infrastructure_failure",
            "whole_stage_rerun",
            "no_single_method_or_candidate_selective_rerun: true",
        ):
            self.assertIn(token, block)

    def test_semantic_utility_gate_rejects_valid_but_empty_or_degenerate_output(self):
        plan = self._plan()
        block = _section(plan, "H0_SEMANTIC_UTILITY_START")
        required_invariants = (
            "semantic_utility_invariants_data_scope: calibration_only",
            "semantic_utility_invariants_sha256",
            "semantic_utility_invariants_frozen_before_candidate_execution: true",
            "candidate_outputs_used_to_set_invariants: false",
            "json_parse_success: true",
            "pydantic_validation_success: true",
            "expected_nonempty_call_ids",
            "minimum_entity_count_by_call",
            "minimum_distinct_normalized_entity_name_count_by_call",
            "expected_episode_indices_by_call",
            "valid_empty_or_degenerate_output: qualification_failure",
            "evaluation_data_used_for_semantic_utility: false",
        )
        for token in required_invariants:
            self.assertIn(token, block)

    def test_oracle_namespace_is_content_addressed_and_h0_cannot_write_formal_oracles(self):
        plan = self._plan()
        for token in (
            "oracle_namespace_content_address_algorithm: sha256",
            "oracle_namespace_binds_qualified_host_manifest: true",
            "h0_formal_oracle_writes: forbidden",
            "old_v2_v3_oracle_reuse: forbidden",
            "correctness_lane: qualified_capture_read_only_replay",
            "performance_lane: live_model_no_response_replay",
        ):
            self.assertIn(token, plan)

    def test_live_performance_has_a_preregistered_two_sided_work_volume_guardrail(self):
        plan = self._plan()
        block = _section(plan, "WORK_VOLUME_GUARDRAIL_START")
        for token in (
            "work_volume_lower_bound",
            "work_volume_upper_bound",
            "llm_call_ratio",
            "input_token_ratio",
            "output_token_ratio",
            "embedding_call_ratio",
            "below_lower_bound: performance_confounded",
            "above_upper_bound: performance_confounded",
            "bounds_frozen_on_calibration_before_evaluation: true",
        ):
            self.assertIn(token, block)

    def test_u0_d0_guardrail_and_quality_feasible_concurrency_tuning_are_fixed(self):
        plan = self._plan()
        block = _section(plan, "BASELINE_FAIRNESS_START")
        for token in (
            "U0=Upstream-Qualified-Graphiti-Serial",
            "D0=Deterministic-Graphiti-Serial",
            "concurrency_candidates: [1, 2, 4, 8]",
            "quality_feasibility_checked_before_performance_selection: true",
            "selection_objective: minimum_calibration_median_makespan",
            "selection_tie_break: smallest_concurrency",
            "selection_data_scope: calibration_only",
            "best_tuned_m1",
            "best_tuned_m2",
            "iso-cap comparison",
        ):
            self.assertIn(token, block)
        self.assertNotIn("iso-resource comparison", block)

    def test_paper_track_is_not_authorized_and_general_runtime_claims_are_forbidden(self):
        plan = self._plan()
        for phase in ("P1", "P2", "P3", "P4"):
            self.assertIn(f"{phase}_authorized=false", plan)
        for token in (
            "paper_track_status=future_work_only",
            "current_pilot_does_not_support_general_agent_memory_runtime_claims",
            "cross_architecture_claims_forbidden=true",
            "MemoryArena_role=trace_replay_workload",
        ):
            self.assertIn(token, plan)

    def test_agent_memory_is_cited_only_as_an_arxiv_preprint(self):
        plan = self._plan()
        revision = self.revision
        for document in (plan, revision):
            self.assertRegex(
                _normalized(document),
                r"Agent Memory: Characterization and System Implications of "
                r"Stateful Long-Horizon Workloads.{0,160}arXiv preprint.{0,80}2026",
            )
            self.assertNotRegex(document, r"IISWC 2026\s*(?:\(|,).*to appear")

    def test_detailed_review_report_is_persisted_with_sources_and_decisions(self):
        self.assertTrue(REVIEW.is_file())
        report = _read(REVIEW)
        for token in (
            "accept_modify_reject",
            "source_url",
            "accessed_at",
            "supported_claim",
            "unsupported_or_qualified_claim",
            "OSDI 2026 Call for Artifacts",
            "NSDI 2026 Call for Artifacts",
            "Graphiti 021d3a5",
            "Qwen3-32B-FP8",
            "vLLM 0.26.0",
            "Agent Memory",
            "arXiv preprint",
        ):
            self.assertIn(token, report)
