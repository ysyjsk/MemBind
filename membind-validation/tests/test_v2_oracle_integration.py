import json
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graphiti_native import M0_NATIVE_SERIAL  # noqa: E402
from embedding_identity import build_operator_fingerprint_manifest  # noqa: E402
from v2_oracle_integration import (  # noqa: E402
    build_v2_oracle_specs,
    run_v2_oracle_integration,
    validate_v2_oracle_statuses,
)


class V2OracleIntegrationTests(TestCase):
    def test_specs_are_m0_capture_then_m0_replay_with_one_cache_id(self):
        specs = build_v2_oracle_specs("v2_oracle_integration_001", "q")

        self.assertEqual([spec["mode"] for spec in specs], ["capture", "replay"])
        self.assertEqual([spec["method"] for spec in specs], [M0_NATIVE_SERIAL, M0_NATIVE_SERIAL])
        self.assertEqual(specs[0]["cache_id"], specs[1]["cache_id"])
        self.assertEqual(specs[0]["run_id"], "v2_oracle_integration_001_M0_capture")
        self.assertEqual(specs[1]["run_id"], "v2_oracle_integration_001_M0_replay")

    def test_status_gate_requires_zero_replay_model_calls_and_clean_graph(self):
        capture = {
            "status": "success",
            "llm_metrics": {"llm_call_count": 3},
            "embedding_metrics": {"embedding_call_count": 2},
            "post_run_node_count": 0,
            "rank_call_count": 0,
        }
        replay = {
            "status": "success",
            "llm_metrics": {"llm_call_count": 0},
            "embedding_metrics": {"embedding_call_count": 0},
            "post_run_node_count": 0,
            "rank_call_count": 0,
            "unexpected_prompt_count": 0,
            "unexpected_embedding_count": 0,
        }
        self.assertEqual(validate_v2_oracle_statuses(capture, replay), [])

        replay["embedding_metrics"]["embedding_call_count"] = 1
        errors = validate_v2_oracle_statuses(capture, replay)
        self.assertIn("replay_embedding_calls", errors)

        capture["llm_metrics"]["llm_call_count"] = 0
        capture["embedding_metrics"]["embedding_call_count"] = 0
        errors = validate_v2_oracle_statuses(capture, replay)
        self.assertIn("capture_llm_calls", errors)
        self.assertIn("capture_embedding_calls", errors)

    def test_status_gate_rejects_nonzero_cross_encoder_and_cleanup_leak(self):
        capture = {
            "status": "success",
            "llm_metrics": {"llm_call_count": 1},
            "embedding_metrics": {"embedding_call_count": 1},
            "post_run_node_count": 0,
            "rank_call_count": 1,
        }
        replay = {
            "status": "success",
            "llm_metrics": {"llm_call_count": 0},
            "embedding_metrics": {"embedding_call_count": 0},
            "post_run_node_count": 3,
            "rank_call_count": 0,
        }
        errors = validate_v2_oracle_statuses(capture, replay)
        self.assertIn("capture_cross_encoder_calls", errors)
        self.assertIn("replay_cleanup", errors)


class V2OracleIntegrationAsyncTests(IsolatedAsyncioTestCase):
    async def test_missing_capture_audit_stops_before_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            namespace = {
                "served_model_id": "qwen3-embedding-0.6b",
                "identity_kind": "deployment_fingerprint",
                "identity_value": "a" * 64,
                "dimension": 1024,
                "dtype": "float16",
                "pooling": "last_token",
                "normalization": "l2",
                "instruction_policy": "none",
                "input_transform": "utf8_exact_v1",
            }
            evidence = {
                field: {
                    "value": namespace[field],
                    "status": "verified",
                    "source": f"test evidence for {field}",
                }
                for field in (
                    "served_model_id",
                    "identity_value",
                    "dimension",
                    "dtype",
                    "pooling",
                    "normalization",
                    "instruction_policy",
                    "input_transform",
                )
            }
            manifest = build_operator_fingerprint_manifest(
                operator_fingerprint="a" * 64,
                namespace=namespace,
                field_evidence=evidence,
                endpoint_observation={"served_model_id": "qwen3-embedding-0.6b"},
            )
            manifest_path = artifacts / "environment" / "embedding_model_fingerprint.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            calls = []

            async def capture_without_audit(**kwargs):
                calls.append(kwargs["spec"]["mode"])
                for directory in ("prompt_cache", "embedding_cache"):
                    path = artifacts / directory / "v2_oracle_integration_001.jsonl"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("capture\n", encoding="utf-8")
                return {
                    "status": "success",
                    "llm_metrics": {"llm_call_count": 1},
                    "embedding_metrics": {"embedding_call_count": 1},
                    "post_run_node_count": 0,
                    "rank_call_count": 0,
                    "canonical_graph_hash": "same",
                    "retrieval_metrics": {"same": True},
                }

            result = await run_v2_oracle_integration(
                artifacts=artifacts,
                run_experiment_fn=capture_without_audit,
            )

            self.assertEqual(calls, ["capture"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["gate_errors"], ["model_oracle_audit_missing"])

    async def test_unresolved_manifest_persists_preflight_failure_without_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            namespace = {
                "served_model_id": "qwen3-embedding-0.6b",
                "identity_kind": "deployment_fingerprint",
                "identity_value": "a" * 64,
                "dimension": 1024,
                "dtype": "unresolved",
                "pooling": "last_token",
                "normalization": "l2",
                "instruction_policy": "none",
                "input_transform": "utf8_exact_v1",
            }
            evidence = {
                field: {
                    "value": namespace[field],
                    "status": "verified",
                    "source": f"test evidence for {field}",
                }
                for field in (
                    "served_model_id",
                    "identity_value",
                    "dimension",
                    "dtype",
                    "pooling",
                    "normalization",
                    "instruction_policy",
                    "input_transform",
                )
            }
            evidence["dtype"] = {
                "value": None,
                "status": "unresolved",
                "source": "remote launch dtype evidence missing",
            }
            manifest = build_operator_fingerprint_manifest(
                operator_fingerprint="a" * 64,
                namespace=namespace,
                field_evidence=evidence,
                endpoint_observation={"served_model_id": "qwen3-embedding-0.6b"},
            )
            manifest_path = artifacts / "environment" / "embedding_model_fingerprint.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            calls = []

            async def must_not_run(**kwargs):
                calls.append(kwargs)
                raise AssertionError("runner must not be called")

            result = await run_v2_oracle_integration(
                artifacts=artifacts,
                run_experiment_fn=must_not_run,
            )

            self.assertEqual(calls, [])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["gate_errors"], ["embedding_runtime_config"])
            self.assertIn("dtype", result["error"])
            self.assertTrue(
                (
                    artifacts
                    / "diagnostics"
                    / "v2_oracle_integration_001_summary.json"
                ).is_file()
            )
            self.assertFalse((artifacts / "prompt_cache").exists())
            self.assertFalse((artifacts / "embedding_cache").exists())

    async def test_replay_cache_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            namespace = {
                "served_model_id": "qwen3-embedding-0.6b",
                "identity_kind": "deployment_fingerprint",
                "identity_value": "a" * 64,
                "dimension": 1024,
                "dtype": "float16",
                "pooling": "last_token",
                "normalization": "l2",
                "instruction_policy": "none",
                "input_transform": "utf8_exact_v1",
            }
            evidence = {
                field: {
                    "value": namespace[field],
                    "status": "verified",
                    "source": f"test evidence for {field}",
                }
                for field in (
                    "served_model_id",
                    "identity_value",
                    "dimension",
                    "dtype",
                    "pooling",
                    "normalization",
                    "instruction_policy",
                    "input_transform",
                )
            }
            manifest = build_operator_fingerprint_manifest(
                operator_fingerprint="a" * 64,
                namespace=namespace,
                field_evidence=evidence,
                endpoint_observation={"served_model_id": "qwen3-embedding-0.6b"},
            )
            manifest_path = artifacts / "environment" / "embedding_model_fingerprint.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            async def fake_run(**kwargs):
                spec = kwargs["spec"]
                prompt = artifacts / "prompt_cache" / "v2_oracle_integration_001.jsonl"
                embedding = artifacts / "embedding_cache" / "v2_oracle_integration_001.jsonl"
                prompt.parent.mkdir(parents=True, exist_ok=True)
                embedding.parent.mkdir(parents=True, exist_ok=True)
                if spec["mode"] == "capture":
                    prompt.write_text("capture\n", encoding="utf-8")
                    embedding.write_text("capture\n", encoding="utf-8")
                    audit_path = kwargs["model_oracle_audit_path"]
                    audit_path.parent.mkdir(parents=True, exist_ok=True)
                    audit_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "membind.model_oracle_audit.v1",
                                "run_id": spec["run_id"],
                                "rank_call_count": 0,
                                "cross_encoder_status": "not_invoked",
                                "blocks_v2": False,
                                "events": [],
                            }
                        ),
                        encoding="utf-8",
                    )
                else:
                    prompt.write_text("mutated replay\n", encoding="utf-8")
                return {
                    "status": "success",
                    "llm_metrics": {
                        "llm_call_count": 1 if spec["mode"] == "capture" else 0
                    },
                    "embedding_metrics": {
                        "embedding_call_count": 1 if spec["mode"] == "capture" else 0
                    },
                    "post_run_node_count": 0,
                    "rank_call_count": 0,
                    "canonical_graph_hash": "same",
                    "retrieval_metrics": {"same": True},
                }

            result = await run_v2_oracle_integration(
                artifacts=artifacts,
                run_experiment_fn=fake_run,
                service_checker=None,
            )

            self.assertEqual(result["status"], "failed")
            self.assertIn("cache_modified", result["gate_errors"])
