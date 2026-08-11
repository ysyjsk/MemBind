"""Contracts for the derived reference-aligned C2 execution freeze.

The historical freeze remains immutable.  This contract permits only execution
identity updates needed to bind pinned Graphiti's generic provider path; the
scientific workload and every runtime treatment remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
UPSTREAM_GENERIC = (
    ROOT
    / ".venv/lib/python3.12/site-packages/graphiti_core/llm_client/openai_generic_client.py"
)
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_freeze import validate_artifact  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NativeCharacterizationReferenceFreezeTests(TestCase):
    def test_reference_freeze_changes_only_execution_identity(self) -> None:
        canonical_path = ROOT / "artifacts/native_characterization/freeze.json"
        derived_path = (
            ROOT
            / "artifacts/native_characterization/freeze_reference_aligned.json"
        )
        canonical = json.loads(canonical_path.read_text(encoding="ascii"))
        derived = json.loads(derived_path.read_text(encoding="ascii"))

        validate_artifact(derived)
        for unchanged in (
            "dataset",
            "objects",
            "protocol",
            "runtime_identities",
            "screening",
        ):
            self.assertEqual(derived[unchanged], canonical[unchanged])

        self.assertEqual(
            derived["derivation"],
            {
                "parent_freeze_path": "artifacts/native_characterization/freeze.json",
                "parent_freeze_sha256": _sha256(canonical_path),
                "reason": "restore_pinned_graphiti_openai_generic_provider_path",
            },
        )
        policy = derived["construction_compatibility_policy"]
        self.assertEqual(
            policy["classification"],
            "reference_aligned_with_declared_project_deviations",
        )
        self.assertEqual(policy["requested_max_tokens"], 16_384)
        self.assertEqual(policy["effective_budget_formula"], "upstream_max_tokens_passthrough")
        self.assertEqual(policy["structured_output_mode"], "json_schema")
        self.assertFalse(policy["upstream_graphiti_behavior"])
        self.assertFalse(policy["project_generate_response_override"])
        self.assertFalse(policy["project_structured_parser"])
        self.assertFalse(policy["project_context_probe"])
        self.assertFalse(policy["project_retry_budget_matrix"])
        self.assertIsNone(policy["structured_output_backend_requested"])
        self.assertEqual(policy["episode_indices"], [0])
        self.assertEqual(
            policy["qwen_transport_fields"],
            {
                "enable_thinking": False,
                "seed": 20260806,
                "top_p": 1.0,
            },
        )
        self.assertEqual(
            derived["state_transition"]["authorization_status"],
            "pending_cleanup",
        )

    def test_reference_freeze_binds_the_actual_provider_sources_and_contract(self) -> None:
        path = ROOT / "artifacts/native_characterization/freeze_reference_aligned.json"
        freeze = json.loads(path.read_text(encoding="ascii"))
        inputs = freeze["input_hashes"]

        self.assertEqual(
            inputs["u0_runtime_source_sha256"],
            _sha256(ROOT / "src/native_characterization_runtime.py"),
        )
        self.assertEqual(
            inputs["qwen_transport_source_sha256"],
            _sha256(ROOT / "src/graphiti_native.py"),
        )
        self.assertEqual(
            inputs["qwen_transport_contract_test_sha256"],
            _sha256(ROOT / "tests/test_qwen_upstream_transport_shim.py"),
        )
        self.assertEqual(
            inputs["pinned_graphiti_openai_generic_source_sha256"],
            _sha256(UPSTREAM_GENERIC),
        )
        self.assertEqual(
            inputs["workplan_sha256"],
            _sha256(REPO / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"),
        )
        self.assertEqual(freeze["runtime_identities"]["graphiti"]["version"], "0.29.3")
        self.assertEqual(
            freeze["runtime_identities"]["graphiti"]["commit"],
            "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        )
        serialized = json.dumps(freeze, sort_keys=True).casefold()
        for forbidden in ("api_key", "bearer ", "gpt55_temporary", "json_object"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    import unittest

    unittest.main()
