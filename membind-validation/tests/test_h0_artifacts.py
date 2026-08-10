"""Content-addressed offline artifacts required before any live H0 request."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_artifacts  # noqa: E402
from h0_artifacts import (  # noqa: E402
    H0_EXECUTION_SOURCE_PATHS,
    build_h0_execution_source_bundle_manifest,
    build_h0_http_retry_manifest,
    build_h0_prompt_bundle_manifest,
    build_h0_schema_bundle_manifest,
    build_h0_semantic_guardrail_manifest,
    build_h0_vllm_launch_manifest,
    resolve_h0_manifests,
)
from h0_runtime import (  # noqa: E402
    ArtifactBinding,
    canonical_json_bytes,
    load_h0_registry,
    sha256_file,
)


ARTIFACT_SET_ID = "v1_3_harness_r5"
ARTIFACT_SET_REL = Path("artifacts/h0_manifest_sets") / ARTIFACT_SET_ID
INDEX_REL = ARTIFACT_SET_REL / "resolved_manifest_index_v1_3_harness_r5.json"
PREVIOUS_ARTIFACT_SET_REL = Path("artifacts/h0_manifest_sets/v1_3_harness_r4")


class H0ArtifactBuilderTests(TestCase):
    def test_execution_source_bundle_binds_complete_h0_live_import_graph(self):
        manifest = build_h0_execution_source_bundle_manifest(ROOT)
        required = {
            "src/h0_bootstrap.py",
            "src/h0_runtime.py",
            "src/h0_completion.py",
            "src/h0_full_history_completion.py",
            "src/h0_live_preflight.py",
            "src/h0_live_runner.py",
            "src/h0_phase_runner.py",
            "src/h0_embedding.py",
            "src/h0_neo4j.py",
            "src/h0_graphiti_adapter.py",
            "src/h0_harness_recovery.py",
            "src/h0_stage_readiness.py",
            "src/h0_full_history_live.py",
            "src/h0_credentials.py",
            "src/h0_repair_admission.py",
            "src/h0_phase_state.py",
            "src/h0_state_transition.py",
            "src/h0_control.py",
            "src/live_outputs.py",
            "src/live_runtime.py",
            "src/deterministic_search.py",
        }
        self.assertTrue(required.issubset(set(H0_EXECUTION_SOURCE_PATHS)))
        self.assertEqual(len(H0_EXECUTION_SOURCE_PATHS), 32)
        self.assertEqual(manifest["file_count"], len(H0_EXECUTION_SOURCE_PATHS))
        self.assertEqual(
            [item["path"] for item in manifest["files"]],
            list(H0_EXECUTION_SOURCE_PATHS),
        )
        for item in manifest["files"]:
            self.assertEqual(item["sha256"], sha256_file(ROOT / item["path"]))
        encoded = json.dumps(manifest, sort_keys=True).casefold()
        self.assertNotIn("gpt55_temporary", encoded)
        self.assertNotIn(".env", encoded)

    def _write_runtime_evidence(
        self,
        root: Path,
        *,
        runtime_updates: dict | None = None,
    ) -> Path:
        runtime = {
            "default_chat_template_kwargs": {"enable_thinking": False},
            "dtype": "bfloat16",
            "engine": "V1",
            "max_model_len": 40960,
            "model_root": "/restricted/models/Qwen3-32B-FP8",
            "model_runner": "V2",
            "quantization": "fp8",
            "served_model_name": "qwen3-32b-fp8",
            "structured_outputs_config": {
                "backend": "auto",
                "disable_additional_properties": False,
                "disable_any_whitespace": False,
                "enable_in_reasoning": False,
                "reasoning_parser": "",
                "reasoning_parser_plugin": "",
            },
            "vllm_version": "0.26.0",
        }
        runtime.update(runtime_updates or {})
        path = (
            root
            / "artifacts/environment/v3_construction_runtime_evidence_20260809.json"
        )
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "classification": (
                        "configured_backend_auto_fresh_service_no_generation_observed"
                    ),
                    "runtime": runtime,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_prompt_bundle_binds_every_pinned_prompt_source_file(self):
        manifest = build_h0_prompt_bundle_manifest(ROOT)
        self.assertEqual(manifest["graphiti_version"], "0.29.3")
        self.assertGreater(manifest["file_count"], 10)
        self.assertEqual(manifest["file_count"], len(manifest["files"]))
        for item in manifest["files"]:
            self.assertTrue(item["path"].startswith("graphiti_core/prompts/"))
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")

    def test_schema_bundle_maps_every_reachable_response_model(self):
        manifest = build_h0_schema_bundle_manifest(ROOT)
        required = {
            "ExtractedEntities",
            "ExtractedEdges",
            "NodeResolutions",
            "SummarizedEntities",
            "EdgeDuplicate",
        }
        self.assertTrue(required.issubset(manifest["models"]))
        entities = manifest["models"]["ExtractedEntities"]
        self.assertNotEqual(
            entities["upstream_schema_sha256"],
            entities["effective_schema_sha256"],
        )
        self.assertEqual(
            entities["json_object_injected_schema_sha256"],
            entities["effective_schema_sha256"],
        )
        self.assertTrue(entities["episode_indices_explicit_single_zero"])

    def test_semantic_manifest_is_frozen_from_calibration_only(self):
        manifest = build_h0_semantic_guardrail_manifest(ROOT)
        split = json.loads(
            (ROOT / "artifacts/dataset/frozen_split_v1_3.json").read_text(
                encoding="utf-8"
            )
        )
        expected = manifest["expected_nonempty_call_ids"]
        self.assertEqual(len(expected), 4)
        self.assertEqual(
            {call.split(":", 1)[0] for call in expected},
            set(split["calibration_question_ids"]),
        )
        encoded = json.dumps(manifest, sort_keys=True)
        for question_id in (
            split["evaluation_question_ids"]
            + split["compatibility_development_question_ids"]
        ):
            self.assertNotIn(question_id, encoded)
        self.assertFalse(manifest["candidate_outputs_used_to_set_invariants"])
        self.assertEqual(manifest["expected_episode_indices"], [0])

    def test_semantic_manifest_rejects_an_empty_source_zero_canary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            source.write_text(
                json.dumps(
                    [
                        {
                            "question_id": "calibration",
                            "haystack_sessions": [[]],
                            "haystack_dates": ["2025/01/01 (Wed) 00:00"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            split = {
                "protocol_version": "current-validation-v1.3",
                "source_path": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "calibration_question_ids": ["calibration"],
                "compatibility_development_question_ids": [],
                "evaluation_question_ids": [],
            }
            split_path = root / "artifacts/dataset/frozen_split_v1_3.json"
            split_path.parent.mkdir(parents=True)
            split_path.write_text(json.dumps(split), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "nonempty.*speaker role"):
                build_h0_semantic_guardrail_manifest(root)

    def test_http_retry_and_launch_manifests_freeze_actual_runtime_contract(self):
        http_manifest = build_h0_http_retry_manifest(ROOT)
        self.assertEqual(http_manifest["openai_sdk_max_retries"], 0)
        self.assertEqual(http_manifest["graphiti_public_retry_attempt_limit"], 4)
        self.assertTrue(http_manifest["candidate_failure_short_circuits_retry"])
        self.assertEqual(http_manifest["timeout_seconds"]["connect"], 5.0)
        self.assertEqual(http_manifest["openai_sdk_platform"], "Linux")
        self.assertFalse(http_manifest["http_client_trust_env"])
        self.assertFalse(http_manifest["http_follow_redirects"])
        local_execution_sources = {
            name
            for name in http_manifest["implementation_sources"]
            if not name.startswith("graphiti_core/")
        }
        self.assertEqual(local_execution_sources, set(H0_EXECUTION_SOURCE_PATHS))
        for relative in local_execution_sources:
            self.assertEqual(
                http_manifest["implementation_sources"][relative],
                sha256_file(ROOT / relative),
            )
        launch = build_h0_vllm_launch_manifest(ROOT)
        self.assertEqual(launch["runtime"]["vllm_version"], "0.26.0")
        self.assertEqual(launch["runtime"]["max_model_len"], 40960)
        self.assertEqual(
            launch["runtime"]["structured_outputs_config"]["backend"], "auto"
        )

    def test_launch_manifest_fails_closed_on_drift_or_unreviewed_runtime_fields(self):
        cases = (
            ({"vllm_version": "0.25.0"}, "vllm_version"),
            ({"max_model_len": 32768}, "max_model_len"),
            (
                {"structured_outputs_config": {"backend": "xgrammar"}},
                "structured_outputs_config",
            ),
            ({"api_key": "synthetic-test-secret"}, "unexpected runtime evidence"),
        )
        for updates, error in cases:
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_runtime_evidence(root, runtime_updates=updates)
                with self.assertRaisesRegex(ValueError, error):
                    build_h0_vllm_launch_manifest(root)

    def test_resolver_closes_all_nine_bindings_without_mutating_source_specs(self):
        registry = load_h0_registry(ROOT)
        source_hashes = {
            item.path: sha256_file(ROOT / item.path) for item in registry.candidates
        }
        source_hashes[registry.base_spec_path] = sha256_file(
            ROOT / registry.base_spec_path
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as tmp:
            relative_directory = Path(tmp).relative_to(ROOT)
            bindings = {}
            for index, name in enumerate(registry.unresolved_fields):
                path = Path(tmp) / f"binding-{index}.json"
                path.write_text(json.dumps({"binding": name}), encoding="utf-8")
                bindings[name] = ArtifactBinding(
                    path=(relative_directory / path.name).as_posix(),
                    sha256=sha256_file(path),
                )
            resolved = resolve_h0_manifests(registry, bindings)
        self.assertEqual(set(resolved["candidates"]), {"Q1", "Q2", "Q3"})
        self.assertEqual(
            resolved["shared_base"]["manifest"]["unresolved_fields"], []
        )
        self.assertTrue(
            all(
                not value["manifest"]["live_eligible"]
                for value in resolved["candidates"].values()
            )
        )
        for path, digest in source_hashes.items():
            self.assertEqual(sha256_file(ROOT / path), digest)


class H0OfflineWriterVerificationTests(TestCase):
    def _stage_root(self, directory: str) -> Path:
        staged = Path(directory) / "membind-validation"
        directory_paths = ("configs/h0",)
        file_paths = (
            *H0_EXECUTION_SOURCE_PATHS,
            "artifacts/dataset/frozen_split_v1_3.json",
            "artifacts/environment/embedding_model_fingerprint.json",
            (
                "artifacts/environment/"
                "v3_construction_runtime_evidence_20260809.json"
            ),
        )
        for relative in directory_paths:
            shutil.copytree(ROOT / relative, staged / relative)
        for relative in file_paths:
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        return staged

    def _tree_snapshot(self, root: Path) -> dict[str, tuple[bytes, str]]:
        if not root.exists():
            return {}
        snapshot = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            content = path.read_bytes()
            snapshot[path.relative_to(root).as_posix()] = (
                content,
                hashlib.sha256(content).hexdigest(),
            )
        return snapshot

    def test_staged_writer_verifies_complete_content_addressed_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            written = h0_artifacts.write_h0_offline_artifacts(staged)
            verified = h0_artifacts.verify_h0_offline_artifacts(staged)

            self.assertEqual(
                verified["status"], "verified_offline_not_live_authorized"
            )
            self.assertEqual(
                verified["schema_version"],
                "membind.h0.offline-artifact-verification.v3",
            )
            self.assertEqual(verified["artifact_set_id"], ARTIFACT_SET_ID)
            self.assertEqual(verified["execution_harness_revision"], 5)
            self.assertEqual(verified["index_path"], INDEX_REL.as_posix())
            self.assertEqual(verified["binding_count"], 10)
            self.assertEqual(verified["resolved_wrapper_count"], 4)
            self.assertEqual(verified["generated_json_file_count"], 11)
            self.assertEqual(
                verified["execution_source_count"], len(H0_EXECUTION_SOURCE_PATHS)
            )
            self.assertTrue(verified["secret_scan_passed"])
            self.assertFalse(verified["live_eligible"])
            self.assertEqual(verified["index_sha256"], written["index_sha256"])

            index = written["index"]
            self.assertEqual(index["schema_version"], "membind.h0.offline-artifacts.v2")
            self.assertEqual(index["artifact_set_id"], ARTIFACT_SET_ID)
            self.assertEqual(index["execution_harness_revision"], 5)
            self.assertEqual(written["index_path"], INDEX_REL.as_posix())
            references = (
                list(index["shared_artifacts"].values())
                + list(index["resolved_manifests"].values())
            )
            self.assertIn("execution_source_bundle", index["shared_artifacts"])
            for reference in references:
                path = staged / reference["path"]
                self.assertEqual(sha256_file(path), reference["sha256"])
                self.assertIn(reference["sha256"], path.name)
                self.assertTrue(
                    reference["path"].startswith(f"{ARTIFACT_SET_REL.as_posix()}/")
                )

    def test_writer_and_verifier_leave_legacy_h0_tree_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            legacy = staged / "artifacts/h0"
            sentinel = legacy / "historical/attempt-binding.json"
            sentinel.parent.mkdir(parents=True)
            sentinel.write_bytes(b'{"legacy":true}')
            before = self._tree_snapshot(legacy)

            written = h0_artifacts.write_h0_offline_artifacts(staged)
            self.assertEqual(self._tree_snapshot(legacy), before)
            verified = h0_artifacts.verify_h0_offline_artifacts(staged)
            self.assertEqual(written["index_path"], INDEX_REL.as_posix())
            self.assertEqual(verified["index_path"], INDEX_REL.as_posix())

    def test_writer_and_verifier_leave_existing_r3_tree_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            previous_source = ROOT / PREVIOUS_ARTIFACT_SET_REL
            previous_staged = staged / PREVIOUS_ARTIFACT_SET_REL
            shutil.copytree(previous_source, previous_staged)
            before = self._tree_snapshot(previous_staged)
            self.assertTrue(before)

            written = h0_artifacts.write_h0_offline_artifacts(staged)
            after_write = self._tree_snapshot(previous_staged)
            self.assertEqual(after_write, before)
            self.assertEqual(written["index_path"], INDEX_REL.as_posix())

            verified = h0_artifacts.verify_h0_offline_artifacts(staged)
            after_verify = self._tree_snapshot(previous_staged)
            self.assertEqual(after_verify, before)
            self.assertEqual(verified["index_path"], INDEX_REL.as_posix())

    def test_verifier_rejects_tampered_indexed_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            written = h0_artifacts.write_h0_offline_artifacts(staged)
            prompt = staged / written["index"]["shared_artifacts"]["prompt_bundle"][
                "path"
            ]
            prompt.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                h0_artifacts.verify_h0_offline_artifacts(staged)

    def test_verifier_rejects_unindexed_secret_or_raw_payload_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            h0_artifacts.write_h0_offline_artifacts(staged)
            rogue = staged / ARTIFACT_SET_REL / "manifests/rogue.json"
            rogue.parent.mkdir(parents=True, exist_ok=True)
            rogue.write_text(
                json.dumps({"raw_response": "synthetic-test-only"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "unsafe H0 artifact"):
                h0_artifacts.verify_h0_offline_artifacts(staged)

    def test_verifier_rejects_safe_but_unindexed_artifact_in_current_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            h0_artifacts.write_h0_offline_artifacts(staged)
            rogue = staged / ARTIFACT_SET_REL / "manifests/rogue.json"
            rogue.parent.mkdir(parents=True, exist_ok=True)
            rogue.write_bytes(b"{}")

            with self.assertRaisesRegex(RuntimeError, "incomplete H0 artifact index"):
                h0_artifacts.verify_h0_offline_artifacts(staged)

    def test_writer_rejects_symlinked_current_artifact_set_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            outside = Path(tmp) / "outside-artifacts"
            outside.mkdir()
            artifact_sets = staged / "artifacts/h0_manifest_sets"
            artifact_sets.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "symlink"):
                h0_artifacts.write_h0_offline_artifacts(staged)
            self.assertEqual(list(outside.iterdir()), [])

    def test_verifier_rejects_indexed_symlink_and_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            written = h0_artifacts.write_h0_offline_artifacts(staged)
            prompt_ref = written["index"]["shared_artifacts"]["prompt_bundle"]
            prompt = staged / prompt_ref["path"]
            target = staged / "artifacts/symlink-target.json"
            target.write_bytes(prompt.read_bytes())
            prompt.unlink()
            prompt.symlink_to(target)

            with self.assertRaisesRegex(RuntimeError, "symlink"):
                h0_artifacts.verify_h0_offline_artifacts(staged)

            prompt.unlink()
            prompt.write_bytes(target.read_bytes())
            index_path = staged / written["index_path"]
            index = written["index"]
            index["shared_artifacts"]["prompt_bundle"]["path"] = (
                "artifacts/h0_manifest_sets/v1_3_harness_r4/../escaped.json"
            )
            index_path.write_bytes(canonical_json_bytes(index))

            with self.assertRaisesRegex(RuntimeError, "invalid indexed path/hash"):
                h0_artifacts.verify_h0_offline_artifacts(staged)

    def test_verifier_rechecks_immutable_source_spec_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            h0_artifacts.write_h0_offline_artifacts(staged)
            q1 = staged / "configs/h0/Q1.json"
            q1.write_text(q1.read_text(encoding="utf-8") + "\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "immutable source spec"):
                h0_artifacts.verify_h0_offline_artifacts(staged)


if __name__ == "__main__":
    import unittest

    unittest.main()
