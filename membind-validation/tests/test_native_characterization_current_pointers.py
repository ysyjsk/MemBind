"""Document contracts for the current Native characterization resume point.

The frozen workplan describes the experiment, while these three maintained
pointers describe execution progress.  Historical H0 ledgers may remain below
the pointer but must not be mistaken for current authority.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DOCUMENTS = (
    REPO / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md",
    ROOT / "EXPERIMENT_PLAN.md",
    ROOT / "GLOBAL_MEMORY.md",
)
START = "<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_START -->"
END = "<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_END -->"

EXPECTED = {
    "protocol_version": "current-validation-v1.3",
    "current_stage": "NATIVE_CHARACTERIZATION",
    "status": "native_characterization_offline_only",
    "current_blocker": "none",
    "current_action_scope": "native_characterization_offline_only",
    "stage_progress.native_characterization": (
        "c0_pass_c2_runner_tdd_pending"
    ),
    "instrumentation_contract_status": "qualified",
    "c1_aa_classification": "clean_pass",
    "c0_dry_run_passed": "true",
    "c0_live_request_performed": "false",
    "authorized_live_actions": "[]",
    "live_h0_candidate_authorized": "false",
    "service_admin_authorized": "false",
    "native_characterization_live_authorized": "false",
    "next_allowed_action": "implement_c2_runner_offline",
}


def _pointer(text: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise AssertionError("current pointer markers must occur exactly once")
    start = text.index(START) + len(START)
    end = text.index(END, start)
    return text[start:end]


def _fields(block: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("```") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


class NativeCharacterizationCurrentPointerTests(TestCase):
    def test_machine_state_is_offline_waiting_for_operator_services(self) -> None:
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))

        self.assertEqual(state["current_stage"], EXPECTED["current_stage"])
        self.assertEqual(state["status"], EXPECTED["status"])
        self.assertIsNone(state["current_blocker"])
        self.assertEqual(
            state["current_action_scope"], EXPECTED["current_action_scope"]
        )
        self.assertEqual(
            state["stage_progress"]["native_characterization"],
            EXPECTED["stage_progress.native_characterization"],
        )
        self.assertEqual(state["authorized_live_actions"], [])
        self.assertFalse(state["live_h0_candidate_authorized"])
        self.assertFalse(state["service_admin_authorized"])
        self.assertEqual(
            state["next_allowed_action"], EXPECTED["next_allowed_action"]
        )
        qualification = state["native_characterization_offline_qualification"]
        self.assertEqual(
            qualification["instrumentation_contract_status"], "qualified"
        )
        self.assertEqual(qualification["c1_aa"]["classification"], "clean_pass")
        self.assertFalse(qualification["c0_dry_run"]["live_request_performed"])
        self.assertFalse(qualification["live_authorized"])

    def test_all_current_documents_have_the_same_exact_pointer(self) -> None:
        blocks = []
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                block = _pointer(text)
                self.assertEqual(_fields(block), EXPECTED)
                blocks.append(block)
        self.assertEqual(len(set(blocks)), 1)

    def test_current_pointer_is_fail_closed_and_h0_is_explicit_history(self) -> None:
        for path in DOCUMENTS:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                current = _pointer(text)
                history = text[text.index(END) + len(END) :]
                self.assertNotIn("live_h0_candidate_authorized=true", current)
                self.assertNotIn("authorized_live_actions=h0_candidate", current)
                self.assertIn("HISTORICAL_SOLUTION_LANE_BELOW=true", history)
                self.assertIn("live_h0_candidate_authorized=true", history)


if __name__ == "__main__":
    import unittest

    unittest.main()
