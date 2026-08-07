import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from replay_driver import (  # noqa: E402
    DEFAULT_EXPECTED_VLLM_VERSION,
    evaluate_vllm_runtime_contract,
    update_construction_blocker,
)


class RemoteRuntimeContractTests(TestCase):
    def test_runtime_contract_accepts_frozen_version_but_rejects_short_context(self):
        result = evaluate_vllm_runtime_contract(
            {
                "data": [
                    {
                        "id": "qwen3-32b-fp8",
                        "owned_by": "vllm",
                        "max_model_len": 32768,
                    }
                ]
            },
            {"version": "0.26.0"},
            requested_model="qwen3-32b-fp8",
            expected_version="0.26.0",
            minimum_context_tokens=40960,
        )

        self.assertTrue(result["models_ok"])
        self.assertTrue(result["version_ok"])
        self.assertFalse(result["context_ok"])
        self.assertFalse(result["runtime_contract_ok"])
        self.assertEqual(result["max_model_len"], 32768)

    def test_runtime_contract_accepts_exact_version_and_sufficient_context(self):
        result = evaluate_vllm_runtime_contract(
            {"data": [{"id": "qwen3-32b-fp8", "max_model_len": 40960}]},
            {"version": "0.26.0"},
            requested_model="qwen3-32b-fp8",
            expected_version="0.26.0",
            minimum_context_tokens=40960,
        )

        self.assertTrue(result["runtime_contract_ok"])
        self.assertEqual(DEFAULT_EXPECTED_VLLM_VERSION, "0.26.0")

    def test_successful_probe_resolves_blocker_without_erasing_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = Path(tmp)
            blocker = environment / "construction_context_blocker.json"
            blocker.write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "formal_gate_allowed": False,
                        "diagnostic_run_id": "diagnostic_context_cap_005",
                    }
                ),
                encoding="utf-8",
            )

            update_construction_blocker(
                environment,
                {
                    "runtime_contract_ok": True,
                    "vllm_version": "0.26.0",
                    "max_model_len": 40960,
                },
            )

            resolved = json.loads(blocker.read_text(encoding="utf-8"))
            self.assertEqual(resolved["status"], "resolved")
            self.assertTrue(resolved["formal_gate_allowed"])
            self.assertEqual(
                resolved["diagnostic_run_id"], "diagnostic_context_cap_005"
            )
            self.assertIn("resolved_at", resolved)

    def test_failed_probe_keeps_blocker_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = Path(tmp)

            update_construction_blocker(
                environment,
                {
                    "runtime_contract_ok": False,
                    "vllm_version": "0.25.0",
                    "expected_vllm_version": "0.26.0",
                    "version_ok": False,
                    "max_model_len": 32768,
                    "minimum_context_tokens": 40960,
                    "context_ok": False,
                },
            )

            blocker = json.loads(
                (environment / "construction_context_blocker.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(blocker["status"], "blocked")
            self.assertFalse(blocker["formal_gate_allowed"])
            self.assertIn("vllm_version_mismatch", blocker["active_reasons"])
            self.assertIn("insufficient_context_window", blocker["active_reasons"])


if __name__ == "__main__":
    import unittest

    unittest.main()
