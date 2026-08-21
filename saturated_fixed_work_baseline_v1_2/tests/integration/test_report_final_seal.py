from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.artifacts import AttemptStore, SealEvidence
from saturated_fixed_work_baseline_v1_2.contracts import ResumeIdentity
from saturated_fixed_work_baseline_v1_2.dataset import (
    EXPECTED_EPISODE_COUNTS,
    EXPECTED_SOURCE_TOKENS,
)
from saturated_fixed_work_baseline_v1_2.report import (
    ReportError,
    build_final_report,
    verify_final_seal,
)
from saturated_fixed_work_baseline_v1_2.formal_run_seal import write_formal_run_seal
from saturated_fixed_work_baseline_v1_2.live import build_formal_plan
from saturated_fixed_work_baseline_v1_2.qualification_seal import (
    write_qualification_seal,
)
from saturated_fixed_work_baseline_v1_2.run_manifest import initialize_run_artifacts
from saturated_fixed_work_baseline_v1_2.schedules import Method


def _block_rows() -> list[dict[str, Any]]:
    rows = []
    for method in Method:
        for index, (history, count) in enumerate(EXPECTED_EPISODE_COUNTS.items()):
            rows.append(
                {
                    "method": method.value,
                    "history_id": history,
                    "valid": True,
                    "episode_count": count,
                    "source_tokens": EXPECTED_SOURCE_TOKENS[history],
                    "build_makespan_s": float((index + 1) * (10 if method is Method.B0_NATIVE_SERIAL else 5)),
                    "llm_input_tokens": count * (200 if method is Method.B0_NATIVE_SERIAL else 210),
                    "direct_semantic_violations": 0,
                    "canonical_exact_match": True,
                    "inversion_count": 0,
                    "resource_availability": "MEASURED",
                }
            )
    return rows


def _qa_rows() -> list[dict[str, Any]]:
    rows = []
    for method in Method:
        for history in EXPECTED_EPISODE_COUNTS:
            for index in range(4):
                rows.append(
                    {
                        "method": method.value,
                        "history_id": history,
                        "question_id": f"{history}-q{index}",
                        "qa_pair_id": f"{history}-q{index}",
                        "recall_at_1": 0.5,
                        "recall_at_5": 1.0,
                        "recall_at_10": 1.0,
                        "mrr": 0.75,
                        "ndcg_at_10": 0.8,
                        "correct": index < 3,
                        "invalid": False,
                        "failure_layer": None,
                        "graph_write_attempts": 0,
                    }
                )
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _self_hashed(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["payload_sha256"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


def _materialize_formal_attempts(root: Path) -> None:
    resource_id = (root / "RESOURCE_ENVELOPE_ID").read_text(encoding="ascii").strip()
    rows = {
        (row["method"], row["history_id"]): row for row in _block_rows()
    }
    for block in build_formal_plan("sfwb-v1-2-report-test"):
        cache_sha = hashlib.sha256(block.cache_salt.encode("ascii")).hexdigest()
        identity = ResumeIdentity(
            project_sha256="1" * 64,
            data_sha256="2" * 64,
            provider_sha256="3" * 64,
            resource_sha256=resource_id,
            config_sha256="5" * 64,
            cache_sha256=cache_sha,
            namespace=block.namespace,
        )
        store = AttemptStore.create(root / "blocks" / block.block_id, identity)
        store.append_event({"event": "BLOCK_STARTED", "source_sequence": None})
        seal = store.seal(
            SealEvidence(
                episode_task_count=EXPECTED_EPISODE_COUNTS[block.history_id],
                terminal_episode_task_count=EXPECTED_EPISODE_COUNTS[block.history_id],
                open_spans=0,
                open_requests=0,
                open_transactions=0,
                orphan_tasks=0,
                unobserved_exceptions=0,
                service_idle=True,
                canonical_snapshot_hashes=("a" * 64, "a" * 64),
            )
        )
        authority = _self_hashed(
            {
                "schema_version": "membind.saturated-fixed-work.live-authority.v1",
                "protocol_version": "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2",
                "run_id": block.run_id,
                "block_id": block.block_id,
                "method": block.method.value,
                "history_id": block.history_id,
                "namespace": block.namespace,
                "attempt_ordinal": 1,
                "cache_salt_sha256": cache_sha,
                "resume_identity": asdict(identity),
            }
        )
        _write_json(store.root / "live_authority.json", authority)
        graph = {
            "entities": [
                {
                    "group_id": block.namespace,
                    "name": f"entity-{block.history_id}",
                    "summary": "same sealed graph",
                    "attributes": {"history": block.history_id},
                }
            ],
            "edges": [],
            "episodes": [],
        }
        _write_json(store.root / "canonical_graph.json", graph)
        metrics = {
            **rows[(block.method.value, block.history_id)],
            "schema_version": "membind.saturated-fixed-work.block-result.v1",
            "block_id": block.block_id,
            "attempt_id": store.root.name,
            "attempt_ordinal": 1,
            "namespace": block.namespace,
            "created_sequences": list(
                range(EXPECTED_EPISODE_COUNTS[block.history_id])
            ),
            "feeder_workload_await_count": (
                EXPECTED_EPISODE_COUNTS[block.history_id]
                if block.method is Method.B0_NATIVE_SERIAL
                else 0
            ),
            "application_gate_count": 0,
            "artificial_sleep_count": 0,
            "configured_max_inflight": None,
            "t0_ns": 1_000_000_000,
            "t_durable_complete_ns": 1_000_000_000
            + int(rows[(block.method.value, block.history_id)]["build_makespan_s"] * 1e9),
            "t_validated_seal_ns": 100_000_000_000,
            "build_makespan_ns": int(
                rows[(block.method.value, block.history_id)]["build_makespan_s"]
                * 1e9
            ),
            "resource_envelope_id": resource_id,
            "canonical_graph_hash": hashlib.sha256(
                _canonical_bytes(graph)
            ).hexdigest(),
            "seal_payload_sha256": seal["payload_sha256"],
        }
        _write_json(store.root / "block_metrics.json", metrics)
    write_formal_run_seal(root)


def _required_prerequisites(root: Path, repository_root: Path) -> None:
    initialize_run_artifacts(
        repository_root=repository_root,
        run_root=root,
        run_id="sfwb-v1-2-report-test",
        resource_envelope={
            "historical_resource_match": True,
            "live_resource_envelope_verified": True,
            "all_formal_blocks_share_one_resource_envelope": "NOT_EVALUATED",
        },
    )
    (root / "tdd_evidence.jsonl").write_text(
        '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
        '"stage":"P6","event":"RED","exit_code":1,'
        '"command":"pytest -q","observed_at":"2026-08-21T04:00:00+08:00",'
        '"output_summary":"one failure"}\n'
        '{"schema_version":"membind.saturated-fixed-work.tdd-evidence.v1",'
        '"stage":"P6","event":"GREEN","exit_code":0,'
        '"command":"pytest -q","observed_at":"2026-08-21T04:01:00+08:00",'
        '"output_summary":"all passed"}\n',
        encoding="utf-8",
    )
    journal_sha = hashlib.sha256((root / "tdd_evidence.jsonl").read_bytes()).hexdigest()
    (root / "test_summary.json").write_text(
        json.dumps(
            {
                "tests_all_green": True,
                "tdd_evidence_verified": True,
                "tdd_evidence_sha256": journal_sha,
                "required_tdd_stages": ["P6"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_qualification_seal(
        root,
        {
            "preflight_passed": True,
            "instrumentation_aa_qualified": True,
            "b0_a_valid": True,
            "b0_b_valid": True,
            "b1_valid": True,
            "b0_schedule_contract": True,
            "b1_schedule_contract": True,
            "qa_read_only_passed": True,
            "canonical_diffs_emitted": True,
            "serial_serial_12_scope": "12_EPISODE_QUALIFICATION_ONLY",
            "qualification_root": "qualification/l1-attempt-001",
        },
    )
    (root / "service_evidence" / "identity.json").write_text("{}\n", encoding="utf-8")
    _materialize_formal_attempts(root)


def test_report_builds_two_real_tables_and_verified_final_seal(
    repository_root: Path, tmp_path: Path
) -> None:
    _required_prerequisites(tmp_path, repository_root)
    _write_jsonl(tmp_path / "qa/qa_rows.jsonl", _qa_rows())
    result = build_final_report(tmp_path)
    assert result["acceptance"]["valid_construction_blocks"] == 8
    assert result["acceptance"]["qa_rows_B0"] == 16
    assert result["acceptance"]["qa_rows_B1"] == 16
    assert result["reducer_output_hash_first"] == result["reducer_output_hash_second"]
    assert result["acceptance"]["all_formal_blocks_share_one_resource_envelope"] is True
    assert result["acceptance"]["final_seal_verified"] is True
    assert len(result["selected_attempts"]) == 8
    assert (tmp_path / "canonical_paired_diffs.json").is_file()
    assert (tmp_path / "block_metrics.jsonl").is_file()
    construction = (tmp_path / "main_table_construction.md").read_text(encoding="utf-8")
    quality = (tmp_path / "main_table_quality.md").read_text(encoding="utf-8")
    assert "development / protocol-qualified / one run per method-history" in construction
    assert "development / protocol-qualified / one run per method-history" in quality
    assert "B0_NATIVE_SERIAL" in construction and "B1_NAIVE_WHOLE_UPDATE_ASYNC" in construction
    assert verify_final_seal(tmp_path)["verified"] is True
    assert (tmp_path / "SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE").exists()
    with pytest.raises(ReportError, match="REPORT_ALREADY_MATERIALIZED"):
        build_final_report(tmp_path)


def test_report_refuses_incomplete_success_state(
    repository_root: Path, tmp_path: Path
) -> None:
    _required_prerequisites(tmp_path, repository_root)
    _write_jsonl(tmp_path / "qa/qa_rows.jsonl", _qa_rows()[:31])
    with pytest.raises(ReportError, match="SUCCESS_ACCEPTANCE_INCOMPLETE"):
        build_final_report(tmp_path)
    assert not (tmp_path / "FINAL_SEAL.json").exists()
    assert not (tmp_path / "SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE").exists()
