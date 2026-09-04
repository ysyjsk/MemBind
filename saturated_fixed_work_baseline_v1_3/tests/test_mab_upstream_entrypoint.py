from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from mab_quality_v2_final_qa.mab8192_adapter import (
    MAB8192_ADAPTER_VERSION,
    adapter_identity,
)
from mab_quality_v2_final_qa.mab_main_dataset import build_authority


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "saturated_fixed_work_baseline_v1_3" / "scripts" / "run_mab_upstream_8b.py"


def _module():
    spec = importlib.util.spec_from_file_location("run_mab_upstream_8b", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upstream_entrypoint_has_no_old_runtime_imports() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    import_lines = "\n".join(
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    )
    prohibited = (
        "shared_structured_output",
        "structured_output_recovery",
        "bounded_edge_tasks",
        "finite_edge_task",
        "membind_v6_1.mab",
        "membind_v6_1.core",
        "runtime_8b",
    )
    assert all(value not in import_lines for value in prohibited)


def test_entrypoint_projects_one_immutable_mab8192_manifest() -> None:
    module = _module()
    authority = build_authority(
        ROOT / "mab_quality_v2_final_qa" / "data" / "official_5_contexts.json"
    )
    context, manifest, episodes, public_authority = module._context_inputs(authority, 0)
    assert manifest.context_id == context.context_id
    assert manifest.dataset_revision == public_authority["revision"]
    assert manifest.to_dict()["adapter_version"] == MAB8192_ADAPTER_VERSION
    assert manifest.to_dict()["adapter_identity"] == adapter_identity()
    assert [episode.source_sequence for episode in episodes] == list(range(len(episodes)))
    assert [episode.original_source_sequence for episode in episodes] == [
        chunk.source_sequence for chunk in manifest.chunks
    ]
    assert [episode.chunk_id for episode in episodes] == [
        chunk.chunk_id for chunk in manifest.chunks
    ]
    assert [episode.body for episode in episodes] == [chunk.body for chunk in manifest.chunks]
    assert len({episode.adapter_version for episode in episodes}) == 1


def test_entrypoint_exposes_only_new_arm_names() -> None:
    module = _module()
    assert tuple(module.METHODS) == (
        "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
        "MEMBIND_V6_1_SHARED_BOUNDED_SO",
        "RELAXED_ORDER_SHARED_BOUNDED_SO",
    )


def test_failed_attempt_persists_terminal_transport_diagnostics(tmp_path: Path) -> None:
    module = _module()
    row = {
        "status": "success",
        "finish_reason": "stop",
        "response_json_valid": False,
        "response_content_sha256": "a" * 64,
    }
    evidence = module._persist_failure_transport_evidence(
        tmp_path,
        SimpleNamespace(_membind_transport_telemetry=[row]),
    )
    assert evidence["row_count"] == 1
    assert evidence["last_transport_response"] == row
    persisted = [
        json.loads(line)
        for line in (tmp_path / "failure_transport_telemetry.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert persisted == [row]


def test_entrypoint_returns_nonzero_on_deterministic_failure(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()

    async def fail(_args):
        raise RuntimeError("deterministic construction failure")

    monkeypatch.setattr(module, "_main", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "run",
            "--attempt-id",
            "attempt",
            "--namespace",
            "local-qwen3-8b-awq-dualreplica-v1-test",
            "--context-index",
            "0",
            "--replicate-id",
            "0",
            "--method",
            "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
            "--platform-manifest",
            str(tmp_path / "platform.json"),
        ],
    )
    assert module.main() == 2
    assert not (tmp_path / "complete.json").exists()
