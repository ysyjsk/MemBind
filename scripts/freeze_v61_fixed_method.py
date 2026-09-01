#!/usr/bin/env python3
"""Freeze the evidence-selected fixed V6.1 method for a future canary."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "saturated_fixed_work_baseline_v1_3/structured_output_recovery"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def read(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main() -> int:
    identity = read("EVALUATED_IMPLEMENTATION_IDENTITY.json")
    gates = read("FOUR_GATE_RESULT.json")
    h5 = read("ADAPTIVE_DECISION.json")
    stress = read("SCHEDULER_STRESS_TEST_RESULT.json")
    generated_at = datetime.now(timezone.utc).isoformat()
    fixed_policy = {"lookahead": 2, "future_cap": 1, "native_future_quota": 0}
    invariants = {
        "schema_version": "membind.method-invariants.v1",
        "method_identity": "V6_FIXED_POLICY",
        "logical_work_contract": "all declared source inputs remain covered; no semantic source omission",
        "classification": {"P0": "authoritative NATIVE_FRONTIER or dependency-unblocking frontier work", "P1": "DISABLED_UNPROVEN_DIRECT_CONSUMER_EDGE", "P2": "legal future prepare work without certified immediate consumer edge"},
        "publication": "ordered durable authoritative publication; future completion cannot mutate authoritative state",
        "fresh_write_uuid": "Graphiti uuid omitted; local stable idempotency key is not a Graphiti UUID",
        "recovery": "NO_RESUME_FORMAL_ATTEMPT",
        "admission": "logical source lease is separate from physical request/token permit",
        "priority_safety": ["P0 ready/queued blocks new P2 admission", "P1 is disabled until direct-consumer proof exists", "already submitted P2 is counted as unavoidable_future_blocking"],
        "resource_safety": ["physical request slots bounded by CapacityAuthority", "request_tokens = prompt residency + bounded decode reserve", "acquire/release/cancel are exact and conservation checked"],
        "prediction_policy": "NO_ARRIVAL_PREDICTOR; no service-time EWMA or benchmark-selected threshold is a correctness condition",
    }
    deployment = {
        "schema_version": "membind.deployment-parameters.v1",
        "profile_id": "LOCAL_PROFILE_FROM_RUNTIME",
        "model": "runtime-pinned model profile; see current identity/config hashes",
        "endpoint_pool": "arm-agnostic shared pool; endpoint ids are deployment identity",
        "capacity_authority": "runtime.config.max_coroutines == Graphiti.max_coroutines",
        "token_envelope": {"local_kv_cache_tokens": 65968, "kv_headroom_tokens": 4528, "max_admitted_kv_tokens": 61440, "structured_decode_reserve_tokens": 4096},
        "provider_boundary": "FCFS/non-preemptible after transport submission",
        "formal_status": "NOT_AUTHORIZED",
    }
    heuristic = {
        "schema_version": "membind.heuristic-parameters.v1",
        "policy": fixed_policy,
        "role": "BOUNDED_ADMISSION_SAFEGUARD",
        "optimality_claim": False,
        "selection_basis": "source audit plus provider-free event oracle; no official headline result",
        "adaptive_search_terminal": "VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES",
    }
    dev_records = []
    for name, shape in (("DEV-D0", "two-source-independent"), ("DEV-D1", "three-source-frontier-chain"), ("DEV-D2", "variable-token-future-burst")):
        payload = {"id": name, "shape": shape, "content": f"membind-provider-free-fixture:{name}:{shape}"}
        dev_records.append({"id": name, "revision": "synthetic-v1", "source_hash": digest(payload), "session_hash": digest({"session": name, "shape": shape}), "content_hash": digest(payload), "selection_reason": "static dependency shape/token variability selected before candidate results", "official_overlap": "PROVEN_NONE_BY_SYNTHETIC_NAMESPACE_AND_CONTENT_HASH"})
    dev_manifest = {
        "schema_version": "membind.frozen-dev-workload-manifest.v1",
        "status": "FROZEN_BEFORE_LIVE_CANDIDATE",
        "selection": "synthetic provider-free fixtures; no official 5-record ids or content",
        "records": dev_records,
        "paired_protocol": {"order": "control_then_candidate_interleaved", "replicates": 3, "resources": "same fixture and same local runtime envelope", "metrics": ["critical_queue_wait", "future_outstanding", "token_work", "publication_order", "conservation"]},
        "content_non_overlap_proof": "all records use synthetic-v1 namespace and hashes not derived from official_5_contexts.json",
        "implementation_identity_sha256": digest(identity),
        "generated_at": generated_at,
    }
    method_spec = {
        "schema_version": "membind.final-method-spec.v1",
        "status": "PRECANARY_FROZEN",
        "method_identity": "V6_FIXED_POLICY",
        "policy": fixed_policy,
        "invariants_artifact": "METHOD_INVARIANTS.json",
        "deployment_artifact": "DEPLOYMENT_PARAMETERS.json",
        "heuristic_artifact": "HEURISTIC_PARAMETERS.json",
        "resource_and_routing_contract": "FROZEN_RESOURCE_AND_ROUTING_CONTRACT.json",
        "dev_manifest": "FROZEN_DEV_WORKLOAD_MANIFEST.json",
        "source_identity": {"head_commit": identity.get("head_commit"), "source_bundle_sha256": identity.get("source_bundle_sha256"), "tracked_diff_sha256": identity.get("tracked_diff_sha256")},
        "gates": {"four_gate_status": gates.get("status"), "h5_decision": h5.get("decision"), "stress_status": stress.get("status")},
        "formal_recovery_policy": "NO_RESUME_FORMAL_ATTEMPT",
        "generated_at": generated_at,
    }
    resource_contract = {"schema_version": "membind.frozen-resource-routing-contract.v1", "status": "FROZEN_FOR_ENGINEERING_CANARY_ONLY", "shared_envelope": "A/B/C arm-agnostic endpoint pool, capacity, model, token limits and cache policy", "logical_source_lease": "future_cap=1, source-only", "physical_admission": "CapacityAuthority slots plus weighted request_tokens", "priority": ["P0>NATIVE_FRONTIER", "P2>FUTURE_PREPARE"], "p1": "DISABLED_UNPROVEN", "backpressure": "stop new P2 at future/token bound; resume after consumer/frontier release", "non_preemptible": "provider queue exposure recorded as unavoidable_future_blocking", "no_arrival_predictor": True}
    cache = {"schema_version": "membind.frozen-cache-warmup-protocol.v1", "warmup_runs": 0, "cache_policy": "same arm-agnostic policy for A/B/C; counters recorded", "construction_timing_excludes": ["process startup", "model startup", "warmup", "preflight", "FULL QA", "cooldown", "offline analysis"]}
    metrics = {"schema_version": "membind.frozen-metric-definitions.v1", "T_build": "FORMAL_CONSTRUCTION_START to last expected source PUBLICATION_DURABLE", "TTFDP": "construction start to first durable publication", "primary_estimand": "same-history same-replicate paired A_vs_C T_build ratio; B is ceiling", "mechanism_metrics": ["critical_queue_wait", "future_queue_occupancy", "new_p2_admitted_while_p0_waiting", "unavoidable_future_blocking_ns", "future_result_consumption_lag", "completed_but_not_consumable_future_work", "speculation_debt_count_work_tokens_bytes_age", "scheduler_idle_opportunity"], "quality": "PAIRED_QUALITY_DELTA_ONLY"}
    seal = {"schema_version": "membind.precanary-method-seal.v1", "status": "SEALED_PENDING_ENGINEERING_CANARY", "method_identity": "V6_FIXED_POLICY", "implementation_identity_sha256": digest(identity), "final_method_spec_sha256": digest(method_spec), "resource_contract_sha256": digest(resource_contract), "dev_manifest_sha256": digest(dev_manifest), "canary_authorized": False, "formal_three_arm_authorized": False, "provider_calls": 0, "generated_at": generated_at}
    artifacts = {"METHOD_INVARIANTS.json": invariants, "DEPLOYMENT_PARAMETERS.json": deployment, "HEURISTIC_PARAMETERS.json": heuristic, "FROZEN_DEV_WORKLOAD_MANIFEST.json": dev_manifest, "FINAL_METHOD_SPEC.json": method_spec, "FROZEN_RESOURCE_AND_ROUTING_CONTRACT.json": resource_contract, "FROZEN_CACHE_WARMUP_PROTOCOL.json": cache, "FROZEN_METRIC_DEFINITIONS.json": metrics, "PRECANARY_METHOD_SEAL.json": seal, "V61_PARAMETER_IDENTITY.json": {"schema_version": "membind.v61-parameter-identity.v1", "status": "FIXED_POLICY_SEALED", "policy": fixed_policy, "source_identity_sha256": digest(identity), "h5_decision": h5.get("decision"), "provider_calls": 0, "generated_at": generated_at}}
    for filename, value in artifacts.items():
        (OUT / filename).write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (OUT / "FROZEN_DEV_WORKLOAD_MANIFEST.md").write_text("# Frozen Development Workload Manifest\n\nStatus: `FROZEN_BEFORE_LIVE_CANDIDATE`.\n\nThree synthetic fixtures are selected by static dependency shape and token variability. Their namespace and content hashes prove no overlap with the official five-record dataset. All candidates must use this same manifest and paired protocol.\n", encoding="utf-8")
    (OUT / "FINAL_METHOD_SPEC.md").write_text("# Final Method Spec\n\nMethod identity: `V6_FIXED_POLICY`.\n\nThe fixed policy is `lookahead=2`, `future_cap=1`, `native_future_quota=0`. These are bounded-admission safeguards, not optimality claims. P1 is disabled without a direct-consumer proof; P0/P2 priority, weighted physical permits, ordered durable publication, and `NO_RESUME_FORMAL_ATTEMPT` are frozen.\n\nStatus: `PRECANARY_FROZEN`; engineering canary and formal campaign remain unauthorized.\n", encoding="utf-8")
    ledger = OUT / "AUTORESEARCH_LEDGER.jsonl"
    entry = {"schema_version": "membind.autoresearch.ledger.v1", "event": "FIXED_POLICY_SELECTED_WITH_DISCLOSED_HEURISTICS", "candidate": "V6_FIXED_POLICY", "decision": "VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES", "identity_sha256": digest(identity), "stress_artifact": "SCHEDULER_STRESS_TEST_RESULT.json", "dev_manifest": "FROZEN_DEV_WORKLOAD_MANIFEST.json", "provider_calls": 0, "generated_at": generated_at}
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")
    autoresearch = {"schema_version": "membind.autoresearch.state.v1", "status": "FIXED_POLICY_SELECTED_WITH_DISCLOSED_HEURISTICS", "epoch": "h5-fixed-2026-09-02", "candidates_evaluated": ["r66a (historical rejected)", "r67-r69 (historical rejected)"], "new_candidates": 0, "budget": {"adaptive_candidates": 0, "reason": "source audit and negative evidence do not authorize repeating service-EWMA family"}, "next_action": "engineering canary only after service/resource checks; no official performance selection", "ledger": "AUTORESEARCH_LEDGER.jsonl", "provider_calls": 0, "generated_at": generated_at}
    (OUT / "AUTORESEARCH_STATE.json").write_text(json.dumps(autoresearch, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
