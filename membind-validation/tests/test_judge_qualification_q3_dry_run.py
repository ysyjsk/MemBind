"""Unified Q3 dry-run contract for the frozen Judge qualification lane.

All request paths terminate in ``httpx.MockTransport``.  This test combines
the five branches required by workplan Q3 into one auditable result without
creating live authorization or touching characterization state.
"""

from __future__ import annotations

import hashlib
import socket
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import (  # noqa: E402
    build_strict_judge_qualification_freeze,
)
from evaluation.judge_qualification_q3 import run_judge_q3_dry_run  # noqa: E402


FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
CORE_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
DEPLOYMENT_EVIDENCE = (
    ROOT / "artifacts/environment/judge_deployment_evidence_20260813.json"
)
CANONICAL_INCOMPLETE = "incomplete_invalid_non_mergeable"


class JudgeQualificationQ3DryRunTests(IsolatedAsyncioTestCase):
    async def test_unified_q3_exercises_all_required_branches_without_external_requests(
        self,
    ) -> None:
        freeze = build_strict_judge_qualification_freeze(
            validation_root=ROOT,
            fixture_path=FIXTURE.relative_to(ROOT),
            offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
            qualification_source_path=CORE_SOURCE.relative_to(ROOT),
            qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
        )
        deployment_binding = {
            "path": DEPLOYMENT_EVIDENCE.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(DEPLOYMENT_EVIDENCE.read_bytes()).hexdigest(),
        }

        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in Q3 dry-run"),
        ):
            report = await run_judge_q3_dry_run(
                validation_root=ROOT,
                runs_root=Path(temporary),
                freeze=freeze,
                deployment_evidence_binding=deployment_binding,
            )

        self.assertEqual(report["schema_version"], "membind.judge-q3-dry-run.v1")
        self.assertEqual(report["scientific_surface"], "JUDGE_QUALIFICATION_ONLY")
        self.assertIs(report["dry_run_only"], True)
        self.assertEqual(report["real_external_requests"], 0)
        self.assertIs(report["live_authorization_created"], False)
        self.assertEqual(
            set(report["scenarios"]),
            {"full_pass", "invalid_stop", "service_error_stop", "tamper", "ambiguous_inflight"},
        )

        passed = report["scenarios"]["full_pass"]
        self.assertEqual(passed["planned_item_count"], 14)
        self.assertEqual(passed["terminal_item_count"], 14)
        self.assertEqual(passed["eligible_item_count"], 14)
        self.assertEqual(passed["models_get_count"], 15)
        self.assertEqual(passed["chat_post_count"], 14)
        self.assertEqual(passed["event_count"], 28)
        self.assertEqual(passed["qualification_status"], "PASS")
        self.assertEqual(passed["verifier_attempt_status"], "complete")
        self.assertIs(passed["verifier_mergeable"], True)

        invalid = report["scenarios"]["invalid_stop"]
        self.assertEqual(invalid["runner_attempt_status"], CANONICAL_INCOMPLETE)
        self.assertEqual(invalid["failure_class"], "invalid_output")
        self.assertEqual(invalid["models_get_count"], 3)
        self.assertEqual(invalid["chat_post_count"], 2)
        self.assertEqual(invalid["event_count"], 4)
        self.assertEqual(invalid["verifier_invalid_output_count"], 1)
        self.assertEqual(invalid["verifier_attempt_status"], CANONICAL_INCOMPLETE)
        self.assertIs(invalid["suffix_dispatched"], False)

        service = report["scenarios"]["service_error_stop"]
        self.assertEqual(service["runner_attempt_status"], CANONICAL_INCOMPLETE)
        self.assertEqual(service["failure_class"], "service_error")
        self.assertEqual(service["models_get_count"], 4)
        self.assertEqual(service["chat_post_count"], 3)
        self.assertEqual(service["event_count"], 6)
        self.assertEqual(service["verifier_service_error_count"], 1)
        self.assertEqual(service["verifier_attempt_status"], CANONICAL_INCOMPLETE)
        self.assertIs(service["suffix_dispatched"], False)

        tamper = report["scenarios"]["tamper"]
        self.assertEqual(tamper["before_tamper_attempt_status"], "complete")
        self.assertEqual(tamper["after_tamper_attempt_status"], CANONICAL_INCOMPLETE)
        self.assertEqual(tamper["after_tamper_failure_class"], "artifact_verification_error")

        ambiguous = report["scenarios"]["ambiguous_inflight"]
        self.assertEqual(ambiguous["models_get_count"], 2)
        self.assertEqual(ambiguous["chat_post_count"], 1)
        self.assertEqual(ambiguous["event_count"], 1)
        self.assertEqual(ambiguous["verifier_attempt_status"], CANONICAL_INCOMPLETE)
        self.assertEqual(ambiguous["verifier_failure_class"], "ambiguous_dispatch_intent")
        self.assertIs(ambiguous["resume_rejected"], True)
        self.assertIs(ambiguous["suffix_dispatched"], False)

        for scenario in report["scenarios"].values():
            self.assertEqual(scenario["real_external_requests"], 0)
            self.assertIs(scenario["live_authorization_created"], False)


if __name__ == "__main__":
    import unittest

    unittest.main()
