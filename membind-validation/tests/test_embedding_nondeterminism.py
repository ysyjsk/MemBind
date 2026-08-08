import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedding_nondeterminism import (  # noqa: E402
    NOT_COMPUTABLE,
    analyze_query_events,
    analyze_retained_artifacts,
    assert_safe_artifact,
    compare_source_state,
    write_retained_diagnostic,
)


class RetainedEmbeddingNondeterminismTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = ROOT / "artifacts"
        cls.run_a = json.loads(
            (
                cls.artifacts
                / "search_forensics"
                / "diagnostic_smoke14_source5_M0_001.json"
            ).read_text(encoding="utf-8")
        )
        cls.run_b = json.loads(
            (
                cls.artifacts
                / "search_forensics"
                / "diagnostic_smoke14_source5_M0_002.json"
            ).read_text(encoding="utf-8")
        )

    def test_real_retained_evidence_closes_v1_with_explicit_limits(self):
        result = analyze_retained_artifacts(self.artifacts)

        self.assertEqual(
            result["schema_version"],
            "membind.v1.retained_embedding_closure.v1",
        )
        self.assertEqual(result["analysis_mode"], "retained_artifact_only")
        self.assertTrue(result["controls"]["same_question_id"])
        self.assertTrue(result["controls"]["same_llm_cache_id"])
        self.assertEqual(result["controls"]["episode_count_each"], 6)
        self.assertEqual(result["controls"]["live_llm_calls_each"], 0)
        self.assertEqual(result["controls"]["post_run_node_count_each"], 0)

        entities = result["source_state"]["entities"]
        edges = result["source_state"]["edges"]
        self.assertTrue(entities["logical_content_equal"])
        self.assertEqual(entities["count_each"], 18)
        self.assertEqual(entities["embedding_hash_equal_count"], 13)
        self.assertEqual(entities["embedding_hash_changed_count"], 5)
        self.assertTrue(edges["logical_content_equal"])
        self.assertEqual(edges["count_each"], 25)
        self.assertEqual(edges["embedding_hash_equal_count"], 23)
        self.assertEqual(edges["embedding_hash_changed_count"], 2)

        numerical = result["numerical_metrics"]
        for name in (
            "cosine_cross_run",
            "l2_cross_run",
            "max_abs_diff",
            "changed_component_count",
            "neo4j_cosine_score_delta",
        ):
            self.assertEqual(numerical[name]["status"], NOT_COMPUTABLE)
            self.assertIsNone(numerical[name]["value"])

        fulltext = result["query_comparison"]["fulltext"]
        self.assertEqual(fulltext["paired_count"], 16)
        self.assertTrue(fulltext["input_keys_equal"])
        self.assertTrue(fulltext["candidate_membership_equal"])
        self.assertTrue(fulltext["candidate_order_equal"])

        cosine = result["query_comparison"]["cosine"]
        self.assertEqual(cosine["pairing_status"], "not_computable_per_input")
        self.assertFalse(cosine["aggregate_vector_hash_bag_equal"])
        self.assertTrue(cosine["aggregate_backend_membership_bag_equal"])
        self.assertFalse(cosine["aggregate_backend_order_bag_equal"])
        self.assertFalse(
            cosine["aggregate_python_selected_membership_bag_equal"]
        )
        self.assertEqual(
            cosine["top_k_membership_changed_per_input"], NOT_COMPUTABLE
        )

        prompt = result["prompt_comparison"]
        self.assertEqual(
            prompt["requested_prompt_hash"],
            "044bf756ec52b373381a210f2df912e7f07d7122a08fe89e95ea23c405daa7f8",
        )
        self.assertEqual(
            prompt["expected_prompt_hash"],
            "61854cc6cdb1c7b1de8b3e355a3a9dc9f8221e8a90c0ea941123e8f93f226570",
        )
        self.assertEqual(prompt["candidate_substitution_count"], 1)
        self.assertEqual(prompt["causal_link_to_embedding"], "not_established")

        self.assertFalse(result["claims"]["live_embedding_bitwise_deterministic"])
        self.assertFalse(
            result["claims"]["live_embedding_suitable_as_bitwise_correctness_oracle"]
        )
        self.assertEqual(
            result["claims"]["embedding_drift_caused_prompt_divergence"],
            "not_established",
        )
        self.assertEqual(
            result["v1_gate"]["status"],
            "pass_with_explicit_evidence_limits",
        )
        self.assertFalse(result["v1_gate"]["raw_vector_recapture_required"])

    def test_source_state_comparison_ignores_misleading_stored_graph_hash(self):
        first = {
            "logical_graph_hash": "hash-including-vector-a",
            "entities": [
                {
                    "name": "A",
                    "summary": "same",
                    "labels": ["Entity"],
                    "embedding_dimension": 2,
                    "embedding_sha256": "a" * 64,
                    "embedding_norm": 1.0,
                }
            ],
        }
        second = copy.deepcopy(first)
        second["logical_graph_hash"] = "hash-including-vector-b"
        second["entities"][0]["embedding_sha256"] = "b" * 64
        second["entities"][0]["embedding_norm"] = 1.000001

        result = compare_source_state(first, second, record_type="entities")

        self.assertTrue(result["logical_content_equal"])
        self.assertEqual(result["embedding_hash_changed_count"], 1)
        self.assertTrue(result["stored_logical_graph_hash_ignored"])

    def test_query_analysis_is_invariant_to_completion_order(self):
        original = analyze_query_events(
            self.run_a["query_events"], self.run_b["query_events"], source_sequence=5
        )
        reordered = analyze_query_events(
            list(reversed(self.run_a["query_events"])),
            self.run_b["query_events"][::2] + self.run_b["query_events"][1::2],
            source_sequence=5,
        )

        self.assertEqual(reordered, original)

    def test_cosine_events_never_claim_per_input_delta_without_input_key(self):
        result = analyze_query_events(
            self.run_a["query_events"], self.run_b["query_events"], source_sequence=5
        )["cosine"]

        self.assertEqual(result["pairing_status"], "not_computable_per_input")
        self.assertIn("correlation", result["reason"])
        self.assertEqual(result["top_k_order_changed_per_input"], NOT_COMPUTABLE)

    def test_artifact_safety_rejects_headers_secrets_and_causal_overclaim(self):
        for unsafe in (
            {"Authorization": "Bearer secret"},
            {"api_key": "secret"},
            {"headers": {"x": "y"}},
            {"claims": {"embedding_drift_caused_prompt_divergence": True}},
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    assert_safe_artifact(unsafe)

    def test_writer_is_exclusive_and_output_contains_no_prompt_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diagnostic.json"

            first = write_retained_diagnostic(self.artifacts, path)
            raw = path.read_text(encoding="utf-8")

            self.assertEqual(first["analysis_mode"], "retained_artifact_only")
            self.assertNotIn("requested_prompt_parts", raw)
            self.assertNotIn("Authorization", raw)
            self.assertNotIn("api_key", raw.casefold())
            with self.assertRaises(FileExistsError):
                write_retained_diagnostic(self.artifacts, path)

    def test_missing_required_evidence_fails_instead_of_fabricating_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "required retained artifact"):
                analyze_retained_artifacts(Path(tmp))

    def test_cli_persists_auditable_summary_and_refuses_overwrite(self):
        script = ROOT / "scripts" / "analyze_embedding_nondeterminism.py"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "embedding_nondeterminism_source5.json"
            command = [
                sys.executable,
                str(script),
                "--artifacts-root",
                str(self.artifacts),
                "--output",
                str(output),
            ]

            first = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            summary = json.loads(first.stdout)
            self.assertEqual(summary["status"], "written")
            self.assertEqual(
                summary["schema_version"],
                "membind.v1.retained_embedding_closure.v1",
            )
            self.assertEqual(
                summary["v1_gate_status"],
                "pass_with_explicit_evidence_limits",
            )
            self.assertEqual(summary["embedding_hash_changed_counts"], {
                "entities": 5,
                "edges": 2,
            })
            self.assertEqual(
                summary["output_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )
            self.assertNotIn("prompt", first.stdout.casefold())
            self.assertNotIn("api_key", first.stdout.casefold())

            original_hash = hashlib.sha256(output.read_bytes()).hexdigest()
            second = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stderr)
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), original_hash)


if __name__ == "__main__":
    import unittest

    unittest.main()
