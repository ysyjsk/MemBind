"""TDD contracts for read-only aligned retrieval and correctness observation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import AlignedBlockArtifactStore
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.aligned_quality_live import (
    AlignedQualityLiveError,
    AlignedQualityLiveHooks,
    NamespaceCorrectnessObservation,
    SessionRetrievalCase,
    SessionRetrievalRequest,
    observe_aligned_quality_live,
    summarize_namespace_correctness,
)


def _plan(*, source_counts: tuple[int, int, int, int] = (3, 3, 3, 3)) -> dict[str, object]:
    return verify_aligned_development_plan(
        build_aligned_development_plan(
            aligned_run_id="aligned-quality-live-test-001",
            history_source_sha256s={
                history_id: [
                    payload_sha256(
                        {"history_id": history_id, "source_sequence": sequence}
                    )
                    for sequence in range(source_counts[index])
                ]
                for index, history_id in enumerate(ALIGNED_DEVELOPMENT_HISTORIES)
            },
            interarrival_ns=10,
            shared_execution_envelope_sha256="a" * 64,
        )
    )


def _complete(root: Path, *, plan: dict[str, object], block_index: int = 0) -> None:
    store = AlignedBlockArtifactStore.create(
        root,
        verified_plan=plan,
        block_index=block_index,
        execution_identity_sha256="b" * 64,
    )
    count = int(plan["blocks"][block_index]["source_count"])
    for sequence in range(count):
        for offset, event_type in enumerate(
            ("ARRIVAL", "ENQUEUED", "SERVICE_STARTED", "PUBLICATION_DURABLE")
        ):
            store.append_lifecycle(
                sequence,
                event_type=event_type,
                timestamp_ns=100 + sequence * 10 + offset,
            )


def _case() -> SessionRetrievalCase:
    return SessionRetrievalCase(
        question_sha256=payload_sha256({"question": "private prompt"}),
        query="private benchmark question",
        gold_session_ids=("session-1", "session-2"),
        allowed_session_ids=tuple(f"session-{index}" for index in range(12)),
    )


def test_read_only_quality_observation_returns_nq_quality_input_and_never_calls_reader_or_judge(
    tmp_path: Path,
) -> None:
    plan = _plan()
    root = tmp_path / "block"
    _complete(root, plan=plan)
    expected_sources = tuple(plan["history_source_sha256s"]["07741c45"])
    calls: list[object] = []

    async def retrieve(
        *, namespace: str, request: SessionRetrievalRequest
    ) -> tuple[str, ...]:
        calls.append(("retrieve", namespace, request))
        assert request.question_sha256 == _case().question_sha256
        assert request.query == "private benchmark question"
        return ("session-0", *(f"session-{index}" for index in range(3, 12)))

    async def observe(
        *, namespace: str, expected_source_sha256s: tuple[str, ...]
    ) -> NamespaceCorrectnessObservation:
        calls.append(("observe", namespace, expected_source_sha256s))
        return NamespaceCorrectnessObservation(
            observed_source_sha256s=expected_source_sha256s,
            namespace_escape_count=0,
        )

    before = {path.name: path.read_bytes() for path in root.iterdir()}
    result = asyncio.run(
        observe_aligned_quality_live(
            root,
            verified_plan=plan,
            block_index=0,
            retrieval_cases=(_case(),),
            hooks=AlignedQualityLiveHooks(
                retrieve_sessions=retrieve,
                observe_namespace_correctness=observe,
            ),
        )
    )
    after = {path.name: path.read_bytes() for path in root.iterdir()}

    assert before == after
    assert [item[0] for item in calls] == ["retrieve", "observe"]
    assert result["quality_and_correctness"]["qa_accuracy"] is None
    assert result["quality_and_correctness"]["quality_status"] == (
        "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE"
    )
    assert result["quality_and_correctness"]["evidence_recall_at_10"] == 0.0
    assert result["quality_and_correctness"]["direct_violations"] == 0
    assert result["retrieval_summary"]["evidence_recall_at_10"] == 0.0
    assert result["correctness_summary"] == {
        "lost_episodic_count": 0,
        "duplicate_episodic_count": 0,
        "unexpected_episodic_count": 0,
        "episodic_namespace_escape_count": 0,
        "direct_violations": 0,
    }
    assert "private benchmark question" not in repr(result)
    assert "session-1" not in repr(result)


def test_retrieval_metric_is_common_read_only_recall_all_at_10_and_correctness_is_exact(
    tmp_path: Path,
) -> None:
    plan = _plan()
    root = tmp_path / "block"
    _complete(root, plan=plan)
    expected_sources = tuple(plan["history_source_sha256s"]["07741c45"])

    async def retrieve(**_kwargs: object) -> tuple[str, ...]:
        return (
            "session-2",
            "session-1",
            "session-3",
            "session-4",
            "session-5",
            "session-6",
            "session-7",
            "session-8",
            "session-9",
            "session-10",
        )

    async def observe(**_kwargs: object) -> NamespaceCorrectnessObservation:
        return NamespaceCorrectnessObservation(
            observed_source_sha256s=(
                expected_sources[0],
                expected_sources[0],
                "f" * 64,
            ),
            namespace_escape_count=2,
        )

    result = asyncio.run(
        observe_aligned_quality_live(
            root,
            verified_plan=plan,
            block_index=0,
            retrieval_cases=(_case(),),
            hooks=AlignedQualityLiveHooks(
                retrieve_sessions=retrieve,
                observe_namespace_correctness=observe,
            ),
        )
    )

    assert result["retrieval_summary"]["evidence_recall_at_10"] == 1.0
    assert result["correctness_summary"] == {
        "lost_episodic_count": 2,
        "duplicate_episodic_count": 1,
        "unexpected_episodic_count": 1,
        "episodic_namespace_escape_count": 2,
        "direct_violations": 6,
    }


@pytest.mark.parametrize("source_count", [49, 49, 46, 44])
def test_generic_correctness_observer_has_no_hard_coded_history_cardinality(
    source_count: int,
) -> None:
    expected = tuple(f"{sequence + 1:064x}" for sequence in range(source_count))
    observed = (expected[0], expected[0], *expected[2:], "f" * 64)

    summary = summarize_namespace_correctness(
        expected_source_sha256s=expected,
        observation=NamespaceCorrectnessObservation(
            observed_source_sha256s=observed,
            namespace_escape_count=3,
        ),
    )

    assert summary == {
        "lost_episodic_count": 1,
        "duplicate_episodic_count": 1,
        "unexpected_episodic_count": 1,
        "episodic_namespace_escape_count": 3,
        "direct_violations": 6,
    }


def test_observer_fails_before_any_read_only_hook_when_block_is_incomplete(tmp_path: Path) -> None:
    plan = _plan()
    root = tmp_path / "block"
    store = AlignedBlockArtifactStore.create(
        root,
        verified_plan=plan,
        block_index=0,
        execution_identity_sha256="b" * 64,
    )
    store.append_lifecycle(0, event_type="ARRIVAL", timestamp_ns=100)
    called = False

    async def retrieve(**_kwargs: object) -> tuple[str, ...]:
        nonlocal called
        called = True
        return tuple(f"session-{index}" for index in range(10))

    async def observe(**_kwargs: object) -> NamespaceCorrectnessObservation:
        nonlocal called
        called = True
        return NamespaceCorrectnessObservation((), 0)

    with pytest.raises(AlignedQualityLiveError, match="complete coverage"):
        asyncio.run(
            observe_aligned_quality_live(
                root,
                verified_plan=plan,
                block_index=0,
                retrieval_cases=(_case(),),
                hooks=AlignedQualityLiveHooks(retrieve, observe),
            )
        )
    assert called is False


def test_observer_rejects_non_top10_or_invalid_hook_contract(tmp_path: Path) -> None:
    plan = _plan()
    root = tmp_path / "block"
    _complete(root, plan=plan)

    async def short_result(**_kwargs: object) -> tuple[str, ...]:
        return tuple(f"session-{index}" for index in range(9))

    async def observer(**_kwargs: object) -> NamespaceCorrectnessObservation:
        return NamespaceCorrectnessObservation(
            observed_source_sha256s=tuple(plan["history_source_sha256s"]["07741c45"]),
            namespace_escape_count=0,
        )

    with pytest.raises(AlignedQualityLiveError, match="top-10"):
        asyncio.run(
            observe_aligned_quality_live(
                root,
                verified_plan=plan,
                block_index=0,
                retrieval_cases=(_case(),),
                hooks=AlignedQualityLiveHooks(short_result, observer),
            )
        )
