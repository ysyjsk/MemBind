from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.preflight_seal import (
    PreflightSealError,
    verify_preflight_seal,
    write_preflight_seal,
)
from saturated_fixed_work_baseline_v1_2.run_manifest import initialize_run_artifacts


def _initialize(repository_root: Path, run_root: Path) -> None:
    initialize_run_artifacts(
        repository_root=repository_root,
        run_root=run_root,
        run_id="sfwb-v1-2-preflight-test",
        resource_envelope={
            "historical_resource_match": True,
            "live_resource_envelope_verified": True,
            "all_formal_blocks_share_one_resource_envelope": "NOT_EVALUATED",
        },
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _write_bound_json(path: Path, value: dict[str, object]) -> str:
    selected = dict(value)
    selected["payload_sha256"] = _hash(selected)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(selected, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(root: Path) -> dict[str, object]:
    test_summary_sha = _write_bound_json(
        root / "test_summary.json",
        {
            "tests_all_green": True,
            "tdd_evidence_verified": True,
            "tdd_evidence_sha256": hashlib.sha256(
                (root / "tdd_evidence.jsonl").read_bytes()
            ).hexdigest(),
            "required_tdd_stages": ["P0"],
        },
    )
    service_sha = _write_bound_json(
        root / "service_evidence/l0_services.json",
        {
            "status": "PASS",
            "construction_canary_passed": True,
            "embedding_canary_passed": True,
            "neo4j_canary_passed": True,
            "construction_cache_salt_passed": True,
            "embedding_cache_salt_passed": True,
            "no_other_clients": True,
        },
    )
    warmup_sha = _write_bound_json(
        root / "preflight/warmup_evidence.json",
        {
            "status": "PASS",
            "manifest_verified": True,
            "disjoint_from_formal_data": True,
            "construction_warmup_passed": True,
            "embedding_warmup_passed": True,
        },
    )
    idle_sha = _write_bound_json(
        root / "preflight/idle_evidence.json",
        {
            "schema_version": "membind.saturated-fixed-work.idle-evidence.v1",
            "status": "PASS",
            "all_services_idle": True,
            "required_consecutive_samples": 2,
            "samples": [{"idle": True}, {"idle": True}],
        },
    )
    sampler_sha = _write_bound_json(
        root / "preflight/sampler_qualification.json",
        {
            "schema_version": "membind.saturated-fixed-work.sampler-qualification.v1",
            "status": "PASS",
            "formal_run_authorized": True,
            "required_sources": [
                "construction_vllm",
                "embedding_vllm",
                "runner_process",
                "neo4j_process",
                "runner_host",
                "provider_gpu",
            ],
            "failed_gates": [],
            "summary": {
                "duration_s": 60.0,
                "expected_samples": 60,
                "actual_samples": 60,
                "coverage": 1.0,
                "gap_p95_s": 1.0,
                "gap_max_s": 1.0,
                "source_coverage": {
                    "construction_vllm": 1.0,
                    "embedding_vllm": 1.0,
                    "runner_process": 1.0,
                    "neo4j_process": 1.0,
                    "runner_host": 1.0,
                    "provider_gpu": 1.0,
                },
            },
        },
    )
    return {
        "tests_all_green": True,
        "repository_identity_verified": True,
        "data_identity_verified": True,
        "provider_identity_verified": True,
        "qa_identity_verified": True,
        "historical_resource_match": True,
        "live_resource_envelope_verified": True,
        "service_evidence_sha256": service_sha,
        "warmup_evidence_sha256": warmup_sha,
        "idle_evidence_sha256": idle_sha,
        "sampler_qualification_sha256": sampler_sha,
        "test_summary_sha256": test_summary_sha,
    }


def test_preflight_seal_binds_every_l0_artifact_and_is_append_only(
    repository_root: Path, tmp_path: Path
) -> None:
    tdd = tmp_path / "tdd_evidence.jsonl"
    tdd.write_text(
        '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
        '"stage":"P0","event":"RED","command":"pytest",'
        '"exit_code":1,"observed_at":"2026-08-21T00:00:00+08:00",'
        '"output_summary":"failed"}\n'
        '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
        '"stage":"P0","event":"GREEN","command":"pytest",'
        '"exit_code":0,"observed_at":"2026-08-21T00:01:00+08:00",'
        '"output_summary":"passed"}\n',
        encoding="utf-8",
    )
    _initialize(repository_root, tmp_path)
    evidence = _evidence(tmp_path)

    seal = write_preflight_seal(tmp_path, evidence)

    assert seal["preflight_passed"] is True
    assert seal["formal_run_authorized"] is True
    assert verify_preflight_seal(tmp_path)["verified"] is True
    with pytest.raises(PreflightSealError, match="PREFLIGHT_SEAL_ALREADY_EXISTS"):
        write_preflight_seal(tmp_path, evidence)


def test_preflight_seal_refuses_false_gate_and_detects_bound_artifact_tamper(
    repository_root: Path, tmp_path: Path
) -> None:
    (tmp_path / "tdd_evidence.jsonl").write_text(
        '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
        '"stage":"P0","event":"RED","command":"pytest","exit_code":1,'
        '"observed_at":"2026-08-21T00:00:00+08:00","output_summary":"failed"}\n'
        '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
        '"stage":"P0","event":"GREEN","command":"pytest","exit_code":0,'
        '"observed_at":"2026-08-21T00:01:00+08:00","output_summary":"passed"}\n',
        encoding="utf-8",
    )
    _initialize(repository_root, tmp_path)
    evidence = _evidence(tmp_path)
    failed = dict(evidence)
    failed["historical_resource_match"] = False
    with pytest.raises(PreflightSealError, match="PREFLIGHT_GATE_INCOMPLETE"):
        write_preflight_seal(tmp_path, failed)

    write_preflight_seal(tmp_path, evidence)
    path = tmp_path / "preflight/idle_evidence.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(PreflightSealError, match="PREFLIGHT_BOUND_ARTIFACT_CHANGED"):
        verify_preflight_seal(tmp_path)

