"""Deterministically reduce aligned per-history blocks to method table rows.

The aligned artifact layer emits one public row for every method/history
block, while the development main table intentionally accepts one row per
method.  This pure offline reducer is the only bridge between those shapes.
It verifies every row against the frozen 12-block plan and pools sealed
per-episode freshness samples; it never averages per-history percentiles or
accepts post-result weighting, filtering, or outlier controls.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import verify_public_aligned_row
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    ALIGNED_METHODS,
    verify_aligned_development_plan,
)


FRESHNESS_SAMPLES_SCHEMA = (
    "membind.paper-eval-v3.membind-v1-aligned-freshness-samples.v1"
)


class AlignedReduceError(ValueError):
    """A block inventory, seal, source sample, or metric failed closed."""


def _fail(code: str) -> AlignedReduceError:
    return AlignedReduceError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return value


def _sequence(value: object, code: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _positive_int(value: object, code: str) -> int:
    result = _nonnegative_int(value, code)
    if result < 1:
        raise _fail(code)
    return result


def _number(value: object, code: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise _fail(code)
    return float(value)


def _probability(value: object, code: str) -> float:
    result = _number(value, code)
    if not 0.0 <= result <= 1.0:
        raise _fail(code)
    return result


def _nearest_rank(values: Sequence[int], probability: float) -> int:
    if not values:
        raise _fail("freshness sample inventory empty")
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _verified_plan(value: Mapping[str, object]) -> dict[str, Any]:
    try:
        return verify_aligned_development_plan(value)
    except ValueError:
        raise _fail("verified plan invalid") from None


def _block_index(value: object) -> int:
    return _nonnegative_int(value, "block inventory invalid")


def _verify_row(
    value: Mapping[str, object], *, plan: Mapping[str, object], block_index: int
) -> dict[str, object]:
    try:
        return verify_public_aligned_row(
            value,
            verified_plan=plan,
            block_index=block_index,
        )
    except (TypeError, ValueError):
        raise _fail("public row invalid") from None


def _validate_consumed_metrics(row: Mapping[str, object]) -> dict[str, object]:
    metrics = _mapping(row.get("metrics"), "public metrics invalid")
    # Validate both consumed and replaced projections.  The pooled tails and
    # recomputed goodput below do not trust their block-level counterparts,
    # but malformed public metrics still cannot cross this reducer boundary.
    result: dict[str, object] = {
        "qa_accuracy": _probability(
            metrics.get("qa_accuracy"), "QA accuracy invalid"
        ),
        "evidence_recall_at_10": _probability(
            metrics.get("evidence_recall_at_10"),
            "Evidence Recall@10 invalid",
        ),
        "direct_violations": _nonnegative_int(
            metrics.get("direct_violations"), "direct violations invalid"
        ),
        "p95_arrival_to_publication_ns": _positive_int(
            metrics.get("p95_arrival_to_publication_ns"),
            "block P95 freshness invalid",
        ),
        "p99_arrival_to_publication_ns": _positive_int(
            metrics.get("p99_arrival_to_publication_ns"),
            "block P99 freshness invalid",
        ),
        "successful_goodput_episodes_per_second": _number(
            metrics.get("successful_goodput_episodes_per_second"),
            "block goodput invalid",
        ),
        "makespan_ns": _positive_int(
            metrics.get("makespan_ns"), "makespan invalid"
        ),
        "max_backlog": _nonnegative_int(
            metrics.get("max_backlog"), "max backlog invalid"
        ),
    }
    if result["successful_goodput_episodes_per_second"] < 0:
        raise _fail("block goodput invalid")
    return result


def _sample_body(
    *,
    plan: Mapping[str, object],
    row: Mapping[str, object],
    samples: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": FRESHNESS_SAMPLES_SCHEMA,
        "aligned_run_id": plan["aligned_run_id"],
        "block_index": row["block_index"],
        "method": row["method"],
        "history_id": row["history_id"],
        "plan_payload_sha256": plan["payload_sha256"],
        "public_row_sha256": row["row_sha256"],
        "source_count": row["source_count"],
        "samples": [deepcopy(dict(sample)) for sample in samples],
    }


def _validate_sample_record(
    value: Mapping[str, object],
    *,
    plan: Mapping[str, object],
    row: Mapping[str, object],
) -> dict[str, object]:
    record = deepcopy(dict(value))
    expected_keys = {
        "schema_version",
        "aligned_run_id",
        "block_index",
        "method",
        "history_id",
        "plan_payload_sha256",
        "public_row_sha256",
        "source_count",
        "samples",
        "samples_sha256",
    }
    if set(record) != expected_keys or record.get(
        "schema_version"
    ) != FRESHNESS_SAMPLES_SCHEMA:
        raise _fail("freshness samples invalid")
    stored = record.get("samples_sha256")
    body = {key: item for key, item in record.items() if key != "samples_sha256"}
    if not isinstance(stored, str) or stored != payload_sha256(body):
        raise _fail("freshness samples hash invalid")
    expected_identity = {
        "aligned_run_id": plan["aligned_run_id"],
        "block_index": row["block_index"],
        "method": row["method"],
        "history_id": row["history_id"],
        "plan_payload_sha256": plan["payload_sha256"],
        "public_row_sha256": row["row_sha256"],
        "source_count": row["source_count"],
    }
    if any(record.get(key) != expected for key, expected in expected_identity.items()):
        raise _fail("freshness samples identity invalid")

    samples = _sequence(record.get("samples"), "freshness samples invalid")
    expected_sources = plan["history_source_sha256s"][row["history_id"]]
    if len(samples) != len(expected_sources):
        raise _fail("freshness sample count invalid")
    normalized: list[dict[str, object]] = []
    for expected_sequence, expected_source in enumerate(expected_sources):
        sample = _mapping(samples[expected_sequence], "freshness sample invalid")
        if set(sample) != {
            "source_sequence",
            "source_sha256",
            "arrival_to_publication_ns",
        }:
            raise _fail("freshness sample invalid")
        if (
            sample.get("source_sequence") != expected_sequence
            or sample.get("source_sha256") != expected_source
        ):
            raise _fail("freshness source coverage invalid")
        normalized.append(
            {
                "source_sequence": expected_sequence,
                "source_sha256": expected_source,
                "arrival_to_publication_ns": _nonnegative_int(
                    sample.get("arrival_to_publication_ns"),
                    "freshness latency invalid",
                ),
            }
        )
    return {**record, "samples": normalized}


def build_aligned_freshness_samples(
    *,
    verified_plan: Mapping[str, object],
    public_row: Mapping[str, object],
    samples: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Seal complete source-level freshness samples for one public block row."""

    plan = _verified_plan(verified_plan)
    row_mapping = _mapping(public_row, "public row invalid")
    block_index = _block_index(row_mapping.get("block_index"))
    row = _verify_row(row_mapping, plan=plan, block_index=block_index)
    raw_samples = _sequence(samples, "freshness samples invalid")
    body = _sample_body(
        plan=plan,
        row=row,
        samples=[_mapping(item, "freshness sample invalid") for item in raw_samples],
    )
    record = {**body, "samples_sha256": payload_sha256(body)}
    return _validate_sample_record(record, plan=plan, row=row)


def _indexed_rows(
    *, plan: Mapping[str, object], public_rows: Sequence[Mapping[str, object]]
) -> dict[int, dict[str, object]]:
    blocks = plan["blocks"]
    if len(public_rows) != len(blocks):
        raise _fail("block inventory invalid")
    indexed: dict[int, dict[str, object]] = {}
    for raw in public_rows:
        row_mapping = _mapping(raw, "public row invalid")
        index = _block_index(row_mapping.get("block_index"))
        if index in indexed or index >= len(blocks):
            raise _fail("block inventory invalid")
        indexed[index] = _verify_row(row_mapping, plan=plan, block_index=index)
    if set(indexed) != set(range(len(blocks))):
        raise _fail("block inventory invalid")
    expected_pairs = {
        (method, history_id)
        for method in ALIGNED_METHODS
        for history_id in ALIGNED_DEVELOPMENT_HISTORIES
    }
    observed_pairs = {
        (row["method"], row["history_id"]) for row in indexed.values()
    }
    if observed_pairs != expected_pairs or len(observed_pairs) != len(indexed):
        raise _fail("method/history block inventory invalid")
    return indexed


def _indexed_samples(
    *,
    plan: Mapping[str, object],
    rows: Mapping[int, Mapping[str, object]],
    freshness_records: Sequence[Mapping[str, object]],
) -> dict[int, dict[str, object]]:
    if len(freshness_records) != len(rows):
        raise _fail("freshness block inventory invalid")
    indexed: dict[int, dict[str, object]] = {}
    for raw in freshness_records:
        record = _mapping(raw, "freshness samples invalid")
        index = _block_index(record.get("block_index"))
        if index in indexed or index not in rows:
            raise _fail("freshness block inventory invalid")
        indexed[index] = _validate_sample_record(
            record,
            plan=plan,
            row=rows[index],
        )
    if set(indexed) != set(rows):
        raise _fail("freshness block inventory invalid")
    return indexed


def reduce_aligned_blocks(
    *,
    verified_plan: Mapping[str, object],
    public_rows: Sequence[Mapping[str, object]],
    freshness_records: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Reduce exactly 12 verified block outputs to three comparable rows.

    QA and Evidence Recall@10 are macro-averaged across the four histories.
    Freshness tails use deterministic nearest-rank over all episode samples.
    Direct violations and makespans are summed, goodput is total episodes over
    total makespan, and max backlog is the maximum observed history value.
    """

    plan = _verified_plan(verified_plan)
    row_items = _sequence(public_rows, "block inventory invalid")
    sample_items = _sequence(freshness_records, "freshness block inventory invalid")
    rows = _indexed_rows(plan=plan, public_rows=row_items)
    sample_records = _indexed_samples(
        plan=plan,
        rows=rows,
        freshness_records=sample_items,
    )

    quality_statuses = {row["quality_status"] for row in rows.values()}
    if len(quality_statuses) != 1:
        raise _fail("quality status inconsistent")
    quality_status = next(iter(quality_statuses))

    reduced: list[dict[str, object]] = []
    for method in ALIGNED_METHODS:
        method_rows = sorted(
            (row for row in rows.values() if row["method"] == method),
            key=lambda row: ALIGNED_DEVELOPMENT_HISTORIES.index(row["history_id"]),
        )
        if [row["history_id"] for row in method_rows] != list(
            ALIGNED_DEVELOPMENT_HISTORIES
        ):
            raise _fail("method/history block inventory invalid")

        metrics = [_validate_consumed_metrics(row) for row in method_rows]
        freshness: list[int] = []
        episode_count = 0
        for row in method_rows:
            record = sample_records[row["block_index"]]
            samples = record["samples"]
            episode_count += len(samples)
            freshness.extend(
                sample["arrival_to_publication_ns"] for sample in samples
            )
        makespan_ns = sum(item["makespan_ns"] for item in metrics)
        p95 = _nearest_rank(freshness, 0.95)
        p99 = _nearest_rank(freshness, 0.99)
        # main_table's comparable-row contract is strictly positive even
        # though source-level accounting permits a zero-duration synthetic
        # sample.  Fail here instead of emitting an unusable projection.
        if p95 < 1 or p99 < 1:
            raise _fail("pooled freshness percentile invalid")

        reduced.append(
            {
                "method": method,
                "execution_status": "COMPLETED",
                "validity_status": "VALID",
                "quality_status": quality_status,
                "aligned_run_id": plan["aligned_run_id"],
                "arrival_trace_sha256": plan["arrival_trace_sha256"],
                "source_manifest_sha256": plan["source_manifest_sha256"],
                "shared_execution_envelope_sha256": plan[
                    "shared_execution_envelope_sha256"
                ],
                "global_llm_admission_k": 2,
                "metrics": {
                    "qa_accuracy": sum(item["qa_accuracy"] for item in metrics)
                    / len(method_rows),
                    "evidence_recall_at_10": sum(
                        item["evidence_recall_at_10"] for item in metrics
                    )
                    / len(method_rows),
                    "direct_violations": sum(
                        item["direct_violations"] for item in metrics
                    ),
                    "p95_arrival_to_publication_ns": p95,
                    "p99_arrival_to_publication_ns": p99,
                    "successful_goodput_episodes_per_second": episode_count
                    / (makespan_ns / 1_000_000_000),
                    "makespan_ns": makespan_ns,
                    "max_backlog": max(item["max_backlog"] for item in metrics),
                },
            }
        )
    return reduced


__all__ = [
    "AlignedReduceError",
    "FRESHNESS_SAMPLES_SCHEMA",
    "build_aligned_freshness_samples",
    "reduce_aligned_blocks",
]
