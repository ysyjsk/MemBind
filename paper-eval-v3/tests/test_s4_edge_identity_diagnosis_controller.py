"""Offline controller gates for the single source-7 D2 execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from paper_eval.s4_edge_identity_diagnosis_controller import (
    ControllerError,
    compose_replay_spec,
    construct_runtime_without_event_loop,
    validate_retry005_state,
)


def _phase(*, mode: str) -> dict:
    capture = mode == "capture"
    return {
        "run_id": (
            "s4-d0-capture-20260815-005"
            if capture
            else "s4-d0-replay-20260815-005"
        ),
        "payload": {
            "stage": "S4",
            "phase": "U0_CAPTURE" if capture else "D0_READ_ONLY_REPLAY",
            "run_id": (
                "s4-d0-capture-20260815-005"
                if capture
                else "s4-d0-replay-20260815-005"
            ),
            "history_id": "07741c45",
            "namespace": (
                "pev3-s4-u0-capture-20260815-005"
                if capture
                else "pev3-s4-d0-replay-20260815-005"
            ),
            "mode": mode,
            "status": "PASS" if capture else "INCOMPLETE",
            "mergeable": capture,
            "completed_source_sequences": list(range(49)) if capture else list(range(7)),
            "error_class": None if capture else "CandidateRemapError",
        },
    }


def test_retry005_state_gate_requires_exact_failed_prefix() -> None:
    checkpoint = {
        "run_id": "s4-d0-replay-20260815-005",
        "phase": "D0_READ_ONLY_REPLAY",
        "status": "incomplete",
        "completed_source_sequences": list(range(7)),
        "namespace": "pev3-s4-d0-replay-20260815-005",
        "namespace_state": {
            "node_count": 32,
            "relationship_count": 48,
            "episode_names": [f"episode-{index}" for index in range(7)],
        },
    }
    events = [
        {
            "event_type": "failure",
            "source_sequence": 7,
            "error_class": "CandidateRemapError",
            "error_code": "AMBIGUOUS_CANDIDATE_IDENTITY",
            "failure_stage": "add_episode",
        }
    ]

    assert validate_retry005_state(
        capture_phase=_phase(mode="capture"),
        replay_phase=_phase(mode="replay"),
        replay_checkpoint=checkpoint,
        replay_events=events,
    ) == {
        "completed_prefix_count": 7,
        "failure_source_sequence": 7,
        "namespace_node_count": 32,
        "namespace_relationship_count": 48,
    }

    events[0]["error_code"] = "OTHER"
    with pytest.raises(ControllerError):
        validate_retry005_state(
            capture_phase=_phase(mode="capture"),
            replay_phase=_phase(mode="replay"),
            replay_checkpoint=checkpoint,
            replay_events=events,
        )


def test_replay_spec_is_exact_and_has_no_cleanup_or_resume_mode() -> None:
    authority = {
        "execution_identity": {
            "history_id": "07741c45",
            "namespace": "pev3-s4-d0-replay-20260815-005",
            "replay_run_id": "s4-d0-replay-20260815-005",
        }
    }

    assert compose_replay_spec(authority) == {
        "phase": "D0_READ_ONLY_REPLAY",
        "run_id": "s4-d0-replay-20260815-005",
        "history_id": "07741c45",
        "namespace": "pev3-s4-d0-replay-20260815-005",
        "method": "D0",
        "mode": "replay",
        "cache_id": "s4-d0-remap-07741c45-20260815-005",
    }


def test_runtime_construction_occurs_without_active_event_loop() -> None:
    driver = SimpleNamespace(_init_task=None)
    graph = SimpleNamespace(driver=driver, clients=SimpleNamespace(driver=driver))
    runtime = SimpleNamespace(graph=graph)

    assert construct_runtime_without_event_loop(lambda: runtime) is runtime


@pytest.mark.asyncio
async def test_runtime_construction_rejects_active_event_loop() -> None:
    with pytest.raises(ControllerError, match="event loop"):
        construct_runtime_without_event_loop(lambda: object())
