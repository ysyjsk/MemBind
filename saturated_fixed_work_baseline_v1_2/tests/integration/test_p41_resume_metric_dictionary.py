from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.contracts import EpisodeInput, ResumeIdentity
from saturated_fixed_work_baseline_v1_2.instrumentation import metric_dictionary
from saturated_fixed_work_baseline_v1_2.stage_orchestration import execute_formal_main_stage


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _identity(root: Path, block: Any) -> ResumeIdentity:
    del root
    return ResumeIdentity(
        project_sha256="1" * 64,
        data_sha256="2" * 64,
        provider_sha256="3" * 64,
        resource_sha256="4" * 64,
        config_sha256="5" * 64,
        cache_sha256="6" * 64,
        namespace=block.namespace,
    )


def _episodes(root: Path, history: str, namespace: str) -> tuple[EpisodeInput, ...]:
    del root
    count = {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}[
        history
    ]
    return tuple(
        EpisodeInput(
            history_id=history,
            session_id=f"s-{index}",
            source_sequence=index,
            source_hash=f"{index + 1:064x}",
            reference_time="2023-01-01T00:00:00Z",
            body="body",
            namespace=namespace,
        )
        for index in range(count)
    )


def _rehearsal(root: Path) -> None:
    body = {
        "schema_version": "membind.saturated-fixed-work.rehearsal-seal.v1",
        "status": "PASS",
        "rehearsal_passed": True,
        "block_count": 2,
        "qa_read_only_passed": True,
    }
    body["payload_sha256"] = _hash(body)
    path = root / "rehearsal/rehearsal_seal.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_formal_resume_skips_sealed_block_and_advances_failed_attempt_ordinal(
    repository_root: Path, tmp_path: Path
) -> None:
    (tmp_path / "protocol_manifest.json").write_text(
        json.dumps({"run_id": "sfwb-v1-2-resume-stage"}) + "\n",
        encoding="utf-8",
    )
    _rehearsal(tmp_path)
    from saturated_fixed_work_baseline_v1_2.live import build_formal_plan

    plan = build_formal_plan("sfwb-v1-2-resume-stage")
    sealed = tmp_path / "blocks" / plan[0].block_id / "attempt-001/seal.json"
    sealed.parent.mkdir(parents=True)
    sealed.write_text("{}\n", encoding="utf-8")
    failed = tmp_path / "blocks" / plan[1].block_id / "attempt-001/failure.json"
    failed.parent.mkdir(parents=True)
    failed.write_text("{}\n", encoding="utf-8")
    attempts: list[tuple[str, int]] = []

    async def executor(**kwargs: Any) -> dict[str, object]:
        block = kwargs["block"]
        attempts.append((block.block_id, block.attempt_ordinal))
        return {"valid": True}

    result = await execute_formal_main_stage(
        repository_root=repository_root,
        run_root=tmp_path,
        dependencies=object(),
        prepare_block=lambda block: True,
        qualification_verifier=lambda root: {
            "verified": True,
            "qualification_passed": True,
        },
        block_executor=executor,
        episode_loader=_episodes,
        source_token_counter=lambda root, episodes: 100,
        identity_builder=_identity,
        formal_seal_writer=lambda root: {
            "valid_construction_blocks": 8,
            "formal_construction_calls": 8,
        },
    )

    assert result["valid_construction_blocks"] == 8
    assert len(attempts) == 7
    assert attempts[0] == (plan[1].block_id, 2)
    assert all(ordinal == 1 for _, ordinal in attempts[1:])


def test_metric_dictionary_covers_every_emitted_formal_block_field() -> None:
    dictionary = metric_dictionary()
    emitted_metrics = {
        "build_makespan_s",
        "source_tokens_per_s",
        "whole_update_active_mean",
        "whole_update_active_max",
        "whole_update_active_k_time_ns",
        "inversion_count",
        "inversion_density",
        "kendall_tau",
        "max_displacement",
        "llm_input_tokens",
        "llm_logical_calls",
        "llm_transport_attempts",
        "embedding_items",
        "db_writes",
        "llm_duration_p50_s",
        "llm_duration_p95_s",
        "llm_duration_p99_s",
        "embedding_duration_p50_s",
        "embedding_duration_p95_s",
        "embedding_duration_p99_s",
        "db_duration_p50_s",
        "db_duration_p95_s",
        "db_duration_p99_s",
        "direct_semantic_violations",
        "direct_semantic_evidence_availability",
        "ordering_observations_counted_as_direct",
        "sampler_coverage",
        "sampler_gap_p95_s",
        "sampler_gap_max_s",
    }
    assert emitted_metrics <= set(dictionary)
    required_metadata = {
        "name",
        "version",
        "level",
        "unit",
        "better_direction",
        "formula",
        "numerator",
        "denominator",
        "source",
        "clock",
        "attribution_scope",
        "availability",
        "core_validity_gate",
        "interpretation",
    }
    assert all(required_metadata <= set(row) for row in dictionary.values())
