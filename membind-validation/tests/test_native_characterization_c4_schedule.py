"""Focused offline contracts for the frozen C4/E3 arrival schedule."""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c4_schedule as c4_schedule  # noqa: E402


RUN_ID = "c2-17cdaabd562e9673"
HISTORY_ID = "07741c45"
SUM_SERVICE_NS = 2_458_498_031_480
EPISODE_COUNT = 49


class NativeCharacterizationC4ScheduleTests(TestCase):
    maxDiff = None

    def test_round_fraction_half_up_is_exact_at_and_around_half(self) -> None:
        self.assertEqual(c4_schedule.round_fraction_half_up(Fraction(3, 2)), 2)
        self.assertEqual(c4_schedule.round_fraction_half_up(Fraction(4, 3)), 1)
        self.assertEqual(c4_schedule.round_fraction_half_up(Fraction(5, 3)), 2)
        with self.assertRaises(c4_schedule.NativeCharacterizationC4ScheduleError):
            c4_schedule.round_fraction_half_up(Fraction(-1, 2))

    def test_retained_c2_history_derives_the_frozen_reference(self) -> None:
        result = c4_schedule.derive_c4_schedule(ROOT, RUN_ID, HISTORY_ID)
        reference = result["service_reference"]

        self.assertEqual(result["schema_version"], c4_schedule.SCHEDULE_SCHEMA)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["stage"], "C4/E3_OFFLINE_SCHEDULE")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["history_id"], HISTORY_ID)
        self.assertEqual(reference["episode_count"], EPISODE_COUNT)
        self.assertEqual(reference["sum_service_ns"], SUM_SERVICE_NS)
        self.assertEqual(
            reference["S_ref_exact_ns"],
            {"numerator": SUM_SERVICE_NS, "denominator": EPISODE_COUNT},
        )
        self.assertEqual(reference["S_ref_rounded_ns"], 50_173_429_214)

    def test_load_grid_and_round_half_up_interarrivals_are_frozen(self) -> None:
        result = c4_schedule.derive_c4_schedule(ROOT, RUN_ID, HISTORY_ID)
        loads = result["load_schedules"]

        self.assertEqual(
            [item["normalized_offered_load"] for item in loads],
            [0.5, 0.8, 1.0, 1.2, 1.5],
        )
        self.assertEqual(
            [item["interarrival_ns"] for item in loads],
            [100_346_858_428, 62_716_786_517, 50_173_429_214,
             41_811_191_012, 33_448_952_809],
        )
        self.assertEqual(
            [item["interarrival_seconds"] for item in loads],
            ["100.346858428", "62.716786517", "50.173429214",
             "41.811191012", "33.448952809"],
        )
        self.assertEqual(result["rounding_rule"], "exact_fraction_round_half_up_to_integer_ns")

    def test_each_load_has_one_absolute_open_loop_offset_per_episode(self) -> None:
        result = c4_schedule.derive_c4_schedule(ROOT, RUN_ID, HISTORY_ID)

        for load in result["load_schedules"]:
            offsets = load["absolute_arrival_offsets_ns"]
            self.assertEqual(len(offsets), EPISODE_COUNT)
            self.assertEqual(offsets[0], 0)
            self.assertEqual(
                offsets,
                [index * load["interarrival_ns"] for index in range(EPISODE_COUNT)],
            )

    def test_c2_verification_and_exact_e1_artifact_are_provenance_bound(self) -> None:
        result = c4_schedule.derive_c4_schedule(ROOT, RUN_ID, HISTORY_ID)
        provenance = result["provenance"]

        self.assertEqual(provenance["c2_verification"]["status"], "verified")
        self.assertEqual(
            provenance["c2_verification"]["manifest_sha256"],
            "f03276ef88bfdc8062967db504514c83d941d37f929a8dbca5c37fab7aa69417",
        )
        self.assertEqual(
            provenance["c2_verification"]["e1_breakdown_sha256"],
            "b06deae7a1387a6705adb5f897c92856fda6f55bebb1c277a39965bdeda952cb",
        )
        self.assertEqual(provenance["e1_block_index"], 0)
        self.assertEqual(provenance["episode_service_field"], "service_latency_ns")
        self.assertEqual(provenance["block_service_cross_check_field"], "total_add_episode_union_ns")
        self.assertEqual(
            provenance["freeze_path"],
            "artifacts/native_characterization/freeze_reference_aligned_64k.json",
        )
        self.assertEqual(
            provenance["freeze_sha256"],
            "3b086ace7841bccc2479f2043f0767b4ab9ea3d4fd74459ce65ae5cccfb0b3b0",
        )

    def test_schedule_binds_all_ten_frozen_method_load_namespace_blocks(self) -> None:
        result = c4_schedule.derive_c4_schedule(ROOT, RUN_ID, HISTORY_ID)
        blocks = result["block_schedules"]

        self.assertEqual(len(blocks), 10)
        self.assertEqual([item["block_index"] for item in blocks], list(range(10)))
        self.assertEqual(
            [item["method"] for item in blocks],
            ["Native-Sync"] * 5 + ["Native-Async-Serial"] * 5,
        )
        self.assertEqual(
            [item["normalized_offered_load"] for item in blocks],
            [0.5, 0.8, 1.0, 1.2, 1.5] * 2,
        )
        self.assertEqual(len({item["graph_namespace"] for item in blocks}), 10)
        expected_interarrivals = [
            100_346_858_428,
            62_716_786_517,
            50_173_429_214,
            41_811_191_012,
            33_448_952_809,
        ] * 2
        self.assertEqual(
            [item["interarrival_ns"] for item in blocks],
            expected_interarrivals,
        )

    def test_verifier_mismatch_fails_closed_before_schedule_derivation(self) -> None:
        with patch.object(
            c4_schedule,
            "verify_c2_run",
            return_value={"status": "verified", "run_id": "c2-0000000000000000"},
        ):
            with self.assertRaisesRegex(
                c4_schedule.NativeCharacterizationC4ScheduleError,
                "c2_verification_mismatch",
            ):
                c4_schedule.derive_c4_schedule(ROOT, RUN_ID, HISTORY_ID)

    def test_wrong_history_is_rejected_and_does_not_select_another_c2_block(self) -> None:
        with self.assertRaisesRegex(
            c4_schedule.NativeCharacterizationC4ScheduleError,
            "history_not_frozen_e3_history",
        ):
            c4_schedule.derive_c4_schedule(ROOT, RUN_ID, "b6019101")

    def test_output_is_deterministic_sealed_and_contains_no_sensitive_payload(self) -> None:
        first = c4_schedule.derive_c4_schedule(ROOT, RUN_ID, HISTORY_ID)
        second = c4_schedule.derive_c4_schedule(ROOT, RUN_ID, HISTORY_ID)

        self.assertEqual(first, second)
        self.assertEqual(first["payload_sha256"], c4_schedule.payload_sha256(first))
        encoded = json.dumps(first, ensure_ascii=True, sort_keys=True)
        for forbidden in ("api_key", "raw_prompt", "raw_response", "Jywc2ncr"):
            self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    import unittest

    unittest.main()
