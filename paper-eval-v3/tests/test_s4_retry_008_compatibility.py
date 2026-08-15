"""TDD for sealing retry-007 as the predecessor of compatibility retry-008."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.s4_retry_008_compatibility import (
    verify_retry_007_duplicate_uuid_failure,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
CAPTURE = NATIVE / "runs/s4-d0-capture-20260815-007"
LOG = PROJECT / "logs/S4_D0_SIDECAR_SMOKE_20260815_007.log"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify(*, checkpoint: dict | None = None, phase_result: dict | None = None):
    return verify_retry_007_duplicate_uuid_failure(
        checkpoint=checkpoint or _load(CAPTURE / "checkpoint.json"),
        phase_result=phase_result or _load(CAPTURE / "phase_result.json"),
        execution_log=LOG.read_text(encoding="utf-8"),
        replay_checkpoint_exists=False,
        replay_phase_result_exists=False,
        smoke_result_exists=False,
    )


def test_retry_007_failure_is_exact_bounded_duplicate_uuid_predecessor() -> None:
    evidence = _verify()

    assert evidence == {
        "attempt_id": "007",
        "failure_stage": "U0_CAPTURE/add_episode/source_sequence=12",
        "error_class": "CandidateSidecarRuntimeError",
        "error_code": "DUPLICATE_UUID_CONFLICT_POLICY_TOO_STRICT",
        "completed_episode_count": 12,
        "expected_episode_count": 49,
        "mergeable": False,
        "replay_started": False,
        "smoke_result_exists": False,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda checkpoint, _result: checkpoint.update(status="completed"),
        lambda checkpoint, _result: checkpoint["completed_source_sequences"].pop(),
        lambda _checkpoint, result: result["payload"].update(mergeable=True),
        lambda _checkpoint, result: result["payload"].update(
            error_class="OtherError"
        ),
    ],
)
def test_retry_007_predecessor_verifier_fails_closed_on_evidence_drift(
    mutation,
) -> None:
    checkpoint = _load(CAPTURE / "checkpoint.json")
    phase_result = _load(CAPTURE / "phase_result.json")
    mutation(checkpoint, phase_result)

    with pytest.raises(ValueError):
        _verify(checkpoint=checkpoint, phase_result=phase_result)


def test_retry_007_predecessor_requires_exact_trace_and_no_downstream_result() -> None:
    checkpoint = _load(CAPTURE / "checkpoint.json")
    phase_result = _load(CAPTURE / "phase_result.json")
    kwargs = {
        "checkpoint": checkpoint,
        "phase_result": phase_result,
        "execution_log": "different failure",
        "replay_checkpoint_exists": False,
        "replay_phase_result_exists": False,
        "smoke_result_exists": False,
    }
    with pytest.raises(ValueError, match="trace"):
        verify_retry_007_duplicate_uuid_failure(**kwargs)

    kwargs["execution_log"] = LOG.read_text(encoding="utf-8")
    kwargs["replay_checkpoint_exists"] = True
    with pytest.raises(ValueError, match="downstream"):
        verify_retry_007_duplicate_uuid_failure(**kwargs)
