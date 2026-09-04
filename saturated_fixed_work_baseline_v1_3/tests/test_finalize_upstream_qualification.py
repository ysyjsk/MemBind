from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "saturated_fixed_work_baseline_v1_3"
    / "scripts"
    / "finalize_upstream_qualification.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "finalize_upstream_qualification", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _qualified_fixture(module, root: Path) -> dict:
    cells = []
    for index, arm in enumerate(module.ARMS):
        cell = {
            "status": "PASS",
            "arm": arm,
            "history_index": 0,
            "history_id": "history-0",
            "replicate_id": 0,
            "attempt_id": f"attempt-{index}",
            "namespace": f"namespace-{index}",
        }
        cells.append(cell)
        attempt = root / "history-0" / "replicate-0" / arm / cell["attempt_id"]
        _write(
            attempt / "complete.json",
            {
                "status": "PASS",
                "attempt_id": cell["attempt_id"],
                "namespace": cell["namespace"],
                "method": arm,
            },
        )
        _write(
            attempt / "run_contract.json",
            {
                "attempt_id": cell["attempt_id"],
                "namespace": cell["namespace"],
                "arm": arm,
                "history_index": 0,
                "replicate_id": 0,
                "dataset_authority_sha256": "d" * 64,
                "chunk_manifest_sha256": "w" * 64,
                "implementation": {"payload_sha256": "s" * 64},
                "platform": {"payload_sha256": "p" * 64},
            },
        )
        _write(
            attempt / "block/construction_seal.json",
            {
                "status": "CONSTRUCTION_SEALED",
                "identity": {
                    "method": arm,
                    "namespace": cell["namespace"],
                    "workload_hash": "w" * 64,
                },
            },
        )
        _write(attempt / "route_seal.json", {"status": "ROUTE_SEALED"})
        _write(
            attempt / "block/adapter_coverage.json",
            {
                "status": "PASS",
                "adapter_version": "MAB_ROLE_AWARE_LOSSLESS_8192_V1",
                "chunk_count": 123,
            },
        )
        _write(
            attempt / "block/work_inventory.json",
            {
                "expected_episode_count": 123,
                "submitted_count": 123,
                "completed_count": 123,
            },
        )
    return {"cells": cells}


def test_finalizer_revalidates_all_three_terminal_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    monkeypatch.setattr(module, "verify_seal", lambda _root: None)
    module._validate_qualification_artifacts(
        qualification_root=tmp_path,
        qualification=qualification,
        source_bundle_sha256="s" * 64,
        platform_payload_sha256="p" * 64,
        dataset_authority_sha256="d" * 64,
        workload_manifest_sha256="w" * 64,
    )


def test_finalizer_rejects_failure_artifact_on_declared_pass(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    first = qualification["cells"][0]
    failure = (
        tmp_path
        / "history-0"
        / "replicate-0"
        / first["arm"]
        / first["attempt_id"]
        / "failure.json"
    )
    _write(failure, {"status": "FAILED"})
    monkeypatch.setattr(module, "verify_seal", lambda _root: None)
    with pytest.raises(RuntimeError, match="failure artifact"):
        module._validate_qualification_artifacts(
            qualification_root=tmp_path,
            qualification=qualification,
            source_bundle_sha256="s" * 64,
            platform_payload_sha256="p" * 64,
            dataset_authority_sha256="d" * 64,
            workload_manifest_sha256="w" * 64,
        )


def test_finalizer_rejects_source_bundle_drift(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    monkeypatch.setattr(module, "verify_seal", lambda _root: None)
    with pytest.raises(RuntimeError, match="artifact identity"):
        module._validate_qualification_artifacts(
            qualification_root=tmp_path,
            qualification=qualification,
            source_bundle_sha256="x" * 64,
            platform_payload_sha256="p" * 64,
            dataset_authority_sha256="d" * 64,
            workload_manifest_sha256="w" * 64,
        )
