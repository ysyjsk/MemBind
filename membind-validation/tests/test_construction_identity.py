"""Contracts for construction checkpoint and launch identity evidence."""

import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from construction_identity import (  # noqa: E402
    collect_directory_manifest,
    collect_vllm_process_evidence,
    compare_deployment_manifest,
    manifest_fingerprint,
    sanitize_vllm_argv,
)


class ConstructionIdentityTests(TestCase):
    def test_manifest_fingerprint_is_order_independent_and_strict(self):
        files = [
            {"path": "b.bin", "size": 2, "sha256": "b" * 64},
            {"path": "a.json", "size": 1, "sha256": "a" * 64},
        ]

        self.assertEqual(
            manifest_fingerprint(files),
            manifest_fingerprint(list(reversed(files))),
        )
        with self.assertRaisesRegex(ValueError, "lowercase SHA256"):
            manifest_fingerprint([{"path": "x", "size": 1, "sha256": "bad"}])
        with self.assertRaisesRegex(ValueError, "relative"):
            manifest_fingerprint(
                [{"path": "/absolute", "size": 1, "sha256": "a" * 64}]
            )

    def test_directory_manifest_hashes_only_the_expected_file_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nested").mkdir()
            (root / "config.json").write_bytes(b"config")
            (root / "nested" / "weight.bin").write_bytes(b"weight")
            (root / "unrelated.tmp").write_bytes(b"ignore")

            result = collect_directory_manifest(
                root,
                expected_paths=["config.json", "nested/weight.bin"],
            )

        self.assertEqual(
            [item["path"] for item in result["files"]],
            ["config.json", "nested/weight.bin"],
        )
        self.assertEqual(result["missing_paths"], [])
        self.assertEqual(len(result["manifest_fingerprint"]), 64)

    def test_vllm_argv_keeps_runtime_flags_but_redacts_authentication(self):
        argv = [
            "vllm",
            "serve",
            "/models/Qwen3-32B-FP8",
            "--port=8000",
            "--api-key",
            "top-secret",
            "--structured-outputs-config.backend",
            "xgrammar",
            "--max-model-len",
            "40960",
        ]

        safe = sanitize_vllm_argv(argv)
        encoded = json.dumps(safe)

        self.assertNotIn("top-secret", encoded)
        self.assertNotIn("api-key", encoded.casefold())
        self.assertIn("--structured-outputs-config.backend", safe)
        self.assertIn("xgrammar", safe)
        self.assertIn("40960", safe)

    def test_process_evidence_selects_port_and_never_emits_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            first = proc / "101"
            second = proc / "202"
            first.mkdir()
            second.mkdir()
            (first / "cmdline").write_bytes(
                b"python\0-m\0vllm.entrypoints.openai.api_server\0--port\08001\0"
            )
            (second / "cmdline").write_bytes(
                b"vllm\0serve\0/model\0--port\08000\0--api-key\0secret\0"
                b"--structured-outputs-config.backend\0xgrammar\0"
            )

            result = collect_vllm_process_evidence(proc, port=8000)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["pid"], 202)
        encoded = json.dumps(result)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("api-key", encoded.casefold())
        self.assertIn("xgrammar", encoded)
        self.assertEqual(len(result[0]["launch_fingerprint"]), 64)

    def test_official_expected_manifest_is_self_consistent(self):
        expected = json.loads(
            (
                ROOT
                / "artifacts"
                / "environment"
                / "qwen3_32b_fp8_expected_manifest_6e2312b8.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(expected["revision"], "6e2312b85c2ae9a31f629f24493b79d8b02eab1a")
        self.assertEqual(len(expected["files"]), 18)
        self.assertEqual(
            expected["manifest_fingerprint"],
            manifest_fingerprint(expected["files"]),
        )
        tokenizer = {item["path"]: item for item in expected["files"]}
        self.assertEqual(
            tokenizer["tokenizer.json"]["sha256"],
            "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
        )

    def test_deployment_comparison_reports_exact_mismatches(self):
        expected = [
            {"path": "config.json", "size": 1, "sha256": "a" * 64},
            {"path": "weight.bin", "size": 2, "sha256": "b" * 64},
        ]
        actual = [
            {"path": "config.json", "size": 1, "sha256": "c" * 64},
            {"path": "extra.json", "size": 3, "sha256": "d" * 64},
        ]

        result = compare_deployment_manifest(expected, actual)

        self.assertFalse(result["exact_match"])
        self.assertEqual(result["missing_paths"], ["weight.bin"])
        self.assertEqual(result["unexpected_paths"], ["extra.json"])
        self.assertEqual(result["changed_paths"], ["config.json"])


if __name__ == "__main__":
    import unittest

    unittest.main()
