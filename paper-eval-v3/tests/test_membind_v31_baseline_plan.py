"""TDD contracts for accepting APC baselines and deriving the v3.1 live plan."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    build_apc_aligned_baseline_plan,
    derive_apc_aligned_performance,
    lifecycle_rows_from_events,
    summarize_direct_violations,
)
from paper_eval.apc_quality_targets import build_apc_quality_target_manifest
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import (
    AlignedBlockArtifactStore,
    CHECKPOINT_SCHEMA,
    EVENT_SCHEMA,
)
from paper_eval.membind_v31.baseline_acceptance import (
    BaselineAcceptanceError,
    EXPECTED_BASELINE_RUN_ID,
    verify_apc_baseline_acceptance,
)
from paper_eval.membind_v31.method_plan import (
    CACHE_AFFINITY_ORDER,
    COMPILE_WORKERS,
    LOOKAHEAD,
    MEMBIND_V31_METHODS,
    PREFIX_MATCH_UNIT,
    build_membind_v31_method_plan,
    build_membind_v31_live_plan,
    verify_membind_v31_method_plan,
)
from paper_eval.quality_evaluation_v1_suite import (
    decide_u0_freeze,
    summarize_quality_v1,
)


COUNTS = {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}
QUALITY_RUN_ID = "qev1-apc-20260817-001"


def _seal(value: dict[str, object]) -> dict[str, object]:
    result = deepcopy(value)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _sources() -> dict[str, list[str]]:
    cursor = 1
    result: dict[str, list[str]] = {}
    for history_id in APC_BASELINE_HISTORIES:
        result[history_id] = [f"{cursor + index:064x}" for index in range(COUNTS[history_id])]
        cursor += COUNTS[history_id]
    return result


def _plan() -> dict[str, object]:
    return build_apc_aligned_baseline_plan(
        run_id=EXPECTED_BASELINE_RUN_ID,
        history_source_sha256s=_sources(),
        interarrival_ns=10,
        execution_envelope_sha256="e" * 64,
        service_reference_ns=12,
        normalized_offered_load=1.2,
    )


def _complete_block(
    run_root: Path, plan: dict[str, object], block_index: int
) -> dict[str, object]:
    block = plan["blocks"][block_index]
    root = run_root / "blocks" / f"block-{block_index:02d}"
    store = AlignedBlockArtifactStore.create(
        root,
        verified_plan=plan,
        block_index=block_index,
        execution_identity_sha256="d" * 64,
    )
    events: list[dict[str, object]] = []
    timestamp = 1
    for source_sequence, source_sha256 in enumerate(store.manifest["source_sha256s"]):
        for event_type, telemetry in (
            ("ARRIVAL", {}),
            ("ENQUEUED", {"caller_return_timestamp_ns": timestamp + 1}),
            ("SERVICE_STARTED", {}),
            ("PUBLICATION_DURABLE", {"visibility_confirmed": True}),
        ):
            event = {
                "schema_version": EVENT_SCHEMA,
                "event_sequence": len(events),
                "source_sequence": source_sequence,
                "source_sha256": source_sha256,
                "event_type": event_type,
                "timestamp_ns": timestamp,
                "telemetry": telemetry,
            }
            events.append({**event, "event_sha256": payload_sha256(event)})
            timestamp += 1
    (root / "events.jsonl").write_text(
        "".join(
            json.dumps(
                {"event": {key: value for key, value in event.items() if key != "event_sha256"},
                 "event_sha256": event["event_sha256"]},
                sort_keys=True,
            )
            + "\n"
            for event in events
        ),
        encoding="utf-8",
    )
    checkpoint_body = {
        "schema_version": CHECKPOINT_SCHEMA,
        "manifest_sha256": store.manifest["manifest_sha256"],
        "event_count": len(events),
        "terminal_status": "COMPLETED",
        "completed_source_prefix": block["source_count"] - 1,
        "complete_coverage": True,
        "source_states": ["PUBLICATION_DURABLE"] * block["source_count"],
        "resume_status": "NOT_NEEDED_COMPLETE",
    }
    _write_json(
        root / "checkpoint.json",
        {**checkpoint_body, "checkpoint_sha256": payload_sha256(checkpoint_body)},
    )
    inspected_events = [dict(value) for value in events]
    lifecycle = lifecycle_rows_from_events(
        inspected_events,
        method=block["method"],
        source_count=block["source_count"],
    )
    performance = derive_apc_aligned_performance(lifecycle)
    publications = [
        value for value in events if value["event_type"] == "PUBLICATION_DURABLE"
    ]
    graph_counts = {
        key: 0
        for key in (
            "lost_episodic_count",
            "duplicate_episodic_count",
            "unexpected_episodic_count",
            "episodic_namespace_escape_count",
            "entity_namespace_escape_count",
            "relation_namespace_escape_count",
            "endpoint_escape_count",
            "provenance_dangling_count",
            "provenance_cross_namespace_count",
            "valid_invalid_reversal_count",
        )
    }
    correctness = summarize_direct_violations(
        expected_source_count=block["source_count"],
        publication_source_sequences=[value["source_sequence"] for value in publications],
        visibility_by_source={value["source_sequence"]: True for value in publications},
        graph_counts=graph_counts,
    )
    result = _seal(
        {
            "schema_version": "membind.paper-eval-v3.apc-aligned-baseline-block-result.v1",
            "status": "PASS",
            "run_id": plan["run_id"],
            "block_index": block_index,
            "method": block["method"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "episode_count": block["source_count"],
            "plan_payload_sha256": plan["payload_sha256"],
            "cache_isolation": {
                "mechanism": "REQUEST_CACHE_SALT",
                "cache_salt_sha256": block["cache_salt_sha256"],
                "cross_block_prefix_identity_reuse": False,
                "within_block_prefix_reuse": True,
            },
            "live": {
                "status": "PASS",
                "run_id": plan["run_id"],
                "block_index": block_index,
                "method": block["method"],
                "history_id": block["history_id"],
                "namespace": block["namespace"],
                "source_count": block["source_count"],
                "source_manifest_sha256": block["source_manifest_sha256"],
                "arrival_trace_sha256": block["arrival_trace_sha256"],
                "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
                "shared_execution_envelope_sha256": block[
                    "shared_execution_envelope_sha256"
                ],
                "global_llm_admission_k": block["global_llm_admission_k"],
                "execution_identity_sha256": "d" * 64,
                "initial_namespace": {
                    "node_count": 0,
                    "relationship_count": 0,
                    "episode_names": [],
                },
            },
            "performance": performance,
            "correctness": correctness,
            "vllm_telemetry": {"sample_count": 1},
            "vllm_telemetry_samples": [],
            "embedding_vllm_telemetry": {"sample_count": 1},
            "embedding_vllm_telemetry_samples": [],
        }
    )
    _write_json(root / "APC_ALIGNED_BLOCK_RESULT.json", result)
    return result


def _quality_row(
    *, target: dict[str, object], quality_identity: dict[str, str], runtime: dict[str, object]
) -> dict[str, object]:
    method = str(target["method"])
    history = str(target["history_id"])
    row = {
        "schema_version": "membind.paper-eval-v3.quality-v1-public.v1",
        "overlay_run_id": QUALITY_RUN_ID,
        "method": method,
        "history_id": history,
        "namespace_sha256": hashlib.sha256(
            str(target["namespace"]).encode("utf-8")
        ).hexdigest(),
        "construction_result_sha256": target["construction_result_sha256"],
        "runtime_identity": runtime,
        "runtime_identity_sha256": payload_sha256(runtime),
        "quality_identity": quality_identity,
        "qa_accuracy": 1,
        "judge_valid_denominator": 1,
        "session_metrics": {
            "recall_at_1": 1,
            "recall_at_3": 1,
            "recall_at_5": 1,
            "recall_at_10": 1,
            "mrr": 1,
            "ndcg_at_10": 1,
        },
        "temporal_diagnostics": {
            "stale_fact_count": 0,
            "active_fact_count": 1,
            "future_fact_count": 0,
            "conflicting_relation_group_count": 0,
            "stale_ranked_before_latest_valid_count": 0,
        },
    }
    return _seal(row)


def _complete_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object], dict[str, object]]:
    run_root = tmp_path / EXPECTED_BASELINE_RUN_ID
    run_root.mkdir()
    plan = _plan()
    _write_json(run_root / "PLAN.json", plan)
    preflight = _seal(
        {
            "schema_version": "membind.paper-eval-v3.apc-aligned-preflight.v1",
            "status": "PASS",
            "execution_identity_sha256": "d" * 64,
        }
    )
    _write_json(run_root / "PREFLIGHT.json", preflight)
    results = [_complete_block(run_root, plan, index) for index in range(12)]
    phase = _seal(
        {
            "status": "PASS",
            "phase": "full",
            "run_id": EXPECTED_BASELINE_RUN_ID,
            "completed_block_indices": list(range(12)),
        }
    )
    _write_json(run_root / "PHASE_RESULT.json", phase)
    targets = build_apc_quality_target_manifest(
        run_id=EXPECTED_BASELINE_RUN_ID, block_results=results
    )
    _write_json(run_root / "QUALITY_TARGETS.json", targets)

    quality_root = tmp_path / QUALITY_RUN_ID
    quality_identity = {
        "retrieval_config_sha256": "1" * 64,
        "reader_config_sha256": "2" * 64,
        "judge_config_sha256": "3" * 64,
        "context_policy_sha256": "4" * 64,
    }
    runtime = {"implementation": "read-only-test-runtime"}
    rows = []
    slugs = {"U0": "u0", "A0": "a0", "P(C=2)": "pc2"}
    for target in targets["targets"]:
        row = _quality_row(
            target=target, quality_identity=quality_identity, runtime=runtime
        )
        rows.append(row)
        _write_json(
            quality_root
            / "units"
            / slugs[target["method"]]
            / target["history_id"]
            / "attempt-001"
            / "public.json",
            row,
        )
    u0 = decide_u0_freeze(rows[:4])
    u0.update({"run_id": QUALITY_RUN_ID, "quality_identity": quality_identity})
    report = _seal(
        {
            "schema_version": "membind.paper-eval-v3.quality-v1-report.v1",
            "run_id": QUALITY_RUN_ID,
            "status": "PASS",
            "u0_decision": u0,
            "summary": summarize_quality_v1(rows),
            "quality_identity": quality_identity,
            "runtime_identity": runtime,
            "construction_rerun": False,
            "construction_latency_includes_quality": False,
        }
    )
    _write_json(quality_root / "QUALITY_EVALUATION_V1_RESULTS.json", report)
    return run_root, quality_root, plan, report


def _reseal_file(path: Path, mutate) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = payload_sha256(value)
    _write_json(path, value)


def test_running_baseline_is_explicitly_not_terminal_and_verifier_writes_nothing(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / EXPECTED_BASELINE_RUN_ID
    run_root.mkdir()
    _write_json(run_root / "PLAN.json", _plan())
    before = sorted(path.relative_to(run_root) for path in run_root.rglob("*"))

    result = verify_apc_baseline_acceptance(run_root, quality_root=None)

    assert result["status"] == "NOT_TERMINAL"
    assert result["completed_block_count"] == 0
    assert "payload_sha256" not in result
    assert sorted(path.relative_to(run_root) for path in run_root.rglob("*")) == before


def test_terminal_baseline_acceptance_recomputes_complete_construction_and_quality_chain(
    tmp_path: Path,
) -> None:
    run_root, quality_root, plan, report = _complete_fixture(tmp_path)

    accepted = verify_apc_baseline_acceptance(run_root, quality_root=quality_root)

    assert accepted["status"] == "PASS"
    assert accepted["artifact_status"] == "SEALED_VALID"
    assert accepted["semantic_verdicts"] == {
        "A0-aligned": {"direct_violations": 0, "semantic_status": "SAFE"},
        "P(C=2)-aligned": {"direct_violations": 0, "semantic_status": "SAFE"},
        "U0-aligned": {"direct_violations": 0, "semantic_status": "SAFE"},
    }
    assert accepted["run_id"] == EXPECTED_BASELINE_RUN_ID
    assert accepted["completed_block_count"] == 12
    assert accepted["terminal_episode_count_per_method"] == 188
    assert accepted["plan_payload_sha256"] == plan["payload_sha256"]
    assert accepted["quality_report_payload_sha256"] == report["payload_sha256"]
    assert accepted["payload_sha256"] == payload_sha256(
        {key: value for key, value in accepted.items() if key != "payload_sha256"}
    )


@pytest.mark.parametrize(
    "relative, mutate, expected",
    [
        (
            "blocks/block-00/APC_ALIGNED_BLOCK_RESULT.json",
            lambda value: value["live"].update(global_llm_admission_k=3),
            "global LLM admission",
        ),
        (
            "blocks/block-00/APC_ALIGNED_BLOCK_RESULT.json",
            lambda value: value["live"].update(arrival_trace_sha256="a" * 64),
            "arrival trace",
        ),
        (
            "blocks/block-00/APC_ALIGNED_BLOCK_RESULT.json",
            lambda value: value["correctness"].update(checker_status="UNMEASURED"),
            "correctness",
        ),
    ],
)
def test_terminal_acceptance_rejects_resealed_runtime_or_correctness_drift(
    tmp_path: Path, relative: str, mutate, expected: str
) -> None:
    run_root, quality_root, _plan_value, _report = _complete_fixture(tmp_path)
    _reseal_file(run_root / relative, mutate)

    with pytest.raises(BaselineAcceptanceError, match=expected):
        verify_apc_baseline_acceptance(run_root, quality_root=quality_root)


def test_terminal_acceptance_rejects_quality_unit_not_bound_to_construction(
    tmp_path: Path,
) -> None:
    run_root, quality_root, _plan_value, _report = _complete_fixture(tmp_path)
    public = quality_root / "units/u0/07741c45/attempt-001/public.json"
    _reseal_file(
        public,
        lambda value: value.update(construction_result_sha256="f" * 64),
    )

    with pytest.raises(BaselineAcceptanceError, match="quality construction binding"):
        verify_apc_baseline_acceptance(run_root, quality_root=quality_root)


def test_v31_plan_is_read_only_six_block_projection_with_one_representative_membind(
    tmp_path: Path,
) -> None:
    run_root, quality_root, baseline_plan, _report = _complete_fixture(tmp_path)
    accepted = verify_apc_baseline_acceptance(run_root, quality_root=quality_root)

    plan = build_membind_v31_method_plan(
        run_id="membind-v31-dev-20260817-001",
        verified_baseline_plan=baseline_plan,
        verified_baseline_acceptance=accepted,
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )

    assert verify_membind_v31_method_plan(plan) == plan
    # Durable JSON uses sorted keys, which must not make mapping insertion
    # order part of the scientific identity.
    persisted = json.loads(json.dumps(plan, sort_keys=True))
    assert verify_membind_v31_method_plan(persisted) == persisted
    assert plan["methods"] == list(MEMBIND_V31_METHODS)
    assert plan["compile_workers"] == COMPILE_WORKERS == 2
    assert plan["lookahead"] == LOOKAHEAD == 2
    assert plan["bind_workers"] == 1
    assert plan["global_llm_admission_k"] == 2
    assert plan["prefix_match_unit"] == PREFIX_MATCH_UNIT == 16
    assert plan["decode_context_parallel_size"] == 1
    assert plan["transport_admission_boundary"] == "openai_chat_completions_create_attempt"
    assert plan["cache_affinity_order"] == list(CACHE_AFFINITY_ORDER)
    assert plan["representative_history_id"] == "07741c45"
    assert len(plan["blocks"]) == 6
    assert [(row["method"], row["history_id"]) for row in plan["blocks"]] == [
        ("MemBind", "07741c45"),
        ("MemBind", "b6019101"),
        ("MemBind", "6071bd76"),
        ("MemBind", "a2f3aa27"),
        ("MemBind-Barrier", "07741c45"),
        ("MemBind-FIFO", "07741c45"),
    ]
    assert len({row["namespace"] for row in plan["blocks"]}) == 6
    assert not ({row["namespace"] for row in plan["blocks"]} & {
        row["namespace"] for row in baseline_plan["blocks"]
    })
    for row in plan["blocks"]:
        assert row["source_manifest_sha256"] == baseline_plan["source_manifest_sha256"]
        assert row["arrival_trace_sha256"] == baseline_plan["arrival_trace_sha256"]
        assert row["shared_execution_envelope_sha256"] == baseline_plan[
            "shared_execution_envelope_sha256"
        ]
        assert row["global_llm_admission_k"] == baseline_plan[
            "global_llm_admission_k"
        ]
        assert row["compile_workers"] == 2
        assert row["lookahead"] == 2
        assert row["bind_workers"] == 1
        assert row["prefix_match_unit"] == 16
        assert row["decode_context_parallel_size"] == 1
        assert row["cache_affinity_order"] == list(CACHE_AFFINITY_ORDER)


def test_live_plan_is_source_bound_before_baseline_acceptance_and_merge_needs_it_later(
    tmp_path: Path,
) -> None:
    _run_root, _quality_root, baseline_plan, _report = _complete_fixture(tmp_path)

    plan = build_membind_v31_live_plan(
        run_id="membind-v31-dev-20260818-001",
        verified_baseline_plan=baseline_plan,
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )

    assert verify_membind_v31_method_plan(plan) == plan
    assert plan["authorization_scope"] == (
        "LIVE_EXECUTION_AUTHORIZED_BASELINE_MERGE_PENDING"
    )
    assert not any("baseline_acceptance" in key for key in plan)
    assert not any(
        "baseline_acceptance" in key for block in plan["blocks"] for key in block
    )


def test_v31_plan_verifier_rejects_document_or_baseline_binding_drift(tmp_path: Path) -> None:
    run_root, quality_root, baseline_plan, _report = _complete_fixture(tmp_path)
    accepted = verify_apc_baseline_acceptance(run_root, quality_root=quality_root)
    plan = build_membind_v31_method_plan(
        run_id="membind-v31-dev-20260817-002",
        verified_baseline_plan=baseline_plan,
        verified_baseline_acceptance=accepted,
        methodology_sha256="a" * 64,
        workplan_sha256="b" * 64,
    )
    plan["blocks"][0]["history_arrival_trace_sha256"] = "c" * 64
    plan["payload_sha256"] = payload_sha256(
        {key: value for key, value in plan.items() if key != "payload_sha256"}
    )

    with pytest.raises(ValueError, match="plan identity drift"):
        verify_membind_v31_method_plan(plan)
