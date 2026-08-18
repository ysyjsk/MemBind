"""Durability and fail-closed tests for one v3.1 live block."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.apc_aligned_baseline import APC_BASELINE_HISTORIES, build_apc_aligned_baseline_plan
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.artifacts import (
    MemBindV31ArtifactsError,
    V31BlockStore,
    inspect_v31_block,
)
from paper_eval.membind_v31.baseline_acceptance import ACCEPTANCE_SCHEMA, EXPECTED_BASELINE_RUN_ID
from paper_eval.membind_v31.method_plan import build_membind_v31_method_plan
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact


def _plan() -> dict[str, object]:
    baseline = build_apc_aligned_baseline_plan(
        run_id=EXPECTED_BASELINE_RUN_ID,
        history_source_sha256s={
            history: [f"{index + 1:064x}"]
            for index, history in enumerate(APC_BASELINE_HISTORIES)
        },
        interarrival_ns=10,
        execution_envelope_sha256="a" * 64,
        service_reference_ns=12,
        normalized_offered_load=1.2,
    )
    acceptance = {
        "schema_version": ACCEPTANCE_SCHEMA,
        "status": "PASS",
        "artifact_status": "SEALED_VALID",
        "semantic_verdicts": {
            method: {"direct_violations": 0, "semantic_status": "SAFE"}
            for method in ("U0-aligned", "A0-aligned", "P(C=2)-aligned")
        },
        "run_id": EXPECTED_BASELINE_RUN_ID,
        "completed_block_count": 12,
        "terminal_episode_count_per_method": 188,
        "plan_payload_sha256": baseline["payload_sha256"],
        "source_manifest_sha256": baseline["source_manifest_sha256"],
        "arrival_trace_sha256": baseline["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": baseline["shared_execution_envelope_sha256"],
        "global_llm_admission_k": 2,
        "execution_identity_sha256": "b" * 64,
        "block_result_payload_sha256s": [f"{100 + index:064x}" for index in range(12)],
        "quality_run_id": "quality-test",
        "quality_report_payload_sha256": "c" * 64,
        "quality_identity_sha256": "d" * 64,
        "quality_runtime_identity_sha256": "e" * 64,
    }
    acceptance["payload_sha256"] = payload_sha256(acceptance)
    return build_membind_v31_method_plan(
        run_id="membind-v31-artifact-test",
        verified_baseline_plan=baseline,
        verified_baseline_acceptance=acceptance,
        methodology_sha256="f" * 64,
        workplan_sha256="1" * 64,
    )


def _artifact() -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=0,
        source_sha256=f"{1:064x}",
        evidence_sha256="2" * 64,
        certification_sha256="3" * 64,
        raw_nodes=[{"uuid": "private-node", "name": "Private"}],
        raw_edges=[],
        pure_intermediates={"node_episode_index_map": {"private-node": [0]}},
    )


def test_store_persists_prepared_privately_and_seals_complete_public_trace(tmp_path: Path) -> None:
    root = tmp_path / "block-00"
    store = V31BlockStore.create(
        root,
        verified_plan=_plan(),
        block_index=0,
        execution_identity_sha256="4" * 64,
        state_cut_certification_sha256="3" * 64,
        compile_workers=2,
        lookahead=2,
    )
    store.append_lifecycle(0, "ARRIVAL", 1)
    store.append_lifecycle(0, "COMPILE_STARTED", 2)
    store.persist_prepared(_artifact())
    store.append_lifecycle(0, "PREPARED_DURABLE", 3)
    store.append_lifecycle(0, "BIND_STARTED", 4, {"predecessor_version": -1})
    store.append_lifecycle(0, "COMMIT_RETURNED", 5)
    assert store.checkpoint["resume_status"] == "AMBIGUOUS_COMMIT_POISONED"
    store.append_lifecycle(0, "PUBLICATION_DURABLE", 6, {"visibility_confirmed": True})

    inspected = inspect_v31_block(root)
    assert inspected["checkpoint"]["complete_coverage"] is True
    assert inspected["checkpoint"]["resume_status"] == "NOT_NEEDED_COMPLETE"
    assert inspected["source_states"] == ["PUBLICATION_DURABLE"]
    assert (root / "private/prepared/00000000.json").is_file()
    assert "Private" not in (root / "events.jsonl").read_text(encoding="utf-8")


def test_store_rejects_private_telemetry_and_duplicate_or_invalid_transition(tmp_path: Path) -> None:
    store = V31BlockStore.create(
        tmp_path / "block-00",
        verified_plan=_plan(),
        block_index=0,
        execution_identity_sha256="4" * 64,
        state_cut_certification_sha256="3" * 64,
        compile_workers=2,
        lookahead=2,
    )
    with pytest.raises(MemBindV31ArtifactsError, match="content_safe"):
        store.append_lifecycle(0, "ARRIVAL", 1, {"prompt": "private"})
    store.append_lifecycle(0, "ARRIVAL", 1)
    with pytest.raises(MemBindV31ArtifactsError, match="lifecycle_transition_invalid"):
        store.append_lifecycle(0, "ARRIVAL", 2)


def test_inspector_detects_event_tamper_and_unpublished_commit_poison(tmp_path: Path) -> None:
    root = tmp_path / "block-00"
    store = V31BlockStore.create(
        root,
        verified_plan=_plan(),
        block_index=0,
        execution_identity_sha256="4" * 64,
        state_cut_certification_sha256="3" * 64,
        compile_workers=2,
        lookahead=2,
    )
    for timestamp, event in enumerate(
        ("ARRIVAL", "COMPILE_STARTED", "PREPARED_DURABLE", "BIND_STARTED", "COMMIT_RETURNED"),
        start=1,
    ):
        if event == "PREPARED_DURABLE":
            store.persist_prepared(_artifact())
        store.append_lifecycle(0, event, timestamp)
    inspected = inspect_v31_block(root)
    assert inspected["checkpoint"]["resume_status"] == "AMBIGUOUS_COMMIT_POISONED"

    rows = (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    value = json.loads(rows[0])
    value["event"]["timestamp_ns"] = 999
    rows[0] = json.dumps(value, sort_keys=True)
    (root / "events.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(MemBindV31ArtifactsError, match="event_hash_mismatch"):
        inspect_v31_block(root)
