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


def test_manifest_has_45_unique_cells_and_fixed_native_ours_async_order(tmp_path: Path) -> None:
    h = _module()
    manifest = h.build_manifest(tmp_path, implementation_identity={"source_bundle_sha256": "s", "evaluator_sha256": "e", "config_sha256": "c"}, method_frozen={"seal_sha256": "m"}, authority={"authority_sha256": "a", "context_ids": [f"h{i}" for i in range(5)]})
    cells = manifest["cells"]
    assert manifest["status"] == "SEALED"
    assert len(cells) == 45
    assert len({cell["cell_id"] for cell in cells}) == 45
    assert len({cell["attempt_id"] for cell in cells}) == 45
    assert len({cell["namespace"] for cell in cells}) == 45
    for history in range(5):
        for replicate, expected in enumerate((h.ARMS, h.ARMS, h.ARMS)):
            observed = [cell["arm"] for cell in cells if cell["history_index"] == history and cell["replicate_id"] == replicate]
            assert observed == list(expected)
            assert list(expected) == [
                "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
                "MEMBIND_V6_1_SHARED_BOUNDED_SO",
                "RELAXED_ORDER_SHARED_BOUNDED_SO",
            ]


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


def test_exact_process_identity_rejects_stale_or_cross_cell_argv(tmp_path: Path) -> None:
    import importlib.util
    import sys

    path = ROOT / "saturated_fixed_work_baseline_v1_3" / "scripts" / "run_formal_three_arm.py"
    scripts_dir = str(path.parent)
    sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_formal_three_arm_for_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_dir)
    cell = {"attempt_id": "abc123", "namespace": "ns-a"}
    attempt_root = tmp_path / "construction"
    argv = [
        "/env/bin/python",
        str(module.RUNNER),
        "--output-root",
        str(attempt_root),
        "--attempt-id",
        "abc123",
        "--namespace",
        "ns-a",
    ]
    assert module._argv_has_exact_identity(argv, attempt_root=attempt_root, cell=cell)
    assert not module._argv_has_exact_identity(argv[:-1] + ["ns-b"], attempt_root=attempt_root, cell=cell)
    assert not module._argv_has_exact_identity(argv, attempt_root=tmp_path / "other", cell=cell)
