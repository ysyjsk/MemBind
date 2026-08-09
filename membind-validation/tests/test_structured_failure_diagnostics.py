"""Contracts for the read-only V3 structured-output failure diagnosis."""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from structured_failure_diagnostics import (  # noqa: E402
    analyze_failure_artifact,
    analyze_failure_records,
    find_unbounded_arrays,
    write_failure_diagnostic,
)


def _record(raw: str, budget: int) -> dict:
    return {
        "episode_key": ["run", 1],
        "error": "JSONDecodeError('truncated')",
        "failure_type": "structured_parse",
        "finish_reason": "length",
        "max_tokens": budget,
        "raw_response": raw,
        "raw_response_length": len(raw),
        "raw_response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "token_usage": {
            "prompt_tokens": 11,
            "completion_tokens": budget,
            "total_tokens": 11 + budget,
        },
    }


class StructuredFailureDiagnosticsTests(TestCase):
    def test_retry_pairs_are_proven_from_ordered_bytes_and_token_usage(self):
        primary = '{"items":[{"name":"A"}'
        retry = primary + ',{"name":"A"}'
        records = [_record(primary, 2), _record(retry, 8)] * 2

        result = analyze_failure_records(records, expected_budgets=(2, 8))

        self.assertEqual(result["record_count"], 4)
        self.assertEqual(result["request_attempt_count"], 2)
        self.assertEqual(result["budget_sequences"], [[2, 8], [2, 8]])
        self.assertTrue(result["all_finish_reason_length"])
        self.assertTrue(result["all_completion_budgets_saturated"])
        self.assertEqual(result["primary_prefix_of_retry_count"], 2)
        self.assertEqual(result["identical_attempt_pair_count"], 2)
        self.assertEqual(result["unique_response_count_by_budget"], {"2": 1, "8": 1})
        self.assertTrue(result["deterministic_repetition_across_attempts"])

    def test_invalid_hash_or_length_fails_closed(self):
        record = _record("{}", 2)
        record["raw_response_sha256"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "raw response SHA256 mismatch"):
            analyze_failure_records([record], expected_budgets=(2,))

    def test_schema_audit_finds_only_arrays_without_a_finite_maximum(self):
        schema = {
            "type": "object",
            "properties": {
                "unbounded": {"type": "array", "items": {"type": "string"}},
                "bounded": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "maxItems": 1,
                },
            },
        }

        self.assertEqual(
            find_unbounded_arrays(schema),
            ["$.properties.unbounded"],
        )

    def test_real_v3_failure_is_four_identical_bounded_retry_trajectories(self):
        source = (
            ROOT
            / "artifacts"
            / "llm_failures"
            / "v3_smoke_v3_smoke_002_M0_c6853660.json"
        )

        result = analyze_failure_artifact(source)

        retries = result["retry_analysis"]
        self.assertEqual(retries["record_count"], 8)
        self.assertEqual(retries["request_attempt_count"], 4)
        self.assertEqual(retries["budget_sequences"], [[2048, 8192]] * 4)
        self.assertEqual(retries["primary_prefix_of_retry_count"], 4)
        self.assertEqual(retries["identical_attempt_pair_count"], 4)
        self.assertEqual(
            retries["unique_response_count_by_budget"],
            {"2048": 1, "8192": 1},
        )
        self.assertTrue(retries["deterministic_repetition_across_attempts"])
        self.assertIn(
            "$[schema].properties.extracted_entities",
            result["schema_analysis"]["unbounded_array_paths"],
        )
        self.assertEqual(
            result["classification"],
            "deterministic_length_truncation_with_schema_permitted_unbounded_array",
        )
        self.assertFalse(result["claims"]["guided_decoding_configuration_root_cause_proven"])

    def test_writer_is_exclusive_and_never_persists_raw_responses(self):
        source = (
            ROOT
            / "artifacts"
            / "llm_failures"
            / "v3_smoke_v3_smoke_002_M0_c6853660.json"
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "diagnostic.json"

            write_failure_diagnostic(source, output)
            encoded = output.read_text(encoding="ascii")

            self.assertNotIn("raw_response", encoded)
            self.assertNotIn("Sagebrush", encoded)
            self.assertNotIn("api_key", encoded.casefold())
            with self.assertRaises(FileExistsError):
                write_failure_diagnostic(source, output)

    def test_cli_persists_a_safe_auditable_summary(self):
        source = (
            ROOT
            / "artifacts"
            / "llm_failures"
            / "v3_smoke_v3_smoke_002_M0_c6853660.json"
        )
        script = ROOT / "scripts" / "analyze_structured_failure.py"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "diagnostic.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--source",
                    str(source),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["request_attempt_count"], 4)
            self.assertEqual(summary["output"], str(output))
            self.assertEqual(
                summary["classification"],
                "deterministic_length_truncation_with_schema_permitted_unbounded_array",
            )
            self.assertTrue(output.is_file())
            self.assertNotIn("Sagebrush", completed.stdout)


if __name__ == "__main__":
    import unittest

    unittest.main()
