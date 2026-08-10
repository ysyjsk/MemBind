"""Offline RED contracts for the source-bound Protocol v1.3 R6 artifact set.

The tests stage only repository files and synthetic immutable sentinels.  They
must not load credentials or contact construction, embedding, Neo4j, or SSH.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_artifacts  # noqa: E402
import h0_live_preflight  # noqa: E402
from h0_runtime import sha256_file  # noqa: E402


R6_ARTIFACT_SET_ID = "v1_3_harness_r6"
R6_HARNESS_REVISION = 6
R6_ARTIFACT_SET_REL = f"artifacts/h0_manifest_sets/{R6_ARTIFACT_SET_ID}"
R6_INDEX_REL = (
    f"{R6_ARTIFACT_SET_REL}/resolved_manifest_index_v1_3_harness_r6.json"
)
FROZEN_INDEX_SHA256 = {
    "v1_3_harness_r2": (
        "be31de29de13fb0d607570cbc1832c7df32fe83af51ec3ab31722ec036f172cf"
    ),
    "v1_3_harness_r3": (
        "13adf4852194399985f5750ed8e91eed6990f9a07d8409feabc0dd3c9f9d7624"
    ),
    "v1_3_harness_r4": (
        "a08b3f704c9680476990f24edc239d4af50ced39edcf9aae0d529b5ed14332d7"
    ),
    "v1_3_harness_r5": (
        "3f41f7520255a1ab64e9ee34efebaccbb05a1d580b7a390057ced0f02b3d13dd"
    ),
}


class H0R6ArtifactIdentityRedTests(TestCase):
    def _stage_root(self, directory: str) -> Path:
        staged = Path(directory) / "membind-validation"
        shutil.copytree(ROOT / "configs/h0", staged / "configs/h0")
        required_files = (
            *h0_artifacts.H0_EXECUTION_SOURCE_PATHS,
            "artifacts/dataset/frozen_split_v1_3.json",
            "artifacts/environment/embedding_model_fingerprint.json",
            (
                "artifacts/environment/"
                "v3_construction_runtime_evidence_20260809.json"
            ),
        )
        for relative in required_files:
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        return staged

    def _tree_snapshot(self, root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def test_current_writer_and_preflight_identity_is_r6(self):
        self.assertEqual(h0_artifacts.H0_ARTIFACT_SET_ID, R6_ARTIFACT_SET_ID)
        self.assertEqual(
            h0_artifacts.H0_EXECUTION_HARNESS_REVISION,
            R6_HARNESS_REVISION,
        )
        self.assertEqual(h0_artifacts.H0_ARTIFACT_SET_REL, R6_ARTIFACT_SET_REL)
        self.assertEqual(
            h0_artifacts.H0_RESOLVED_MANIFEST_INDEX_REL,
            R6_INDEX_REL,
        )
        self.assertEqual(h0_live_preflight._ARTIFACT_SET_ID, R6_ARTIFACT_SET_ID)
        self.assertEqual(
            h0_live_preflight._EXECUTION_HARNESS_REVISION,
            R6_HARNESS_REVISION,
        )
        self.assertEqual(h0_live_preflight._RESOLVED_INDEX_REL, R6_INDEX_REL)

    def test_r6_execution_bundle_binds_every_current_mainline_h0_source(self):
        manifest = h0_artifacts.build_h0_execution_source_bundle_manifest(ROOT)
        bundled = {item["path"] for item in manifest["files"]}
        current_h0_sources = {
            path.relative_to(ROOT).as_posix()
            for path in ROOT.glob("src/h0_*.py")
            if path.is_file() and not path.is_symlink()
        }
        especially_required = {
            "src/h0_embedding.py",
            "src/h0_runtime.py",
            "src/h0_phase_runner.py",
            "src/h0_harness_recovery.py",
            "src/h0_repair_admission.py",
            "src/h0_full_history_live.py",
            "src/h0_live_preflight.py",
            "src/h0_control.py",
            "src/h0_artifacts.py",
        }

        self.assertTrue(especially_required.issubset(bundled))
        self.assertTrue(current_h0_sources.issubset(bundled))
        self.assertEqual(bundled, set(h0_artifacts.H0_EXECUTION_SOURCE_PATHS))
        self.assertEqual(manifest["file_count"], len(bundled))
        for source in manifest["files"]:
            self.assertEqual(source["sha256"], sha256_file(ROOT / source["path"]))
        self.assertFalse(manifest["temporary_gpt_lane_included"])
        self.assertFalse(manifest["environment_file_included"])
        self.assertEqual(manifest["artifact_set_id"], R6_ARTIFACT_SET_ID)
        self.assertEqual(
            manifest["execution_harness_revision"], R6_HARNESS_REVISION
        )

    def test_r2_r3_r4_r5_resolved_indexes_remain_byte_frozen(self):
        for artifact_set_id, expected_sha256 in FROZEN_INDEX_SHA256.items():
            revision = artifact_set_id.rsplit("r", 1)[1]
            relative = (
                f"artifacts/h0_manifest_sets/{artifact_set_id}/"
                f"resolved_manifest_index_v1_3_harness_r{revision}.json"
            )
            with self.subTest(artifact_set_id=artifact_set_id):
                self.assertEqual(sha256_file(ROOT / relative), expected_sha256)

    def test_r6_writer_never_targets_or_changes_r2_r3_r4_r5_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = self._stage_root(tmp)
            old_roots = []
            for artifact_set_id in FROZEN_INDEX_SHA256:
                old_root = staged / f"artifacts/h0_manifest_sets/{artifact_set_id}"
                old_root.mkdir(parents=True, exist_ok=True)
                (old_root / "immutable-sentinel.json").write_bytes(
                    f'{{"artifact_set_id":"{artifact_set_id}"}}'.encode("ascii")
                )
                old_roots.append(old_root)
            before = {root.name: self._tree_snapshot(root) for root in old_roots}
            write_targets = []
            original_write = h0_artifacts._write_canonical

            def observe_write(root, path, value, *, label):
                write_targets.append(path.relative_to(root).as_posix())
                return original_write(root, path, value, label=label)

            with patch.object(
                h0_artifacts,
                "_write_canonical",
                side_effect=observe_write,
            ):
                written = h0_artifacts.write_h0_offline_artifacts(staged)

            after = {root.name: self._tree_snapshot(root) for root in old_roots}
            self.assertEqual(after, before)
            self.assertTrue(write_targets)
            self.assertTrue(
                all(path.startswith(f"{R6_ARTIFACT_SET_REL}/") for path in write_targets)
            )
            self.assertEqual(written["index_path"], R6_INDEX_REL)
            self.assertTrue((staged / R6_INDEX_REL).is_file())


if __name__ == "__main__":
    import unittest

    unittest.main()
