"""Bounded synthetic observer campaign for R1-R3 contract validation.

It exercises the sealed reference model only. No provider, Graphiti, native
publication, treatment flag, replay, or persistent Apply is reachable.
"""

from __future__ import annotations

import random
from typing import Any, Iterable

from .analysis import certifiable_stable_portion, confusion_matrix, false_stable_rate, mutation_locality, semantic_change_amplification
from .certificates import CertificateStatus, Witness, certify_exact_topk
from .opportunity import DagNode, counterfactual
from .reference_model import build_trace, maintain_trace
from .semantics import SnapshotToken, alpha_equivalent, canonical_trace
from .state_delta import DeltaChange, StateDelta


def _fixture(sequence: int, *, changed: bool) -> tuple[dict[str, Any], StateDelta]:
    old = {"nodes": {"n1": {"name": f"name-{sequence}", "embedding": (1.0, 0.0)}}}
    if not changed:
        return old, StateDelta(sequence, sequence, ())
    delta = StateDelta(
        sequence,
        sequence + 1,
        (
            DeltaChange(
                "node",
                "n1",
                frozenset({"name", "embedding"}),
                before={"name": f"name-{sequence}", "embedding": (1.0, 0.0)},
                after={"name": f"name-{sequence}-changed", "embedding": (0.0, 1.0)},
            ),
        ),
    )
    return old, delta


def run_r2_observer(*, seed: int) -> dict[str, Any]:
    random.Random(seed)  # seed is recorded even though this reference fixture is deterministic
    old, delta = _fixture(0, changed=True)
    snapshot = SnapshotToken(1, "reference-db", 1)
    old_trace = build_trace(old, "episode-0", SnapshotToken(0, "reference-db", 0))
    fresh_trace = build_trace({"nodes": {"n1": {"name": "name-0-changed", "embedding": (0.0, 1.0)}}}, "episode-0", snapshot)
    maintained = maintain_trace(old, delta, "episode-0", snapshot)
    return {
        "schema_version": "membind.v7.r2-observer.v1",
        "status": "OBSERVER_ONLY",
        "seed": seed,
        "source_count": 2,
        "treatment_calls": 0,
        "publication_calls": 0,
        "old_trace_digest": canonical_trace(old_trace),
        "fresh_trace_digest": canonical_trace(fresh_trace),
        "maintained_trace_digest": canonical_trace(maintained),
        "canonical_seam_equal": alpha_equivalent(maintained.seam_output, fresh_trace.seam_output),
        "mutation_locality": mutation_locality(changed_objects=1, total_objects=1),
        "missing_side_is_coverage_only": True,
        "completion_order_counted": False,
    }


def run_r3_block(*, seed: int, source_count: int = 6) -> dict[str, Any]:
    if source_count <= 0:
        raise ValueError("source_count must be positive")
    rng = random.Random(seed)
    predictions: list[str] = []
    truths: list[str] = []
    rows: list[dict[str, Any]] = []
    direct_wall = 0.0
    affected_wall = 0.0
    for sequence in range(source_count):
        changed = bool(rng.randrange(2))
        old, delta = _fixture(sequence, changed=changed)
        witness = Witness("node_cosine", (1.0, 0.0), ("n1",), ("n1",), 1, 0.8, (), "embed-1", "idx-1")
        certificate = certify_exact_topk(witness, delta)
        prediction = certificate.status.value
        truth = "CHANGED" if changed else "SAME"
        predictions.append(prediction)
        truths.append(truth)
        rows.append({"source_sequence": sequence, "prediction": prediction, "truth": truth, "changed": changed, "certificate_reason": certificate.reason})
        if changed:
            direct_wall += 1.0
            affected_wall += 2.0
    matrix = confusion_matrix(predictions, truths)
    dag = (
        DagNode("read", (), 2.0),
        DagNode("demand", ("read",), 3.0),
        DagNode("publish", ("demand",), 1.0),
    )
    opportunity = counterfactual(dag, removed={"read"})
    return {
        "schema_version": "membind.v7.r3-observer-block.v1",
        "status": "OBSERVER_ONLY",
        "seed": seed,
        "source_count": source_count,
        "treatment_calls": 0,
        "publication_calls": 0,
        "predictions": predictions,
        "truths": truths,
        "rows": rows,
        "confusion_matrix": matrix,
        "false_stable_rate": false_stable_rate(matrix),
        "certifiable_stable_portion": certifiable_stable_portion([(0.0, 1.0)] if not direct_wall else [], [(0.0, float(source_count))]),
        "semantic_change_amplification": semantic_change_amplification({"wall": (direct_wall, affected_wall)}),
        "counterfactual": {"baseline_cp": opportunity.baseline.cost, "candidate_cp": opportunity.candidate.cost, "gross_saved_cp": opportunity.saved_cost, "path": opportunity.path},
        "live_interference_measured": False,
        "claim_boundary": "synthetic_reference_contract_only",
    }


def run_observer_campaign(*, seeds: Iterable[int] = (17, 23), source_count: int = 6) -> dict[str, Any]:
    seeds = tuple(seeds)
    return {
        "schema_version": "membind.v7.observer-campaign.v1",
        "status": "OBSERVER_ONLY",
        "treatment_calls": 0,
        "publication_calls": 0,
        "r2": run_r2_observer(seed=seeds[0]),
        "r3_blocks": [run_r3_block(seed=seed, source_count=source_count) for seed in seeds],
        "independent_blocks": len(seeds) == 2 and seeds[0] != seeds[1],
    }


__all__ = ["run_observer_campaign", "run_r2_observer", "run_r3_block"]
