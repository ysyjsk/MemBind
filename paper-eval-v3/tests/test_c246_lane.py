"""TDD contracts for the isolated U0/P(C=2)/P(C=4) APC lane."""

from __future__ import annotations

import asyncio

import pytest

from paper_eval.c246_plan import (
    C246_METHODS,
    build_c8_extension_plan,
    build_c246_plan,
    verify_c8_extension_plan,
    verify_c246_plan,
)
from paper_eval.membind_v1.aligned_schedule import (
    AlignedEpisodeRef,
    P_C4_ALIGNED,
    P_C8_ALIGNED,
    run_aligned_baseline,
)


def _sources() -> dict[str, list[str]]:
    return {name: [f"{i + 1:064x}" for i in range(3)] for name in ("07741c45", "b6019101", "6071bd76", "a2f3aa27")}


def test_c246_plan_has_exactly_twelve_blocks_and_three_methods() -> None:
    plan = build_c246_plan(
        run_id="c246-baseline-test-001",
        history_source_sha256s=_sources(),
        interarrival_ns=100,
        service_reference_ns=120,
        execution_envelope_sha256="a" * 64,
        construction_model_identity_sha256="b" * 64,
        embedding_model_identity_sha256="c" * 64,
    )
    assert tuple(plan["methods"]) == C246_METHODS
    assert len(plan["blocks"]) == 12
    assert all("cold_start" in block for block in plan["blocks"])
    assert verify_c246_plan(plan) == plan


def test_p_c4_uses_exactly_four_workers_and_overlaps() -> None:
    episodes = tuple(
        AlignedEpisodeRef(i, f"{i + 1:064x}", object()) for i in range(4)
    )
    active = 0
    maximum = 0

    async def add(_episode: object) -> None:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1

    result = asyncio.run(
        run_aligned_baseline(
            method=P_C4_ALIGNED,
            episodes=episodes,
            arrival_offsets_ns=[0, 0, 0, 0],
            native_add_episode=add,
        )
    )
    assert result["configured_worker_count"] == 4
    assert result["observed_max_active_updates"] == 4
    assert result["whole_update_interval_overlap_observed"] is True


def test_p_c8_uses_exactly_eight_workers_and_overlaps() -> None:
    episodes = tuple(
        AlignedEpisodeRef(i, f"{i + 1:064x}", object()) for i in range(8)
    )
    active = 0
    maximum = 0

    async def add(_episode: object) -> None:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1

    result = asyncio.run(
        run_aligned_baseline(
            method=P_C8_ALIGNED,
            episodes=episodes,
            arrival_offsets_ns=[0] * 8,
            native_add_episode=add,
        )
    )
    assert result["configured_worker_count"] == 8
    assert result["observed_max_active_updates"] == 8
    assert result["whole_update_interval_overlap_observed"] is True


def test_c8_extension_requires_sealed_full_base_result() -> None:
    base = build_c246_plan(
        run_id="c246-baseline-test-001",
        history_source_sha256s=_sources(),
        interarrival_ns=100,
        service_reference_ns=120,
        execution_envelope_sha256="a" * 64,
        construction_model_identity_sha256="b" * 64,
        embedding_model_identity_sha256="c" * 64,
    )
    full = {
        "status": "PASS",
        "phase": "full",
        "run_id": base["run_id"],
        "completed_block_indices": list(range(12)),
    }
    full["payload_sha256"] = __import__("paper_eval.artifacts", fromlist=["payload_sha256"]).payload_sha256(full)
    extension = build_c8_extension_plan(base_plan=base, full_phase_result=full)
    assert len(extension["blocks"]) == 4
    assert {row["method"] for row in extension["blocks"]} == {"P(C=8)-aligned"}
    assert verify_c8_extension_plan(extension) == extension

    full["completed_block_indices"] = list(range(11))
    full["payload_sha256"] = __import__("paper_eval.artifacts", fromlist=["payload_sha256"]).payload_sha256(
        {key: value for key, value in full.items() if key != "payload_sha256"}
    )
    with pytest.raises(ValueError, match="full base result required"):
        build_c8_extension_plan(base_plan=base, full_phase_result=full)


def test_c246_rejects_method_set_drift() -> None:
    with pytest.raises(ValueError):
        build_c246_plan(
            run_id="c246-baseline-test-001",
            history_source_sha256s=_sources(),
            interarrival_ns=100,
            service_reference_ns=120,
            execution_envelope_sha256="a" * 64,
            construction_model_identity_sha256="b" * 64,
            embedding_model_identity_sha256="c" * 64,
            methods=("U0-aligned", "A0-aligned", "P(C=2)-aligned"),
        )
