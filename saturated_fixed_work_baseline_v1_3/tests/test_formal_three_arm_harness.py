from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _module():
    import importlib.util

    path = ROOT / "saturated_fixed_work_baseline_v1_3" / "scripts" / "formal_three_arm_harness.py"
    spec = importlib.util.spec_from_file_location("formal_three_arm_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_has_45_unique_cells_and_counterbalanced_order(tmp_path: Path) -> None:
    h = _module()
    manifest = h.build_manifest(tmp_path, implementation_identity={"source_bundle_sha256": "s", "evaluator_sha256": "e", "config_sha256": "c"}, method_frozen={"seal_sha256": "m"}, authority={"authority_sha256": "a", "context_ids": [f"h{i}" for i in range(5)]})
    cells = manifest["cells"]
    assert manifest["status"] == "SEALED"
    assert len(cells) == 45
    assert len({cell["cell_id"] for cell in cells}) == 45
    assert len({cell["attempt_id"] for cell in cells}) == 45
    assert len({cell["namespace"] for cell in cells}) == 45
    for history in range(5):
        for replicate, expected in enumerate((h.ARMS, h.ARMS[1:] + h.ARMS[:1], h.ARMS[2:] + h.ARMS[:2])):
            observed = [cell["arm"] for cell in cells if cell["history_index"] == history and cell["replicate_id"] == replicate]
            assert observed == list(expected)


def test_manifest_rejects_duplicate_identity(tmp_path: Path) -> None:
    h = _module()
    manifest = h.build_manifest(tmp_path, implementation_identity={"source_bundle_sha256": "s", "evaluator_sha256": "e", "config_sha256": "c"}, method_frozen={"seal_sha256": "m"}, authority={"authority_sha256": "a", "context_ids": [f"h{i}" for i in range(5)]})
    manifest["cells"][1]["attempt_id"] = manifest["cells"][0]["attempt_id"]
    with pytest.raises(ValueError, match="duplicate"):
        h.validate_manifest(manifest)


def test_reducer_requires_full_construction_and_qa_before_pass(tmp_path: Path) -> None:
    h = _module()
    rows = [{"cell_id": f"c{i}", "history_index": i // 9, "replicate_id": (i // 3) % 3, "arm": h.ARMS[i % 3], "construction_status": "PASS", "qa_status": "PASS", "qa_rows": 60, "t_build_ns": 10} for i in range(45)]
    result = h.reduce_formal(rows)
    assert result["status"] == "PASS"
    assert result["construction_cell_count"] == 45
    assert result["qa_seal_count"] == 45
    rows[0]["qa_status"] = "INVALID"
    blocked = h.reduce_formal(rows)
    assert blocked["status"] == "INCOMPLETE"
    assert blocked["history_effects"] == []
