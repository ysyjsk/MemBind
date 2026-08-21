from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.run_manifest import (
    RunManifestError,
    initialize_run_artifacts,
    verify_run_artifacts,
)


def test_initialize_run_materializes_frozen_auditable_inputs(
    repository_root: Path, tmp_path: Path
) -> None:
    run_root = tmp_path / "sfwb-v1-2-test-run"
    resource = {
        "schema_version": "membind.saturated-fixed-work.resource-envelope.v1",
        "historical_resource_match": False,
        "live_resource_envelope_verified": False,
        "all_formal_blocks_share_one_resource_envelope": False,
        "provider_gpu_uuid": None,
        "missing_evidence": ["provider_gpu_uuid"],
    }
    result = initialize_run_artifacts(
        repository_root=repository_root,
        run_root=run_root,
        run_id="sfwb-v1-2-test-run",
        resource_envelope=resource,
    )
    required = {
        "audit_manifest.json",
        "reuse_manifest.json",
        "protocol_manifest.json",
        "config_hashes.json",
        "provider_envelope.json",
        "resource_envelope.json",
        "RESOURCE_ENVELOPE_ID",
        "failed_attempts.jsonl",
        "service_evidence",
    }
    assert required <= {path.name for path in run_root.iterdir()}
    assert result["verified"] is True
    assert result["formal_block_count"] == 8
    protocol = json.loads((run_root / "protocol_manifest.json").read_text())
    assert protocol["formal_order"][0]["history_id"] == "07741c45"
    assert protocol["formal_order"][0]["method"] == "B0_NATIVE_SERIAL"
    assert protocol["qualification_passed"] is False
    audit = json.loads((run_root / "audit_manifest.json").read_text())
    assert audit["dataset"]["episode_count"] == 188
    assert audit["qa_inventory"]["question_count"] == 16
    resource_value = json.loads((run_root / "resource_envelope.json").read_text())
    canonical_resource = json.dumps(
        resource_value, sort_keys=True, separators=(",", ":")
    ).encode()
    assert (run_root / "RESOURCE_ENVELOPE_ID").read_text().strip() == hashlib.sha256(
        canonical_resource
    ).hexdigest()
    assert verify_run_artifacts(run_root)["verified"] is True


def test_initialize_run_is_append_only_and_refuses_overwrite(
    repository_root: Path, tmp_path: Path
) -> None:
    run_root = tmp_path / "sfwb-v1-2-test-run"
    resource = {
        "historical_resource_match": False,
        "live_resource_envelope_verified": False,
        "all_formal_blocks_share_one_resource_envelope": False,
    }
    initialize_run_artifacts(
        repository_root=repository_root,
        run_root=run_root,
        run_id="sfwb-v1-2-test-run",
        resource_envelope=resource,
    )
    with pytest.raises(RunManifestError, match="RUN_ROOT_ALREADY_INITIALIZED"):
        initialize_run_artifacts(
            repository_root=repository_root,
            run_root=run_root,
            run_id="sfwb-v1-2-test-run",
            resource_envelope=resource,
        )


def test_initialize_run_preserves_the_only_preexisting_tdd_journal(
    repository_root: Path, tmp_path: Path
) -> None:
    run_root = tmp_path / "sfwb-v1-2-test-run"
    run_root.mkdir()
    journal = run_root / "tdd_evidence.jsonl"
    original = (
        '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
        '"stage":"P0","event":"RED","exit_code":1}\n'
    )
    journal.write_text(original, encoding="utf-8")

    result = initialize_run_artifacts(
        repository_root=repository_root,
        run_root=run_root,
        run_id="sfwb-v1-2-test-run",
        resource_envelope={
            "historical_resource_match": False,
            "live_resource_envelope_verified": False,
            "all_formal_blocks_share_one_resource_envelope": False,
        },
    )

    assert result["verified"] is True
    assert journal.read_text(encoding="utf-8") == original
    with journal.open("a", encoding="utf-8") as stream:
        stream.write(
            '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
            '"stage":"P0","event":"GREEN","exit_code":0}\n'
        )
    assert verify_run_artifacts(run_root)["verified"] is True


@pytest.mark.parametrize(
    "preexisting_name",
    ("unexpected.json", "protocol_manifest.json", "service_evidence"),
)
def test_initialize_run_rejects_any_other_preexisting_entry(
    repository_root: Path, tmp_path: Path, preexisting_name: str
) -> None:
    run_root = tmp_path / "sfwb-v1-2-test-run"
    run_root.mkdir()
    path = run_root / preexisting_name
    if preexisting_name == "service_evidence":
        path.mkdir()
    else:
        path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RunManifestError, match="RUN_ROOT_ALREADY_INITIALIZED"):
        initialize_run_artifacts(
            repository_root=repository_root,
            run_root=run_root,
            run_id="sfwb-v1-2-test-run",
            resource_envelope={},
        )


@pytest.mark.parametrize("contents", ("", "not-json\n", "{}\n"))
def test_initialize_run_rejects_invalid_preexisting_tdd_journal(
    repository_root: Path, tmp_path: Path, contents: str
) -> None:
    run_root = tmp_path / "sfwb-v1-2-test-run"
    run_root.mkdir()
    (run_root / "tdd_evidence.jsonl").write_text(contents, encoding="utf-8")

    with pytest.raises(RunManifestError, match="TDD_EVIDENCE_INVALID"):
        initialize_run_artifacts(
            repository_root=repository_root,
            run_root=run_root,
            run_id="sfwb-v1-2-test-run",
            resource_envelope={},
        )


def test_verify_run_detects_config_tampering(
    repository_root: Path, tmp_path: Path
) -> None:
    run_root = tmp_path / "sfwb-v1-2-test-run"
    initialize_run_artifacts(
        repository_root=repository_root,
        run_root=run_root,
        run_id="sfwb-v1-2-test-run",
        resource_envelope={
            "historical_resource_match": False,
            "live_resource_envelope_verified": False,
            "all_formal_blocks_share_one_resource_envelope": False,
        },
    )
    (run_root / "provider_envelope.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(RunManifestError, match="RUN_MANIFEST_HASH_MISMATCH"):
        verify_run_artifacts(run_root)
