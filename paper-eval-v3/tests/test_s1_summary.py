from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

from paper_eval.s1_summary import finalize_s1_summary
from paper_eval.s1_u0_smoke import DurableRun


class Graph:
    async def add_episode(self, **_: object) -> None:
        return None

    async def search(self, **_: object) -> list[dict[str, str]]:
        return [{"uuid": "edge-1"}]

    async def close(self) -> None:
        return None


def test_final_summary_proves_coverage_and_contains_no_source_text(tmp_path: Path) -> None:
    run = DurableRun(tmp_path, "run-1", "07741c45", "pev3-ns")
    result = asyncio.run(run.execute(Graph(), [0, 1, 2], query="private question"))
    assert result.status == "completed"

    output = tmp_path / "run-1" / "summary.json"
    summary = finalize_s1_summary(
        run_dir=tmp_path / "run-1",
        output_path=output,
        expected_episode_count=3,
        git_commit="deadbeef",
    )
    assert summary["status"] == "finalized"
    assert summary["payload"]["verdict"] == "PASS"
    assert summary["payload"]["coverage"] == {
        "expected": 3,
        "intents": 3,
        "published": 3,
        "lost": [],
        "duplicates": [],
    }
    assert summary["payload"]["add_episode_call_count"] == 3
    serialized = json.dumps(summary).lower()
    assert "private question" not in serialized
    assert "api_key" not in serialized
    assert "episode_body" not in serialized


def test_final_summary_rejects_tampered_event_payload(tmp_path: Path) -> None:
    run = DurableRun(tmp_path, "run-tamper", "07741c45", "pev3-ns")
    result = asyncio.run(run.execute(Graph(), [0, 1], query="q"))
    assert result.status == "completed"
    events = (tmp_path / "run-tamper" / "events.jsonl").read_text().splitlines()
    event = json.loads(events[0])
    event["source_sequence"] = 99
    events[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    (tmp_path / "run-tamper" / "events.jsonl").write_text("\n".join(events) + "\n")
    summary = finalize_s1_summary(
        run_dir=tmp_path / "run-tamper",
        output_path=tmp_path / "tampered.json",
        expected_episode_count=2,
        git_commit="deadbeef",
    )
    assert summary["payload"]["verdict"] == "FAIL"
    assert summary["payload"]["integrity"]["event_hash_failures"] == 1


def test_final_summary_exposes_failure_events_and_is_not_clean_pass(tmp_path: Path) -> None:
    run = DurableRun(tmp_path, "run-failure", "07741c45", "pev3-ns")
    graph = Graph()
    result = asyncio.run(run.execute(graph, [0, 1], query="q"))
    assert result.status == "completed"
    events_path = tmp_path / "run-failure" / "events.jsonl"
    lines = events_path.read_text().splitlines()
    failure = {
        "schema_version": "membind.paper-eval-v3.s1-event.v1",
        "run_id": "run-failure",
        "history_id": "07741c45",
        "namespace": "pev3-ns",
        "event_type": "failure",
        "source_sequence": 0,
        "timestamp_ns": 1,
        "error_class": "SyntheticFailure",
        "failure_stage": "add_episode",
    }
    from paper_eval.artifacts import payload_sha256

    failure["payload_sha256"] = payload_sha256(failure)
    events_path.write_text("\n".join([*lines, json.dumps(failure, sort_keys=True, separators=(",", ":"))]) + "\n")
    summary = finalize_s1_summary(
        run_dir=tmp_path / "run-failure",
        output_path=tmp_path / "failure.json",
        expected_episode_count=2,
        git_commit="deadbeef",
    )
    assert summary["payload"]["failure_count"] == 1
    assert summary["payload"]["failure_error_classes"] == ["SyntheticFailure"]
    assert summary["payload"]["verdict"] == "FAIL"


def test_final_summary_rejects_tampered_checkpoint_payload(tmp_path: Path) -> None:
    run = DurableRun(tmp_path, "run-checkpoint", "07741c45", "pev3-ns")
    result = asyncio.run(run.execute(Graph(), [0], query="q"))
    assert result.status == "completed"
    checkpoint_path = tmp_path / "run-checkpoint" / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["namespace"] = "tampered"
    checkpoint_path.write_text(json.dumps(checkpoint))

    summary = finalize_s1_summary(
        run_dir=tmp_path / "run-checkpoint",
        output_path=tmp_path / "checkpoint-tampered.json",
        expected_episode_count=1,
        git_commit="deadbeef",
    )
    assert summary["payload"]["verdict"] == "FAIL"
    assert summary["payload"]["integrity"]["checkpoint_hash_valid"] is False


def test_final_summary_rejects_event_identity_drift_even_with_valid_hash(
    tmp_path: Path,
) -> None:
    run = DurableRun(tmp_path, "run-identity", "07741c45", "pev3-ns")
    result = asyncio.run(run.execute(Graph(), [0], query="q"))
    assert result.status == "completed"
    events_path = tmp_path / "run-identity" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    event = events[0]
    event["namespace"] = "another-namespace"
    from paper_eval.artifacts import payload_sha256

    event.pop("payload_sha256")
    event["payload_sha256"] = payload_sha256(event)
    events_path.write_text(
        "\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in events)
        + "\n"
    )

    summary = finalize_s1_summary(
        run_dir=tmp_path / "run-identity",
        output_path=tmp_path / "identity-drift.json",
        expected_episode_count=1,
        git_commit="deadbeef",
    )
    assert summary["payload"]["verdict"] == "FAIL"
    assert summary["payload"]["integrity"]["event_identity_failures"] == 1


def test_final_summary_rejects_fully_resealed_run_identity_drift(
    tmp_path: Path,
) -> None:
    run = DurableRun(tmp_path, "run-bound", "07741c45", "pev3-ns")
    result = asyncio.run(run.execute(Graph(), [0], query="q"))
    assert result.status == "completed"
    from paper_eval.artifacts import payload_sha256

    checkpoint_path = tmp_path / "run-bound" / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["run_id"] = "another-run"
    checkpoint.pop("payload_sha256")
    checkpoint["payload_sha256"] = payload_sha256(checkpoint)
    checkpoint_path.write_text(json.dumps(checkpoint))
    events_path = tmp_path / "run-bound" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    for event in events:
        event["run_id"] = "another-run"
        event.pop("payload_sha256")
        event["payload_sha256"] = payload_sha256(event)
    events_path.write_text(
        "\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in events)
        + "\n"
    )

    summary = finalize_s1_summary(
        run_dir=tmp_path / "run-bound",
        output_path=tmp_path / "bound.json",
        expected_episode_count=1,
        git_commit="deadbeef",
    )
    assert summary["payload"]["verdict"] == "FAIL"
    assert summary["payload"]["integrity"]["checkpoint_identity_valid"] is False


def test_final_summary_requires_exact_serial_event_pattern(tmp_path: Path) -> None:
    run = DurableRun(tmp_path, "run-order", "07741c45", "pev3-ns")
    result = asyncio.run(run.execute(Graph(), [0, 1], query="q"))
    assert result.status == "completed"
    events_path = tmp_path / "run-order" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    events[1], events[2] = events[2], events[1]
    events_path.write_text(
        "\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in events)
        + "\n"
    )
    summary = finalize_s1_summary(
        run_dir=tmp_path / "run-order",
        output_path=tmp_path / "order.json",
        expected_episode_count=2,
        git_commit="deadbeef",
    )
    assert summary["payload"]["verdict"] == "FAIL"
    assert summary["payload"]["integrity"]["event_pattern_valid"] is False
