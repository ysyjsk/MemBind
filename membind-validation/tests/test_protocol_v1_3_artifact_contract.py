"""Artifact-level RED contracts for the reviewed Protocol v1.3 overlay.

These checks keep document claims tied to reproducible, content-addressed files.
They are offline-only and must not contact model, database, or remote services.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
CONFIGS = ROOT / "configs" / "h0"
SPLIT = ROOT / "artifacts" / "dataset" / "frozen_split_v1_3.json"
LEGACY_SPLIT = ROOT / "artifacts" / "dataset" / "frozen_split.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProtocolV13ArtifactContractTests(TestCase):
    """Require the static v1.3 artifacts to be internally reproducible."""

    def test_candidate_delta_specs_share_one_content_addressed_base(self):
        base_path = CONFIGS / "shared_host_base_v1_3.json"
        self.assertTrue(base_path.is_file())
        base = _json(base_path)
        base_hash = _sha256(base_path)

        self.assertEqual(base["manifest_kind"], "shared_host_base_spec")
        self.assertFalse(base["live_eligible"])
        self.assertEqual(base["graphiti"]["commit"], "021d3a5")
        self.assertEqual(base["construction"]["vllm_version"], "0.26.0")
        self.assertEqual(
            base["construction"]["request_selected_backend"], "unobserved"
        )
        self.assertEqual(
            base["dataset"]["split_manifest_sha256"], _sha256(SPLIT)
        )
        embedding_manifest = ROOT / base["embedding"]["manifest"]
        self.assertEqual(
            base["embedding"]["manifest_sha256"], _sha256(embedding_manifest)
        )
        self.assertIn("resolved_client_implementation_sha256", base["unresolved_fields"])
        self.assertIn("effective_schema_sha256", base["unresolved_fields"])

        for candidate_id in ("Q1", "Q2", "Q3"):
            candidate = _json(CONFIGS / f"{candidate_id}.json")
            self.assertEqual(candidate["manifest_kind"], "candidate_delta_spec")
            self.assertEqual(
                candidate["shared_host_base_spec"],
                "configs/h0/shared_host_base_v1_3.json",
            )
            self.assertEqual(candidate["shared_host_base_spec_sha256"], base_hash)
            self.assertFalse(candidate["live_eligible"])

    def test_candidate_request_diffs_are_exact_and_seed_is_fixed(self):
        q0 = _json(CONFIGS / "Q0_historical.json")
        q1 = _json(CONFIGS / "Q1.json")
        q2 = _json(CONFIGS / "Q2.json")
        q3 = _json(CONFIGS / "Q3.json")

        self.assertEqual(q0["seed_policy"], "fixed_20260806")
        for candidate in (q1, q2, q3):
            self.assertEqual(candidate["seed_policy"], "fixed_20260806")

        self.assertEqual(
            q1["candidate_diff_from_previous"], ["completion_budget_policy"]
        )
        self.assertEqual(
            q2["candidate_diff_from_previous"],
            ["temperature", "top_p", "top_k", "min_p"],
        )
        self.assertEqual(
            q3["candidate_diff_from_previous"], ["structured_output_mode"]
        )
        self.assertEqual(q3["request_selected_backend"], "unobserved")
        self.assertEqual(
            q3["pydantic_json_schema_enforcement"], "not_applicable"
        )

        plan = (REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Q1_diff_from_Q0: completion_budget_policy_only", plan)
        self.assertIn("q0_to_q1_causal_ab_claim: forbidden", plan)
        self.assertIn("trial_seed_policy: fixed_20260806", plan)
        self.assertIn("logical_trials_statistically_independent: false", plan)

    def test_v1_3_split_is_reproducible_by_its_recorded_generator(self):
        manifest = _json(SPLIT)
        self.assertIn("generator_script", manifest)
        generator = ROOT / manifest["generator_script"]
        self.assertTrue(generator.is_file())
        self.assertEqual(manifest["generator_script_sha256"], _sha256(generator))
        self.assertEqual(manifest["legacy_split_sha256"], _sha256(LEGACY_SPLIT))

        spec = importlib.util.spec_from_file_location("dataset_v1_3", generator)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        derived = module.derive_split_v1_3(
            data_path=Path(manifest["source_path"]),
            legacy_split_path=LEGACY_SPLIT,
            quarantined_question_ids=manifest[
                "compatibility_development_question_ids"
            ],
            quarantine_reason=manifest["quarantine_reason"],
        )
        self.assertEqual(derived, manifest)

    def test_execution_plan_v3r_v4_v5_matches_authoritative_overlay(self):
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        self.assertIn("## V3-R, V4, and V5 Gates", execution)
        self.assertIn("new calibration smoke ID `v3r_smoke_001`", execution)
        self.assertIn("U0=Upstream-Qualified-Graphiti-Serial", execution)
        self.assertIn("D0=Deterministic-Graphiti-Serial", execution)
        self.assertIn("C={1,2,4,8}", execution)
        self.assertIn("quality-feasible", execution)
        self.assertNotIn("existing smoke instance", execution)
        self.assertNotIn("does not run an upstream semantic guardrail", execution)

    def test_proposal_marks_old_v2_as_history_and_freezes_verified_bfloat16(self):
        proposal = (REPO / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        base = (ROOT / "configs" / "base.yaml").read_text(encoding="utf-8")
        self.assertIn("### V2 pilot boundary (historical completed evidence)", proposal)
        self.assertIn("dtype：BF16", proposal)
        self.assertIn("embedding_dtype: bfloat16", base)
        self.assertNotIn("dtype：FP16 或 BF16", proposal)
        self.assertIn("CURRENT VALIDATION v1.3 V4 frozen minimal lifecycle", proposal)
