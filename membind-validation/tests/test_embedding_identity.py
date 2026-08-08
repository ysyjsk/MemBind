import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedding_identity import (  # noqa: E402
    assess_endpoint_identity,
    build_operator_fingerprint_manifest,
    validate_embedding_model_manifest,
    write_embedding_model_manifest,
    write_identity_probe,
)


FINGERPRINT = "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"


def deployment_namespace(**overrides):
    value = {
        "served_model_id": "qwen3-embedding-0.6b",
        "identity_kind": "deployment_fingerprint",
        "identity_value": FINGERPRINT,
        "dimension": 1024,
        "dtype": "bfloat16",
        "pooling": "last_token",
        "normalization": "l2",
        "instruction_policy": "none",
        "input_transform": "utf8_exact_v1",
        "tokenizer_fingerprint": None,
        "model_fingerprint": FINGERPRINT,
        "schema_version": "membind.embedding_oracle.v1",
    }
    value.update(overrides)
    return value


def field_evidence(**status_overrides):
    statuses = {
        "served_model_id": "endpoint_observed",
        "identity_value": "operator_asserted",
        "dimension": "runtime_observed",
        "dtype": "deployment_config_verified",
        "pooling": "deployment_config_verified",
        "normalization": "runtime_behavior_verified",
        "instruction_policy": "client_code_verified",
        "input_transform": "client_code_verified",
    }
    statuses.update(status_overrides)
    namespace = deployment_namespace()
    return {
        field: {
            "value": namespace[field],
            "status": status,
            "source": f"test evidence for {field}",
        }
        for field, status in statuses.items()
    }


class EmbeddingIdentityTests(TestCase):
    def test_alias_root_and_vllm_version_do_not_satisfy_identity(self):
        result = assess_endpoint_identity(
            {
                "data": [
                    {
                        "id": "qwen3-embedding-0.6b",
                        "root": "/models/Qwen3-Embedding-0.6B",
                        "max_model_len": 32768,
                    }
                ]
            },
            {"version": "0.26.0"},
            expected_model="qwen3-embedding-0.6b",
        )

        self.assertEqual(result["status"], "blocked_missing_immutable_identity")
        self.assertIsNone(result["endpoint_reported_revision"])
        self.assertEqual(result["served_model_id"], "qwen3-embedding-0.6b")
        self.assertEqual(result["vllm_version"], "0.26.0")
        self.assertIn("served_alias", result["rejected_identity_sources"])
        self.assertIn("model_root_path", result["rejected_identity_sources"])
        self.assertIn("behavior_probe", result["rejected_identity_sources"])
        self.assertTrue(result["blocks_v2_live_integration"])

    def test_real_endpoint_revision_is_accepted_as_identity_source(self):
        result = assess_endpoint_identity(
            {
                "data": [
                    {
                        "id": "qwen3-embedding-0.6b",
                        "revision": "0123456789abcdef",
                    }
                ]
            },
            {"version": "0.26.0"},
            expected_model="qwen3-embedding-0.6b",
        )

        self.assertEqual(result["status"], "endpoint_revision_available")
        self.assertEqual(result["identity_kind"], "endpoint_revision")
        self.assertEqual(result["identity_value"], "0123456789abcdef")
        self.assertFalse(result["blocks_v2_live_integration"])

    def test_missing_or_ambiguous_served_model_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            assess_endpoint_identity(
                {"data": []},
                {"version": "0.26.0"},
                expected_model="qwen3-embedding-0.6b",
            )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            assess_endpoint_identity(
                {
                    "data": [
                        {"id": "qwen3-embedding-0.6b"},
                        {"id": "qwen3-embedding-0.6b"},
                    ]
                },
                {"version": "0.26.0"},
                expected_model="qwen3-embedding-0.6b",
            )

    def test_probe_writer_is_exclusive_and_contains_no_credentials(self):
        payload = assess_endpoint_identity(
            {"data": [{"id": "qwen3-embedding-0.6b"}]},
            {"version": "0.26.0"},
            expected_model="qwen3-embedding-0.6b",
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "embedding_identity_probe.json"
            write_identity_probe(payload, output)
            raw = output.read_text(encoding="utf-8")

            self.assertEqual(json.loads(raw)["status"], payload["status"])
            self.assertNotIn("authorization", raw.casefold())
            self.assertNotIn("api_key", raw.casefold())
            self.assertNotIn("bearer ", raw.casefold())
            with self.assertRaises(FileExistsError):
                write_identity_probe(payload, output)

    def test_operator_manifest_binds_fingerprint_namespace_and_field_evidence(self):
        manifest = build_operator_fingerprint_manifest(
            operator_fingerprint=FINGERPRINT,
            namespace=deployment_namespace(),
            field_evidence=field_evidence(),
            endpoint_observation={
                "served_model_id": "qwen3-embedding-0.6b",
                "reported_revision": None,
                "reported_model_root": "/models/Qwen3-Embedding-0.6B",
                "reported_max_model_len": 32768,
                "vllm_version": "0.26.0",
            },
        )

        namespace = validate_embedding_model_manifest(manifest)

        self.assertEqual(manifest["gate_status"], "pass")
        self.assertEqual(manifest["unresolved_fields"], [])
        self.assertEqual(namespace.identity_value, FINGERPRINT)
        self.assertEqual(namespace.dimension, 1024)
        self.assertEqual(manifest["namespace_sha256"], namespace.sha256)

    def test_unresolved_runtime_field_is_persisted_but_fails_closed_for_v2(self):
        evidence = field_evidence(dtype="unresolved")
        evidence["dtype"] = {
            "value": None,
            "status": "unresolved",
            "source": (
                "remote model dtype is absent from endpoint metadata; actual launch "
                "argv, startup log, or deployed model config is required"
            ),
            "candidate_values": ["float16", "bfloat16"],
        }
        namespace = deployment_namespace(dtype="unresolved")
        manifest = build_operator_fingerprint_manifest(
            operator_fingerprint=FINGERPRINT,
            namespace=namespace,
            field_evidence=evidence,
            endpoint_observation={"served_model_id": "qwen3-embedding-0.6b"},
        )

        self.assertEqual(manifest["gate_status"], "blocked_unresolved_runtime_config")
        self.assertEqual(manifest["unresolved_fields"], ["dtype"])
        with self.assertRaisesRegex(ValueError, "unresolved runtime config.*dtype"):
            validate_embedding_model_manifest(manifest)

    def test_manifest_rejects_fingerprint_mismatch_and_unsafe_metadata(self):
        with self.assertRaisesRegex(ValueError, "does not match namespace"):
            build_operator_fingerprint_manifest(
                operator_fingerprint=FINGERPRINT,
                namespace=deployment_namespace(identity_value="a" * 64),
                field_evidence=field_evidence(),
                endpoint_observation={"served_model_id": "qwen3-embedding-0.6b"},
            )

        with self.assertRaisesRegex(ValueError, "unsafe identity.*api_key"):
            build_operator_fingerprint_manifest(
                operator_fingerprint=FINGERPRINT,
                namespace=deployment_namespace(),
                field_evidence=field_evidence(),
                endpoint_observation={"api_key": "must-not-be-persisted"},
            )

    def test_manifest_writer_is_exclusive_and_ascii_safe(self):
        manifest = build_operator_fingerprint_manifest(
            operator_fingerprint=FINGERPRINT,
            namespace=deployment_namespace(),
            field_evidence=field_evidence(),
            endpoint_observation={"served_model_id": "qwen3-embedding-0.6b"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "embedding_model_fingerprint.json"
            write_embedding_model_manifest(manifest, output)
            raw = output.read_bytes()

            self.assertTrue(raw.endswith(b"\n"))
            raw.decode("ascii")
            self.assertEqual(json.loads(raw)["identity"]["value"], FINGERPRINT)
            with self.assertRaises(FileExistsError):
                write_embedding_model_manifest(manifest, output)
