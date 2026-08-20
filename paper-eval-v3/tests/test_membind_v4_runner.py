"""TDD checks for the v4 candidate runner's offline and blocked paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v4.autoresearch import CandidateStore
from paper_eval.membind_v4.runner import run_candidate


def _assert_candidate_status(candidate: dict[str, object], expected: str) -> None:
    digest = candidate.pop("payload_sha256")
    assert digest == payload_sha256(candidate)
    assert candidate["status"] == expected


def test_candidate_store_rejects_double_finalize_without_overwriting_summary(
    tmp_path: Path,
) -> None:
    store = CandidateStore.create(tmp_path, "c01", source_count=6)
    store.event("publication", source_sequence=0)
    store.finalize(status="PASS", marker="first")
    summary_path = store.root / "summary.json"
    original = summary_path.read_bytes()

    with pytest.raises(ValueError, match="candidate_manifest_already_terminal"):
        store.finalize(status="PASS", marker="second")

    assert summary_path.read_bytes() == original
    assert not (store.root / "failure.json").exists()


def test_candidate_store_rejects_finalize_then_failure_without_mixed_terminals(
    tmp_path: Path,
) -> None:
    store = CandidateStore.create(tmp_path, "c01", source_count=6)
    store.finalize(status="PASS")

    with pytest.raises(ValueError, match="candidate_manifest_already_terminal"):
        store.failure(RuntimeError("late failure"))

    assert (store.root / "summary.json").is_file()
    assert not (store.root / "failure.json").exists()


def test_candidate_store_rejects_failure_then_finalize_without_mixed_terminals(
    tmp_path: Path,
) -> None:
    store = CandidateStore.create(tmp_path, "c01", source_count=6)
    store.failure(RuntimeError("first failure"))

    with pytest.raises(ValueError, match="candidate_manifest_already_terminal"):
        store.finalize(status="PASS")

    assert (store.root / "failure.json").is_file()
    assert not (store.root / "summary.json").exists()


def test_candidate_store_rejects_events_after_terminal_without_appending(
    tmp_path: Path,
) -> None:
    store = CandidateStore.create(tmp_path, "c01", source_count=6)
    store.event("publication", source_sequence=0)
    store.finalize(status="PASS")
    original = store.events_path.read_bytes()

    with pytest.raises(ValueError, match="candidate_manifest_already_terminal"):
        store.event("publication", source_sequence=1)

    assert store.events_path.read_bytes() == original


@pytest.mark.parametrize("terminal", ("finalize", "failure"))
def test_candidate_store_manifest_tamper_writes_no_terminal_artifact(
    tmp_path: Path,
    terminal: str,
) -> None:
    store = CandidateStore.create(tmp_path, "c01", source_count=6)
    manifest_path = store.root / "candidate.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_count"] = 12
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="candidate_manifest_payload_hash_mismatch"):
        if terminal == "finalize":
            store.finalize(status="PASS")
        else:
            store.failure(RuntimeError("failure"))

    assert not (store.root / "summary.json").exists()
    assert not (store.root / "failure.json").exists()


@pytest.mark.parametrize(
    "private_fields",
    (
        {"metadata": {"prompt": "secret"}},
        {"attempts": [{"response": "secret"}]},
    ),
)
def test_candidate_store_rejects_nested_private_event_without_writing_trace(
    tmp_path: Path,
    private_fields: dict[str, object],
) -> None:
    store = CandidateStore.create(tmp_path, "c01", source_count=6)

    with pytest.raises(ValueError, match="private_telemetry_field"):
        store.event("llm", **private_fields)

    assert not store.events_path.exists()


def test_blocked_candidate_is_non_mergeable_and_keeps_preflight(tmp_path: Path) -> None:
    result = run_candidate(
        candidate_id="c01",
        history_id="07741c45",
        source_count=6,
        output_root=tmp_path,
        mode="blocked",
        preflight={
            "status": "BLOCKED",
            "classification": "EXECUTION_SANDBOX_NETWORK_ISOLATION",
        },
    )
    assert result["status"] == "FAILED_NON_MERGEABLE"
    root = tmp_path / "candidates" / "c01"
    assert (root / "candidate.json").is_file()
    assert (root / "preflight.json").is_file()
    candidate = json.loads((root / "candidate.json").read_text())
    _assert_candidate_status(candidate, "FAILED_NON_MERGEABLE")
    failure = json.loads((root / "failure.json").read_text())
    assert failure["status"] == "FAILED_NON_MERGEABLE"
    assert failure["classification"] == "EXECUTION_SANDBOX_NETWORK_ISOLATION"


def test_fixture_candidate_runs_through_real_gate_and_writes_summary(tmp_path: Path) -> None:
    result = run_candidate(
        candidate_id="c01",
        history_id="07741c45",
        source_count=4,
        output_root=tmp_path,
        mode="fixture",
    )
    assert result["status"] == "PASS"
    summary = json.loads(
        (tmp_path / "candidates" / "c01" / "summary.json").read_text()
    )
    candidate = json.loads(
        (tmp_path / "candidates" / "c01" / "candidate.json").read_text()
    )
    _assert_candidate_status(candidate, "COMPLETED")
    assert summary["qualified_node_resolve_count"] == 3
    assert summary["semantic_hit_count"] >= 1
    assert summary["direct_violation_count"] == 0


def test_a1_twenty_source_fixture_is_not_authorized(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="a1_fixture_not_authorized"):
        run_candidate(
            candidate_id="c01",
            history_id="07741c45",
            source_count=20,
            output_root=tmp_path,
            mode="fixture",
            protocol_amendment="A1",
            a1_audit_path=tmp_path / "audit.json",
            a1_amendment_path=tmp_path / "amendment.json",
        )


def test_live_result_projection_is_persisted_without_private_payload(tmp_path: Path) -> None:
    def live_runner(**_kwargs):
        return {
            "schema_version": "fixture.live.v1",
            "status": "PASS",
            "source_count": 6,
            "performance": {"makespan_ns": 100, "p95_freshness_ns": 20},
            "telemetry": {
                "events": [
                    {"event_type": "speculation_launched", "source_sequence": 1},
                    {"event_type": "semantic_hit", "source_sequence": 1},
                ],
            },
            "prompt": "must not be persisted",
        }

    result = run_candidate(
        candidate_id="c01",
        history_id="07741c45",
        source_count=6,
        output_root=tmp_path,
        mode="live",
        preflight={"status": "READY"},
        live_runner=live_runner,
    )
    assert result["status"] == "PASS"
    summary_text = (tmp_path / "candidates" / "c01" / "summary.json").read_text()
    assert "must not be persisted" not in summary_text
    summary = json.loads(summary_text)
    candidate = json.loads(
        (tmp_path / "candidates" / "c01" / "candidate.json").read_text()
    )
    _assert_candidate_status(candidate, "COMPLETED")
    assert summary["makespan_ns"] == 100
    assert summary["result"]["performance"]["p95_freshness_ns"] == 20
    assert summary["semantic_hit_count"] == 1
