"""Fail-closed contracts for the repeated C2 structured-output failure.

The transition is intentionally offline-only: it binds immutable sanitized
evidence, consumes the C2 live grant, and records the exact decision boundary
without cleaning Neo4j or selecting a replacement structured-output mode.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import native_characterization_c2_second_failure as failure


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

# This fixture represents the source contract at the historical JSONDecodeError
# boundary.  Keep it local so later CURRENT_STATE transitions cannot invalidate
# an otherwise immutable evidence-validation test.
_HISTORICAL_SECOND_FAILURE_SOURCE: dict[str, object] = {
    "authorized_live_actions": ["native_characterization_c2"],
    "current_action_scope": "native_characterization_c2_live_only",
    "current_blocker": None,
    "current_stage": "NATIVE_CHARACTERIZATION",
    "next_allowed_action": "run_native_characterization_c2",
    "protocol_version": "current-validation-v1.3",
    "service_admin_authorized": False,
    "stage_progress": {
        "full_unit_regression": "pass",
        "native_characterization": (
            "c0_c1_pass_c2_replacement_authorized_from_episode_0"
        ),
    },
    "status": "native_characterization_offline_only",
    "unrelated_historical_evidence": {"preserved": True},
}
_HISTORICAL_SECOND_FAILURE_SOURCE_SHA256 = (
    "d3c78ca40acc481550b59ab0de347ddfb6632f706026149261a1178f163c486a"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _historical_second_failure_source_state() -> dict[str, object]:
    """Return an isolated copy of the immutable historical source fixture."""

    source = deepcopy(_HISTORICAL_SECOND_FAILURE_SOURCE)
    if (
        hashlib.sha256(_canonical(source)).hexdigest()
        != _HISTORICAL_SECOND_FAILURE_SOURCE_SHA256
    ):
        raise AssertionError("historical C2 second-failure source fixture drifted")
    return source


class NativeCharacterizationC2SecondFailureTests(TestCase):
    def setUp(self) -> None:
        self.source = _historical_second_failure_source_state()
        self.bindings = failure.C2SecondFailureBindings(
            source_state_sha256=_HISTORICAL_SECOND_FAILURE_SOURCE_SHA256,
            checkpoint_path=failure.CHECKPOINT_RELATIVE_PATH,
            checkpoint_sha256=failure.CHECKPOINT_SHA256,
            trace_path=failure.TRACE_RELATIVE_PATH,
            trace_sha256=failure.TRACE_SHA256,
            outer_log_path=failure.OUTER_LOG_RELATIVE_PATH,
            outer_log_sha256=failure.OUTER_LOG_SHA256,
            prior_checkpoint_path=failure.PRIOR_CHECKPOINT_RELATIVE_PATH,
            prior_checkpoint_sha256=failure.PRIOR_CHECKPOINT_SHA256,
            freeze_sha256=failure.FREEZE_SHA256,
            workplan_sha256=failure.WORKPLAN_SHA256,
        )

    def test_build_revokes_live_authority_and_binds_failure(self) -> None:
        target, report = failure.build_second_failure_state_and_report(
            self.source,
            validation_root=ROOT,
            repo_root=REPO,
            bindings=self.bindings,
        )

        self.assertEqual(target["authorized_live_actions"], [])
        self.assertEqual(
            target["current_action_scope"], "native_characterization_offline_only"
        )
        self.assertEqual(
            target["current_blocker"],
            "c2_second_structured_output_failure_requires_protocol_decision",
        )
        self.assertEqual(
            target["next_allowed_action"],
            "assess_c2_json_object_protocol_deviation",
        )
        self.assertFalse(target["service_admin_authorized"])
        metadata = target["native_characterization_c2_second_failure"]
        self.assertEqual(metadata["run_id"], failure.RUN_ID)
        self.assertEqual(metadata["completed_episode_count"], 10)
        self.assertFalse(metadata["attempt_valid"])
        self.assertFalse(metadata["attempt_mergeable"])
        self.assertFalse(metadata["resume_allowed"])
        self.assertFalse(metadata["live_authorized"])
        self.assertEqual(metadata["report_payload_sha256"], report["payload_sha256"])
        self.assertEqual(
            report["classification"],
            "repeated_same_boundary_json_schema_decode_failure",
        )
        self.assertEqual(report["structured_output_mode"], "json_schema")
        self.assertEqual(report["completed_episode_count"], 10)
        candidate = deepcopy(report)
        observed = candidate.pop("payload_sha256")
        self.assertEqual(observed, hashlib.sha256(_canonical(candidate)).hexdigest())

    def test_evidence_or_source_drift_fails_closed(self) -> None:
        drifted_source = deepcopy(self.source)
        drifted_source["next_allowed_action"] = "something_else"
        with self.assertRaisesRegex(
            failure.C2SecondFailureError, "source_state_hash_mismatch"
        ):
            failure.build_second_failure_state_and_report(
                drifted_source,
                validation_root=ROOT,
                repo_root=REPO,
                bindings=self.bindings,
            )

        drifted_bindings = failure.C2SecondFailureBindings(
            **{
                **self.bindings.__dict__,
                "checkpoint_sha256": "0" * 64,
            }
        )
        with self.assertRaisesRegex(
            failure.C2SecondFailureError, "checkpoint_hash_mismatch"
        ):
            failure.build_second_failure_state_and_report(
                self.source,
                validation_root=ROOT,
                repo_root=REPO,
                bindings=drifted_bindings,
            )

    def test_finalize_dry_run_then_atomic_apply_is_idempotent(self) -> None:
        with TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            state_path = temporary_root / "CURRENT_STATE.json"
            report_path = temporary_root / "second_failure.json"
            state_path.write_bytes(_canonical(self.source) + b"\n")

            dry = failure.finalize_second_failure(
                state_path=state_path,
                report_path=report_path,
                validation_root=ROOT,
                repo_root=REPO,
                bindings=self.bindings,
                apply=False,
            )
            self.assertEqual(dry["status"], "validated_not_applied")
            self.assertFalse(report_path.exists())
            self.assertEqual(
                json.loads(state_path.read_text("ascii")), self.source
            )

            applied = failure.finalize_second_failure(
                state_path=state_path,
                report_path=report_path,
                validation_root=ROOT,
                repo_root=REPO,
                bindings=self.bindings,
                apply=True,
            )
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(report_path.is_file())
            target = json.loads(state_path.read_text("ascii"))
            self.assertEqual(target["authorized_live_actions"], [])

            repeated = failure.finalize_second_failure(
                state_path=state_path,
                report_path=report_path,
                validation_root=ROOT,
                repo_root=REPO,
                bindings=self.bindings,
                apply=True,
            )
            self.assertEqual(repeated["status"], "already_applied")
            self.assertEqual(json.loads(state_path.read_text("ascii")), target)

    def test_report_contains_no_raw_content_or_credentials(self) -> None:
        _, report = failure.build_second_failure_state_and_report(
            self.source,
            validation_root=ROOT,
            repo_root=REPO,
            bindings=self.bindings,
        )
        encoded = _canonical(report).decode("ascii").casefold()
        for forbidden in (
            "api_key",
            "authorization",
            "password",
            "raw_prompt",
            "raw_response",
            "bearer ",
            ".env",
        ):
            self.assertNotIn(forbidden, encoded)
