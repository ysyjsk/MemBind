"""Focused contracts for the one-shot C2 64K envelope recovery.

The recovery is intentionally bound to one failed attempt.  These tests keep
the state transition and derived-freeze identity reviewable without contacting
Neo4j or either model service.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from unittest import TestCase

from native_characterization_c2_serving_envelope_recovery import (
    ENVELOPE_EVIDENCE_RELATIVE_PATH,
    FAILED_RUN_ID,
    NEW_REFERENCE_FREEZE_RELATIVE_PATH,
    OLD_REFERENCE_FREEZE_RELATIVE_PATH,
    build_64k_envelope_evidence,
    build_cleanup_only_state,
    derive_64k_reference_freeze,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _live_source() -> dict[str, object]:
    return {
        "protocol_version": "current-validation-v1.3",
        "current_stage": "NATIVE_CHARACTERIZATION",
        "status": "native_characterization_c2_live_only",
        "current_blocker": None,
        "current_action_scope": "native_characterization_c2_live_only",
        "authorized_live_actions": ["native_characterization_c2"],
        "native_characterization_live_authorized": True,
        "live_h0_candidate_authorized": False,
        "service_admin_authorized": False,
        "next_allowed_action": "run_native_characterization_c2",
        "stage_progress": {
            "native_characterization": (
                "c0_c1_pass_reference_aligned_c2_authorized_from_episode_0"
            ),
            "unrelated": "preserved",
        },
        "native_characterization_reference_alignment": {
            "status": "c2_live_authorized",
            "reference_freeze_path": OLD_REFERENCE_FREEZE_RELATIVE_PATH,
            "reference_freeze_sha256": "a" * 64,
            "cleanup": {"historical": True},
            "fresh_c2": {
                "live_authorized": True,
                "semantic_attempts_remaining": 1,
                "run_id_pattern": "c2-[0-9a-f]{16}",
                "start_source_sequence": 0,
                "resume_allowed": False,
                "prefix_merge_allowed": False,
                "structured_output_mode": "json_schema",
            },
        },
        "native_characterization_reference_c2_authorization": {
            "live_authorized": True,
            "replacement_resume_allowed": False,
            "replacement_start_source_sequence": 0,
            "semantic_attempts_authorized": 1,
        },
        "native_characterization_c2_interruption": {
            "run_id": "c2-2fe3711c62933407",
            "attempt_valid": False,
        },
        "native_characterization_c2_second_failure": {
            "run_id": "c2-723261287e32e182",
            "attempt_valid": False,
        },
    }


def _sealed(payload: dict[str, object]) -> dict[str, object]:
    value = deepcopy(payload)
    value["payload_sha256"] = _sha(_canonical(value))
    return value


class NativeCharacterizationC2ServingEnvelopeRecoveryTests(TestCase):
    def test_64k_evidence_records_the_qualified_envelope_without_secrets(self) -> None:
        evidence = build_64k_envelope_evidence()

        self.assertEqual(evidence["qualification_status"], "64K_ENVELOPE_PASS")
        self.assertEqual(evidence["runtime"]["vllm_version"], "0.26.0")
        self.assertEqual(evidence["runtime"]["max_model_len"], 65_536)
        self.assertEqual(evidence["runtime"]["rope_type"], "yarn")
        self.assertEqual(evidence["runtime"]["yarn_factor"], 2.0)
        self.assertEqual(evidence["probe"]["actual_prompt_tokens"], 26_024)
        self.assertEqual(evidence["probe"]["requested_max_tokens"], 16_384)
        self.assertEqual(evidence["probe"]["admission_envelope_tokens"], 42_408)
        self.assertEqual(evidence["probe"]["http_status"], 200)
        self.assertTrue(evidence["probe"]["structured_json_valid"])
        self.assertNotIn("api_key", _canonical(evidence).decode("ascii").casefold())

    def test_cleanup_transition_revokes_authority_and_preserves_failure_history(self) -> None:
        source = _live_source()
        source_sha = _sha(_canonical(source))
        report = _sealed(
            {
                "schema_version": "membind.native-characterization-c2-serving-envelope-failure.v1",
                "run_id": FAILED_RUN_ID,
                "attempt_valid": False,
                "attempt_mergeable": False,
                "resume_allowed": False,
                "prefix_merge_allowed": False,
            }
        )
        envelope = build_64k_envelope_evidence()

        target = build_cleanup_only_state(
            source,
            source_state_sha256=source_sha,
            failure_report=report,
            failure_report_sha256=_sha(_canonical(report) + b"\n"),
            envelope_evidence=envelope,
            envelope_evidence_sha256=_sha(_canonical(envelope) + b"\n"),
            old_freeze_sha256="a" * 64,
        )

        self.assertEqual(target["status"], "native_characterization_cleanup_only")
        self.assertEqual(target["authorized_live_actions"], [])
        self.assertFalse(target["native_characterization_live_authorized"])
        self.assertEqual(
            target["native_characterization_c2_interruption"],
            source["native_characterization_c2_interruption"],
        )
        self.assertEqual(
            target["native_characterization_c2_second_failure"],
            source["native_characterization_c2_second_failure"],
        )
        cleanup = target["native_characterization_reference_alignment"]["cleanup"]
        self.assertEqual(cleanup["failed_attempt_id"], FAILED_RUN_ID)
        self.assertFalse(cleanup["failed_attempt_valid"])
        self.assertFalse(cleanup["failed_attempt_mergeable"])
        self.assertFalse(cleanup["replacement_resume_allowed"])
        receipt = target["native_characterization_reference_c2_authorization"]
        self.assertFalse(receipt["live_authorized"])
        self.assertEqual(receipt["consumed_by_run_id"], FAILED_RUN_ID)

    def test_derived_freeze_changes_only_the_construction_envelope_identity(self) -> None:
        parent = _sealed(
            {
                "schema_version": "membind.native-characterization-freeze.v1",
                "artifact_id": "native-characterization-freeze-reference-aligned",
                "run_id": "native-characterization-freeze-reference-aligned",
                "creation_command": "old",
                "derivation": {"reason": "old"},
                "state_transition": {"live_authorized": False},
                "runtime_identities": {
                    "construction": {
                        "served_model_id": "qwen3-32b-fp8",
                        "vllm_version": "0.26.0",
                        "max_model_len": 40_960,
                        "enable_thinking": False,
                    },
                    "embedding": {"served_model_id": "qwen3-embedding-0.6b"},
                    "graphiti": {"version": "0.29.3"},
                    "neo4j": {"version": "5.26.0"},
                },
                "construction_compatibility_policy": {
                    "structured_output_mode": "json_schema",
                    "requested_max_tokens": 16_384,
                },
                "input_hashes": {"u0_runtime_source_sha256": "b" * 64},
                "dataset": {"source_sha256": "c" * 64},
                "objects": {"primary": {"id": "U0"}},
                "screening": {"e1_e2": {"block_order": []}},
            }
        )
        parent_sha = _sha(_canonical(parent) + b"\n")
        envelope = build_64k_envelope_evidence()
        envelope_sha = _sha(_canonical(envelope) + b"\n")

        derived = derive_64k_reference_freeze(
            parent,
            parent_freeze_sha256=parent_sha,
            envelope_evidence_sha256=envelope_sha,
            u0_runtime_source_sha256="d" * 64,
        )

        construction = derived["runtime_identities"]["construction"]
        self.assertEqual(construction["max_model_len"], 65_536)
        self.assertEqual(construction["rope_type"], "yarn")
        self.assertEqual(construction["yarn_factor"], 2.0)
        self.assertEqual(construction["original_max_position_embeddings"], 32_768)
        self.assertEqual(construction["rope_theta"], 1_000_000)
        self.assertEqual(
            derived["runtime_identities"]["embedding"],
            parent["runtime_identities"]["embedding"],
        )
        self.assertEqual(derived["dataset"], parent["dataset"])
        self.assertEqual(derived["objects"], parent["objects"])
        self.assertEqual(derived["screening"], parent["screening"])
        self.assertEqual(
            derived["construction_compatibility_policy"],
            parent["construction_compatibility_policy"],
        )
        self.assertEqual(
            derived["derivation"]["parent_freeze_path"],
            OLD_REFERENCE_FREEZE_RELATIVE_PATH,
        )
        self.assertEqual(
            derived["derivation"]["execution_envelope_evidence_path"],
            ENVELOPE_EVIDENCE_RELATIVE_PATH,
        )
        self.assertEqual(
            derived["input_hashes"]["u0_runtime_source_sha256"], "d" * 64
        )
        self.assertEqual(
            NEW_REFERENCE_FREEZE_RELATIVE_PATH,
            "artifacts/native_characterization/freeze_reference_aligned_64k.json",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
