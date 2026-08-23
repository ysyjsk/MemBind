from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.longmemeval_fact_consolidation import (
    EXPECTED_ABSTENTION_COUNT,
    EXPECTED_KNOWLEDGE_UPDATE_COUNT,
    EXPECTED_NON_ABSTENTION_COUNT,
    EXPECTED_RAW_SHA256,
    EXPECTED_RECORD_COUNT,
    LongMemEvalOperationError,
    build_episode_inputs,
    build_graph_only_qa_projection,
    build_operation_manifest,
    build_workload_identity,
    discover_completed_graph_coverage,
    load_longmemeval_records,
    select_longmemeval_cases,
    write_operation_artifact,
)


RAW = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
FORMAL_ROOT = Path(
    "/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/artifacts/"
    "sfwb-v1-3-formal-baseline-20260822-002"
)


@pytest.fixture(scope="module")
def records() -> tuple[dict[str, object], ...]:
    return load_longmemeval_records(RAW)


@pytest.fixture(scope="module")
def cases(records: tuple[dict[str, object], ...]):
    return select_longmemeval_cases(records)


def test_pinned_inventory_and_gold_only_cohort(
    records: tuple[dict[str, object], ...], cases
) -> None:
    assert len(records) == EXPECTED_RECORD_COUNT == 500
    knowledge = [row for row in records if row["question_type"] == "knowledge-update"]
    assert len(knowledge) == EXPECTED_KNOWLEDGE_UPDATE_COUNT
    assert sum(str(row["question_id"]).endswith("_abs") for row in knowledge) == EXPECTED_ABSTENTION_COUNT
    assert len(cases) == EXPECTED_NON_ABSTENTION_COUNT == 72
    assert all(case.old_value is None and case.new_value is None for case in cases)
    assert all(case.old_value_status == "OPAQUE_UNLESS_PROVABLE" for case in cases)
    assert all(case.selection_reason == "KNOWLEDGE_UPDATE_TWO_SESSION_NON_ABSTENTION" for case in cases)


def test_answer_pair_is_source_ordered_and_dates_are_not_used_for_construction(cases) -> None:
    assert all(case.old_segment_index < case.new_segment_index for case in cases)
    assert all(
        segment.reference_time.endswith("Z")
        for case in cases
        for segment in case.segments
    )
    # One official record has non-monotonic human-readable dates.  This is
    # recorded rather than silently repaired; construction uses the frozen
    # source-order monotonic policy for every case.
    statuses = {case.raw_date_order_status for case in cases}
    assert statuses == {"MONOTONIC", "NON_MONOTONIC_OR_EQUAL"}


def test_segment_projection_preserves_original_content_and_b0_b1_identity(cases) -> None:
    case = next(item for item in cases if item.question_id == "07741c45")
    b0 = build_episode_inputs(case, "longmemeval-b0-fresh")
    b1 = build_episode_inputs(case, "longmemeval-b1-fresh")
    assert len(b0) == len(case.segments)
    assert [item.source_sequence for item in b0] == list(range(len(b0)))
    assert [item.session_id for item in b0] == [item.session_id for item in b1]
    assert [item.body for item in b0] == [item.body for item in b1]
    assert [item.source_hash for item in b0] == [item.source_hash for item in b1]
    assert [item.reference_time for item in b0] == [item.reference_time for item in b1]
    assert hashlib.sha256(
        b0[case.old_segment_index].body.encode("utf-8")
    ).hexdigest() == case.old_session_body_sha256
    assert hashlib.sha256(
        b0[case.new_segment_index].body.encode("utf-8")
    ).hexdigest() == case.new_session_body_sha256
    assert "Where do I currently keep my old sneakers?" in case.question


def test_workload_identity_excludes_namespace(cases) -> None:
    case = cases[0]
    b0 = build_episode_inputs(case, "namespace-b0")
    b1 = build_episode_inputs(case, "namespace-b1")
    assert build_workload_identity(case, b0) == build_workload_identity(case, b1)


def test_graph_only_projection_is_gold_bound_but_context_free(cases) -> None:
    case = cases[0]
    public, private = build_graph_only_qa_projection(case)
    assert public["evidence_surface"] == "GRAPH_ONLY"
    assert public["source_local_context_included"] is False
    assert public["full_gold_conversation_included"] is False
    assert public["query_after_all_injection"] is True
    assert "gold_current_answer" not in public
    assert "gold_answer_session_ids" not in public
    assert private["gold_current_answer"] == case.gold_current_answer
    assert private["old_value_status"] == "OPAQUE_UNLESS_PROVABLE"


def test_manifest_records_literature_boundary_and_does_not_use_results(cases) -> None:
    coverage = discover_completed_graph_coverage(FORMAL_ROOT)
    manifest = build_operation_manifest(cases, completed_graph_coverage=coverage)
    assert manifest["selection_reads_b0_b1_results"] is False
    assert manifest["selection_reads_execution_outcomes"] is False
    provenance = manifest["literature_provenance"]
    assert provenance["benchmark"] == "MemoryAgentBench"
    assert provenance["venue"] == "ICLR 2026"
    assert provenance["operation"] == "Selective Forgetting / FactConsolidation"
    assert provenance["exact_memoryagentbench_reproduction"] is False
    assert manifest["source_dataset"]["non_abstention_cohort_count"] == 72
    assert coverage["completed_graph_coverage"] == 4
    assert set(coverage["paired_history_ids"]) == {"07741c45", "b6019101", "6071bd76", "a2f3aa27"}
    assert manifest["payload_sha256"]
    assert len(manifest["cases"]) == 72


def test_artifact_writer_is_append_only(tmp_path: Path, cases) -> None:
    path = tmp_path / "operation_manifest.json"
    payload = build_operation_manifest(cases[:1])
    write_operation_artifact(path, payload)
    assert json.loads(path.read_text(encoding="utf-8"))["payload_sha256"] == payload["payload_sha256"]
    with pytest.raises(LongMemEvalOperationError, match="ARTIFACT_ALREADY_EXISTS"):
        write_operation_artifact(path, payload)


def test_pinned_raw_hash_is_explicit(records: tuple[dict[str, object], ...]) -> None:
    # The loader checks the bytes, not only the parsed JSON structure.
    assert hashlib.sha256(RAW.read_bytes()).hexdigest() == EXPECTED_RAW_SHA256
