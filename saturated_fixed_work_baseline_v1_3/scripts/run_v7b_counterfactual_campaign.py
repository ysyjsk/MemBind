#!/usr/bin/env python3
"""Generate provider-free FRESH/C0/C1 counterfactual evidence.

FRESH is the frozen V7-FRESH control.  C0 repairs the full transitive
dependency closure.  C1 uses guarded dynamic repair and exact reconvergence.
The campaign is deliberately provider-free and therefore cannot authorize a
live treatment or establish a wall-clock speedup.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "saturated_fixed_work_baseline_v1_3/src"))

from saturated_fixed_work_baseline_v1_3.membind_v7.v7b import (  # noqa: E402
    ViewDefinition,
    V7FreshEngine,
    V7IncrementalEngine,
    extract_source_ir,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.state_delta import (  # noqa: E402
    DeltaChange,
    StateDelta,
)


def definitions() -> tuple[ViewDefinition, ...]:
    return (
        ViewDefinition(
            "source_ir", kind="source_local", cost=2.0,
            compute=lambda ir, _state, _views: ir.digest,
        ),
        ViewDefinition(
            "mention_inventory", kind="source_local", predecessors=("source_ir",), cost=3.0,
            compute=lambda ir, _state, views: {
                "ir": views["source_ir"],
                "mentions": tuple(item.normalized for item in ir.mentions),
            },
        ),
        ViewDefinition(
            "entity_resolution", kind="stateful", predecessors=("mention_inventory",),
            state_dependencies=frozenset({"entities"}), cost=8.0,
            compute=lambda _ir, state, views: {
                "mentions": views["mention_inventory"]["mentions"],
                "known_entities": tuple(sorted(state.get("entities", ()) or ())),
            },
        ),
        ViewDefinition(
            "relation_resolution", kind="stateful", predecessors=("entity_resolution",),
            state_dependencies=frozenset({"relations", "entities"}), cost=7.0,
            compute=lambda _ir, state, views: {
                "entity": views["entity_resolution"],
                "relations": tuple(sorted(state.get("relations", ()) or ())),
            },
        ),
        ViewDefinition(
            "temporal_view", kind="stateful", predecessors=("relation_resolution",),
            state_dependencies=frozenset({"temporal"}), cost=4.0,
            compute=lambda _ir, state, views: {
                "relation": views["relation_resolution"],
                "temporal": tuple(sorted(state.get("temporal", ()) or ())),
            },
        ),
        ViewDefinition(
            "temporal_year_bucket", kind="stateful", predecessors=("temporal_view",),
            state_dependencies=frozenset({"temporal"}), cost=2.0,
            compute=lambda _ir, state, _views: tuple(
                sorted({str(value)[:4] for value in (state.get("temporal", ()) or ())})
            ),
        ),
        # A downstream view that depends only on the normalized year bucket.
        # It is intentionally independent of the raw temporal delta so C1 can
        # demonstrate its guarded stop rule when the bucket reconverges.
        ViewDefinition(
            "year_only_index", kind="stateful", predecessors=("temporal_year_bucket",),
            state_dependencies=frozenset({"unrelated"}), cost=9.0,
            compute=lambda _ir, _state, views: {"year": views["temporal_year_bucket"]},
        ),
        ViewDefinition(
            "publication_plan", kind="stateful",
            predecessors=("temporal_view", "temporal_year_bucket", "year_only_index"),
            state_dependencies=frozenset({"publication"}), cost=5.0,
            compute=lambda _ir, state, views: {
                "temporal": views["temporal_view"],
                "year_bucket": views["temporal_year_bucket"],
                "publication": state.get("publication", "ordered"),
            },
        ),
    )


def _state(frontier: int, entities: tuple[str, ...], relations: tuple[str, ...], temporal: tuple[str, ...]) -> dict[str, Any]:
    return {
        "frontier": frontier,
        "entities": entities,
        "relations": relations,
        "temporal": temporal,
        "publication": "ordered",
    }


def _pair(defs: tuple[ViewDefinition, ...], block_id: str, source_index: int, mutate: str) -> dict[str, Any]:
    ir = extract_source_ir(f"{block_id}-s{source_index}", f"Alice met Bob in block {block_id}, source {source_index}.")
    old_state = _state(0, ("alice",), ("knows",), ("2025-01-01",))
    new_state = _state(1, ("alice",), ("knows",), ("2025-01-01",))
    if mutate == "relations":
        new_state["relations"] = ("knows", "met")
    elif mutate == "temporal":
        new_state["temporal"] = ("2025-01-01", "2025-01-02")
    elif mutate == "entities":
        new_state["entities"] = ("alice", "bob")
    else:
        raise ValueError(f"unknown mutation: {mutate}")
    fresh_engine = V7FreshEngine(defs)
    old = fresh_engine.build(ir, old_state)
    fresh = fresh_engine.build(ir, new_state)
    delta = StateDelta(
        0, 1,
        changes=(DeltaChange("memory", mutate, changed_fields=frozenset({mutate}), before={mutate: old_state[mutate]}, after={mutate: new_state[mutate]}),),
    )
    c0 = V7IncrementalEngine(defs).maintain(old, ir, new_state, delta)
    c1 = V7IncrementalEngine(defs).maintain_guarded(old, ir, new_state, delta)
    if c0.canonical_views != fresh.canonical_views or c1.canonical_views != fresh.canonical_views:
        raise RuntimeError("FRESH/C0/C1 canonical differential mismatch")
    total = float(fresh.work_cost)
    row = {
        "block_id": block_id,
        "source_index": source_index,
        "mutation": mutate,
        "fresh_work": total,
        "c0_work": float(c0.work_cost),
        "c1_work": float(c1.work_cost),
        "c0_saved_work": total - float(c0.work_cost),
        "c1_saved_work": total - float(c1.work_cost),
        "c0_affected_work_fraction": float(c0.work_cost) / total if total else None,
        "c1_affected_work_fraction": float(c1.work_cost) / total if total else None,
        "stable_ir_fraction": sum(v.cost for v in fresh.views.values() if v.kind == "source_local") / total if total else None,
        "c0_repaired_views": list(c0.repaired_view_ids),
        "c1_repaired_views": list(c1.repaired_view_ids),
        "c0_reused_views": list(c0.reused_view_ids),
        "c1_reused_views": list(c1.reused_view_ids),
        "c1_reconverged_views": list(c1.reconverged_view_ids),
        "c0_fallback": c0.fallback,
        "c1_fallback": c1.fallback,
        "canonical_differential_equal": True,
    }
    return row


def _longest_path_cost(defs: tuple[ViewDefinition, ...], removed: set[str]) -> float:
    """Provider-free serialized lower bound for the explicit view DAG."""

    by_id = {item.view_id: item for item in defs}
    memo: dict[str, float] = {}

    def path(view_id: str) -> float:
        if view_id in memo:
            return memo[view_id]
        item = by_id[view_id]
        own = 0.0 if view_id in removed else float(item.cost)
        value = own + max((path(parent) for parent in item.predecessors), default=0.0)
        memo[view_id] = value
        return value

    return max((path(item.view_id) for item in defs), default=0.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_root.resolve()
    if root.exists():
        raise SystemExit(f"refusing to overwrite existing output root: {root}")
    root.mkdir(parents=True, mode=0o700)
    defs = definitions()
    rows = [_pair(defs, "R2-two-source", 0, "entities")]
    rows.extend(_pair(defs, "R3-A-six-source", i, "entities") for i in range(6))
    rows.extend(_pair(defs, "R3-B-six-source", i, "temporal") for i in range(6))
    fresh_total = sum(row["fresh_work"] for row in rows)
    c0_total = sum(row["c0_work"] for row in rows)
    c1_total = sum(row["c1_work"] for row in rows)
    summary = {
        "schema_version": "membind.v7b.counterfactual-campaign.v1",
        "campaign_scope": "PROVIDER_FREE_FRESH_C0_C1_TDD",
        "provider_calls": 0,
        "treatment_calls": 0,
        "source_pairs": len(rows),
        "all_canonical_differential_equal": all(row["canonical_differential_equal"] for row in rows),
        "fallback_count_c0": sum(1 for row in rows if row["c0_fallback"]),
        "fallback_count_c1": sum(1 for row in rows if row["c1_fallback"]),
        "fresh_work_total": fresh_total,
        "c0_work_total": c0_total,
        "c1_work_total": c1_total,
        "c0_saved_work_total": fresh_total - c0_total,
        "c1_saved_work_total": fresh_total - c1_total,
        "c0_affected_work_fraction_mean": c0_total / fresh_total if fresh_total else None,
        "c1_affected_work_fraction_mean": c1_total / fresh_total if fresh_total else None,
        "c1_vs_c0_work_reduction": (c0_total - c1_total) / c0_total if c0_total else None,
        "stable_ir_fraction_mean": sum(row["stable_ir_fraction"] for row in rows) / len(rows),
        "c1_reconvergence_pair_fraction": sum(1 for row in rows if row["c1_reconverged_views"]) / len(rows),
        "critical_path_lower_bound": {
            "fresh_serial_view_dag_cost": _longest_path_cost(defs, set()),
            "note": "provider-free work-cost lower bound; not a wall-clock or live authorization",
            "d0_status": "PENDING_LIVE_V7_FRESH_EXECUTION_DAG"
        },
        "rows": rows,
        "next_gate": "B0_MATCHED_QUALITY_AND_LIVE_OBSERVER_TARGET_AUDIT"
    }
    (root / "counterfactual_summary.json").write_text(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="ascii")
    (root / "counterfactual_summary.json").chmod(0o600)
    (root / "counterfactual_rows.jsonl").write_text("".join(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n" for row in rows), encoding="ascii")
    (root / "counterfactual_rows.jsonl").chmod(0o600)
    print(json.dumps({k: summary[k] for k in ("source_pairs", "all_canonical_differential_equal", "c0_affected_work_fraction_mean", "c1_affected_work_fraction_mean", "c1_vs_c0_work_reduction")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
