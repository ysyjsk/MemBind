from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_upstream_l2_qualification.py"


def _module():
    scripts = str(SCRIPT.parent)
    import sys

    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("upstream_l2_cell_result", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
    return module


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path: Path, *, episodes: list[dict], entities=None, edges=None,
             implementation: str = "s" * 64) -> tuple[object, Path, dict]:
    module = _module()
    cell = {
        "cell_id": "l2-h0-r0-native",
        "campaign_id": "campaign",
        "history_index": 0,
        "history_id": "history-0",
        "replicate_id": 0,
        "arm": module.ARMS[0],
        "attempt_id": "attempt-1",
        "namespace": "namespace-1",
        "workload_manifest_sha256": "w" * 64,
        "dataset_authority_sha256": "d" * 64,
        "implementation_source_bundle_sha256": "s" * 64,
        "platform_manifest_sha256": "p" * 64,
        "expected_construction_artifacts": [
            "complete.json", "run_contract.json", "block/construction_seal.json",
            "block/adapter_coverage.json", "block/work_inventory.json",
            "block/runtime_identity.json", "block/lifecycle_validation.json",
            "block/order_validation.json", "block/refinement_validation.json",
            "block/graph_diagnostics.json", "route_seal.json",
        ],
    }
    attempt = tmp_path / "history-0/replicate-0" / cell["arm"] / cell["attempt_id"]
    _write(attempt / "complete.json", {
        "status": "PASS", "attempt_id": cell["attempt_id"],
        "namespace": cell["namespace"], "method": cell["arm"],
    })
    _write(attempt / "run_contract.json", {
        "attempt_id": cell["attempt_id"], "namespace": cell["namespace"],
        "arm": cell["arm"], "history_index": 0, "history_id": cell["history_id"],
        "replicate_id": 0, "chunk_manifest_sha256": cell["workload_manifest_sha256"],
        "dataset_authority_sha256": cell["dataset_authority_sha256"],
        "implementation": {"payload_sha256": implementation},
        "platform": {"payload_sha256": cell["platform_manifest_sha256"]},
    })
    _write(attempt / "block/construction_seal.json", {
        "status": "CONSTRUCTION_SEALED",
        "identity": {"namespace": cell["namespace"], "method": cell["arm"],
                      "context_id": cell["history_id"]},
    })
    _write(attempt / "block/adapter_coverage.json", {
        "status": "PASS", "adapter_version": "MAB_ROLE_AWARE_LOSSLESS_8192_V1",
        "chunk_count": 3,
    })
    _write(attempt / "block/work_inventory.json", {
        "expected_episode_count": 3, "submitted_count": 3, "completed_count": 3,
    })
    _write(attempt / "block/runtime_identity.json", {})
    _write(attempt / "block/lifecycle_validation.json", {"contract_status": "PASS"})
    _write(attempt / "block/order_validation.json", {"order_contract_status": "PASS"})
    _write(attempt / "block/refinement_validation.json", {"refinement_status": "PASS"})
    _write(attempt / "block/graph_diagnostics.json", {
        "status": "PASS", "episodes": episodes,
        "entities": entities if entities is not None else [{"name": "Alice"}],
        "edges": edges if edges is not None else [{"fact": "Alice knows Bob"}],
    })
    _write(attempt / "route_seal.json", {"status": "ROUTE_SEALED"})
    # The test isolates _cell_result's contract logic from cryptographic seal details.
    module.verify_seal = lambda _root: None
    module.strict_formal_runtime_identity_errors = lambda *_args, **_kwargs: []
    return module, tmp_path, cell


def _episodes(*sequences: int) -> list[dict]:
    return [
        {"source_sequence": sequence, "source_hash": f"hash-{sequence}", "session_id": "s"}
        for sequence in sequences
    ]


def test_cell_result_accepts_complete_nonempty_graph(tmp_path: Path) -> None:
    module, root, cell = _fixture(tmp_path, episodes=_episodes(0, 1, 2))
    result = module._cell_result(root, cell, 0)
    assert result["status"] == "PASS"
    assert result["graph_sanity"]["status"] == "PASS"
    assert result["graph_sanity"]["expected_episode_count"] == 3


@pytest.mark.parametrize(
    "episodes, entities, edges",
    [
        (_episodes(0, 1, 2), [], []),          # episode-only graph
        (_episodes(0, 1), None, None),        # missing episode
        (_episodes(0, 1, 1), None, None),     # duplicate episode
        (_episodes(0, 1, 2), [{"name": "Alice", "group_id": "other"}], None),
    ],
)
def test_cell_result_rejects_graph_sanity_failures(
    tmp_path: Path, episodes: list[dict], entities, edges
) -> None:
    module, root, cell = _fixture(
        tmp_path, episodes=episodes, entities=entities, edges=edges
    )
    result = module._cell_result(root, cell, 0)
    assert result["status"] == "FAIL"
    assert result["graph_sanity"]["status"] == "FAIL"


def test_cell_result_marks_implementation_source_mismatch_invalid(tmp_path: Path) -> None:
    module, root, cell = _fixture(
        tmp_path, episodes=_episodes(0, 1, 2), implementation="x" * 64
    )
    result = module._cell_result(root, cell, 0)
    assert result["construction_status"] == "INVALID"

