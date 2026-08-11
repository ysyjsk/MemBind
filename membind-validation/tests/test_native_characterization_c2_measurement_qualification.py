"""Offline TDD for the scoped C2 measurement-path C1 requalification."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_c2_measurement_qualification import (  # noqa: E402
    PAIR_ORDERS,
    canonical_bytes,
    classify_overhead,
    run_qualification,
    sha256_file,
    validate_result,
    write_result,
)


class NativeCharacterizationC2MeasurementQualificationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = asyncio.run(run_qualification(work_units=2_000))

    def test_runs_exactly_five_alternating_pairs_with_semantic_parity(self) -> None:
        result = self.result

        self.assertEqual(result["pair_count"], 5)
        self.assertEqual(
            [tuple(pair["execution_order"]) for pair in result["pairs"]],
            list(PAIR_ORDERS),
        )
        self.assertTrue(result["semantic_parity"])
        self.assertEqual(result["return_hash_count"], 1)
        self.assertEqual(result["event_sequence_hash_count"], 1)
        self.assertEqual(result["state_hash_count"], 1)
        self.assertTrue(
            all(pair["trace_on_span_count"] > 0 for pair in result["pairs"])
        )
        self.assertTrue(
            all(pair["trace_off_span_count"] == 0 for pair in result["pairs"])
        )
        self.assertIn(
            result["overhead_classification"],
            {"clean_pass", "warning_continue", "block_repair"},
        )
        self.assertEqual(
            result["qualification_status"],
            (
                "blocked_overhead"
                if result["overhead_classification"] == "block_repair"
                else "pass"
            ),
        )
        benchmark = result["benchmark_contract"]
        self.assertEqual(benchmark["work_units_per_operation"], 2_000)
        self.assertEqual(benchmark["timed_episode_count_per_arm"], 10)
        self.assertEqual(
            benchmark["unmeasured_warmup_order"], ["trace_off", "trace_on"]
        )
        self.assertEqual(benchmark["unmeasured_warmup_episode_count_per_mode"], 1)
        self.assertEqual(
            benchmark["gc_policy"],
            "collect_before_arm_disable_during_arm_restore_after_arm",
        )
        self.assertEqual(
            benchmark["cache_policy"], "fresh_fixture_per_arm_no_cross_arm_state"
        )
        self.assertEqual(
            result["measurement_scope"], "in_memory_span_wrapper_overhead_only"
        )
        self.assertEqual(result["clock"]["implementation"], "time.perf_counter_ns")
        self.assertTrue(result["clock"]["monotonic"])
        self.assertGreater(result["clock"]["resolution_ns"], 0)
        self.assertEqual(result["runtime_identity"]["python_implementation"], "cpython")
        self.assertTrue(result["runtime_identity"]["cpu_affinity"])

    def test_real_pinned_alias_smoke_is_network_denied_and_restores_everything(self) -> None:
        smoke = self.result["pinned_graphiti_alias_smoke"]

        self.assertEqual(smoke["graphiti_version"], "0.29.3")
        self.assertEqual(
            smoke["graphiti_commit"],
            "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        )
        self.assertRegex(smoke["direct_url_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(smoke["status"], "pass")
        self.assertTrue(smoke["base_and_adapter_installed_together"])
        self.assertTrue(smoke["all_targets_callable"])
        self.assertTrue(smoke["all_targets_patched"])
        self.assertTrue(smoke["all_identities_restored"])
        self.assertTrue(smoke["double_restore_idempotent"])
        self.assertTrue(smoke["reinstall_after_restore_succeeds"])
        self.assertTrue(smoke["embedder_instance_attributes_removed"])
        self.assertEqual(smoke["trace_record_count"], 0)
        self.assertEqual(smoke["network_attempt_count"], 0)

    def test_result_binds_all_scoped_sources_and_validates_payload_hash(self) -> None:
        result = self.result
        source_hashes = result["source_hashes"]

        self.assertEqual(
            set(source_hashes),
            {
                "adapter_source_sha256",
                "base_instrumentation_source_sha256",
                "c2_runner_source_sha256",
                "qualification_source_sha256",
                "qualification_test_sha256",
                "tracing_source_sha256",
            },
        )
        self.assertTrue(all(len(value) == 64 for value in source_hashes.values()))
        validate_result(result)

        tampered = deepcopy(result)
        tampered["semantic_parity"] = False
        with self.assertRaisesRegex(ValueError, "payload_sha256"):
            validate_result(tampered)

    def test_validator_recomputes_sources_distribution_and_graphiti_identity(self) -> None:
        def rehash(value: dict) -> dict:
            value["payload_sha256"] = ""
            value.pop("payload_sha256")
            value["payload_sha256"] = __import__("hashlib").sha256(
                canonical_bytes(value)
            ).hexdigest()
            return value

        stale_source = deepcopy(self.result)
        stale_source["source_hashes"]["tracing_source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source hash.*mismatch"):
            validate_result(rehash(stale_source))

        false_distribution = deepcopy(self.result)
        false_distribution["paired_distribution"]["median_ratio"] = 0.0
        with self.assertRaisesRegex(ValueError, "paired distribution mismatch"):
            validate_result(rehash(false_distribution))

        wrong_revision = deepcopy(self.result)
        wrong_revision["pinned_graphiti_alias_smoke"]["graphiti_commit"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "pinned alias.*mismatch"):
            validate_result(rehash(wrong_revision))

    def test_classification_boundaries_are_exact_and_never_coerce_block_to_pass(self) -> None:
        self.assertEqual(classify_overhead(0.02), "clean_pass")
        self.assertEqual(classify_overhead(0.0200001), "warning_continue")
        self.assertEqual(classify_overhead(0.05), "warning_continue")
        self.assertEqual(classify_overhead(0.0500001), "block_repair")

    def test_writer_emits_sanitized_canonical_json_and_file_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "qualification.json"
            observed_sha = write_result(self.result, path)

            self.assertEqual(observed_sha, sha256_file(path))
            self.assertEqual(path.read_bytes(), canonical_bytes(self.result) + b"\n")
            loaded = json.loads(path.read_text("ascii"))
            validate_result(loaded)
            serialized = path.read_text("ascii").casefold()
            for forbidden in (
                "api_key",
                "authorization",
                "raw_prompt",
                "raw_response",
                "private_",
            ):
                self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    import unittest

    unittest.main()
