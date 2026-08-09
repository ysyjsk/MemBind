"""Contracts for the exact, database-free V3 extraction compatibility probe."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
sys.path.insert(0, str(ROOT / "src"))

from v3_structured_compatibility_probe import (  # noqa: E402
    analyze_runtime_drift,
    build_extraction_probe,
    classify_compatibility_result,
    correct_compatibility_artifact,
    sanitize_failure_records,
    write_compatibility_probe,
    write_runtime_drift_diagnostic,
)


class V3StructuredCompatibilityProbeTests(TestCase):
    def test_old_controls_are_rejected_after_public_wrapper_omission_is_found(self):
        result = analyze_runtime_drift(
            ROOT
            / "artifacts"
            / "environment"
            / "v3_actual_schema_compatibility_probe_20260809_002_source0_control.json",
            ROOT
            / "artifacts"
            / "environment"
            / "v3_actual_schema_compatibility_probe_20260809_001.json",
            ROOT
            / "artifacts"
            / "prompt_cache"
            / "v3_smoke_v3_smoke_002_c6853660.jsonl",
            ROOT
            / "artifacts"
            / "llm_failures"
            / "v3_smoke_v3_smoke_002_M0_c6853660.json",
            ROOT
            / "artifacts"
            / "environment"
            / "v3_vllm_metadata_probe_20260809_attempt03.json",
        )

        self.assertTrue(result["controls"]["source0_prompt_bytes_match_history"])
        self.assertEqual(result["token_deltas"]["source0"], -43)
        self.assertEqual(result["token_deltas"]["source1"], -43)
        self.assertTrue(result["token_deltas"]["equal_across_controls"])
        self.assertEqual(result["historical_outcomes"]["source1"], "length_truncated")
        self.assertEqual(result["current_outcomes"]["source1"], "parsed")
        self.assertFalse(result["controls"]["public_generate_response_path"])
        self.assertFalse(result["claims"]["construction_runtime_identity_drift_detected"])
        self.assertFalse(result["claims"]["checkpoint_identity_proven_equal"])
        self.assertFalse(result["claims"]["structured_output_backend_proven_equal"])
        self.assertEqual(
            result["gate"]["status"],
            "invalid_probe_bypassed_public_generate_response_wrapper",
        )

    def test_runtime_drift_writer_is_exclusive_and_body_free(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "drift.json"
            args = (
                ROOT / "artifacts/environment/v3_actual_schema_compatibility_probe_20260809_002_source0_control.json",
                ROOT / "artifacts/environment/v3_actual_schema_compatibility_probe_20260809_001.json",
                ROOT / "artifacts/prompt_cache/v3_smoke_v3_smoke_002_c6853660.jsonl",
                ROOT / "artifacts/llm_failures/v3_smoke_v3_smoke_002_M0_c6853660.json",
                ROOT / "artifacts/environment/v3_vllm_metadata_probe_20260809_attempt03.json",
            )

            write_runtime_drift_diagnostic(*args, output)
            encoded = output.read_text(encoding="ascii")

            self.assertNotIn("raw_response", encoded)
            self.assertNotIn("Sagebrush", encoded)
            self.assertNotIn("api_key", encoded.casefold())
            with self.assertRaises(FileExistsError):
                write_runtime_drift_diagnostic(*args, output)

    def test_live_writer_rejects_existing_output_before_model_call(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "already-exists.json"
            output.write_text("{}\n", encoding="ascii")
            with patch(
                "v3_structured_compatibility_probe.run_compatibility_probe",
                new_callable=AsyncMock,
            ) as run_probe:
                with self.assertRaises(FileExistsError):
                    asyncio.run(
                        write_compatibility_probe(
                            DATA,
                            "c6853660",
                            output,
                            source_sequence=1,
                        )
                    )

            run_probe.assert_not_awaited()

    def test_source_zero_reconstruction_matches_the_retained_capture_prompt(self):
        probe = build_extraction_probe(DATA, "c6853660", source_sequence=0)
        retained = json.loads(
            (
                ROOT
                / "artifacts"
                / "prompt_cache"
                / "v3_smoke_v3_smoke_002_c6853660.jsonl"
            ).read_text(encoding="utf-8").splitlines()[3]
        )

        self.assertEqual(
            probe.evidence["pre_wrapper_message_content_sha256"],
            [
                "da974483b3b54c8388e1ff5ef78b344599d191d56a36b13b687ef570c173ef3e",
                "8501f776c0f2b11925ae4487befdff53c0bf2e3abfb947edd37207f56bec0a00",
            ],
        )
        self.assertEqual(probe.evidence["historical_prompt_tokens"], 4515)
        self.assertEqual(
            probe.evidence["pre_wrapper_message_content_sha256"],
            [
                hashlib.sha256(
                    retained["prompt_parts"]["system_prompt"].encode("utf-8")
                ).hexdigest(),
                hashlib.sha256(
                    retained["prompt_parts"]["user_prompt"].encode("utf-8")
                ).hexdigest(),
            ],
        )
        self.assertEqual(
            probe.evidence["message_content_sha256"][0],
            "0e8619b822b5e9b316819b46abc1b3c5bd616801f85c976fc2f2bcc1cd44574c",
        )
        self.assertTrue(probe.evidence["wrapper_language_instruction_applied"])

    def test_source_one_reconstruction_freezes_the_failed_request_contract(self):
        probe = build_extraction_probe(DATA, "c6853660", source_sequence=1)

        self.assertEqual(
            probe.evidence["source_hash"],
            "90f8ffc85858a5db4b0ef53b769b8e4ced76ec39c764fd436c85b243cf3fb802",
        )
        self.assertEqual(
            probe.evidence["previous_source_hashes"],
            ["7e25231116afa6797c4e614f851e55e4f8e0e3c33817a24ee9bbff49277c81ea"],
        )
        self.assertEqual(
            probe.evidence["pre_wrapper_message_content_sha256"],
            [
                "da974483b3b54c8388e1ff5ef78b344599d191d56a36b13b687ef570c173ef3e",
                "e257dc7a1014282f8f2447a5f95fc7349488d07d7da3c26671e7e825d04de7b7",
            ],
        )
        self.assertEqual(
            probe.evidence["message_content_sha256"],
            [
                "0e8619b822b5e9b316819b46abc1b3c5bd616801f85c976fc2f2bcc1cd44574c",
                "e257dc7a1014282f8f2447a5f95fc7349488d07d7da3c26671e7e825d04de7b7",
            ],
        )
        self.assertEqual(probe.evidence["message_content_lengths"], [391, 26559])
        self.assertTrue(probe.evidence["wrapper_language_instruction_applied"])
        self.assertEqual(
            probe.evidence["json_schema_sha256"],
            "96cedd296936b90ddbed2156b20411b1662389f1c94c7a0b57df00f3cadd21d5",
        )
        self.assertEqual(probe.evidence["budget_sequence"], [2048, 8192])
        self.assertEqual(probe.evidence["historical_prompt_tokens"], 5795)
        self.assertFalse(probe.evidence["database_called"])
        self.assertFalse(probe.evidence["embedding_called"])

    def test_failure_sanitizer_retains_hashes_but_removes_bodies_and_credentials(self):
        records = [
            {
                "max_tokens": 2048,
                "finish_reason": "length",
                "raw_response": "private model body",
                "raw_response_length": 18,
                "raw_response_sha256": "a" * 64,
                "token_usage": {"prompt_tokens": 5795, "completion_tokens": 2048},
                "request_evidence": {"request_envelope_sha256": "b" * 64},
                "Authorization": "Bearer secret",
            }
        ]

        sanitized = sanitize_failure_records(records)
        encoded = json.dumps(sanitized, sort_keys=True)

        self.assertEqual(sanitized[0]["body_sha256"], "a" * 64)
        self.assertEqual(sanitized[0]["body_length"], 18)
        self.assertNotIn("private model body", encoded)
        self.assertNotIn("Authorization", encoded)
        self.assertNotIn("Bearer secret", encoded)
        self.assertNotIn("raw_response", encoded)

    def test_classification_requires_both_frozen_budgets_to_match_history(self):
        events = [
            {
                "max_tokens": 2048,
                "finish_reason": "length",
                "body_sha256": "d9340f0bc347bbb5d7049aa55bee2739485755444f28e216e523c4bd3a5b0a16",
                "completion_tokens": 2048,
            },
            {
                "max_tokens": 8192,
                "finish_reason": "length",
                "body_sha256": "94fb64c3921b3e1e7bfecee99e6faa00e620fea7f949cc0d839d8b185035aef0",
                "completion_tokens": 8192,
            },
        ]

        result = classify_compatibility_result(events, parsed=False)

        self.assertEqual(result, "exact_historical_truncation_reproduced")
        self.assertEqual(
            classify_compatibility_result(events * 4, parsed=False),
            "exact_historical_truncation_reproduced",
        )
        changed = [dict(item) for item in events]
        changed[-1]["body_sha256"] = "0" * 64
        self.assertEqual(
            classify_compatibility_result(changed, parsed=False),
            "structured_failure_not_bitwise_identical_to_history",
        )
        self.assertEqual(
            classify_compatibility_result([], parsed=True),
            "frozen_actual_schema_request_parsed",
        )

    def test_public_path_artifact_is_corrected_without_repeating_model_calls(self):
        result = correct_compatibility_artifact(
            ROOT
            / "artifacts"
            / "environment"
            / "v3_actual_schema_compatibility_probe_20260809_003_public_path.json"
        )

        self.assertEqual(
            result["classification"],
            "exact_historical_truncation_reproduced",
        )
        self.assertEqual(result["high_level_attempt_count"], 4)
        self.assertEqual(result["outer_retry_count"], 3)
        self.assertEqual(result["llm_call_count"], 8)
        self.assertTrue(result["prompt_token_count_matches_history"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["model_called_during_correction"])
        self.assertEqual(
            result["superseded_artifact"]["sha256"],
            "7df5ada3d29142eb82190d825938d546cdb7016f95538e3fb01e32ca0ed0ca03",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
