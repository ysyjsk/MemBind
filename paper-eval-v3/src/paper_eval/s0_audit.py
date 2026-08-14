"""Lightweight S0 identity/reuse audit; it performs no live qualification."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope, sha256_file
from .roles import build_role_registry


DEFAULT_REUSE = {
    "C0_serving_viability": "REUSE",
    "C1_instrumentation_qualification": "REUSE",
    "C2_harness_schema_checkpoint": "REUSE",
    "C2_numeric_results": "HISTORICAL_ONLY",
    "C3_dependency_framework": "REUSE",
    "C3_numeric_bounds": "HISTORICAL_ONLY",
    "C4_results": "HISTORICAL_NON_MERGEABLE_ONLY",
    "C5_durability_concurrency_framework": "REUSE",
    "C5_results": "HISTORICAL_PROBLEM_EVIDENCE_ONLY",
}


def build_s0_artifacts(
    *,
    repo_root: Path,
    protocol_path: Path,
    output_root: Path,
    dataset_path: Path,
    exposed_ids: Iterable[str],
    pilot_ids: Iterable[str] = (),
    final_ids: Iterable[str] = (),
    git_commit: str,
    working_tree_status: str = "unknown",
    identities: Mapping[str, Any] | None = None,
    source_hashes: Mapping[str, str] | None = None,
    reuse_decisions: Mapping[str, str] | None = None,
    role_metadata: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
    planned_or_identity_only_ids: Iterable[str] = (),
    exposure_evidence: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Write the three frozen S0 artifacts and return their envelopes."""

    role_registry = build_role_registry(
        inspected_ids=exposed_ids,
        pilot_ids=pilot_ids,
        final_ids=final_ids,
    )
    protocol_hash = sha256_file(protocol_path)
    run_id = f"s0-{protocol_hash[:16]}"
    sources = {
        "protocol": protocol_hash,
        "dataset": sha256_file(dataset_path),
        **dict(source_hashes or {}),
    }
    current_payload = {
        "stage": "S0",
        "generated_at": generated_at or "UNRECORDED",
        "working_tree_status": working_tree_status,
        "working_tree_dirty_is_recorded_not_failed": True,
        "repo_root": str(repo_root),
        "source_hashes": sources,
        "runtime_identities": dict(identities or {}),
        "live_actions_performed": 0,
    }
    reuse_payload = {
        "stage": "S0",
        "decisions": dict(reuse_decisions or DEFAULT_REUSE),
        "c2_exact_u0_reuse_decision": "DEFERRED_TO_S2",
        "policy": "requalify_only_affected_component_on_relevant_drift",
        "c6_scheduled": False,
    }
    roles_payload = {
        "stage": "S0",
        "roles": role_registry,
        "actual_outcome_exposed_ids": sorted(set(exposed_ids)),
        "planned_or_identity_only_seen_ids": sorted(
            set(planned_or_identity_only_ids)
        ),
        "exposure_evidence": {
            str(history_id): sorted(str(path) for path in paths)
            for history_id, paths in sorted((exposure_evidence or {}).items())
        },
        "synthetic_judge_fixture_is_not_benchmark_exposure": True,
        **dict(role_metadata or {}),
    }
    payloads = {
        "S0_CURRENT_STATE.json": current_payload,
        "S0_REUSE_AUDIT.json": reuse_payload,
        "DEVELOPMENT_EXPOSED_IDS.json": roles_payload,
    }
    finalized: dict[str, dict[str, Any]] = {}
    for name, payload in payloads.items():
        envelope = finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=run_id,
        )
        atomic_write_json(output_root / name, envelope)
        finalized[name] = envelope
    return finalized
