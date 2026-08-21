from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import saturated_fixed_work_baseline_v1_2.dataset as dataset_module
from saturated_fixed_work_baseline_v1_2.dataset import (
    DatasetError,
    EXPECTED_EPISODE_COUNTS,
    load_frozen_qa_source_record,
)
from saturated_fixed_work_baseline_v1_2.idle import (
    IdleEvidenceError,
    collect_idle_evidence,
    write_idle_evidence,
)


def _metrics(running: int = 0, waiting: int = 0) -> str:
    return "\n".join(
        (
            f"vllm:num_requests_running {running}",
            f"vllm:num_requests_waiting {waiting}",
            "vllm:kv_cache_usage_perc 0.0",
            "vllm:prefix_cache_queries_total 10",
            "vllm:prefix_cache_hits_total 4",
            "vllm:num_preemptions_total 0",
            "vllm:prompt_tokens_total 100",
            "vllm:generation_tokens_total 20",
        )
    )


def test_frozen_qa_source_record_loader_rechecks_source_sha(
    repository_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = load_frozen_qa_source_record(repository_root, "07741c45")
    assert record["question_id"] == "07741c45"
    assert len(record["haystack_sessions"]) == EXPECTED_EPISODE_COUNTS["07741c45"]
    assert len(record["haystack_session_ids"]) == EXPECTED_EPISODE_COUNTS["07741c45"]
    assert len(record["haystack_dates"]) == EXPECTED_EPISODE_COUNTS["07741c45"]

    changed = tmp_path / "changed.json"
    changed.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(dataset_module, "RAW_DATASET", changed)
    with pytest.raises(DatasetError, match="DATASET_FILE_HASH_MISMATCH"):
        load_frozen_qa_source_record(repository_root, "07741c45")


def test_idle_evidence_requires_two_consecutive_samples_for_both_vllm_and_neo4j(
    repository_root: Path, tmp_path: Path
) -> None:
    calls: list[str] = []

    def getter(url: str, timeout_s: float) -> dict[str, str]:
        del timeout_s
        calls.append(url)
        return {"text": _metrics()}

    sleeps: list[float] = []
    evidence = collect_idle_evidence(
        repository_root=repository_root,
        http_getter=getter,
        neo4j_idle_probe=lambda: {"idle": True, "active_transactions": 0},
        sample_count=2,
        interval_s=1.0,
        sleep=sleeps.append,
    )
    assert evidence["status"] == "PASS"
    assert evidence["all_services_idle"] is True
    assert len(evidence["samples"]) == 2
    assert sleeps == [1.0]
    assert calls == [
        "http://10.87.5.247:8000/metrics",
        "http://10.87.5.247:8001/metrics",
        "http://10.87.5.247:8000/metrics",
        "http://10.87.5.247:8001/metrics",
    ]

    output = tmp_path / "idle_evidence.json"
    written = write_idle_evidence(output, evidence)
    assert json.loads(output.read_text(encoding="utf-8")) == written
    with pytest.raises(IdleEvidenceError, match="IDLE_EVIDENCE_ALREADY_EXISTS"):
        write_idle_evidence(output, evidence)


def test_idle_evidence_preserves_busy_sample_and_fails_gate(
    repository_root: Path,
) -> None:
    payloads = iter((_metrics(), _metrics(), _metrics(waiting=1), _metrics()))
    evidence = collect_idle_evidence(
        repository_root=repository_root,
        http_getter=lambda url, timeout_s: {"text": next(payloads)},
        neo4j_idle_probe=lambda: {"idle": True, "active_transactions": 0},
        sample_count=2,
        interval_s=0.0,
        sleep=lambda seconds: None,
    )
    assert evidence["status"] == "INVALID"
    assert evidence["all_services_idle"] is False
    assert evidence["samples"][1]["construction"]["waiting_requests"] == 1.0
