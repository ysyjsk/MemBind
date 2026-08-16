"""Offline TDD for the sanitized S5 P*(C=2) progress inspector."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_durable_attempt_store import S5AttemptStore
from paper_eval.s5_pstar_post_observation import build_s5_pstar_post_observation
from paper_eval.s5_pstar_progress import (
    S5PStarProgressPaths,
    inspect_s5_pstar_progress,
)


RUN_ID = "s5-p-star-20260816-901"
SOURCES = tuple(f"{index + 1:064x}" for index in range(4))


def _paths(root: Path) -> S5PStarProgressPaths:
    return S5PStarProgressPaths(
        controller_root=root / "controller",
        attempt_root=root / "attempt",
        post_observation=root / "post_observation.json",
        final_result=root / "S5_PSTAR_RESULT.json",
    )


def _write_controller(root: Path) -> None:
    root.mkdir(parents=True)
    events = []
    for sequence, event_type in enumerate(
        (
            "authority_consumed",
            "runtime_constructed",
            "runtime_ready",
            "native_runner_started",
            "runtime_closed",
            "raw_runner_evidence_complete",
        )
    ):
        event = {
            "schema_version": "membind.paper-eval-v3.s5-pstar-controller-event.v1",
            "event_sequence": sequence,
            "event_type": event_type,
            "run_id": RUN_ID,
            "method": "P*",
        }
        events.append({"event": event, "event_sha256": payload_sha256(event)})
    (root / "events.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in events),
        encoding="ascii",
    )
    checkpoint = {
        "schema_version": "membind.paper-eval-v3.s5-pstar-controller-checkpoint.v1",
        "run_id": RUN_ID,
        "event_count": len(events),
        "status": "controller_complete_evidence_only",
        "native_attempt_status": "scientific_outcome_complete",
        "scientific_outcome_candidate": True,
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
        "next_method_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }
    checkpoint["checkpoint_sha256"] = payload_sha256(checkpoint)
    (root / "checkpoint.json").write_text(
        json.dumps(checkpoint, sort_keys=True) + "\n", encoding="ascii"
    )


def _scientific_treatment_attempt(root: Path) -> None:
    store = S5AttemptStore.create(
        root,
        run_id=RUN_ID,
        method="P*",
        production_core_identity_sha256="a" * 64,
        source_sha256s=SOURCES,
    )
    rows = (
        ("publication", 0, None),
        ("source_terminal", 0, "PUBLISHED"),
        ("source_terminal", 1, "TREATMENT_FAILED"),
        (
            "source_terminal",
            2,
            "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE",
        ),
        (
            "source_terminal",
            3,
            "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE",
        ),
    )
    events: list[dict[str, object]] = []
    for sequence, (event_type, source, classification) in enumerate(rows):
        event: dict[str, object] = {
            "event_sequence": sequence,
            "event_type": event_type,
            "run_id": RUN_ID,
            "method": "P*",
            "source_sequence": source,
            "source_sha256": SOURCES[source],
        }
        if classification is not None:
            event["terminal_classification"] = classification
        store.append_event(event)
        events.append(event)
    store.finalize(
        {
            "schema_version": "membind.paper-eval-v3.test-pstar-evidence.v1",
            "run_id": RUN_ID,
            "method": "P*",
            "production_core_identity_sha256": "a" * 64,
            "status": "SCIENTIFIC_OUTCOME_COMPLETE",
            "mergeable": True,
            "events": events,
        }
    )


def test_not_started_projection_is_fixed_and_never_authorizes_resume(
    tmp_path: Path,
) -> None:
    progress = inspect_s5_pstar_progress(_paths(tmp_path))

    assert progress == {
        "schema_version": "membind.paper-eval-v3.s5-pstar-progress.v1",
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
            "terminal_source_count": 0,
            "published_terminal_count": 0,
            "treatment_failed_terminal_count": 0,
            "censored_terminal_count": 0,
        },
        "failure": {"stage": None, "error_class": None},
        "post_observation_status": "NOT_AVAILABLE",
        "final_result_status": "NOT_AVAILABLE",
        "resume_authorized": False,
    }


def test_complete_treatment_failure_is_scientific_progress_not_corruption(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_controller(paths.controller_root)
    _scientific_treatment_attempt(paths.attempt_root)

    progress = inspect_s5_pstar_progress(paths)

    assert progress["controller"] == {
        "status": "controller_complete_evidence_only",
        "last_stage": "raw_runner_evidence_complete",
        "event_count": 6,
    }
    assert progress["attempt"] == {
        "status": "scientific_outcome_complete",
        "event_count": 5,
        "published_count": 1,
        "last_published_source_sequence": 0,
        "terminal_source_count": 4,
        "published_terminal_count": 1,
        "treatment_failed_terminal_count": 1,
        "censored_terminal_count": 2,
    }
    assert progress["failure"] == {
        "stage": "native_execution",
        "error_class": None,
    }
    assert progress["resume_authorized"] is False
    rendered = repr(progress)
    assert RUN_ID not in rendered
    assert "pev3-" not in rendered


def test_post_and_final_statuses_are_read_only_verifier_projections(
    tmp_path: Path, monkeypatch
) -> None:
    paths = _paths(tmp_path)
    expected = [
        {"source_sequence": index, "source_sha256": f"{index + 1:064x}"}
        for index in range(49)
    ]
    post = build_s5_pstar_post_observation(
        run_id=RUN_ID,
        expected_sources=expected,
        source_terminals=[
            {**row, "terminal_classification": "PUBLISHED"} for row in expected
        ],
        observed_episodics=expected,
        violation_counts={},
        per_source_violation_counts={str(index): 0 for index in range(49)},
    )
    paths.post_observation.write_text(
        json.dumps(post, sort_keys=True) + "\n", encoding="ascii"
    )
    paths.final_result.write_text('{"opaque": "sealed"}\n', encoding="ascii")

    from paper_eval import s5_pstar_result_finalizer

    calls: list[object] = []

    def verify(value):
        calls.append(value)
        return {"payload": {"verdict": "SCIENTIFIC_OUTCOME_COMPLETE"}}

    monkeypatch.setattr(
        s5_pstar_result_finalizer,
        "verify_s5_pstar_result",
        verify,
        raising=False,
    )

    progress = inspect_s5_pstar_progress(paths)

    assert calls == [{"opaque": "sealed"}]
    assert progress["post_observation_status"] == "PASS"
    assert progress["final_result_status"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert progress["failure"] == {"stage": None, "error_class": None}


def test_corrupt_evidence_is_sanitized_and_cannot_be_resumed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.attempt_root.mkdir(parents=True)
    (paths.attempt_root / "manifest.json").write_text(
        "private namespace, prompt, and credential\n", encoding="utf-8"
    )

    progress = inspect_s5_pstar_progress(paths)

    assert progress["attempt"]["status"] == "INVALID_EVIDENCE"
    assert progress["failure"]["stage"] == "attempt_inspection"
    assert progress["failure"]["error_class"].endswith("S5StoreError")
    assert "private namespace" not in repr(progress)
    assert "credential" not in repr(progress)
    assert progress["resume_authorized"] is False
