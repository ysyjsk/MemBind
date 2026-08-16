"""TDD contracts for side-effect-free M* durable-progress inspection."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_durable_attempt_store import S5AttemptStore
from paper_eval.s5_mstar_progress import (
    S5MStarProgressPaths,
    inspect_s5_mstar_progress,
)


def _paths(root: Path) -> S5MStarProgressPaths:
    return S5MStarProgressPaths(
        controller_root=root / "controller",
        attempt_root=root / "attempt",
        publication_journal=root / "attempt" / "publication_journal.jsonl",
        post_observation=root / "post_observation.json",
        final_result=root / "S5_MSTAR_RESULT.json",
    )


def test_not_started_projection_is_fixed_and_never_authorizes_resume(tmp_path: Path) -> None:
    progress = inspect_s5_mstar_progress(_paths(tmp_path))
    assert progress == {
        "schema_version": "membind.paper-eval-v3.s5-mstar-progress.v1",
        "controller": {"status": "NOT_STARTED", "last_stage": None, "event_count": 0},
        "attempt": {
            "status": "NOT_STARTED",
            "event_count": 0,
            "intent_count": 0,
            "prepared_count": 0,
            "commit_returned_count": 0,
            "published_count": 0,
            "last_published_source_sequence": None,
        },
        "publication_journal": {
            "status": "NOT_STARTED",
            "intent_count": 0,
            "commit_count": 0,
            "publication_count": 0,
            "recovered_publication_count": 0,
        },
        "failure": {"stage": None, "error_class": None},
        "post_observation_status": "NOT_AVAILABLE",
        "final_result_status": "NOT_AVAILABLE",
        "resume_authorized": False,
    }


def test_running_prefix_reports_attempt_and_journal_without_private_identity(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    store = S5AttemptStore.create(
        paths.attempt_root,
        run_id="s5-mstar-20260816-203",
        method="M*",
        production_core_identity_sha256="a" * 64,
        source_sha256s=["b" * 64, "c" * 64],
    )
    for sequence, event_type in enumerate(("intent", "prepared", "commit_returned", "publication")):
        store.append_event(
            {
                "event_sequence": sequence,
                "event_type": event_type,
                "run_id": "s5-mstar-20260816-203",
                "method": "M*",
                "source_sequence": 0,
                "source_sha256": "b" * 64,
            }
        )
    paths.publication_journal.write_text(
        "\n".join(
            json.dumps({"event": event, "event_sha256": payload_sha256(event)})
            for event in (
                {
                    "event_sequence": 0,
                    "event_type": "intent",
                    "operation_id": "d" * 64,
                    "source_sha256": "b" * 64,
                },
                {
                    "event_sequence": 1,
                    "event_type": "commit",
                    "operation_id": "d" * 64,
                    "source_sha256": "b" * 64,
                    "commit_sha256": "e" * 64,
                },
                {
                    "event_sequence": 2,
                    "event_type": "publication",
                    "operation_id": "d" * 64,
                    "source_sha256": "b" * 64,
                    "commit_sha256": "e" * 64,
                    "recovered": False,
                },
            )
        )
        + "\n",
        encoding="ascii",
    )

    progress = inspect_s5_mstar_progress(paths)
    assert progress["attempt"]["status"] == "running"
    assert progress["attempt"]["published_count"] == 1
    assert progress["publication_journal"]["publication_count"] == 1
    assert progress["resume_authorized"] is False
    assert "s5-mstar-20260816-203" not in repr(progress)


def test_corrupt_journal_is_sanitized_and_cannot_authorize_resume(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.publication_journal.parent.mkdir(parents=True)
    paths.publication_journal.write_text("private prompt and credential\n", encoding="utf-8")
    progress = inspect_s5_mstar_progress(paths)
    assert progress["publication_journal"]["status"] == "INVALID_EVIDENCE"
    assert progress["failure"]["stage"] == "publication_journal_inspection"
    assert "private prompt" not in repr(progress)
    assert progress["resume_authorized"] is False

