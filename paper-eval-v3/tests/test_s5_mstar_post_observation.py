"""TDD contracts for M* publication-journal/post-observation binding."""

from __future__ import annotations

import copy

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_mstar_post_observation import (
    S5MStarPostObservationError,
    build_s5_mstar_post_observation,
    verify_s5_mstar_post_observation,
)


RUN_ID = "s5-mstar-20260816-201"
SOURCES = [
    {"source_sequence": index, "source_sha256": f"{index + 1:064x}"}
    for index in range(49)
]


def _native(*, violations: int = 0) -> dict[str, object]:
    counts = {
        "expected_source_count": 49,
        "durable_publication_count": 49,
        "episodic_count": 49,
        "lost_episodic_count": 0,
        "duplicate_episodic_count": 0,
        "unexpected_episodic_count": 0,
        "entity_count": 2,
        "relates_to_count": 1,
        "entity_namespace_escape_count": violations,
        "relation_namespace_escape_count": 0,
        "endpoint_escape_count": 0,
        "provenance_dangling_count": 0,
        "provenance_cross_namespace_count": 0,
        "valid_invalid_reversal_count": 0,
    }
    value: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.s5-native-post-observation.v1",
        "method": "M*",
        "status": "PASS" if violations == 0 else "INVARIANT_VIOLATIONS_OBSERVED",
        "run_id_sha256": __import__("hashlib").sha256(RUN_ID.encode()).hexdigest(),
        "namespace_sha256": __import__("hashlib").sha256(
            f"pev3-{RUN_ID}".encode()
        ).hexdigest(),
        "execution_identity_sha256": payload_sha256(
            {"run_id": RUN_ID, "namespace": f"pev3-{RUN_ID}"}
        ),
        "source_manifest_sha256": payload_sha256(SOURCES),
        "durable_publication_manifest_sha256": payload_sha256(SOURCES),
        "counts": counts,
        "source_classifications": [
            {**row, "classification": "OBSERVED_EXACTLY_ONCE"} for row in SOURCES
        ],
        "per_source_violation_counts": {
            str(index): violations if index == 0 else 0 for index in range(49)
        },
        "violation_classifications": [
            {"classification": field, "count": counts[field]}
            for field in (
                "entity_namespace_escape_count",
                "relation_namespace_escape_count",
                "endpoint_escape_count",
                "provenance_dangling_count",
                "provenance_cross_namespace_count",
                "valid_invalid_reversal_count",
            )
        ],
        "global_violation_total": violations,
    }
    value["observation_sha256"] = payload_sha256(value)
    return value


def _attempt_publications() -> list[dict[str, object]]:
    return [{"event_type": "publication", **row} for row in SOURCES]


def _journal() -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    operations: list[str] = []
    for row in SOURCES:
        operation = payload_sha256({"run_id": RUN_ID, **row})
        operations.append(operation)
        events.append(
            {
                "event_sequence": len(events),
                "event_type": "intent",
                "operation_id": operation,
                "source_sha256": row["source_sha256"],
            }
        )
    for index, (row, operation) in enumerate(zip(SOURCES, operations, strict=True)):
        commit = f"{index + 101:064x}"
        events.append(
            {
                "event_sequence": len(events),
                "event_type": "commit",
                "operation_id": operation,
                "source_sha256": row["source_sha256"],
                "commit_sha256": commit,
            }
        )
        events.append(
            {
                "event_sequence": len(events),
                "event_type": "publication",
                "operation_id": operation,
                "source_sha256": row["source_sha256"],
                "commit_sha256": commit,
                "recovered": False,
            }
        )
    return events


def _build(*, violations: int = 0) -> dict[str, object]:
    return build_s5_mstar_post_observation(
        run_id=RUN_ID,
        expected_sources=SOURCES,
        attempt_publications=_attempt_publications(),
        journal_events=_journal(),
        native_observation=_native(violations=violations),
    )


def test_exact_three_way_publication_binding_is_sealed_and_sanitized() -> None:
    checked = verify_s5_mstar_post_observation(
        _build(), expected_run_id=RUN_ID
    )

    assert checked["status"] == "PASS"
    assert checked["summary"] == {
        "expected_source_count": 49,
        "attempt_publication_count": 49,
        "journal_intent_count": 49,
        "journal_commit_count": 49,
        "journal_publication_count": 49,
        "journal_recovered_publication_count": 0,
        "global_violation_total": 0,
    }
    rendered = repr(checked)
    assert RUN_ID not in rendered
    assert "namespace" not in checked
    assert "commit_sha256" not in rendered


@pytest.mark.parametrize("mutation", ["missing_commit", "source_conflict"])
def test_incomplete_or_conflicting_journal_fails_closed(mutation: str) -> None:
    journal = _journal()
    if mutation == "missing_commit":
        del journal[49]
        for index, event in enumerate(journal):
            event["event_sequence"] = index
    else:
        journal[49]["source_sha256"] = "f" * 64

    with pytest.raises(S5MStarPostObservationError, match="journal"):
        build_s5_mstar_post_observation(
            run_id=RUN_ID,
            expected_sources=SOURCES,
            attempt_publications=_attempt_publications(),
            journal_events=journal,
            native_observation=_native(),
        )


def test_direct_invariant_observation_is_retained_not_relabelled_pass() -> None:
    checked = verify_s5_mstar_post_observation(
        _build(violations=2), expected_run_id=RUN_ID
    )
    assert checked["status"] == "DIRECT_INVARIANT_VIOLATION_OBSERVED"
    assert checked["summary"]["global_violation_total"] == 2


def test_resealed_operation_manifest_cannot_cross_bind_to_another_run() -> None:
    artifact = _build()
    artifact["operation_manifest_sha256"] = "f" * 64
    artifact["post_observation_sha256"] = payload_sha256(
        {key: value for key, value in artifact.items() if key != "post_observation_sha256"}
    )
    with pytest.raises(S5MStarPostObservationError):
        verify_s5_mstar_post_observation(artifact, expected_run_id=RUN_ID)

