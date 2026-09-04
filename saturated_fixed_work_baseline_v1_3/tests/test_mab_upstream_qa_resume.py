from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from mab_quality_v2_final_qa.mab8192_adapter import (
    MAB8192_ADAPTER_VERSION,
    MAB8192Manifest,
)
from mab_quality_v2_final_qa.mab_main_dataset import build_authority


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "saturated_fixed_work_baseline_v1_3"
    / "scripts"
    / "run_mab_v13_qa_resume.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("run_mab_v13_qa_resume", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qa_resume_has_no_runtime_construction_import() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    prohibited = (
        "bounded_edge_tasks",
        "finite_edge_task",
        "structured_output_recovery",
        "membind_v6_1.runtime_8b",
    )
    assert all(value not in source for value in prohibited)


def test_qa_resume_accepts_shared_bounded_formal_arm_names() -> None:
    module = _module()
    assert module.FORMAL_UPSTREAM_ARMS == frozenset(
        {
            "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
            "MEMBIND_V6_1_SHARED_BOUNDED_SO",
            "RELAXED_ORDER_SHARED_BOUNDED_SO",
        }
    )


def test_qa_target_rebuilds_chunk_manifest_and_original_session_provenance(
    tmp_path: Path,
) -> None:
    module = _module()
    authority = build_authority(
        ROOT / "mab_quality_v2_final_qa" / "data" / "official_5_contexts.json"
    )
    public = {key: value for key, value in authority.items() if key != "contexts"}
    context = authority["contexts"][0]
    manifest = MAB8192Manifest.from_context(
        context, dataset_revision=str(public["revision"])
    )
    block = tmp_path / "block"
    block.mkdir()
    (block / "construction_seal.json").write_text(
        json.dumps(
            {
                "identity": {
                    "dataset_authority_sha256": public["authority_sha256"],
                    "method": "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
                    "namespace": "qa-test-namespace",
                    "context_id": context.context_id,
                    "workload_hash": manifest.manifest_sha256,
                }
            }
        ),
        encoding="utf-8",
    )
    (block / "adapter_coverage.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "adapter_version": MAB8192_ADAPTER_VERSION,
                "chunk_count": len(manifest.chunks),
                "session_count": len(context.sessions),
            }
        ),
        encoding="utf-8",
    )
    module.verify_seal = lambda _root: None
    _, selected_context, selected_manifest = module._validate_target(
        block_root=block,
        frozen_authority=public,
        authority=authority,
    )
    provenance = module._qa_episode_provenance(selected_manifest)
    assert selected_context.context_id == context.context_id
    assert len(provenance) == len(manifest.chunks)
    assert [item.source_sequence for item in provenance] == list(
        range(len(manifest.chunks))
    )
    assert [item.session_id for item in provenance] == [
        chunk.session_id for chunk in manifest.chunks
    ]
