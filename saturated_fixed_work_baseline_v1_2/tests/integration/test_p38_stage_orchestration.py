from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.live import FORMAL_ORDER
from saturated_fixed_work_baseline_v1_2.contracts import ResumeIdentity
from saturated_fixed_work_baseline_v1_2.stage_orchestration import (
    StageOrchestrationError,
    execute_formal_main_stage,
    execute_qualification_stage,
    execute_rehearsal_stage,
)


def _protocol(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "protocol_manifest.json").write_text(
        json.dumps({"run_id": "sfwb-v1-2-stage-test"}) + "\n",
        encoding="utf-8",
    )


def _episode_loader(
    repository_root: Path, history_id: str, namespace: str
) -> tuple[Any, ...]:
    del repository_root
    from saturated_fixed_work_baseline_v1_2.contracts import EpisodeInput

    count = {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}[
        history_id
    ]
    return tuple(
        EpisodeInput(
            history_id=history_id,
            session_id=f"session-{index}",
            source_sequence=index,
            source_hash=f"{index + 1:064x}",
            reference_time="2023-01-01T00:00:00Z",
            body=f"body-{index}",
            namespace=namespace,
        )
        for index in range(count)
    )


def _graph(episodes: tuple[Any, ...]) -> dict[str, object]:
    return {
        "entities": [],
        "edges": [],
        "episodes": [
            {
                "source_sequence": episode.source_sequence,
                "source_hash": episode.source_hash,
                "session_id": episode.session_id,
            }
            for episode in episodes
        ],
    }


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


@pytest.mark.asyncio
async def test_l1_runs_exact_prefix_order_and_seals_canonical_diagnostic(
    repository_root: Path, tmp_path: Path
) -> None:
    _protocol(tmp_path)
    calls: list[tuple[str, str, int]] = []
    prepared: list[str] = []

    async def execute_block(**kwargs: Any) -> dict[str, object]:
        block = kwargs["block"]
        episodes = tuple(kwargs["episodes"])
        calls.append((block.block_id, block.method.value, len(episodes)))
        return {
            "valid": True,
            "created_sequences": list(range(12)),
            "feeder_workload_await_count": (
                12 if block.method.value == "B0_NATIVE_SERIAL" else 0
            ),
            "application_gate_count": 0,
            "artificial_sleep_count": 0,
            "configured_max_inflight": None,
            "canonical_graph": _graph(episodes),
        }

    sealed: list[dict[str, object]] = []
    result = await execute_qualification_stage(
        repository_root=repository_root,
        run_root=tmp_path,
        dependencies=object(),
        instrumentation_aa={"qualified": True, "overhead_fraction": 0.01},
        prepare_block=lambda block: prepared.append(block.block_id) or True,
        qa_read_only_probe=lambda blocks: len(blocks) == 3,
        preflight_verifier=lambda root: {"verified": True, "preflight_passed": True},
        block_executor=execute_block,
        episode_loader=_episode_loader,
        source_token_counter=lambda root, episodes: len(episodes) * 10,
        identity_builder=_identity,
        seal_writer=lambda root, evidence: sealed.append(dict(evidence))
        or {"qualification_passed": True},
    )

    assert calls == [
        ("qualification-b0-a", "B0_NATIVE_SERIAL", 12),
        ("qualification-b0-b", "B0_NATIVE_SERIAL", 12),
        ("qualification-b1", "B1_NAIVE_WHOLE_UPDATE_ASYNC", 12),
    ]
    assert prepared == [row[0] for row in calls]
    assert result["qualification_passed"] is True
    assert sealed[0]["serial_serial_12_scope"] == "12_EPISODE_QUALIFICATION_ONLY"
    assert sealed[0]["canonical_diffs_emitted"] is True
    assert (tmp_path / "qualification/l1-attempt-001/qualification_diagnostics.json").is_file()


@pytest.mark.asyncio
async def test_l2_is_separate_and_l3_runs_exact_formal_order_after_each_preparation(
    repository_root: Path, tmp_path: Path
) -> None:
    _protocol(tmp_path)
    events: list[tuple[str, str]] = []

    async def execute_block(**kwargs: Any) -> dict[str, object]:
        block = kwargs["block"]
        events.append(("execute", block.block_id))
        return {"valid": True}

    async def prepare(block: Any) -> bool:
        events.append(("prepare", block.block_id))
        return True

    rehearsal = await execute_rehearsal_stage(
        repository_root=repository_root,
        run_root=tmp_path,
        dependencies=object(),
        prepare_block=prepare,
        qa_read_only_probe=lambda blocks: len(blocks) == 2,
        qualification_verifier=lambda root: {
            "verified": True,
            "qualification_passed": True,
        },
        block_executor=execute_block,
        episode_loader=_episode_loader,
        source_token_counter=lambda root, episodes: 104_014,
        identity_builder=_identity,
    )
    assert rehearsal["rehearsal_passed"] is True
    assert (tmp_path / "rehearsal/rehearsal_seal.json").is_file()
    assert all("rehearsal" in block_id for _, block_id in events)

    events.clear()
    selected: list[tuple[str, str]] = []
    result = await execute_formal_main_stage(
        repository_root=repository_root,
        run_root=tmp_path,
        dependencies=object(),
        prepare_block=prepare,
        qualification_verifier=lambda root: {
            "verified": True,
            "qualification_passed": True,
        },
        block_executor=execute_block,
        episode_loader=_episode_loader,
        source_token_counter=lambda root, episodes: 100_000 + len(episodes),
        identity_builder=_identity,
        formal_seal_writer=lambda root: {
            "valid_construction_blocks": 8,
            "formal_construction_calls": 8,
        },
    )
    for expected, (prepare_event, execute_event) in zip(
        FORMAL_ORDER, zip(events[::2], events[1::2], strict=True), strict=True
    ):
        assert prepare_event[0] == "prepare"
        assert execute_event[0] == "execute"
        assert prepare_event[1] == execute_event[1]
        selected.append((expected[0], expected[1].value))
    assert len(events) == 16
    assert result["valid_construction_blocks"] == 8
    assert selected == [(history, method.value) for history, method in FORMAL_ORDER]


@pytest.mark.asyncio
async def test_live_stages_do_not_call_executor_before_prerequisite_seals(
    repository_root: Path, tmp_path: Path
) -> None:
    _protocol(tmp_path)
    called = False

    async def execute_block(**kwargs: Any) -> dict[str, object]:
        nonlocal called
        called = True
        return {"valid": True}

    with pytest.raises(StageOrchestrationError, match="PREFLIGHT_NOT_VERIFIED"):
        await execute_qualification_stage(
            repository_root=repository_root,
            run_root=tmp_path,
            dependencies=object(),
            instrumentation_aa={"qualified": True},
            prepare_block=lambda block: True,
            qa_read_only_probe=lambda blocks: True,
            preflight_verifier=lambda root: {"verified": False},
            block_executor=execute_block,
            episode_loader=_episode_loader,
            source_token_counter=lambda root, episodes: 1,
            identity_builder=_identity,
        )
    assert called is False

    with pytest.raises(StageOrchestrationError, match="QUALIFICATION_NOT_VERIFIED"):
        await execute_formal_main_stage(
            repository_root=repository_root,
            run_root=tmp_path,
            dependencies=object(),
            prepare_block=lambda block: True,
            qualification_verifier=lambda root: {"verified": False},
            block_executor=execute_block,
            episode_loader=_episode_loader,
            source_token_counter=lambda root, episodes: 1,
            identity_builder=_identity,
            formal_seal_writer=lambda root: {},
        )
    assert called is False
