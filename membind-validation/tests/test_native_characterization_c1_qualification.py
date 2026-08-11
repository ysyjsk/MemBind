"""C1 A/A overhead qualification contracts for the offline fixture."""

from __future__ import annotations

import asyncio
import copy
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_c1_qualification import (  # noqa: E402
    build_qualification_result,
    canonical_bytes,
    classify_overhead,
    run_qualification,
    validate_result,
    write_result,
)


class C1QualificationTests(TestCase):
    def test_classification_uses_the_frozen_median_guardrail(self) -> None:
        self.assertEqual(classify_overhead([-0.01, 0.0, 0.01, 0.02, 0.50]), "clean_pass")
        self.assertEqual(classify_overhead([0.01, 0.02, 0.03, 0.04, 0.50]), "warning_continue")
        self.assertEqual(classify_overhead([0.01, 0.05, 0.051, 0.06, 0.07]), "block_and_repair")

    def test_result_requires_exactly_five_alternating_pairs(self) -> None:
        pairs = [
            {
                "pair_index": index,
                "execution_order": (
                    ["trace_off", "trace_on"]
                    if index % 2 == 0
                    else ["trace_on", "trace_off"]
                ),
                "trace_off_ns": 100,
                "trace_on_ns": 101 + index,
            }
            for index in range(5)
        ]
        result = build_qualification_result(
            pairs,
            state_sha256="1" * 64,
            event_sequence_sha256="2" * 64,
            source_hashes={"fixture": "3" * 64},
        )
        validate_result(result)
        self.assertEqual(result["pair_count"], 5)
        self.assertEqual(len(result["paired_distribution"]["overhead_ratio"]), 5)
        self.assertEqual(result["classification"], "warning_continue")

        with self.assertRaisesRegex(ValueError, "five"):
            build_qualification_result(
                pairs[:4],
                state_sha256="1" * 64,
                event_sequence_sha256="2" * 64,
                source_hashes={"fixture": "3" * 64},
            )

    def test_mutation_and_secret_bearing_fields_fail_closed(self) -> None:
        result = asyncio.run(run_qualification(work_units=50))
        validate_result(result)
        mutated = copy.deepcopy(result)
        mutated["classification"] = "clean_pass"
        with self.assertRaisesRegex(ValueError, "payload_sha256"):
            validate_result(mutated)
        unsafe = copy.deepcopy(result)
        unsafe["api_key"] = "not-persistable"
        unsafe.pop("payload_sha256")
        with self.assertRaisesRegex(ValueError, "forbidden"):
            build_qualification_result(
                unsafe["pairs"],
                state_sha256=unsafe["semantic_parity"]["state_sha256"],
                event_sequence_sha256=unsafe["semantic_parity"][
                    "event_sequence_sha256"
                ],
                source_hashes={"api_key": "4" * 64},
            )

    def test_actual_fixture_preserves_semantics_and_alternates_order(self) -> None:
        result = asyncio.run(run_qualification(work_units=100))
        validate_result(result)
        self.assertTrue(result["semantic_parity"]["passed"])
        self.assertEqual(result["pair_count"], 5)
        self.assertEqual(
            [pair["execution_order"] for pair in result["pairs"]],
            [
                ["trace_off", "trace_on"],
                ["trace_on", "trace_off"],
                ["trace_off", "trace_on"],
                ["trace_on", "trace_off"],
                ["trace_off", "trace_on"],
            ],
        )

    def test_writer_is_ascii_canonical_and_exact(self) -> None:
        result = asyncio.run(run_qualification(work_units=50))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "qualification.json"
            digest = write_result(result, path)
            raw = path.read_bytes()
        raw.decode("ascii")
        self.assertEqual(raw, canonical_bytes(result) + b"\n")
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    import unittest

    unittest.main()
