"""TDD tests for namespace-safe, checkpointed S4 capture/replay phases."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s4_d0_runner import (
    S4NamespaceMismatch,
    S4PhaseFailed,
    _merge_runtime_evidence,
    evaluate_s4_smoke,
    normalize_isolated_namespace_graph,
    run_s4_phase,
)


REMAP_RUNTIME = {
    "exact_prompt_hit_count": 7,
    "candidate_remap_hit_count": 2,
    "candidate_remap_node_hit_count": 1,
    "candidate_remap_edge_hit_count": 1,
    "candidate_remap_rejection_count": 0,
}


@dataclass(frozen=True)
class Episode:
    source_sequence: int


class FakeGraph:
    def __init__(self, *, fail_on: int | None = None) -> None:
        self.published: list[int] = []
        self.fail_on = fail_on
        self.closed = 0

    async def add_episode(self, *, source_sequence: int) -> None:
        if source_sequence == self.fail_on:
            raise ConnectionError("private service detail")
        self.published.append(source_sequence)

    async def close(self) -> None:
        self.closed += 1


class CodedFailureGraph(FakeGraph):
    async def add_episode(self, *, source_sequence: int) -> None:
        error = RuntimeError("private candidate content")
        error.code = "CANDIDATE_MEMBERSHIP_DRIFT"
        raise error


def _spec(*, mode: str = "capture") -> dict:
    method = "U0" if mode == "capture" else "D0"
    return {
        "phase": "U0_CAPTURE" if mode == "capture" else "D0_READ_ONLY_REPLAY",
        "run_id": f"s4-test-{mode}",
        "history_id": "07741c45",
        "namespace": f"pev3-s4-test-{mode}",
        "method": method,
        "mode": mode,
        "cache_id": "s4-test-cache",
    }


def _state(graph: FakeGraph):
    async def probe() -> dict:
        return {
            "node_count": len(graph.published),
            "relationship_count": 0,
            "episode_names": [str(value) for value in graph.published],
        }

    return probe


def _export(graph: FakeGraph):
    async def export(_graph, _episodes, namespace: str) -> dict:
        return {
            "entities": [
                {
                    "group_id": namespace,
                    "name": f"entity-{value}",
                    "labels": ["Entity"],
                    "summary": "same",
                    "attributes": {},
                }
                for value in graph.published
            ],
            "edges": [],
            "episodes": [
                {
                    "source_sequence": value,
                    "source_hash": f"hash-{value}",
                    "session_id": f"session-{value}",
                }
                for value in graph.published
            ],
        }

    return export


def _runtime_evidence(*, mode: str, count: int) -> dict:
    live = count if mode == "capture" else 0
    return {
        "live_llm_calls": live,
        "live_embedding_calls": live,
        "resolved_prompt_count": count,
        "resolved_embedding_count": count,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "cross_encoder_call_count": 0,
    }


def test_runtime_evidence_merges_the_complete_candidate_remap_counter_set() -> None:
    current = {**_runtime_evidence(mode="replay", count=9), **REMAP_RUNTIME}
    prefix = {
        **_runtime_evidence(mode="replay", count=4),
        **{
            "exact_prompt_hit_count": 3,
            "candidate_remap_hit_count": 1,
            "candidate_remap_node_hit_count": 1,
            "candidate_remap_edge_hit_count": 0,
            "candidate_remap_rejection_count": 0,
        },
    }

    merged = _merge_runtime_evidence(prefix, current)

    assert merged["resolved_prompt_count"] == 13
    assert merged["exact_prompt_hit_count"] == 10
    assert merged["candidate_remap_hit_count"] == 3
    assert merged["candidate_remap_node_hit_count"] == 2
    assert merged["candidate_remap_edge_hit_count"] == 1


def test_runtime_evidence_rejects_partial_candidate_remap_counters() -> None:
    partial = {
        **_runtime_evidence(mode="replay", count=9),
        "candidate_remap_hit_count": 1,
    }

    with pytest.raises(ValueError, match="shape drift"):
        _merge_runtime_evidence({}, partial)


async def _run(
    tmp_path: Path,
    graph: FakeGraph,
    *,
    mode: str = "capture",
    cleanup_calls: list[str] | None = None,
    cleanup_error: bool = False,
    graph_exporter=None,
) -> dict:
    calls = cleanup_calls if cleanup_calls is not None else []

    async def cleanup(namespace: str) -> None:
        calls.append(namespace)
        if cleanup_error:
            raise RuntimeError("private cleanup detail")
        graph.published.clear()

    count = 3
    return await run_s4_phase(
        spec=_spec(mode=mode),
        episodes=[Episode(index) for index in range(count)],
        graph=graph,
        episode_kwargs=lambda item: {"source_sequence": item.source_sequence},
        namespace_probe=_state(graph),
        graph_exporter=graph_exporter or _export(graph),
        runtime_evidence=lambda: _runtime_evidence(mode=mode, count=count),
        cache_evidence=lambda: {
            "prompt_cache_sha256": "1" * 64,
            "embedding_cache_sha256": "2" * 64,
        },
        cleanup_namespace=cleanup,
        artifact_root=tmp_path,
        expected_episode_count=count,
        git_commit="deadbeef",
    )


@pytest.mark.asyncio
async def test_phase_checkpoints_every_episode_and_cleans_exact_namespace(
    tmp_path: Path,
) -> None:
    graph = FakeGraph()
    cleanup_calls: list[str] = []

    result = await _run(tmp_path, graph, cleanup_calls=cleanup_calls)

    payload = result["payload"]
    assert payload["status"] == "PASS"
    assert payload["completed_source_sequences"] == [0, 1, 2]
    assert payload["episode_coverage"] == 1.0
    assert payload["cleanup"] == {
        "global_cleanup_used": False,
        "namespace": "pev3-s4-test-capture",
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
        "scope": "EXACT_GROUP_ID_ONLY",
    }
    assert cleanup_calls == ["pev3-s4-test-capture"]
    assert graph.closed == 1

    run_dir = tmp_path / "s4-test-capture"
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
    assert checkpoint["status"] == "completed"
    assert checkpoint["payload_sha256"] == payload_sha256(
        {key: value for key, value in checkpoint.items() if key != "payload_sha256"}
    )
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert [event["event_type"] for event in events] == [
        "intent",
        "publication",
        "intent",
        "publication",
        "intent",
        "publication",
        "terminal",
    ]
    assert all(
        event["payload_sha256"]
        == payload_sha256(
            {key: value for key, value in event.items() if key != "payload_sha256"}
        )
        for event in events
    )


@pytest.mark.asyncio
async def test_failure_keeps_prefix_and_resume_continues_without_duplicates(
    tmp_path: Path,
) -> None:
    graph = FakeGraph(fail_on=1)
    with pytest.raises(S4PhaseFailed) as raised:
        await _run(tmp_path, graph)

    assert raised.value.result["payload"]["status"] == "INCOMPLETE"
    assert raised.value.result["payload"]["completed_source_sequences"] == [0]
    assert raised.value.result["payload"]["error_class"] == "ConnectionError"
    assert "private service detail" not in json.dumps(raised.value.result)
    assert graph.published == [0]

    graph.fail_on = None
    result = await _run(tmp_path, graph)

    assert result["payload"]["status"] == "PASS"
    assert result["payload"]["completed_source_sequences"] == [0, 1, 2]
    events = [
        json.loads(line)
        for line in (tmp_path / "s4-test-capture/events.jsonl").read_text().splitlines()
    ]
    assert [event["event_type"] for event in events].count("resume") == 1
    assert [
        event["source_sequence"]
        for event in events
        if event["event_type"] == "publication"
    ] == [0, 1, 2]


@pytest.mark.asyncio
async def test_failure_event_persists_only_a_sanitized_machine_error_code(
    tmp_path: Path,
) -> None:
    graph = CodedFailureGraph()

    with pytest.raises(S4PhaseFailed):
        await _run(tmp_path, graph, mode="replay")

    events = [
        json.loads(line)
        for line in (tmp_path / "s4-test-replay/events.jsonl").read_text().splitlines()
    ]
    failure = next(event for event in events if event["event_type"] == "failure")
    assert failure["error_class"] == "RuntimeError"
    assert failure["error_code"] == "CANDIDATE_MEMBERSHIP_DRIFT"
    assert "private candidate content" not in json.dumps(events)


@pytest.mark.asyncio
async def test_resume_accumulates_runtime_work_across_process_counters(
    tmp_path: Path,
) -> None:
    graph = FakeGraph(fail_on=1)
    process_start = 0

    def evidence() -> dict:
        current = len(graph.published) - process_start
        return _runtime_evidence(mode="capture", count=current)

    async def cleanup(_namespace: str) -> None:
        graph.published.clear()

    async def execute() -> dict:
        return await run_s4_phase(
            spec=_spec(mode="capture"),
            episodes=[Episode(index) for index in range(3)],
            graph=graph,
            episode_kwargs=lambda item: {"source_sequence": item.source_sequence},
            namespace_probe=_state(graph),
            graph_exporter=_export(graph),
            runtime_evidence=evidence,
            cache_evidence=lambda: {
                "prompt_cache_sha256": "1" * 64,
                "embedding_cache_sha256": "2" * 64,
            },
            cleanup_namespace=cleanup,
            artifact_root=tmp_path,
            expected_episode_count=3,
            git_commit="deadbeef",
        )

    with pytest.raises(S4PhaseFailed):
        await execute()
    checkpoint = json.loads((tmp_path / "s4-test-capture/checkpoint.json").read_text())
    assert checkpoint["runtime_evidence_cumulative"]["live_llm_calls"] == 1

    graph.fail_on = None
    process_start = len(graph.published)
    result = await execute()

    assert result["payload"]["runtime_evidence"]["live_llm_calls"] == 3
    assert result["payload"]["runtime_evidence"]["resolved_prompt_count"] == 3
    checkpoint = json.loads((tmp_path / "s4-test-capture/checkpoint.json").read_text())
    assert checkpoint["runtime_evidence_cumulative"] == result["payload"][
        "runtime_evidence"
    ]


@pytest.mark.asyncio
async def test_nonempty_namespace_without_checkpoint_fails_before_mutation(
    tmp_path: Path,
) -> None:
    graph = FakeGraph()
    graph.published.append(7)

    with pytest.raises(S4NamespaceMismatch, match="nonempty"):
        await _run(tmp_path, graph)

    assert graph.published == [7]


@pytest.mark.asyncio
async def test_resume_rejects_namespace_state_that_drifted_from_checkpoint(
    tmp_path: Path,
) -> None:
    graph = FakeGraph(fail_on=1)
    with pytest.raises(S4PhaseFailed):
        await _run(tmp_path, graph)
    graph.published.append(99)
    graph.fail_on = None

    with pytest.raises(S4NamespaceMismatch, match="namespace state"):
        await _run(tmp_path, graph)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["graph_export", "cleanup"])
async def test_finalization_failure_is_checkpointed_and_sanitized(
    tmp_path: Path, failure_stage: str
) -> None:
    graph = FakeGraph()

    async def failing_export(*_args) -> dict:
        raise ValueError("private graph export detail")

    with pytest.raises(S4PhaseFailed) as raised:
        await _run(
            tmp_path,
            graph,
            cleanup_error=failure_stage == "cleanup",
            graph_exporter=(
                failing_export if failure_stage == "graph_export" else None
            ),
        )

    payload = raised.value.result["payload"]
    assert payload["status"] == "INCOMPLETE"
    assert payload["error_class"] in {"ValueError", "RuntimeError"}
    assert "private" not in json.dumps(raised.value.result)
    persisted = json.loads(
        (tmp_path / "s4-test-capture/phase_result.json").read_text()
    )
    assert persisted == raised.value.result
    checkpoint = json.loads(
        (tmp_path / "s4-test-capture/checkpoint.json").read_text()
    )
    assert checkpoint["status"] == "incomplete"
    events = [
        json.loads(line)
        for line in (tmp_path / "s4-test-capture/events.jsonl").read_text().splitlines()
    ]
    assert events[-1]["event_type"] == "failure"
    assert events[-1]["failure_stage"] == "finalization"


def _phase_result(
    *,
    mode: str,
    graph_sha: str = "a" * 64,
    cache_prompt: str = "1" * 64,
    cache_embedding: str = "2" * 64,
) -> dict:
    count = 49
    return {
        "status": "finalized",
        "payload": {
            "phase": "U0_CAPTURE" if mode == "capture" else "D0_READ_ONLY_REPLAY",
            "mode": mode,
            "status": "PASS",
            "completed_source_sequences": list(range(count)),
            "expected_episode_count": count,
            "canonical_graph_sha256": graph_sha,
            "runtime_evidence": _runtime_evidence(mode=mode, count=count),
            "cache_evidence": {
                "prompt_cache_sha256": cache_prompt,
                "embedding_cache_sha256": cache_embedding,
            },
        },
    }


def test_smoke_evaluator_requires_exact_graph_work_and_cache_parity() -> None:
    summary = evaluate_s4_smoke(
        capture_result=_phase_result(mode="capture"),
        replay_result=_phase_result(mode="replay"),
    )

    assert summary["verdict"] == "PASS"
    assert summary["failures"] == []
    assert summary["canonical_graph_parity"] is True
    assert summary["cache_mutation_during_replay"] is False
    assert summary["s4_four_history_qualification_authorized"] is True
    assert summary["s5_authorized"] is False


def _with_remap_runtime(replay: dict) -> dict:
    selected = copy.deepcopy(replay)
    selected["payload"]["runtime_evidence"].update(
        {
            "exact_prompt_hit_count": 47,
            "candidate_remap_hit_count": 2,
            "candidate_remap_node_hit_count": 1,
            "candidate_remap_edge_hit_count": 1,
            "candidate_remap_rejection_count": 0,
        }
    )
    return selected


def test_smoke_evaluator_accepts_fully_accounted_zero_rejection_remaps() -> None:
    summary = evaluate_s4_smoke(
        capture_result=_phase_result(mode="capture"),
        replay_result=_with_remap_runtime(_phase_result(mode="replay")),
    )

    assert summary["verdict"] == "PASS"
    assert summary["candidate_remap_used"] is True
    assert summary["candidate_remap_hit_count"] == 2
    assert summary["candidate_oracle_resolution_accounting"] is True


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    [
        (
            lambda runtime: runtime.update(candidate_remap_rejection_count=1),
            "candidate_remap_rejection",
        ),
        (
            lambda runtime: runtime.update(exact_prompt_hit_count=46),
            "candidate_oracle_resolution_accounting",
        ),
        (
            lambda runtime: runtime.update(candidate_remap_node_hit_count=0),
            "candidate_remap_breakdown",
        ),
    ],
)
def test_smoke_evaluator_fails_closed_on_candidate_oracle_evidence(
    mutation,
    expected_failure: str,
) -> None:
    replay = _with_remap_runtime(_phase_result(mode="replay"))
    mutation(replay["payload"]["runtime_evidence"])

    summary = evaluate_s4_smoke(
        capture_result=_phase_result(mode="capture"),
        replay_result=replay,
    )

    assert summary["verdict"] == "FAIL"
    assert expected_failure in summary["failures"]
    assert summary["s4_four_history_qualification_authorized"] is False


@pytest.mark.parametrize(
    "mutation,expected_failure",
    [
        ("graph", "canonical_graph_parity"),
        ("prompt_miss", "replay_oracle_miss"),
        ("live_call", "replay_live_model_call"),
        ("cross_encoder", "replay_cross_encoder_call"),
        ("cache", "cache_mutation"),
        ("coverage", "replay_episode_coverage"),
        ("work", "resolved_prompt_count"),
    ],
)
def test_smoke_evaluator_fails_closed(
    mutation: str, expected_failure: str
) -> None:
    capture = _phase_result(mode="capture")
    replay = _phase_result(mode="replay")
    if mutation == "graph":
        replay["payload"]["canonical_graph_sha256"] = "b" * 64
    elif mutation == "prompt_miss":
        replay["payload"]["runtime_evidence"]["unexpected_prompt_count"] = 1
    elif mutation == "live_call":
        replay["payload"]["runtime_evidence"]["live_llm_calls"] = 1
    elif mutation == "cross_encoder":
        replay["payload"]["runtime_evidence"]["cross_encoder_call_count"] = 1
    elif mutation == "cache":
        replay["payload"]["cache_evidence"]["prompt_cache_sha256"] = "3" * 64
    elif mutation == "coverage":
        replay["payload"]["completed_source_sequences"].pop()
    else:
        replay["payload"]["runtime_evidence"]["resolved_prompt_count"] = 48

    summary = evaluate_s4_smoke(
        capture_result=capture,
        replay_result=replay,
    )

    assert summary["verdict"] == "FAIL"
    assert expected_failure in summary["failures"]
    assert summary["s4_four_history_qualification_authorized"] is False


def test_namespace_projection_changes_only_entity_group_id() -> None:
    graph = {
        "canonical_graph_hash": "old",
        "entities": [
            {
                "group_id": "capture-namespace",
                "name": "Alice",
                "summary": "works here",
                "attributes": {"role": "engineer"},
            }
        ],
        "edges": [{"fact": "Alice works here", "valid_at": "2026-01-01"}],
        "episodes": [{"source_sequence": 0, "source_hash": "same"}],
    }
    original = copy.deepcopy(graph)

    projected = normalize_isolated_namespace_graph(graph)

    assert projected["entities"][0]["group_id"] == "__S4_ISOLATED_NAMESPACE__"
    assert projected["entities"][0]["name"] == "Alice"
    assert projected["edges"] == original["edges"]
    assert projected["episodes"] == original["episodes"]
    assert "canonical_graph_hash" not in projected
    assert graph == original
