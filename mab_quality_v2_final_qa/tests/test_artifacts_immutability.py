from __future__ import annotations

from mab_quality_v2_final_qa.artifacts import (
    ArtifactStore,
    assert_snapshot_unchanged,
    snapshot_paths,
)


def test_artifact_store_cannot_overlap_protected_root(tmp_path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "old.json").write_text("old", encoding="utf-8")
    snapshot = snapshot_paths([protected])
    store = ArtifactStore(tmp_path / "new-run", protected_roots=[protected])
    store.write_json("manifest.json", {"new": True})
    assert_snapshot_unchanged(snapshot)
