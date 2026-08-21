from __future__ import annotations

from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.artifacts import (
    ArtifactError,
    AttemptStore,
    SealEvidence,
)
from saturated_fixed_work_baseline_v1_2.contracts import ResumeIdentity


def _identity(namespace: str = "v1_2/B0/07741c45/run") -> ResumeIdentity:
    return ResumeIdentity(
        project_sha256="1" * 64,
        data_sha256="2" * 64,
        provider_sha256="3" * 64,
        resource_sha256="4" * 64,
        config_sha256="5" * 64,
        cache_sha256="6" * 64,
        namespace=namespace,
    )


def _seal(**changes: object) -> SealEvidence:
    values: dict[str, object] = {
        "episode_task_count": 12,
        "terminal_episode_task_count": 12,
        "open_spans": 0,
        "open_requests": 0,
        "open_transactions": 0,
        "orphan_tasks": 0,
        "unobserved_exceptions": 0,
        "service_idle": True,
        "canonical_snapshot_hashes": ("a" * 64, "a" * 64),
    }
    values.update(changes)
    return SealEvidence(**values)


def test_append_only_journal_recovers_complete_prefix_after_truncated_tail(
    tmp_path: Path,
) -> None:
    store = AttemptStore.create(tmp_path, _identity())
    store.append_event({"event": "BLOCK_STARTED", "source_sequence": None})
    store.append_event({"event": "EPISODE_TERMINAL", "source_sequence": 0})
    with store.journal_path.open("ab") as stream:
        stream.write(b'{"truncated":')
    recovered = store.recover_journal()
    assert [row["event"] for row in recovered.events] == [
        "BLOCK_STARTED",
        "EPISODE_TERMINAL",
    ]
    assert recovered.truncated_tail is True
    assert recovered.action == "START_NEW_ATTEMPT_DO_NOT_APPEND"


def test_failed_or_partial_attempt_is_never_overwritten(tmp_path: Path) -> None:
    first = AttemptStore.create(tmp_path, _identity())
    first.append_event({"event": "BLOCK_STARTED", "source_sequence": None})
    first.record_failure("timeout", {"stage": "durable_completion"})
    second = AttemptStore.create(tmp_path, _identity())
    assert first.root.name == "attempt-001"
    assert second.root.name == "attempt-002"
    assert first.failure_path.exists()
    with pytest.raises(ArtifactError, match="ARTIFACT_ALREADY_EXISTS"):
        first.record_failure("again", {})


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"terminal_episode_task_count": 11}, "EPISODE_TASKS_NOT_TERMINAL"),
        ({"open_spans": 1}, "OPEN_SPANS"),
        ({"open_requests": 1}, "OPEN_REQUESTS"),
        ({"open_transactions": 1}, "OPEN_TRANSACTIONS"),
        ({"orphan_tasks": 1}, "ORPHAN_TASKS"),
        ({"unobserved_exceptions": 1}, "UNOBSERVED_EXCEPTIONS"),
        ({"service_idle": False}, "SERVICE_NOT_IDLE"),
        (
            {"canonical_snapshot_hashes": ("a" * 64, "b" * 64)},
            "CANONICAL_SNAPSHOT_UNSTABLE",
        ),
    ],
)
def test_seal_rejects_incomplete_or_unstable_evidence(
    tmp_path: Path, changes: dict[str, object], code: str
) -> None:
    store = AttemptStore.create(tmp_path, _identity())
    with pytest.raises(ArtifactError, match=code):
        store.seal(_seal(**changes))
    assert not store.seal_path.exists()


def test_seal_is_atomic_verified_and_timeout_is_diagnosis_only(tmp_path: Path) -> None:
    store = AttemptStore.create(tmp_path, _identity())
    sealed = store.seal(_seal())
    assert sealed["status"] == "VALIDATED_SEALED"
    assert store.verify_seal()["payload_sha256"] == sealed["payload_sha256"]
    with pytest.raises(ArtifactError, match="ARTIFACT_ALREADY_EXISTS"):
        store.seal(_seal())

    timed_out = AttemptStore.create(tmp_path, _identity("v1_2/B1/07741c45/run"))
    diagnosis = timed_out.record_timeout(
        stage="durable_completion",
        terminal_tasks=10,
        expected_tasks=12,
    )
    assert diagnosis["status"] == "FAILED_TIMEOUT_ACTION_REQUIRED"
    assert diagnosis["next_action"] == "START_NEW_NAMESPACE_AND_ATTEMPT"
    assert not timed_out.seal_path.exists()

