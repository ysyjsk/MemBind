"""Offline TDD for the sanitized, read-only S5 A0 progress inspector."""

from __future__ import annotations

import asyncio
from pathlib import Path

from paper_eval.s5_a0_controller import execute_s5_a0_controller
from paper_eval.s5_a0_progress import (
    S5A0ProgressPaths,
    inspect_s5_a0_progress,
)
from paper_eval.s5_a0_result_finalizer import finalize_s5_a0_result
from tests.test_s5_a0_controller import _chain, _dependencies
from tests.test_s5_a0_result_finalizer import _completed_chain


def _paths(root: Path) -> S5A0ProgressPaths:
    return S5A0ProgressPaths(
        controller_root=root / "controller",
        attempt_root=root / "attempt",
        post_observation=root / "post-observation.json",
        final_result=root / "result.json",
    )


def test_not_started_projection_is_stable_and_has_no_resume_authority(
    tmp_path: Path,
) -> None:
    progress = inspect_s5_a0_progress(_paths(tmp_path))

    assert progress == {
        "schema_version": "membind.paper-eval-v3.s5-a0-progress.v1",
        "controller": {
            "status": "NOT_STARTED",
            "last_stage": None,
            "event_count": 0,
        },
        "attempt": {
            "status": "NOT_STARTED",
            "event_count": 0,
            "published_count": 0,
            "last_published_source_sequence": None,
        },
        "failure": {"stage": None, "error_class": None},
        "post_observation_status": "NOT_AVAILABLE",
        "final_result_status": "NOT_AVAILABLE",
        "resume_authorized": False,
    }


def test_controller_failure_reports_only_sanitized_checkpoint_state(
    tmp_path: Path,
) -> None:
    controller_paths, episodes = _chain(tmp_path)
    trace: list[str] = []
    result = asyncio.run(
        execute_s5_a0_controller(
            paths=controller_paths,
            episodes=episodes,
            git_commit="deadbeef",
            **_dependencies(
                trace,
                runtime_error=RuntimeError(
                    "private namespace, endpoint, and credential material"
                ),
            ),
        )
    )
    assert result["status"] == "incomplete_non_mergeable"

    progress = inspect_s5_a0_progress(
        S5A0ProgressPaths(
            controller_root=controller_paths.controller_root,
            attempt_root=controller_paths.attempt_root,
            post_observation=tmp_path / "missing-post.json",
            final_result=tmp_path / "missing-result.json",
        )
    )

    assert progress["controller"] == {
        "status": "incomplete_non_mergeable",
        "last_stage": "controller_failure",
        "event_count": 2,
    }
    assert progress["attempt"]["status"] == "NOT_STARTED"
    assert progress["failure"] == {
        "stage": "runtime_construction",
        "error_class": "builtins.RuntimeError",
    }
    rendered = repr(progress)
    assert "private namespace" not in rendered
    assert "credential" not in rendered
    assert "pev3-" not in rendered
    assert "s5-a0-20260816-101" not in rendered
    assert progress["resume_authorized"] is False


def test_complete_chain_reports_counts_without_execution_identity(
    tmp_path: Path,
) -> None:
    finalizer_paths = _completed_chain(tmp_path)
    final = finalize_s5_a0_result(paths=finalizer_paths, git_commit="deadbeef")
    assert final["payload"]["verdict"] == "PASS"

    progress = inspect_s5_a0_progress(
        S5A0ProgressPaths(
            controller_root=finalizer_paths.controller_root,
            attempt_root=finalizer_paths.attempt_root,
            post_observation=finalizer_paths.post_observation,
            final_result=finalizer_paths.result,
        )
    )

    assert progress["controller"] == {
        "status": "controller_complete_evidence_only",
        "last_stage": "raw_runner_evidence_complete",
        "event_count": 6,
    }
    assert progress["attempt"] == {
        "status": "complete",
        "event_count": 148,
        "published_count": 49,
        "last_published_source_sequence": 48,
    }
    assert progress["failure"] == {"stage": None, "error_class": None}
    assert progress["post_observation_status"] == "PASS"
    assert progress["final_result_status"] == "PASS"
    assert progress["resume_authorized"] is False
    rendered = repr(progress)
    assert "pev3-" not in rendered
    assert "s5-a0-20260816-101" not in rendered


def test_corrupt_evidence_is_classified_without_leaking_parser_details(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.controller_root.mkdir(parents=True)
    (paths.controller_root / "events.jsonl").write_text(
        "private raw namespace and secret\n", encoding="utf-8"
    )

    progress = inspect_s5_a0_progress(paths)

    assert progress["controller"]["status"] == "INVALID_EVIDENCE"
    assert progress["controller"]["last_stage"] == "inspection_failed"
    assert progress["failure"]["stage"] == "controller_inspection"
    assert progress["failure"]["error_class"].endswith("S5A0ControllerError")
    assert "private raw" not in repr(progress)
    assert progress["resume_authorized"] is False
