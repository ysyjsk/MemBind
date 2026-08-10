"""RED/GREEN contract for the R6 fourth admission layer in the live runner."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import h0_full_history_live as live  # noqa: E402
import h0_harness_recovery as recovery  # noqa: E402
import h0_repair_admission as repair_admission  # noqa: E402
from test_h0_r6_recovery import (  # noqa: E402
    R5_INDEX_REL,
    R5_INDEX_SHA256,
    R6_INDEX_REL,
    REPLACEMENT_ATTEMPT_ID,
    _expected_admission,
    _expected_classification,
    _r6_verification,
)


class H0R6LiveExtractionTests(TestCase):
    def _authorization(self) -> dict[str, object]:
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))
        authorization = deepcopy(state["live_h0_authorization"])
        authorization["authorized_stage_attempt_id"] = REPLACEMENT_ATTEMPT_ID
        authorization["resolved_manifest_index_path"] = R6_INDEX_REL
        authorization["resolved_manifest_index_sha256"] = "6" * 64
        authorization["r6_recovery_admission"] = _expected_admission()
        return authorization

    def test_r6_extracts_inherited_three_layers_and_exact_fourth(self):
        extract = getattr(live, "_extract_h0_b_r6_recovery_admission", None)
        self.assertIsNotNone(extract)
        authorization = self._authorization()
        observed = extract(authorization, stage_attempt_id=REPLACEMENT_ATTEMPT_ID)
        self.assertEqual(observed, authorization["r6_recovery_admission"])

    def test_r6_extraction_rejects_old_grant_r5_manifest_and_tampered_history(self):
        extract = live._extract_h0_b_r6_recovery_admission
        cases: list[dict[str, object]] = []
        old = self._authorization()
        old["authorized_stage_attempt_id"] = "h0-q1-b-20260810-replacement-003"
        cases.append(old)
        r5 = self._authorization()
        r5["resolved_manifest_index_path"] = R5_INDEX_REL
        r5["resolved_manifest_index_sha256"] = R5_INDEX_SHA256
        cases.append(r5)
        tampered = self._authorization()
        tampered["post_workload_repair_admission"] = deepcopy(
            tampered["post_workload_repair_admission"]
        )
        tampered["post_workload_repair_admission"]["replacement_attempt_id"] = "wrong"
        cases.append(tampered)
        for changed in cases:
            with self.subTest(changed=changed.get("authorized_stage_attempt_id")):
                with self.assertRaises(live.H0StateGateError):
                    extract(changed, stage_attempt_id=REPLACEMENT_ATTEMPT_ID)

    def test_r6_state_builder_preserves_the_frozen_r5_admission_chain(self):
        source = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))
        prior_authorization = deepcopy(source["live_h0_authorization"])
        revoked = recovery.build_h0_b_r6_recovery_revoked_state(
            source,
            classification=_expected_classification(),
        )
        verification = _r6_verification()
        admission = repair_admission.build_h0_b_r6_recovery_admission(
            classification=_expected_classification(),
            manifest_verification=verification,
        )
        bindings = {
            "resolved_manifest_index_path": verification["index_path"],
            "resolved_manifest_index_sha256": verification["index_sha256"],
            "resolved_candidate_manifest_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r6/"
                "resolved_candidates/Q1." + "1" * 64 + ".json"
            ),
            "resolved_candidate_manifest_sha256": "1" * 64,
            "resolved_shared_base_manifest_path": (
                "artifacts/h0_manifest_sets/v1_3_harness_r6/"
                "resolved_candidates/shared_base." + "2" * 64 + ".json"
            ),
            "resolved_shared_base_manifest_sha256": "2" * 64,
        }
        bound = recovery.build_h0_b_r6_recovery_bound_state(
            revoked,
            root=ROOT,
            manifest_verification=verification,
            tdd_evidence={
                "latest_red": {},
                "latest_green": {},
                "latest_focused": {},
                "latest_full_regression": {},
            },
            r6_recovery_admission=admission,
            artifact_bindings=bindings,
            tdd_validator=lambda _root, value: value,
        )
        authorization = recovery.build_h0_b_r6_recovery_live_state(bound)[
            "live_h0_authorization"
        ]

        for field in (
            "repair_admission",
            "infrastructure_rerun_admission",
            "post_workload_repair_admission",
        ):
            self.assertEqual(authorization[field], prior_authorization[field])
            self.assertIsNot(authorization[field], prior_authorization[field])
        observed = live._extract_h0_b_r6_recovery_admission(
            authorization,
            stage_attempt_id=REPLACEMENT_ATTEMPT_ID,
        )
        self.assertEqual(observed, admission)

        for field in (
            "repair_admission",
            "infrastructure_rerun_admission",
            "post_workload_repair_admission",
        ):
            with self.subTest(missing_history_layer=field):
                changed = deepcopy(authorization)
                changed.pop(field)
                with self.assertRaises(recovery.H0HarnessRecoveryError):
                    recovery.validate_h0_b_r6_live_authorization(
                        changed,
                        stage_attempt_id=REPLACEMENT_ATTEMPT_ID,
                    )


if __name__ == "__main__":
    import unittest

    unittest.main()
