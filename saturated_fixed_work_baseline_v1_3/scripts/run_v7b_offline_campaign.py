#!/usr/bin/env python3
"""Run the provider-free V7-B causal/observer campaign.

The script intentionally never imports a model client, Graphiti, Neo4j or an
embedder.  It produces append-only offline evidence for the Stable IR and
semantic-view boundary before any live treatment can be authorized.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "saturated_fixed_work_baseline_v1_3"
sys.path.insert(0, str(PACKAGE / "src"))

from saturated_fixed_work_baseline_v1_3.membind_v7.v7b import (  # noqa: E402
    V7FreshEngine,
    V7IncrementalEngine,
    ViewDefinition,
    extract_source_ir,
    materialize_offline_artifacts,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.state_delta import (  # noqa: E402
    DeltaChange,
    StateDelta,
)


def definitions() -> tuple[ViewDefinition, ...]:
    return (
        ViewDefinition(
            "source_ir",
            kind="source_local",
            cost=2.0,
            compute=lambda ir, _state, _views: ir.digest,
        ),
        ViewDefinition(
            "mention_inventory",
            kind="source_local",
            predecessors=("source_ir",),
            cost=3.0,
            compute=lambda ir, _state, views: {
                "ir": views["source_ir"],
                "mentions": tuple(item.normalized for item in ir.mentions),
            },
        ),
        ViewDefinition(
            "entity_resolution",
            kind="stateful",
            predecessors=("mention_inventory",),
            state_dependencies=frozenset({"entities"}),
            cost=8.0,
            compute=lambda _ir, state, views: {
                "mentions": views["mention_inventory"]["mentions"],
                "known_entities": tuple(sorted(state.get("entities", ()) or ())),
            },
        ),
        ViewDefinition(
            "relation_resolution",
            kind="stateful",
            predecessors=("entity_resolution",),
            state_dependencies=frozenset({"relations", "entities"}),
            cost=7.0,
            compute=lambda _ir, state, views: {
                "entity": views["entity_resolution"],
                "relations": tuple(sorted(state.get("relations", ()) or ())),
            },
        ),
        ViewDefinition(
            "temporal_view",
            kind="stateful",
            predecessors=("relation_resolution",),
            state_dependencies=frozenset({"temporal"}),
            cost=4.0,
            compute=lambda _ir, state, views: {
                "relation": views["relation_resolution"],
                "temporal": tuple(sorted(state.get("temporal", ()) or ())),
            },
        ),
        ViewDefinition(
            "temporal_year_bucket",
            kind="stateful",
            predecessors=("temporal_view",),
            state_dependencies=frozenset({"temporal"}),
            cost=2.0,
            # Same-year insertions invalidate and then exactly reconverge.
            compute=lambda _ir, state, _views: tuple(
                sorted({str(value)[:4] for value in (state.get("temporal", ()) or ())})
            ),
        ),
        ViewDefinition(
            "publication_plan",
            kind="stateful",
            predecessors=("temporal_view", "temporal_year_bucket"),
            state_dependencies=frozenset({"publication"}),
            cost=5.0,
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


def run_pair(root: Path, *, block_id: str, source_index: int, mutate: str) -> dict[str, Any]:
    defs = definitions()
    fresh_engine = V7FreshEngine(defs)
    incremental_engine = V7IncrementalEngine(defs)
    source = f"Alice met Bob in block {block_id}, source {source_index}."
    ir = extract_source_ir(f"{block_id}-s{source_index}", source)
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
    old = fresh_engine.build(ir, old_state)
    fresh = fresh_engine.build(ir, new_state)
    delta = StateDelta(
        source_version=0,
        target_version=1,
        changes=(
            DeltaChange(
                "memory",
                mutate,
                changed_fields=frozenset({mutate}),
                before={mutate: old_state[mutate]},
                after={mutate: new_state[mutate]},
            ),
        ),
    )
    incremental = incremental_engine.maintain(old, ir, new_state, delta)
    if incremental.canonical_views != fresh.canonical_views:
        raise RuntimeError("provider-free differential mismatch")
    pair_root = root / block_id / f"source-{source_index:02d}"
    materialize_offline_artifacts(pair_root, fresh=fresh, incremental=incremental, delta=delta)
    total = fresh.work_cost
    return {
        "block_id": block_id,
        "source_index": source_index,
        "mutation": mutate,
        "fresh_work": total,
        "incremental_work": incremental.work_cost,
        "saved_work": total - incremental.work_cost,
        "stable_ir_fraction": sum(v.cost for v in fresh.views.values() if v.kind == "source_local") / total,
        "affected_work_fraction": incremental.work_cost / total,
        "reused_views": list(incremental.reused_view_ids),
        "repaired_views": list(incremental.repaired_view_ids),
        "reconverged_views": list(incremental.reconverged_view_ids),
        "fallback": incremental.fallback,
        "canonical_equal": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v7b-offline-campaign-20260829"),
    )
    args = parser.parse_args()
    root = args.output_root.resolve()
    if root.exists():
        raise SystemExit(f"refusing to overwrite existing output root: {root}")
    root.mkdir(parents=True, mode=0o700)
    rows: list[dict[str, Any]] = []
    rows.append(run_pair(root, block_id="R2-two-source", source_index=0, mutate="entities"))
    for block_id, mutate in (("R3-A-six-source", "entities"), ("R3-B-six-source", "temporal")):
        for index in range(6):
            rows.append(run_pair(root, block_id=block_id, source_index=index, mutate=mutate))
    total = sum(row["fresh_work"] for row in rows)
    saved = sum(row["saved_work"] for row in rows)
    summary = {
        "schema_version": "membind.v7b.offline-campaign.v1",
        "campaign_scope": "PROVIDER_FREE_TDD_OBSERVER_ONLY",
        "provider_calls": 0,
        "treatment_calls": 0,
        "source_pairs": len(rows),
        "all_differential_equal": all(row["canonical_equal"] for row in rows),
        "fallback_count": sum(1 for row in rows if row["fallback"]),
        "stable_ir_fraction_mean": sum(row["stable_ir_fraction"] for row in rows) / len(rows),
        "affected_work_fraction_mean": sum(row["affected_work_fraction"] for row in rows) / len(rows),
        "fresh_work_total": total,
        "incremental_saved_work_total": saved,
        "pure_incremental_work_ratio": (total - saved) / total if total else None,
        "rows": rows,
        "next_gate": "V7_FRESH_LIVE_QUALIFICATION_REQUIRED",
    }
    (root / "campaign_summary.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="ascii")
    (root / "campaign_summary.json").chmod(0o600)
    (root / "campaign_summary.md").write_text(
        "# V7-B Offline Campaign\n\n"
        f"Pairs: {len(rows)}; canonical differential: {summary['all_differential_equal']}; "
        f"stable IR fraction: {summary['stable_ir_fraction_mean']:.3f}; "
        f"affected work fraction: {summary['affected_work_fraction_mean']:.3f}; "
        f"saved work: {saved:.1f}/{total:.1f}.\n\n"
        "This is provider-free observer evidence. It does not authorize live treatment or a paper performance claim.\n",
        encoding="ascii",
    )
    (root / "campaign_summary.md").chmod(0o600)
    print(json.dumps({"status": "COMPLETED", "output_root": str(root), **{k: summary[k] for k in ("source_pairs", "all_differential_equal", "stable_ir_fraction_mean", "affected_work_fraction_mean", "incremental_saved_work_total")}}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
