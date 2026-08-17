"""TDD contract for reducing 12 aligned blocks to three method rows.

The live lane publishes one sealed row per (method, history) block.  The main
table accepts one row per method.  These tests freeze the missing offline
boundary, including the per-episode samples needed to pool tail freshness
without averaging per-history percentiles.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import (
    MANIFEST_SCHEMA,
    PUBLIC_ROW_SCHEMA,
)
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    ALIGNED_METHODS,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.aligned_reduce import (
    AlignedReduceError,
    build_aligned_freshness_samples,
    reduce_aligned_blocks,
)


def _plan() -> dict[str, object]:
    sources = {
        history_id: [
            payload_sha256({"history_id": history_id, "source_sequence": sequence})
            for sequence in range(5)
        ]
        for history_id in ALIGNED_DEVELOPMENT_HISTORIES
    }
    return verify_aligned_development_plan(
        build_aligned_development_plan(
            aligned_run_id="aligned-reduce-test-001",
            history_source_sha256s=sources,
            interarrival_ns=10,
            shared_execution_envelope_sha256="a" * 64,
        )
    )


def _sealed_public_row(
    plan: dict[str, object], block: dict[str, object]
) -> dict[str, object]:
    method_index = ALIGNED_METHODS.index(block["method"])
    history_index = ALIGNED_DEVELOPMENT_HISTORIES.index(block["history_id"])
    execution_identity = f"{block['block_index'] + 1:064x}"
    sources = plan["history_source_sha256s"][block["history_id"]]
    manifest_body = {
        "schema_version": MANIFEST_SCHEMA,
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "namespace": block["namespace"],
        "source_sha256s": sources,
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block[
            "history_arrival_trace_sha256"
        ],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": 2,
        "plan_payload_sha256": plan["payload_sha256"],
        "plan_block_sha256": payload_sha256(block),
        "execution_identity_sha256": execution_identity,
    }
    manifest_sha256 = payload_sha256(manifest_body)
    body = {
        "schema_version": PUBLIC_ROW_SCHEMA,
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "source_count": len(sources),
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block[
            "history_arrival_trace_sha256"
        ],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": 2,
        "plan_payload_sha256": plan["payload_sha256"],
        "plan_block_sha256": payload_sha256(block),
        "manifest_sha256": manifest_sha256,
        "execution_identity_sha256": execution_identity,
        "checkpoint_sha256": f"{block['block_index'] + 101:064x}",
        "execution_status": "COMPLETED",
        "validity_status": "VALID",
        "quality_status": "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE",
        # Deliberately unrelated block percentiles prove the reducer pools raw
        # episode samples rather than averaging pre-aggregated tails.
        "metrics": {
            "qa_accuracy": history_index * 0.2,
            "evidence_recall_at_10": 1.0 - history_index * 0.2,
            "direct_violations": history_index,
            "p95_arrival_to_publication_ns": 999_998,
            "p99_arrival_to_publication_ns": 999_999,
            "successful_goodput_episodes_per_second": 999.0,
            "makespan_ns": 1_000 * (history_index + 1) * (method_index + 1),
            "max_backlog": method_index + history_index,
        },
    }
    return {**body, "row_sha256": payload_sha256(body)}


def _inputs() -> tuple[
    dict[str, object], list[dict[str, object]], list[dict[str, object]]
]:
    plan = _plan()
    rows = [_sealed_public_row(plan, block) for block in plan["blocks"]]
    samples = []
    for row in rows:
        method_index = ALIGNED_METHODS.index(row["method"])
        history_index = ALIGNED_DEVELOPMENT_HISTORIES.index(row["history_id"])
        values = [
            {
                "source_sequence": sequence,
                "source_sha256": source_sha256,
                "arrival_to_publication_ns": (
                    (method_index + 1) * 1_000 + history_index * 100 + sequence + 1
                ),
            }
            for sequence, source_sha256 in enumerate(
                plan["history_source_sha256s"][row["history_id"]]
            )
        ]
        samples.append(
            build_aligned_freshness_samples(
                verified_plan=plan,
                public_row=row,
                samples=values,
            )
        )
    return plan, rows, samples


def _reseal_samples(value: dict[str, object]) -> None:
    value["samples_sha256"] = payload_sha256(
        {key: item for key, item in value.items() if key != "samples_sha256"}
    )


def test_reducer_builds_three_ordered_main_table_rows_with_frozen_formulas() -> None:
    plan, rows, samples = _inputs()

    reduced = reduce_aligned_blocks(
        verified_plan=plan,
        public_rows=list(reversed(rows)),
        freshness_records=list(reversed(samples)),
    )

    assert [row["method"] for row in reduced] == list(ALIGNED_METHODS)
    for method_index, row in enumerate(reduced):
        assert row["execution_status"] == "COMPLETED"
        assert row["validity_status"] == "VALID"
        assert row["quality_status"] == "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE"
        assert row["aligned_run_id"] == plan["aligned_run_id"]
        assert row["source_manifest_sha256"] == plan["source_manifest_sha256"]
        assert row["arrival_trace_sha256"] == plan["arrival_trace_sha256"]
        assert row["shared_execution_envelope_sha256"] == plan[
            "shared_execution_envelope_sha256"
        ]
        assert row["global_llm_admission_k"] == 2

        metrics = row["metrics"]
        assert metrics["qa_accuracy"] == pytest.approx(0.3)
        assert metrics["evidence_recall_at_10"] == pytest.approx(0.7)
        assert metrics["direct_violations"] == 6
        assert metrics["p95_arrival_to_publication_ns"] == (
            (method_index + 1) * 1_000 + 304
        )
        assert metrics["p99_arrival_to_publication_ns"] == (
            (method_index + 1) * 1_000 + 305
        )
        expected_makespan = 10_000 * (method_index + 1)
        assert metrics["makespan_ns"] == expected_makespan
        assert metrics["successful_goodput_episodes_per_second"] == pytest.approx(
            20 / (expected_makespan / 1_000_000_000)
        )
        assert metrics["max_backlog"] == method_index + 3


def test_reducer_is_deterministic_under_input_order_and_exposes_no_tuning_knobs() -> None:
    plan, rows, samples = _inputs()
    expected = reduce_aligned_blocks(
        verified_plan=plan,
        public_rows=rows,
        freshness_records=samples,
    )

    assert reduce_aligned_blocks(
        verified_plan=plan,
        public_rows=rows[4:] + rows[:4],
        freshness_records=samples[7:] + samples[:7],
    ) == expected


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda _plan, rows, _samples: rows.pop(), "block inventory"),
        (
            lambda _plan, rows, _samples: rows.__setitem__(1, deepcopy(rows[0])),
            "block inventory",
        ),
        (
            lambda _plan, rows, _samples: rows[0].update(row_sha256="0" * 64),
            "public row",
        ),
        (
            lambda _plan, _rows, samples: samples[0]["samples"][0].update(
                arrival_to_publication_ns=123
            ),
            "freshness samples hash",
        ),
        (
            lambda _plan, _rows, samples: samples[0].update(
                public_row_sha256=samples[1]["public_row_sha256"]
            ),
            "freshness samples hash",
        ),
    ],
)
def test_reducer_fails_closed_on_inventory_row_seal_or_sample_seal_drift(
    mutation, message
) -> None:
    plan, rows, samples = _inputs()
    mutation(plan, rows, samples)

    with pytest.raises(AlignedReduceError, match=message):
        reduce_aligned_blocks(
            verified_plan=plan,
            public_rows=rows,
            freshness_records=samples,
        )


def test_reducer_rejects_resealed_missing_or_duplicate_source_coverage() -> None:
    plan, rows, samples = _inputs()
    samples[0]["samples"].pop()
    _reseal_samples(samples[0])

    with pytest.raises(AlignedReduceError, match="sample count"):
        reduce_aligned_blocks(
            verified_plan=plan,
            public_rows=rows,
            freshness_records=samples,
        )

    plan, rows, samples = _inputs()
    samples[0]["samples"][1] = deepcopy(samples[0]["samples"][0])
    _reseal_samples(samples[0])

    with pytest.raises(AlignedReduceError, match="source coverage"):
        reduce_aligned_blocks(
            verified_plan=plan,
            public_rows=rows,
            freshness_records=samples,
        )


def test_reducer_rejects_resealed_row_with_invalid_metric_or_quality_status_drift() -> None:
    plan, rows, samples = _inputs()
    rows[0]["metrics"]["qa_accuracy"] = 2.0
    rows[0]["row_sha256"] = payload_sha256(
        {key: value for key, value in rows[0].items() if key != "row_sha256"}
    )
    samples[0]["public_row_sha256"] = rows[0]["row_sha256"]
    _reseal_samples(samples[0])

    with pytest.raises(AlignedReduceError, match="QA accuracy"):
        reduce_aligned_blocks(
            verified_plan=plan,
            public_rows=rows,
            freshness_records=samples,
        )

    plan, rows, samples = _inputs()
    rows[0]["quality_status"] = "NUMERICALLY_COMPARABLE"
    rows[0]["row_sha256"] = payload_sha256(
        {key: value for key, value in rows[0].items() if key != "row_sha256"}
    )
    samples[0]["public_row_sha256"] = rows[0]["row_sha256"]
    _reseal_samples(samples[0])

    with pytest.raises(AlignedReduceError, match="quality status"):
        reduce_aligned_blocks(
            verified_plan=plan,
            public_rows=rows,
            freshness_records=samples,
        )
