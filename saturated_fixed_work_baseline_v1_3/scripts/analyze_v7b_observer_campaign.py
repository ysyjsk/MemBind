#!/usr/bin/env python3
"""Materialize explicit R2/R3 observer artifacts from the frozen offline run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _write(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=True, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.campaign_root.resolve()
    summary = json.loads((root / "campaign_summary.json").read_text(encoding="utf-8"))
    rows = list(summary.get("rows") or [])
    if len(rows) != 13 or not summary.get("all_differential_equal"):
        raise SystemExit("observer campaign is incomplete or differential failed")
    direct_cost = {"entities": 8.0, "relations": 7.0, "temporal": 4.0}
    enriched: list[dict[str, Any]] = []
    for row in rows:
        direct = direct_cost.get(str(row["mutation"]), 0.0)
        affected = float(row["incremental_work"])
        enriched.append(
            {
                **row,
                "direct_delta_work": direct,
                "semantic_change_amplification_work": (affected / direct if direct else None),
                "repaired_view_count": len(row.get("repaired_views") or []),
                "reused_view_count": len(row.get("reused_views") or []),
                "reconverged_view_count": len(row.get("reconverged_views") or []),
            }
        )
    by_block = {
        block: [row for row in enriched if row["block_id"] == block]
        for block in ("R2-two-source", "R3-A-six-source", "R3-B-six-source")
    }
    reconvergence = {
        block: {
            "pair_count": len(values),
            "exact_differential_count": sum(bool(row["canonical_equal"]) for row in values),
            "fallback_count": sum(bool(row["fallback"]) for row in values),
            "mean_repaired_views": sum(row["repaired_view_count"] for row in values) / len(values),
            "mean_reused_views": sum(row["reused_view_count"] for row in values) / len(values),
            "mean_reconverged_views": sum(row["reconverged_view_count"] for row in values) / len(values),
        }
        for block, values in by_block.items()
    }
    locality = {
        "schema_version": "membind.v7b.mutation-locality.v1",
        "campaign_scope": "PROVIDER_FREE_OBSERVER_ONLY",
        "state_domain_count": 4,
        "changed_domain_count": 1,
        "changed_domain_fraction": 0.25,
        "rows": [{"block_id": row["block_id"], "source_index": row["source_index"], "mutation": row["mutation"]} for row in enriched],
    }
    affected = {
        "schema_version": "membind.v7b.affected-set-oracle.v1",
        "campaign_scope": "PROVIDER_FREE_OBSERVER_ONLY",
        "rows": enriched,
        "mean_affected_work_fraction": summary["affected_work_fraction_mean"],
        "mean_stable_ir_fraction": summary["stable_ir_fraction_mean"],
    }
    sca = {
        "schema_version": "membind.v7b.sca-work.v1",
        "campaign_scope": "PROVIDER_FREE_OBSERVER_ONLY",
        "rows": enriched,
        "mean_sca_work": sum(
            row["semantic_change_amplification_work"]
            for row in enriched
            if row["semantic_change_amplification_work"] is not None
        ) / len(enriched),
    }
    reconvergence_report = {
        "schema_version": "membind.v7b.reconvergence-report.v1",
        "campaign_scope": "PROVIDER_FREE_OBSERVER_ONLY",
        "all_canonical_differential_equal": True,
        "by_block": reconvergence,
    }
    work_bound = {
        "schema_version": "membind.v7b.work-saving-bound.v1",
        "campaign_scope": "PROVIDER_FREE_OBSERVER_ONLY",
        "fresh_work_total": summary["fresh_work_total"],
        "incremental_repair_work_total": summary["fresh_work_total"] - summary["incremental_saved_work_total"],
        "gross_saved_work": summary["incremental_saved_work_total"],
        "online_wall_clock_claim": False,
        "provider_work_claim": False,
    }
    fallback = {
        "schema_version": "membind.v7b.fallback-simulation.v1",
        "campaign_scope": "PROVIDER_FREE_OBSERVER_ONLY",
        "fallback_count": summary["fallback_count"],
        "policy_tested": "default_fallback_policy",
        "semantic_safety": "canonical differential required after every maintain",
    }
    decision = {
        "schema_version": "membind.v7b.r3-decision-input.v1",
        "campaign_scope": "PROVIDER_FREE_OBSERVER_ONLY",
        "status": "OBSERVER_PASS_LIVE_QUALITY_AND_ECONOMICS_PENDING",
        "canonical_differential": "PASS",
        "stable_ir_fraction_mean": summary["stable_ir_fraction_mean"],
        "affected_work_fraction_mean": summary["affected_work_fraction_mean"],
        "reconvergence": reconvergence_report,
        "online_treatment_authorized": False,
        "required_next_gate": "V7_FRESH_QUALITY_AND_TWO_SOURCE_ONLINE_ECONOMICS",
    }
    for name, payload in {
        "MUTATION_LOCALITY.json": locality,
        "AFFECTED_SET_ORACLE.json": affected,
        "SCA_WORK.json": sca,
        "RECONVERGENCE_REPORT.json": reconvergence_report,
        "WORK_SAVING_BOUND.json": work_bound,
        "FALLBACK_SIMULATION.json": fallback,
        "R3_DECISION_INPUT.json": decision,
    }.items():
        _write(root / name, payload)
    seal = {
        "schema_version": "membind.v7b.observer-seal.v1",
        "status": "PASS",
        "campaign_summary_sha256": _sha(summary),
        "artifact_count": 7,
        "provider_calls": 0,
        "treatment_calls": 0,
        "online_treatment_authorized": False,
    }
    _write(root / "OBSERVER_SEAL.json", seal)
    print(json.dumps({"status": "PASS", "pairs": len(rows), "stable_ir_fraction": summary["stable_ir_fraction_mean"], "affected_work_fraction": summary["affected_work_fraction_mean"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
