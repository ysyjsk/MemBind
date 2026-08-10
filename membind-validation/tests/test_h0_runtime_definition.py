"""Bound runtime-definition contracts that must pass before H0 reads ``.env``."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_artifacts  # noqa: E402
from h0_live_preflight import load_authorized_h0_runtime_definition  # noqa: E402
from h0_runtime import (  # noqa: E402
    H0ManifestError,
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_file,
)


class H0RuntimeDefinitionTests(TestCase):
    def _stage_root(self, directory: str) -> Path:
        staged = Path(directory) / "membind-validation"
        shutil.copytree(ROOT / "configs/h0", staged / "configs/h0")
        for relative in (
            *h0_artifacts.H0_EXECUTION_SOURCE_PATHS,
            "artifacts/dataset/frozen_split_v1_3.json",
            "artifacts/environment/embedding_model_fingerprint.json",
            "artifacts/environment/v3_construction_runtime_evidence_20260809.json",
        ):
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        return staged

    def _written(self, root: Path):
        written = h0_artifacts.write_h0_offline_artifacts(root)
        index = written["index"]
        return written, {
            "candidate_id": "Q1",
            "phase": "H0-A",
            "resolved_manifest_index_path": written["index_path"],
            "resolved_manifest_index_sha256": written["index_sha256"],
            "resolved_candidate_manifest_path": index["resolved_manifests"]["Q1"]["path"],
            "resolved_candidate_manifest_sha256": index["resolved_manifests"]["Q1"]["sha256"],
            "resolved_shared_base_manifest_path": index["resolved_manifests"]["shared_base"]["path"],
            "resolved_shared_base_manifest_sha256": index["resolved_manifests"]["shared_base"]["sha256"],
        }

    def _rewrite_candidate_and_index(
        self,
        root: Path,
        written: dict[str, object],
        authorization: dict[str, object],
        mutate,
    ) -> dict[str, object]:
        candidate_path = root / str(authorization["resolved_candidate_manifest_path"])
        candidate = json.loads(candidate_path.read_text(encoding="ascii"))
        mutate(candidate)
        encoded_candidate = canonical_json_bytes(candidate)
        candidate_sha = canonical_json_sha256(candidate)
        candidate_relative = (
            f"{h0_artifacts.H0_ARTIFACT_SET_REL}/resolved_candidates/"
            f"Q1.{candidate_sha}.json"
        )
        candidate_path = root / candidate_relative
        candidate_path.write_bytes(encoded_candidate)

        index_path = root / str(authorization["resolved_manifest_index_path"])
        index = dict(written["index"])
        index["resolved_manifests"] = dict(index["resolved_manifests"])
        index["resolved_manifests"]["Q1"] = {
            "path": candidate_relative,
            "sha256": candidate_sha,
        }
        index_path.write_bytes(canonical_json_bytes(index))
        return {
            **authorization,
            "resolved_manifest_index_sha256": sha256_file(index_path),
            "resolved_candidate_manifest_path": candidate_relative,
            "resolved_candidate_manifest_sha256": candidate_sha,
        }

    def test_definition_uses_bound_candidate_guardrail_and_embedding_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._stage_root(tmp)
            _, authorization = self._written(root)

            definition = load_authorized_h0_runtime_definition(
                authorization, root=root
            )

            self.assertEqual(definition.identity["candidate_id"], "Q1")
            self.assertEqual(definition.identity["phase"], "H0-A")
            self.assertEqual(definition.identity["served_model_id"], "qwen3-32b-fp8")
            self.assertEqual(definition.identity["context_limit"], 40960)
            self.assertEqual(
                definition.identity["artifact_set_id"], "v1_3_harness_r5"
            )
            self.assertEqual(definition.identity["execution_harness_revision"], 5)
            self.assertEqual(definition.candidate.candidate_id, "Q1")
            self.assertEqual(definition.candidate.structured_output_mode, "json_schema")
            self.assertEqual(definition.candidate.temperature, 0.0)
            self.assertEqual(definition.candidate.top_p, 1.0)
            self.assertIsNone(definition.candidate.top_k)
            self.assertIsNone(definition.candidate.min_p)
            self.assertEqual(definition.candidate.seed, 20260806)
            self.assertEqual(definition.candidate.requested_max_tokens, 16384)
            self.assertEqual(definition.candidate.safety_margin_tokens, 32)
            self.assertEqual(
                definition.semantic_guardrail["schema_version"],
                "membind.h0.semantic-guardrail.v1",
            )
            self.assertEqual(
                len(definition.semantic_guardrail["expected_nonempty_call_ids"]), 4
            )
            self.assertEqual(
                definition.embedding_namespace["served_model_id"],
                "qwen3-embedding-0.6b",
            )
            self.assertEqual(definition.embedding_namespace["dimension"], 1024)
            self.assertEqual(
                definition.embedding_namespace["model_fingerprint"],
                "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626",
            )
            self.assertRegex(definition.definition_sha256, r"^[0-9a-f]{64}$")
            source_binding = definition.resolved_artifacts[
                "execution_source_bundle_sha256"
            ]
            self.assertEqual(
                source_binding,
                json.loads(
                    (
                        root
                        / authorization["resolved_candidate_manifest_path"]
                    ).read_text(encoding="ascii")
                )["execution_source_bundle"],
            )

    def test_rehashed_execution_source_bundle_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._stage_root(tmp)
            written, authorization = self._written(root)
            rehashed = self._rewrite_candidate_and_index(
                root,
                written,
                authorization,
                lambda candidate: candidate["execution_source_bundle"].update(
                    {"sha256": "f" * 64}
                ),
            )
            with self.assertRaisesRegex(H0ManifestError, "execution source"):
                load_authorized_h0_runtime_definition(rehashed, root=root)

    def test_rehashed_candidate_configuration_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._stage_root(tmp)
            written, authorization = self._written(root)
            rehashed = self._rewrite_candidate_and_index(
                root,
                written,
                authorization,
                lambda candidate: candidate["candidate_configuration"].update(
                    {"temperature": 0.25}
                ),
            )

            with self.assertRaisesRegex(H0ManifestError, "candidate configuration"):
                load_authorized_h0_runtime_definition(rehashed, root=root)

    def test_rehashed_missing_semantic_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._stage_root(tmp)
            written, authorization = self._written(root)

            def remove_binding(candidate):
                candidate["resolved_shared_artifacts"].pop(
                    "semantic_guardrail_manifest_sha256"
                )

            rehashed = self._rewrite_candidate_and_index(
                root, written, authorization, remove_binding
            )
            with self.assertRaisesRegex(H0ManifestError, "resolved artifact bindings"):
                load_authorized_h0_runtime_definition(rehashed, root=root)


if __name__ == "__main__":
    import unittest

    unittest.main()
