from __future__ import annotations

from pathlib import Path

from saturated_fixed_work_baseline_v1_2.run_manifest import initialize_run_artifacts


def test_initialization_preserves_pre_manifest_stop_and_recovery_evidence(
    repository_root: Path, tmp_path: Path
) -> None:
    journal = tmp_path / "tdd_evidence.jsonl"
    journal.write_text(
        '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
        '"stage":"P0","event":"RED","command":"pytest","exit_code":1,'
        '"observed_at":"2026-08-21T00:00:00+08:00","output_summary":"failed"}\n',
        encoding="utf-8",
    )
    recovery = tmp_path / "service_evidence/recovery_round_001.json"
    recovery.parent.mkdir(parents=True)
    recovery.write_text('{"immutable":true}\n', encoding="utf-8")
    stop = tmp_path / "STOP_WITH_EXTERNAL_DIAGNOSIS.json"
    stop.write_text('{"completed":false}\n', encoding="utf-8")
    before = {path: path.read_bytes() for path in (journal, recovery, stop)}

    initialize_run_artifacts(
        repository_root=repository_root,
        run_root=tmp_path,
        run_id="sfwb-v1-2-resume-test",
        resource_envelope={
            "historical_resource_match": True,
            "live_resource_envelope_verified": True,
        },
    )

    assert all(path.read_bytes() == payload for path, payload in before.items())
    assert (tmp_path / "run_manifest_inventory.json").is_file()
